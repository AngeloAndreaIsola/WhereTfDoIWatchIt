"""YouTube dedicated collector — title-targeted "Free with ads" lookup (Phase E).

Second title-targeted :class:`AvailabilitySource` (after streaming_availability),
modeling YouTube's free ad-supported movie program without any API key, login,
or cookie:

    region probe (GET https://www.youtube.com/ -> ytcfg ``"GL"``)
        -> innertube search (POST /youtubei/v1/search, keyless)
        -> badge filter: ``BADGE_STYLE_TYPE_YPC`` / label "Free with ads"
        -> title verification against the requested TMDb title
        -> one DiscoveredContent + one ProviderOffer (movie granularity)
        -> AvailabilityRefreshService (targeted path: ProviderContent +
           OfferRepository; offers already carry the requested tmdb id)

API contract (public endpoints, researched 2026-10, see docs/PLAN.md Phase E):

- ``POST https://www.youtube.com/youtubei/v1/search?prettyPrint=false`` with a
  WEB client context and **no** ``key`` parameter returns
  ``contents.twoColumnSearchResultsRenderer.itemSectionRenderer.contents[]``
  of ``movieRenderer``/``videoRenderer`` entries. A payload without that shell
  is an upstream shape we cannot use and raises :class:`SourceFetchError` —
  an API-level failure (``{"error": ...}``) likewise — never an empty result.
- Region is decided by **egress IP**, not request fields: ``gl``/``regionCode``
  overrides were verified to be ignored, so the only honest country for an
  offer is the egress country observed from the homepage ytcfg (``"GL"``, with
  ``countryCode`` as fallback). A homepage whose country cannot be parsed is a
  retrieval failure (the offer country would be unknowable).

Product semantics (verified against Google's help pages and live responses,
not guessed):

- **Free program countries: US, AU, JP, DE, GB.** Google's "free with ads"
  program only operates in those five; the dev machine's NL egress, for
  instance, returns zero free-badged results. An observed region outside the
  set therefore yields an empty (successful) result *before* the search is
  spent — there is nothing to observe there. ``FREE_PROGRAM_COUNTRIES`` is the
  one line to widen if the program grows.
- **Badge vocabulary is the offer-type signal**: ``Free with ads`` (YPC) is
  the only free state; ``Buy`` / ``Buy or rent`` (YPC) are rent/buy offers,
  which this project excludes by design (SPEC.md) — they are parsed and
  logged, never persisted. Media badges (``PG``, ``CC``, ``4K``) are ratings /
  caption markers, not offer types; ``4K`` is recorded as quality.
- **Movies only**: the collector is queried with a requested movie title and
  only ever emits ``movie`` rows. Series/season/episode requests return an
  empty result without network — search would surface clips and unauthorized
  uploads, not modelled series availability.
- **No language or date metadata exists in the search payload**, so audio/
  subtitle languages and availability windows are always ``None``
  (``supports_languages=False``); the ``CC`` badge proves captions exist but
  not in which language.

Deliberate limitations, all documented rather than papered over:

- **Title verification, not TMDb verification**: candidates are matched by
  normalized title equality (trailing ``"(YYYY)"`` stripped; a candidate's
  explicit year must agree with the request's when both exist). The request's
  year is also folded into the search query, but the payload exposes no year
  field for unannotated titles, so a homonymous film cannot be fully ruled
  out by data — this is a title match, recorded with the requested
  ``tmdb_id`` after verification (``supports_tmdb_ids=False``: the source
  provides no external ids of its own).
- **One search, first page only**: search continuations exist but a
  title-targeted lookup takes page 1 (free-badged ``movieRenderer`` entries
  were only observed on page 1 during research).
- **The result is never authoritative**: a single query's matches are a
  subset of the program's catalogue, so absence never deactivates anything
  (``supports_authoritative_snapshots=False``).
- **Offer country is the egress country**: per-video
  ``player.microformat.availableCountries`` (which could narrow further) is
  deliberately not fetched — the badge itself is only rendered where the
  video is free, which is the evidence we record.
- **Budget/rate limiting stay with the sweep**: one refresh consumes one
  budget unit and one shared rate-limiter token (the collector performs at
  most two HTTP calls: region probe + search) and implements no quota
  accounting of its own.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import requests

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_content_type,
    normalize_country,
    normalize_offer_type,
)
from .base import (
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

HOME_URL = "https://www.youtube.com/"
SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
CLIENT_NAME = "WEB"
CLIENT_VERSION = "2.20261001.01.00"
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Countries where Google's free ad-supported movie program operates
#: (Google Play help, "Free with ads" program). Observed region outside this
#: set -> empty result without spending a search request.
FREE_PROGRAM_COUNTRIES = frozenset({"US", "AU", "JP", "DE", "GB"})

#: YPC badge label -> canonical ``OfferType`` token. Only the free state is
#: ever persisted; the paid labels are recognized so a rent/buy listing is
#: logged as an observed-but-excluded offer (SPEC excludes rent/buy by
#: design) instead of being mistaken for a missing badge.
FREE_BADGE_LABELS = frozenset({"free with ads"})
PAID_BADGE_LABELS = frozenset({"buy", "buy or rent"})

REGION_PATTERNS = (re.compile(r'"GL"\s*:\s*"([A-Z]{2})"'),
                   re.compile(r'"countryCode"\s*:\s*"([A-Z]{2})"'))
_TRAILING_YEAR = re.compile(r"\s*[([]\s*((?:19|20)\d{2})\s*[)\]]\s*$")


class YouTubeClient:
    """Thin client for YouTube's public innertube endpoints.

    ``http_get``/``http_post`` are injectable callables (``url -> str`` and
    ``url, payload -> dict``); tests pass a fixture router so no test ever
    performs network I/O. ``region`` short-closes the homepage probe when the
    caller already knows the egress country. Retrieval failures (HTTP error,
    transport error, unparsable payload) always surface as
    :class:`SourceFetchError` — never as an empty-but-valid result.
    """

    def __init__(self, *, region: Optional[str] = None, http_get=None,
                 http_post=None, timeout: int = DEFAULT_TIMEOUT):
        self._explicit_region = _validate_region(region) if region else None
        self.http_get = http_get
        self.http_post = http_post
        self.timeout = timeout
        self._region: Optional[str] = None

    def region(self) -> str:
        """Egress country (two-letter, upper) as YouTube itself reports it.

        Detected from the homepage ytcfg once per client instance and cached:
        the country is a property of the connection, not of the query.
        """
        if self._region is not None:
            return self._region
        if self._explicit_region is not None:
            self._region = self._explicit_region
            return self._region
        html = self._get(HOME_URL)
        if not isinstance(html, str):
            raise SourceFetchError("Unexpected YouTube homepage response type")
        for pattern in REGION_PATTERNS:
            match = pattern.search(html)
            if match:
                self._region = match.group(1).upper()
                return self._region
        raise SourceFetchError("YouTube homepage did not expose a country code")

    def search(self, query: str) -> dict:
        """One keyless innertube search; returns the raw response payload."""
        payload = {
            "context": {
                "client": {
                    "hl": "en",
                    "gl": self.region(),
                    "clientName": CLIENT_NAME,
                    "clientVersion": CLIENT_VERSION,
                    "platform": "DESKTOP",
                },
                "user": {"lockedSafetyMode": False},
                "request": {"useSsl": True},
            },
            "query": query,
        }
        response = self._post(SEARCH_URL, payload)
        if not isinstance(response, dict):
            raise SourceFetchError("Unexpected YouTube search response type")
        if "error" in response:
            error = response.get("error") or {}
            raise SourceFetchError(
                "YouTube search returned an API error: "
                f"{error.get('code')} {error.get('message')}"
            )
        return response

    # ------------------------------------------------------------- transport

    def _get(self, url: str):
        if self.http_get is not None:
            return self.http_get(url)
        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(f"YouTube request failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceFetchError(f"YouTube returned HTTP {response.status_code} for {url}")
        return response.text

    def _post(self, url: str, payload: dict) -> dict:
        if self.http_post is not None:
            try:
                return self.http_post(url, payload)
            except SourceFetchError:
                raise
            except Exception as exc:  # fixture routers raise plain exceptions
                raise SourceFetchError(f"YouTube search request failed: {exc}") from exc
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "Origin": "https://www.youtube.com",
                    "Referer": "https://www.youtube.com/",
                    "Accept-Language": "en-US,en;q=0.9",
                    "X-YouTube-Client-Name": "1",
                    "X-YouTube-Client-Version": CLIENT_VERSION,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(f"YouTube search request failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceFetchError(f"YouTube search returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError("YouTube search returned a non-JSON body") from exc


class YouTubeSource:
    """Title-targeted YouTube source (``content=None`` fetches are rejected)."""

    slug = "youtube"
    name = "YouTube"
    source_type = SourceType.WEB
    capabilities = SourceCapabilities(
        # One provider, offers only where the free program operates.
        supported_providers=frozenset({"youtube"}),
        supported_countries=frozenset(FREE_PROGRAM_COUNTRIES),
        # The source is queried with a movie title and only emits movie rows;
        # series requests short-circuit to an empty result (no network).
        supported_content_types=frozenset({ContentType.MOVIE}),
        supports_incremental_sync=False,
        supports_catalogue_sync=False,
        # One query's matches are a subset of the program's catalogue:
        # absence must never deactivate offers.
        supports_authoritative_snapshots=False,
        # The source provides no external ids of its own; offers carry the
        # requested tmdb id after title verification.
        supports_tmdb_ids=False,
        supports_seasons=False,
        supports_episodes=False,
        # Search payloads expose no audio/subtitle language metadata.
        supports_languages=False,
    )

    def __init__(self, client=None):
        self.client = client or YouTubeClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is None:
            raise ValueError(
                "youtube is title-targeted: a content request is required"
            )
        request = content if isinstance(content, ContentRequest) else ContentRequest.from_content(content)
        content_type = normalize_content_type(request.content_type)
        if content_type != ContentType.MOVIE:
            # Outside the declared scope: an empty result is the honest
            # answer and no request is spent on a query we would not model.
            logger.info(
                "youtube_unsupported_content_type tmdb_id=%s content_type=%s",
                request.tmdb_id, request.content_type,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)
        if not (request.title or "").strip():
            logger.warning("youtube_missing_title tmdb_id=%s", request.tmdb_id)
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        region = self.client.region()
        if region not in FREE_PROGRAM_COUNTRIES:
            # Egress outside the free program: no free-badged result can be
            # rendered there, so the source legitimately reports nothing.
            logger.info(
                "youtube_region_outside_program tmdb_id=%s region=%s",
                request.tmdb_id, region,
            )
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        query = request.title if request.year is None else f"{request.title} {request.year}"
        payload = self.client.search(query)
        candidate = _find_free_candidate(payload, request)
        if candidate is None:
            return SourceFetchResult(sync_mode=SyncMode.TARGETED)

        renderer, video_id = candidate
        offer = ProviderOffer(
            provider_id=self.slug,
            provider_content_id=video_id,
            country=normalize_country(region),
            content_type=ContentType.MOVIE,
            tmdb_id=request.tmdb_id,
            season_number=None,
            episode_number=None,
            offer_type=normalize_offer_type("free_with_ads"),
            provider_url=WATCH_URL.format(video_id=video_id),
            quality=_quality(renderer),
            audio_languages=None,
            subtitle_languages=None,
            available_from=None,
            available_until=None,
            source_record_id=video_id,
            source_version=CLIENT_VERSION,
        )
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=video_id,
            content_type=ContentType.MOVIE,
            title=_title(renderer),
            url=WATCH_URL.format(video_id=video_id),
            external_ids={"tmdb": str(request.tmdb_id)},
            source_id=self.slug,
        )
        return SourceFetchResult(
            contents=[content],
            offers=[offer],
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.TARGETED,
            cursor=None,
        )


# ----------------------------------------------------------------- parsing


def _search_items(payload: dict) -> list[dict]:
    """``itemSectionRenderer.contents`` of a well-formed search response.

    The shell exists even when nothing matches (the section then carries
    didYouMean/message renderers), so a payload *without* it is an upstream
    shape change — loud failure, never a silent empty catalogue.
    """
    try:
        items = payload["contents"]["twoColumnSearchResultsRenderer"][
            "itemSectionRenderer"
        ]["contents"]
    except (KeyError, TypeError) as exc:
        raise SourceFetchError("Unexpected YouTube search payload shape") from exc
    if not isinstance(items, list):
        raise SourceFetchError("Unexpected YouTube search item section type")
    return [item for item in items if isinstance(item, dict)]


def _find_free_candidate(payload: dict, request: ContentRequest):
    """First verified free-with-ads candidate: ``(renderer, video_id)``.

    Verification order per entry: a free YPC badge, then normalized title
    equality with the request (trailing ``"(YYYY)"`` stripped, an explicit
    candidate year required to agree with the request's). Paid YPC badges on
    a title-matching entry are logged as observed-but-excluded (rent/buy are
    out of scope by design) and the scan continues — a later free listing may
    still exist.
    """
    wanted = _normalize_title(request.title)
    for item in _search_items(payload):
        for key in ("movieRenderer", "videoRenderer"):
            renderer = item.get(key)
            if not isinstance(renderer, dict):
                continue
            label = _ypc_label(renderer)
            if label is None:
                continue
            if label in PAID_BADGE_LABELS:
                if _normalize_title(_title(renderer)) == wanted:
                    logger.info(
                        "youtube_paid_offer_excluded video_id=%s label=%r",
                        renderer.get("videoId"), label,
                    )
                continue
            if label not in FREE_BADGE_LABELS:
                logger.warning(
                    "youtube_unknown_ypc_label video_id=%s label=%r",
                    renderer.get("videoId"), label,
                )
                continue
            if _titles_match(renderer, request, wanted):
                video_id = str(renderer.get("videoId") or "").strip()
                if not video_id:
                    continue
                return renderer, video_id
    return None


def _titles_match(renderer: dict, request: ContentRequest, wanted: str) -> bool:
    candidate = _title(renderer)
    candidate_year = None
    match = _TRAILING_YEAR.search(candidate)
    if match:
        candidate_year = int(match.group(1))
        candidate = candidate[: match.start()]
    if _normalize_title(candidate) != wanted:
        return False
    if request.year and candidate_year and candidate_year != request.year:
        logger.info(
            "youtube_year_mismatch video_id=%s wanted=%s candidate=%s",
            renderer.get("videoId"), request.year, candidate_year,
        )
        return False
    return True


def _ypc_label(renderer: dict) -> Optional[str]:
    """Label of the YPC (commerce) badge, or ``None`` when absent."""
    for badge in renderer.get("badges") or []:
        if not isinstance(badge, dict):
            continue
        meta = badge.get("metadataBadgeRenderer")
        if not isinstance(meta, dict):
            continue
        if str(meta.get("style") or "") == "BADGE_STYLE_TYPE_YPC":
            return str(meta.get("label") or "").strip().lower()
    return None


def _quality(renderer: dict) -> str:
    for badge in renderer.get("badges") or []:
        if not isinstance(badge, dict):
            continue
        meta = badge.get("metadataBadgeRenderer")
        if isinstance(meta, dict) and str(meta.get("label") or "").upper() == "4K":
            return "4k"
    return ""


def _title(renderer: dict) -> str:
    title = renderer.get("title")
    if not isinstance(title, dict):
        return ""
    if isinstance(title.get("simpleText"), str):
        return title["simpleText"].strip()
    runs = title.get("runs")
    if isinstance(runs, list):
        return "".join(
            str(run.get("text") or "") for run in runs if isinstance(run, dict)
        ).strip()
    return ""


def _normalize_title(value: str) -> str:
    """Casefolded, accent-stripped, punctuation-insensitive title key."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold()
    keep = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in lowered)
    return " ".join(keep.split())


def _validate_region(value: str) -> str:
    code = str(value or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"Unsupported YouTube region: {value!r}")
    return code
