"""CBC Gem dedicated collector — catalogue -> provider content -> offers (Phase 5).

First provider-specific :class:`AvailabilitySource`, proving the Phase 4
architecture for a source that is *catalogue-driven* rather than title-targeted:

    CBC category index (paginated cards)
        -> one CBC catalogue detail payload per card (CBC content ID = URL slug)
        -> DiscoveredContent (provider-native discovery record, no offers implied)
        -> ProviderOffer rows (series / season / episode / movie granularity)
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

API contract (public endpoints, no auth, ``device=web``):

- ``GET {BASE}/category/{slug}?device=web&pageNumber=N&pageSize=M`` ->
  ``content[0].items = {totalPages, totalRecords, pageNumber, pageSize, results[]}``.
  Three master category indices cover the catalogue and are mutually disjoint:
  ``shows`` (series), ``all-films`` (films), ``kids-all`` (kids). Genre and
  ``section/*`` indices exist but overlap them (~200 extra editorial items,
  awards/live specials); they are deliberately excluded to bound refresh cost —
  absence never deactivates anything (see below), so widening coverage later is
  a non-breaking addition.
- ``GET {BASE}/show/{slug}?device=web`` -> series *and* movie detail in one
  payload shape: ``structuredMetadata`` (``@type`` = ``Movie`` / ``TVSeries``,
  ``inLanguage``), ``hasOnlyATrailer``, ``content[0].lineups[]`` = seasons with
  ``seasonNumber``/``url`` and their episode items (``url``, ``episodeNumber``,
  ``mediaType``, ``tier``, ``metadata.availabilityDate``/``productionYear``).
- The documented search endpoint returns ``totalRecords: 0`` for every query
  shape tried, so discovery goes through category indices only.
- Page numbers beyond ``totalPages`` are rejected by the API (HTTP 400), so
  pagination terminates strictly from each page's own ``totalPages`` plus a
  repeated-page guard; a mid-pagination transport/HTTP failure raises
  :class:`SourceFetchError` (failure is never reported as an empty catalogue).

Product semantics behind ``permission.tier`` (verified against CBC's own copy
and help centre, not guessed):

- ``Standard`` / ``gem:standard`` — watchable without an account (kids
  catalogue, trailers, select live events) -> **free**.
- ``Member`` / ``gem:member`` — "This content is only available to our signed
  in members": a free registered CBC account, not a paid plan -> **free**.
- ``Premium`` / ``gem:premium`` — CBC Gem Premium (paid) -> **subscription**.

Deliberate limitations, all documented rather than papered over:

- **No expiry data exists anywhere in the CBC payloads** (only start dates:
  ``availabilityDate``), so ``available_until`` is always ``None``.
- **The catalogue is never an authoritative snapshot**: ``/browse`` shows the
  union of indices cannot be proven complete, and CBC exposes no
  "removed from catalogue" signal per item. The source therefore declares
  ``supports_authoritative_snapshots=False`` and results are never
  ``authoritative``/``complete_snapshot`` — unseen offers are never
  deactivated (absence is not evidence of unavailability).
- **No external IDs**: CBC exposes no TMDb ids (``supports_tmdb_ids=False``).
  Mapping happens in the refresh service via local title/year matching; an
  unmapped item yields no ``Offer`` rows (``Offer.tmdb_id`` is non-null) but
  still counts as discovered content.
- **``provider_content_id`` is the catalogue slug** (e.g.
  ``murdoch-mysteries``): it is CBC's stable identity for the item *and* the
  ``ContentMapping`` key. Season/episode rows share it and stay distinct
  through the offer identity's season/episode slots (§ identity unchanged).
- Unauthenticated catalogue responses report ``grantedRight: "None"`` for
  every card (session-dependent playback state, not an availability signal);
  catalogue presence plus a non-trailer item is treated as current
  availability. Episode/media probing endpoints require an auth claims token
  and are never called.
- CBC's egress geo-blocking is not observable from catalogue metadata (the
  full index was returned during research); offers are recorded for the
  source's own country scope, ``CA``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import requests

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
    normalize_content_type,
    normalize_country,
    normalize_languages,
    normalize_offer_type,
)
from .base import (
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://services.radio-canada.ca/ott/catalog/v2/gem"
WATCH_URL = "https://gem.cbc.ca/{path}"
DEFAULT_CATEGORIES = ("shows", "all-films", "kids-all")
DEFAULT_PAGE_SIZE = 100
DEFAULT_TIMEOUT = 30
# Safety net against a runaway category (real indices are far smaller).
MAX_PAGES_PER_CATEGORY = 500
SOURCE_VERSION = "ott-catalog-v2"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# ``permission.tier`` -> canonical offer type. Standard/Member are both free
# (no payment; Member only needs a free registered account); Premium is paid.
TIER_OFFER_TYPES = {
    "standard": "free",
    "member": "free",
    "premium": "subscription",
}

_STRUCTURAL_TYPES = {
    "Movie": ContentType.MOVIE,
    "TVSeries": ContentType.SERIES,
}


class CBCGemClient:
    """Thin client for the public CBC Gem OTT catalogue API.

    ``requester`` is a ``url -> dict`` callable; tests inject a fixture router
    so no test ever performs network I/O. Retrieval failures (HTTP error,
    transport error, malformed payload) always surface as
    :class:`SourceFetchError` — never as an empty-but-valid result.
    """

    def __init__(self, requester=None, page_size=DEFAULT_PAGE_SIZE,
                 timeout=DEFAULT_TIMEOUT):
        self.requester = requester
        self.page_size = page_size
        self.timeout = timeout

    def category_page(self, category: str, page_number: int) -> dict:
        """One page of a category index; returns the ``items`` payload."""
        url = (
            f"{BASE_URL}/category/{category}?device=web"
            f"&pageNumber={page_number}&pageSize={self.page_size}"
        )
        payload = self._get(url)
        try:
            items = payload["content"][0]["items"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SourceFetchError(
                f"Unexpected CBC Gem category payload for {category!r} "
                f"page {page_number}"
            ) from exc
        if not isinstance(items, dict):
            raise SourceFetchError(
                f"Unexpected CBC Gem category items for {category!r} "
                f"page {page_number}"
            )
        return items

    def item_detail(self, slug: str) -> dict:
        """Full detail payload for one catalogue item (series or movie)."""
        payload = self._get(f"{BASE_URL}/show/{slug}?device=web")
        if not isinstance(payload.get("structuredMetadata"), dict):
            raise SourceFetchError(f"Unexpected CBC Gem detail payload for {slug!r}")
        # ``content`` may be null for a legitimately-listed item that has
        # nothing playable yet (a "COMING SOON" series carries lineups only
        # once episodes exist): that is a valid detail, parsed as a catalogue
        # entry with no offers. Only a payload without structural metadata at
        # all is an upstream shape we cannot use.
        return payload

    def _get(self, url: str) -> dict:
        try:
            if self.requester is not None:
                payload = self.requester(url)
            else:
                payload = self._http(url)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"CBC Gem request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceFetchError(f"Unexpected CBC Gem response type for {url}")
        return payload

    def _http(self, url: str) -> dict:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SourceFetchError(
                f"CBC Gem returned HTTP {response.status_code} for {url}"
            )
        return response.json()


class CBCGemSource:
    """Catalogue-driven CBC Gem source (``content=None`` fetches only)."""

    slug = "cbc_gem"
    name = "CBC Gem"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"cbc_gem"}),
        supported_countries=frozenset({"CA"}),
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # The category indices cannot be proven complete and CBC exposes no
        # per-item removal signal: absence must never deactivate offers.
        supports_authoritative_snapshots=False,
        # CBC exposes no external ids; mapping happens in the refresh service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        supports_languages=True,
    )

    def __init__(self, client=None):
        self.client = client or CBCGemClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "cbc_gem is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints (CBC only ever reports
        # CA/cbc_gem); cursor is accepted but unused — CBC exposes page numbers,
        # not a resumable server-side cursor, so one fetch walks the whole
        # configured catalogue instead of inventing a cursor token.
        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        seen_slugs: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()

        for category in DEFAULT_CATEGORIES:
            page_number = 1
            while True:
                items = self.client.category_page(category, page_number)
                results = [
                    card for card in (items.get("results") or []) if isinstance(card, dict)
                ]
                if not results:
                    break
                signature = tuple(
                    str(card.get("url") or card.get("key") or "") for card in results
                )
                if signature in seen_pages:
                    logger.warning(
                        "cbc_gem_repeated_page category=%s page=%s",
                        category, page_number,
                    )
                    break
                seen_pages.add(signature)

                for card in results:
                    slug = str(card.get("url") or "").strip().strip("/")
                    if not slug or slug in seen_slugs:
                        continue
                    if any(ch in slug for ch in "?#&="):
                        # Editorial cards deep-link into live events / external
                        # surfaces, not stable catalogue items.
                        continue
                    seen_slugs.add(slug)
                    detail = self.client.item_detail(slug)
                    parsed_content, parsed_offers = self._parse_item(slug, card, detail)
                    if parsed_content is not None:
                        contents.append(parsed_content)
                    offers.extend(parsed_offers)

                total_pages = _as_int(items.get("totalPages"), default=1)
                if page_number >= total_pages or page_number >= MAX_PAGES_PER_CATEGORY:
                    break
                page_number += 1

        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=None,
        )

    # ------------------------------------------------------------------ parse

    def _parse_item(self, slug, card, detail):
        """One catalogue item -> (DiscoveredContent, list[ProviderOffer])."""
        structured = detail.get("structuredMetadata") or {}
        content_type = _STRUCTURAL_TYPES.get(str(structured.get("@type") or ""))
        if content_type is None:
            # Not a modelled content shape (live events, channels, ...).
            logger.info("cbc_gem_unsupported_item slug=%s type=%r", slug,
                        structured.get("@type"))
            return None, []

        title = str(detail.get("title") or structured.get("name") or card.get("title") or "").strip()
        url = WATCH_URL.format(path=slug)
        languages = _languages(structured)
        poster = _image(detail) or _image(card) or ""
        description = str(detail.get("description") or card.get("description") or "")
        lineups = _lineups(detail)
        playable = [
            (lineup, item)
            for lineup in lineups
            for item in lineup.get("items") or []
            if _is_playable(item)
        ]

        if content_type == ContentType.MOVIE:
            offers = self._movie_offers(slug, card, detail, playable, url, languages)
        else:
            offers = self._series_offers(slug, card, detail, lineups, playable, url, languages)

        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=slug,
            content_type=content_type,
            title=title,
            # Movies carry a production year only (recorded as Jan 1 of that
            # year, year precision). Series dates are catalog/re-add dates,
            # not first-air years (e.g. Murdoch episodes date from their
            # re-add, Coronation Street from the current production year), so
            # series deliberately get no release_date at all.
            release_date=(
                _movie_release_date(detail, playable)
                if content_type == ContentType.MOVIE else None
            ),
            url=url,
            poster=poster,
            description=description,
            external_ids={},
            source_id=self.slug,
        )
        return content, offers

    def _movie_offers(self, slug, card, detail, playable, url, languages):
        if detail.get("hasOnlyATrailer") or not playable:
            # Listed, but nothing is actually watchable (trailer-only film).
            return []
        lineup, item = playable[0]
        offer = ProviderOffer(
            provider_id=self.slug,
            provider_content_id=slug,
            country=normalize_country("CA"),
            content_type=ContentType.MOVIE,
            tmdb_id=None,
            season_number=None,
            episode_number=None,
            offer_type=_offer_type(_tier(item) or _tier(lineup) or _tier(card)),
            provider_url=url,
            quality="",
            audio_languages=languages,
            subtitle_languages=None,
            available_from=_availability_date(item) or _availability_date(detail),
            # CBC never exposes an availability end date.
            available_until=None,
            source_record_id=slug,
            source_version=SOURCE_VERSION,
        )
        return [offer]

    def _series_offers(self, slug, card, detail, lineups, playable, url, languages):
        if detail.get("hasOnlyATrailer") or not playable:
            return []
        offers = [
            ProviderOffer(
                provider_id=self.slug,
                provider_content_id=slug,
                country=normalize_country("CA"),
                content_type=ContentType.SERIES,
                tmdb_id=None,
                season_number=None,
                episode_number=None,
                offer_type=_offer_type(_tier(card) or _tier(lineups[0])),
                provider_url=url,
                quality="",
                audio_languages=languages,
                subtitle_languages=None,
                # CBC exposes no series-level availability start.
                available_from=None,
                available_until=None,
                source_record_id=slug,
                source_version=SOURCE_VERSION,
            )
        ]
        for lineup in lineups:
            season_number = _as_int(lineup.get("seasonNumber"), default=None)
            season_items = [
                item for item in (lineup.get("items") or []) if _is_playable(item)
            ]
            if season_number is None or not season_items:
                # Without a season number the repository cannot store the row;
                # a lineup with nothing watchable (only trailers) is not offered.
                continue
            lineup_url = WATCH_URL.format(path=lineup["url"]) if lineup.get("url") else ""
            offers.append(
                ProviderOffer(
                    provider_id=self.slug,
                    provider_content_id=slug,
                    country=normalize_country("CA"),
                    content_type=ContentType.SEASON,
                    tmdb_id=None,
                    season_number=season_number,
                    episode_number=None,
                    offer_type=_offer_type(_tier(lineup) or _tier(card)),
                    provider_url=lineup_url,
                    quality="",
                    audio_languages=languages,
                    subtitle_languages=None,
                    available_from=None,
                    available_until=None,
                    source_record_id=str(lineup.get("url") or slug),
                    source_version=SOURCE_VERSION,
                )
            )
            for item in season_items:
                episode_number = _as_int(item.get("episodeNumber"), default=None)
                if episode_number is None:
                    logger.warning(
                        "cbc_gem_episode_skipped slug=%s item=%s reason=missing_episode_number",
                        slug, item.get("url"),
                    )
                    continue
                item_path = str(item.get("url") or "")
                offers.append(
                    ProviderOffer(
                        provider_id=self.slug,
                        provider_content_id=slug,
                        country=normalize_country("CA"),
                        content_type=ContentType.EPISODE,
                        tmdb_id=None,
                        season_number=season_number,
                        episode_number=episode_number,
                        offer_type=_offer_type(_tier(item) or _tier(lineup)),
                        provider_url=WATCH_URL.format(path=item_path) if item_path else "",
                        quality="",
                        # The series language applies to its episodes; CBC has
                        # no per-episode language metadata (None stays None).
                        audio_languages=languages,
                        subtitle_languages=None,
                        available_from=_availability_date(item),
                        available_until=None,
                        source_record_id=item_path or slug,
                        source_version=SOURCE_VERSION,
                    )
                )
        return offers


def _is_playable(item) -> bool:
    return str(item.get("mediaType") or "").strip().lower() != "trailer"


def _tier(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    permission = obj.get("permission") if isinstance(obj.get("permission"), dict) else {}
    return str(obj.get("tier") or permission.get("tier") or "").strip()


def _offer_type(tier: str) -> str:
    if not tier:
        return normalize_offer_type("")
    return normalize_offer_type(TIER_OFFER_TYPES.get(tier.lower(), tier))


def _languages(structured: dict) -> Optional[list[str]]:
    in_language = structured.get("inLanguage")
    if not in_language:
        return None
    # "en-CA" -> base language; normalize canonicalizes the code ("EN").
    return normalize_languages([str(in_language).split("-")[0]])


def _lineups(detail: dict) -> list[dict]:
    try:
        content = detail["content"][0]
    except (KeyError, IndexError, TypeError):
        return []
    lineups = content.get("lineups")
    return [lineup for lineup in (lineups or []) if isinstance(lineup, dict)]


def _image(payload: dict) -> str:
    images = payload.get("images") if isinstance(payload.get("images"), dict) else {}
    card = images.get("card") if isinstance(images.get("card"), dict) else {}
    return str(card.get("url") or "")


def _as_int(value, *, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _metadata(payload: dict) -> dict:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _availability_date(payload: dict) -> Optional[datetime]:
    """CBC ``availabilityDate`` (``YYYY-MM-DD``, UTC) -> aware datetime, else None."""
    value = _metadata(payload).get("availabilityDate")
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _movie_release_date(detail: dict, playable) -> Optional[date]:
    year = None
    for payload in ([item for _lineup, item in playable], [detail]):
        for candidate in payload:
            year = _as_int(_metadata(candidate).get("productionYear"), default=None)
            if year:
                break
        if year:
            break
    if not year or year < 1800 or year > 2200:
        return None
    return date(year, 1, 1)
