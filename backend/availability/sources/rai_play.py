"""RaiPlay dedicated collector — sitemap discovery -> SSR programme pages -> video access spec -> offers (Phase 12).

Fifth provider-specific :class:`AvailabilitySource`, and the second whose
discovery is sitemap-driven (Tubi being the first), but whose payload lives in
a hydration object instead of a CMS-templated table:

    https://www.raiplay.it/sitemap.xml (sitemap index, 114 children)
        -> only the programme sitemaps  sitemap.raiplay.programmi.MM.YYYY.xml
           (36 children, 2239 unique ``/programmi/<slug>`` URLs; the 36
           ``...video.MM.YYYY``, 35 ``...collezioni.MM.YYYY`` and 7 static
           children are filtered out by URL shape and never requested)
        -> one programme page per entry (round-robin across sitemaps,
           de-duplicated on the canonical provider id, bounded)
        -> ``window.WashiContext`` embedded in the page's HTML (strict JSON)
        -> ``first_item`` -> one ``/video/YYYY/MM/<slug>.json`` video URL,
           answered either by the rendered player page (hydration object
           embedded) or by the bare JSON record (no access model), in which
           case the record's own ``weblink`` (observed, never constructed)
           leads to the ``.html`` player page that carries it
        -> ``schema.potentialAction.actionAccessibilityRequirement`` -> one
           ``DiscoveredContent`` (the programme) + one ``ProviderOffer``
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

Research basis (every claim below was verified against captured pages — 72
programme pages, 48 video pages, the sitemap index and all 36 monthly
programme sitemaps; nothing here is guessed):

- **Hydration.** Both page kinds embed one ``window.WashiContext = {…}`` whose
  value parses as **strict JSON** with ``json.JSONDecoder().raw_decode`` on
  125/125 captured pages (unlike Tubi's JS object literal, no ``undefined``/
  trailing-comma scanning is needed). A payload carrying neither the marker
  nor a leading JSON object (consent interstitial, template change) — or one
  whose value is not an object — is a retrieval failure
  (:class:`SourceFetchError`), never an empty catalogue.
- **Two video representations.** The ``first_item`` ``.json`` URL answers with
  either shape: the rendered player page (``text/html``, hydration object, as
  captured during research) or — as observed live on 2026-09-30 for 3/3
  sampled videos — a **bare JSON record** (``application/json``, ~46 keys)
  that carries no ``data_type``, no ``schema`` and therefore no access model.
  The record's own ``weblink`` (observed, never constructed) is the ``.html``
  player page, which always carried the full hydration object (113 keys
  including the access spec). The source follows that link only when the
  first payload is not already the player state, so a rendered player page
  still costs one request.
- **Programme page identity.** ``type == "RaiPlay Programma Item"``,
  ``data_type == "program"``, ``layout`` is ``"single"`` (48/72 captured) or
  ``"multi"`` (24/72), ``uniquename`` is RaiPlay's stable id
  (``ContentItem-<uuid>``), ``url`` is the absolute programme URL and
  ``first_item`` points at the representative video's ``.json`` URL. In all
  14 captured page/video pairs the video's ``program_uniquename`` equalled the
  programme's ``uniquename`` and ``first_item`` equalled the video's
  ``path_id`` — so ``uniquename`` is the provider-native identity for both the
  content row and its offer.
- **Video page.** ``type == "RaiPlay Video Item"``, ``data_type == "video"``;
  the access model sits at ``schema.potentialAction.actionAccessibilityRequirement``
  = ``{"@type": "ActionAccessSpecification", "category", "availabilityStarts",
  "availabilityEnds", "eligibleRegion": {"@type": "Country", "name": "IT"}}``.
  ``schema.releasedEvent`` is the *publication* event and is never used as a
  release date; ``create_date`` (``"DD-MM-YYYY"``) and ``start_date`` are
  publication dates too, so neither reaches ``release_date``.
- **Access vocabulary** (48/48 video pages): ``category`` is ``"free"``
  (28 pages) or ``"nologinrequired"`` (20 pages), and ``login_required`` is
  ``True``/``False`` respectively in every case — the flag is perfectly
  redundant with ``category``, so it is documented rather than modelled. Both
  categories are free-to-watch, so both normalize to ``OfferType.FREE``; an
  unrecognized ``category`` inside an existing spec becomes
  ``OfferType.UNKNOWN`` (logged), and a spec that is absent altogether skips
  the offer.
- **Geo.** ``eligibleRegion.name`` is ``"IT"`` on all 48 captured video pages,
  matching the Italian-only catalogue; a region the source does not declare is
  skipped, and a missing region is skipped as unobservable rather than assumed.
- **Windows.** ``availabilityStarts``/``availabilityEnds`` are ISO-8601,
  either naive (``2026-09-30T00:00:00.000``, read as UTC) or offset with the
  compact ``+0000`` form (normalized to ``+00:00``).
- **Languages.** ``subtitlesArray`` is a mapping of ``{language, label, url}``
  records: ``{}`` on 44/48 captures (a *known empty* list -> ``[]``), populated
  on 4/48 (``it``, ``it``+``en`` -> ``["IT"]``); absent/non-mapping is
  ``None``. There is no audio-language field at all, so ``audio_languages`` is
  always ``None`` — Italian audio is never assumed from the provider.
- **Quality.** ``uhd`` is ``null`` on all 48 captures -> ``quality = ""``; a
  truthy value maps to ``"4K"`` (branch never observed live, documented as
  such) using the shared ranking vocabulary.
- **Numbering.** ``season``/``episode`` on video pages are *labels*, not
  integers: observed ``(None, None)``, ``("1", "1")``, ``("2026/27", "")``,
  ``("2024", None)``, ``("1990", None)`` … over the 48 captures. They describe
  one representative record (a sports season, a festival year, an episode of a
  series) and cannot be attributed to the whole programme, so the source emits
  **no season/episode offers at all** (``supports_seasons=False``,
  ``supports_episodes=False``): one ``MOVIE`` offer for a ``single`` layout,
  one ``SERIES`` offer for a ``multi`` layout. There is consequently no
  numbering to be malformed, so no ``rai_play_unparseable_numbering`` event
  exists — see ``docs/PLAN.md``.
- **Trailers/clips.** Trailer records are separate ``/video/`` pages linked
  from listing pages through ``trailerInternalLink.linkUnit``
  (``linkedType == "RaiPlay Trailer Item"``, ``seo-url`` values such as
  ``Settembre---Trailer``, ``Trailer---Tale-e-Quale-Show``,
  ``Tirreno-Adriatico-trailer`` — 171 distinct observed). They are never a
  programme's ``first_item`` (0/72 programme pages) and 0/2239 sitemap entries
  are trailer-named, so discovery structurally never reaches them; the source
  additionally filters on that observed slug convention, keyed on the video
  page's own ``weblink``/``path_id``.
- **Live content.** ``sitemap.dirette.xml`` lists ``/dirette/<channel>``
  pages and is never fetched (it is not a programme sitemap), and all 2239
  programme entries are ``/programmi/…``. Both rules are re-checked per entry
  defensively so a future index that leaks a live or editorial URL is skipped
  with a structured log instead of being parsed as a programme.

Product semantics, all documented rather than papered over:

- **Offer type is always ``FREE`` for the observed categories** — both
  ``free`` (needs a free account) and ``nologinrequired`` (needs none) are
  free-to-watch. The canonical ``OfferType`` has no *free-with-account* tier
  and ``Offer`` has no field for it either, so the login distinction is a
  documented limitation rather than a claim that everything is logged-out
  playable (Phase 12 §18).
- **Movies take every video fact; series take none of them.** A ``single``
  programme's representative video *is* the movie, so duration, availability
  window, subtitles and quality come from it. A ``multi`` programme's
  representative video is one episode of unknown scope (and RaiPlay's own
  ``first_item`` is not always episode 1 — a captured documentary programme
  points at a backstage clip), so a SERIES offer carries only provider,
  country, access model and geo, with ``duration``/``release_date``/window/
  subtitles/quality unset.
- **Dates.** ``release_date`` is only ever the programme's ``year`` field with
  year precision (``date(year, 1, 1)``) and only for movies; the field is
  undocumented by RaiPlay and every candidate date on the page is a
  publication date, so series resolve title-only exactly like CBC's series.
  Years outside 1870..2100 are treated as unset.
- **The catalogue is never an authoritative snapshot**: sitemap entries go
  stale (one captured stale entry is the reason ``programme_page`` may return
  ``None``), the walk is bounded by ``max_contents``, and RaiPlay exposes no
  per-item removal signal. Results are always ``authoritative=False`` /
  ``complete_snapshot=False`` (absence never deactivates) even though
  ``sync_mode`` is ``FULL``.
- **No TMDb ids** (``supports_tmdb_ids=False``): mapping happens in the
  refresh service by local title/year matching.
- **Runtime bound**: 2239 programmes x 2 pages = ~4500 requests for a full
  walk. ``DEFAULT_MAX_CONTENTS = 400`` bounds one run to ~840 requests
  (~1-2 minutes at 0.1 s politeness delay) and the truncation is logged; a
  full walk needs ~6 daily runs, which the non-authoritative semantics make
  safe (no offer is ever lost to a missing observation).
- **Bounded, checkpointed walk across runs**: the walk is *resumable*, so the
  ~840-request window is not re-read from the top after an interruption. A
  checkpoint records the last programme URL the walk completed and the next run
  resumes immediately after it. Without this, a restart at 90% discarded ~750
  requests and the effective coverage rate was set by restart frequency rather
  than by the run bound. The anchor is the programme's own URL — the sitemap
  entry's stable identity — so a sitemap re-order cannot shift the resume point.
  ``supports_resumable_catalogue_sync=True`` describes this traversal progress;
  ``supports_incremental_sync`` stays ``False`` (RaiPlay serves no offer delta).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
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
from . import checkpoints

logger = logging.getLogger(__name__)

BASE_URL = "https://www.raiplay.it"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap.xml"
# The provider row the offers resolve to (``tracker`` migration 0011); it is
# deliberately *not* the source slug, exactly like Prime's ``prime``/``prime_video``.
PROVIDER = "raiplay"
COUNTRY = "IT"
DEFAULT_TIMEOUT = 30
# Politeness gap between live requests only (tests route through ``requester``
# and never sleep): one walk hits ~840 pages.
DEFAULT_DELAY = 0.1
# Safety bound on one run. The full catalogue is 2239 programmes x 2 pages;
# interleaving the 36 monthly sitemaps keeps a capped walk spread across
# months. ``None`` disables the bound (tests use it; production must not).
DEFAULT_MAX_CONTENTS = 400
#: Bump when the traversal algorithm changes: a stored checkpoint from another
#: version restarts the walk instead of resuming a stale anchor.
CURSOR_VERSION = 1
#: The single part this walk checkpoints (it has one traversal dimension).
PART_PROGRAMMES = "programmes"
SOURCE_VERSION = "rai-play-ssr-v1"
STATE_MARKER = "window.WashiContext"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Only the 36 monthly programme sitemaps; every other child of the index is
# editorial (collezioni/generi/…) or a different granularity (video).
PROGRAMME_SITEMAP_RE = re.compile(
    r"^https://www\.raiplay\.it/sitemap\.raiplay\.programmi\.\d{2}\.\d{4}\.xml$"
)
PROGRAMME_PATH = "/programmi/"
LIVE_PATH = "/dirette/"
# Observed trailer naming on RaiPlay's own ``seo-url`` link units: the linked
# trailer record is ``{title}---Trailer`` / ``Trailer---{title}`` (also seen
# with a single dash). A title whose own slug merely ends in ``-trailer`` is
# conservatively skipped too: no offer beats a trailer offer.
TRAILER_SLUG_RE = re.compile(r"(?:^|[-_])trailer(?:[-_]|$)", re.IGNORECASE)
_TIMEZONE_RE = re.compile(r"([+-]\d{2})(\d{2})$")

# RaiPlay's observed access vocabulary. Both observed categories are
# free-to-watch, so both are canonical ``free``; anything else falls through to
# ``normalize_offer_type`` so a canonical value RaiPlay might publish later
# (``subscription``, ``rent``…) is still classified honestly instead of being
# flattened to free.
CATEGORY_OFFER_TYPES = {"free": "free", "nologinrequired": "free"}


class RaiPlayClient:
    """Thin client for RaiPlay's public sitemaps and server-rendered pages.

    ``requester`` is a ``url -> html`` callable so tests inject a fixture router
    and never touch the network. A ``None`` payload (live: HTTP 404/410) means
    "that entry is gone upstream" — a stale sitemap entry the caller skips.
    Everything else that goes wrong (transport error, other HTTP status, a
    payload that is not the expected XML or page) surfaces as
    :class:`SourceFetchError`, never as an empty-but-valid result. Page-level
    interpretation belongs to :func:`_state_from_html`, not to the client.
    """

    def __init__(self, requester=None, timeout=DEFAULT_TIMEOUT, delay=DEFAULT_DELAY):
        self.requester = requester
        self.timeout = timeout
        self.delay = delay

    def sitemap_index(self) -> list[str]:
        """Child ``<loc>`` URLs of the sitemap index."""
        payload = self._get(SITEMAP_INDEX_URL)
        if payload is None:
            raise SourceFetchError(f"RaiPlay sitemap index is gone: {SITEMAP_INDEX_URL}")
        return _xml_locs(payload, expect="sitemapindex", url=SITEMAP_INDEX_URL)

    def sitemap_entries(self, url: str) -> list[str]:
        """``<loc>`` URLs of one monthly programme sitemap (provider's order)."""
        payload = self._get(url)
        if payload is None:
            raise SourceFetchError(f"RaiPlay sitemap is gone: {url}")
        return _xml_locs(payload, expect="urlset", url=url)

    def programme_page(self, url: str) -> Optional[str]:
        """Raw HTML of one programme page, or ``None`` when it is gone."""
        return self._get(url)

    def video_page(self, url: str) -> Optional[str]:
        """Raw HTML of one video page, or ``None`` when it is gone."""
        return self._get(url)

    def _get(self, url: str) -> Optional[str]:
        try:
            if self.requester is not None:
                payload = self.requester(url)
            else:
                payload = self._http(url)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"RaiPlay request failed: {exc}") from exc
        if payload is None:
            return None
        if not isinstance(payload, str):
            raise SourceFetchError(f"Unexpected RaiPlay response type for {url}")
        return payload

    def _http(self, url: str) -> Optional[str]:
        if self.delay:
            time.sleep(self.delay)
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"},
            timeout=self.timeout,
        )
        if response.status_code in (404, 410):
            # Stale entry, not a retrieval failure: the caller logs it.
            return None
        if response.status_code != 200:
            raise SourceFetchError(
                f"RaiPlay returned HTTP {response.status_code} for {url}"
            )
        return response.text


class RaiPlaySource:
    """Catalogue-driven RaiPlay source (``content=None`` fetches only)."""

    slug = "rai_play"
    name = "RaiPlay"
    source_type = SourceType.WEB
    capabilities = SourceCapabilities(
        supported_providers=frozenset({PROVIDER}),
        supported_countries=frozenset({COUNTRY}),
        supported_content_types=frozenset({ContentType.MOVIE, ContentType.SERIES}),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Bounded, checkpointed traversal (see module docstring).
        supports_resumable_catalogue_sync=True,
        # Sitemap entries go stale, the walk is bounded, and RaiPlay publishes
        # no per-item removal signal: absence can never be evidence.
        supports_authoritative_snapshots=False,
        # RaiPlay publishes no TMDb ids; mapping happens in the refresh service.
        supports_tmdb_ids=False,
        # Season/episode values are labels, not integers (see module docstring),
        # and no season/episode ProviderContent identity is modelled.
        supports_seasons=False,
        supports_episodes=False,
        supports_languages=True,
    )

    def __init__(self, client=None, max_contents=DEFAULT_MAX_CONTENTS):
        self.client = client or RaiPlayClient()
        self.max_contents = max_contents

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "rai_play is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints, already validated
        # against ``capabilities`` by the refresh service; RaiPlay only ever
        # reports its own (raiplay, IT).
        #
        # ``cursor`` is the durable walk checkpoint: the last programme URL a
        # previous fetch completed. One fetch walks at most ``max_contents``
        # entries and hands the next checkpoint back, so an interruption does
        # not discard the whole window and re-request it from the top.
        resume = checkpoints.decode(
            cursor, version=CURSOR_VERSION, parts=(PART_PROGRAMMES,),
            log_prefix="rai_play",
        )
        entries = self._entries()
        after = resume.anchor(PART_PROGRAMMES)
        if after:
            # Resume immediately after the checkpointed programme. If it is no
            # longer in the sitemaps the walk restarts from the top, which only
            # repeats already-persisted work — it never skips ahead.
            for position, url in enumerate(entries):
                if url == after:
                    entries = entries[position + 1:]
                    logger.info(
                        "rai_play_walk_resume after=%s remaining=%d",
                        after, len(entries),
                    )
                    break
            else:
                logger.warning(
                    "rai_play_cursor_checkpoint_missing after=%s reason=restarting",
                    after,
                )
        total = len(entries)
        scanned = entries if self.max_contents is None else entries[: self.max_contents]
        truncated = len(scanned) < total
        if truncated:
            logger.warning(
                "rai_play_catalogue_truncated entries=%d scanned=%d max_contents=%d",
                total, len(scanned), self.max_contents,
            )

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        observed = 0
        skipped = 0
        last_url = after
        for url in scanned:
            path = urlparse(url).path
            # The anchor moves as soon as the entry is inspected, so a stale or
            # unsupported entry is still progress rather than an infinite
            # re-read.
            last_url = url or last_url
            if path.startswith(LIVE_PATH):
                # Never present in a programme sitemap today; a live channel is
                # not an on-demand movie/series and must not become an offer.
                logger.info("rai_play_live_content_skipped url=%s", url)
                skipped += 1
                continue
            if not path.startswith(PROGRAMME_PATH):
                # Same for any other editorial/structural URL.
                logger.warning(
                    "rai_play_unsupported_content url=%s reason=not_a_programme_path", url
                )
                skipped += 1
                continue
            html = self.client.programme_page(url)
            if html is None:
                # Stale sitemap entry: the programme is not observable right now.
                logger.info("rai_play_content_missing url=%s reason=not_found", url)
                skipped += 1
                continue
            state = _state_from_payload(html, url=url)
            parsed = self._parse_programme(url=url, state=state)
            if parsed is None:
                skipped += 1
                continue
            discovered, page_offers = parsed
            contents.append(discovered)
            offers.extend(page_offers)
            observed += 1

        if scanned and observed == 0:
            # Everything came back gone/unsupported: that reads like a broken
            # fetch path (template change) rather than an empty catalogue. Log
            # it loudly; still never raise, because the walk itself succeeded
            # and nothing gets deactivated either way.
            logger.warning(
                "rai_play_catalogue_nothing_observed fetched=%d skipped=%d",
                len(scanned), skipped,
            )
        elif contents and not offers:
            # Catalogue rows are real even without offers, but a whole run with
            # no playable offer means the access spec moved.
            logger.warning("rai_play_catalogue_no_offers contents=%d", len(contents))
        logger.info(
            "rai_play_catalogue_completed entries=%d contents=%d offers=%d skipped=%d",
            total, len(contents), len(offers), skipped,
        )
        # A checkpoint is only emitted while entries remain unwalked; an
        # exhausted walk returns None so the refresh service drops the row rather
        # than resuming a finished catalogue.
        next_cursor = (
            checkpoints.encode(CURSOR_VERSION, {PART_PROGRAMMES: last_url})
            if truncated else None
        )
        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=next_cursor,
        )

    # ------------------------------------------------------------------ walk

    def _entries(self) -> list[str]:
        """Programme URLs from the index, interleaved and de-duplicated."""
        index = self.client.sitemap_index()
        sitemaps = [url for url in index if PROGRAMME_SITEMAP_RE.match(url)]
        if not sitemaps:
            raise SourceFetchError("RaiPlay sitemap index lists no programme sitemaps")
        logger.info("rai_play_catalogue_started sitemaps=%d", len(sitemaps))
        queues = [self.client.sitemap_entries(url) for url in sitemaps]
        # Dedupe on the canonical URL (the same programme is listed by more
        # than one month), never on the title.
        return list(dict.fromkeys(_interleave(queues)))

    # ------------------------------------------------------------------ parse

    def _parse_programme(self, *, url: str, state: dict):
        """One programme page -> (DiscoveredContent, list[ProviderOffer])."""
        if str(state.get("data_type") or "").strip() != "program":
            logger.info("rai_play_content_missing url=%s reason=not_a_programme", url)
            return None
        provider_content_id = str(state.get("uniquename") or "").strip()
        title = str(state.get("title") or "").strip()
        if not provider_content_id or not title:
            logger.warning(
                "rai_play_unparseable_content url=%s reason=missing_identity", url
            )
            return None
        layout = str(state.get("layout") or "").strip().lower()
        if layout == "single":
            content_type = ContentType.MOVIE
        elif layout == "multi":
            content_type = ContentType.SERIES
        else:
            # RaiPlay's other layouts are editorial surfaces, not watchable
            # titles; they never become canonical availability rows.
            logger.warning(
                "rai_play_unsupported_content url=%s content=%s layout=%r",
                url, provider_content_id, layout,
            )
            return None

        video = self._video_state(provider_content_id=provider_content_id, programme=state)
        content = self._content(
            provider_content_id=provider_content_id,
            content_type=content_type,
            programme=state,
            video=video,
            fallback_url=url,
        )
        offers: list[ProviderOffer] = []
        if video is not None:
            offer = self._offer(
                provider_content_id=provider_content_id,
                content_type=content_type,
                video=video,
                provider_url=content.url,
            )
            if offer is not None:
                offers.append(offer)
        return content, offers

    def _video_state(self, *, provider_content_id: str, programme: dict) -> Optional[dict]:
        """The programme's representative video page, or ``None`` when unusable.

        The content row stands either way (an unobservable video costs the
        offer, never the catalogue entry).
        """
        video_url = _video_url(programme.get("first_item"))
        if video_url is None:
            logger.info(
                "rai_play_content_missing content=%s reason=no_first_item",
                provider_content_id,
            )
            return None
        html = self.client.video_page(video_url)
        if html is None:
            logger.info(
                "rai_play_content_missing content=%s url=%s reason=video_not_found",
                provider_content_id, video_url,
            )
            return None
        state = _state_from_payload(html, url=video_url)
        state_url = video_url
        if not str(state.get("data_type") or "").strip():
            # No ``data_type`` means this is not a hydration state but the
            # bare JSON record, which carries no access model of its own; the
            # record's ``weblink`` (observed, never constructed) is the player
            # page that does. One extra request only for this shape.
            player_url = _video_url(state.get("weblink"))
            if player_url is None:
                logger.info(
                    "rai_play_content_missing content=%s url=%s reason=no_player_page",
                    provider_content_id, video_url,
                )
                return None
            html = self.client.video_page(player_url)
            if html is None:
                logger.info(
                    "rai_play_content_missing content=%s url=%s reason=video_not_found",
                    provider_content_id, player_url,
                )
                return None
            state = _state_from_payload(html, url=player_url)
            state_url = player_url
        if str(state.get("data_type") or "").strip() != "video":
            logger.info(
                "rai_play_content_missing content=%s url=%s reason=not_a_video",
                provider_content_id, state_url,
            )
            return None
        if _is_trailer(state):
            # Trailers are not watchable content; their records are only ever
            # reachable through listing links, never through ``first_item``.
            logger.info(
                "rai_play_trailer_skipped content=%s video=%s url=%s",
                provider_content_id, state.get("uniquename"), state_url,
            )
            return None
        return state

    def _content(self, *, provider_content_id: str, content_type: str, programme: dict,
                 video: Optional[dict], fallback_url: str) -> DiscoveredContent:
        is_movie = content_type == ContentType.MOVIE
        year = _as_int(programme.get("year"))
        release_date = None
        if is_movie and year is not None and 1870 <= year <= 2100:
            release_date = date(year, 1, 1)
        return DiscoveredContent(
            provider_id=PROVIDER,
            provider_content_id=provider_content_id,
            content_type=normalize_content_type(content_type),
            title=str(programme.get("title") or "").strip(),
            # Series deliberately get none: RaiPlay never documents what
            # ``year`` means on a multi-entry programme, and a wrong year is
            # worse than none for title/year matching.
            release_date=release_date,
            # Duration is one episode's runtime, so only movies carry it.
            duration=_seconds(video.get("duration")) if (is_movie and video) else None,
            url=_content_url(programme, fallback_url),
            poster=_poster(programme),
            description=_description(programme),
            external_ids={},
            source_id=self.slug,
        )

    def _offer(self, *, provider_content_id: str, content_type: str, video: dict,
               provider_url: str) -> Optional[ProviderOffer]:
        """One provider-stated offer, or ``None`` when it cannot be supported."""
        spec = _access_spec(video)
        if spec is None:
            logger.info(
                "rai_play_offer_skipped content=%s reason=no_access_spec",
                provider_content_id,
            )
            return None

        category = str(spec.get("category") or "").strip().lower()
        offer_type = normalize_offer_type(CATEGORY_OFFER_TYPES.get(category, category))
        if offer_type == OfferType.UNKNOWN:
            logger.warning(
                "rai_play_unknown_access content=%s category=%r",
                provider_content_id, spec.get("category"),
            )

        region = spec.get("eligibleRegion")
        region_name = (
            str(region.get("name") or "").strip().upper()
            if isinstance(region, dict) else ""
        )
        if not region_name:
            # The catalogue is Italian, but a spec that states no region states
            # nothing about where it plays: skip instead of assuming IT.
            logger.info(
                "rai_play_offer_skipped content=%s reason=region_unobserved",
                provider_content_id,
            )
            return None
        if region_name != COUNTRY:
            logger.info(
                "rai_play_offer_skipped content=%s region=%s reason=region_mismatch",
                provider_content_id, region_name,
            )
            return None

        is_movie = content_type == ContentType.MOVIE
        available_from = _parse_timestamp(spec.get("availabilityStarts")) if is_movie else None
        available_until = _parse_timestamp(spec.get("availabilityEnds")) if is_movie else None
        if is_movie:
            # The read path treats stored offers as current regardless of the
            # window, so a window RaiPlay has already closed (or not opened)
            # must not be published. A series offer carries no window at all:
            # the spec belongs to one representative episode.
            now = datetime.now(timezone.utc)
            if available_from is not None and available_from > now:
                logger.info(
                    "rai_play_offer_skipped content=%s available_from=%s "
                    "reason=window_not_open",
                    provider_content_id, available_from.isoformat(),
                )
                return None
            if available_until is not None and available_until <= now:
                logger.info(
                    "rai_play_offer_skipped content=%s available_until=%s "
                    "reason=expired",
                    provider_content_id, available_until.isoformat(),
                )
                return None

        return ProviderOffer(
            provider_id=PROVIDER,
            provider_content_id=provider_content_id,
            country=normalize_country(COUNTRY),
            content_type=normalize_content_type(content_type),
            tmdb_id=None,
            season_number=None,
            episode_number=None,
            offer_type=offer_type,
            provider_url=provider_url,
            quality=_quality(video) if is_movie else "",
            # RaiPlay publishes no audio-language field; never assume Italian.
            audio_languages=None,
            subtitle_languages=_subtitle_languages(video) if is_movie else None,
            available_from=available_from,
            available_until=available_until,
            # The underlying RaiPlay record behind the offer, not the programme.
            source_record_id=str(video.get("uniquename") or "").strip(),
            source_version=SOURCE_VERSION,
        )


# ------------------------------------------------------------------ walk


def _interleave(queues: list[list[str]]) -> list[str]:
    """Round-robin across monthly sitemaps so a capped walk mixes old/new."""
    merged: list[str] = []
    index = 0
    while any(index < len(queue) for queue in queues):
        for queue in queues:
            if index < len(queue):
                merged.append(queue[index])
        index += 1
    return merged


# ------------------------------------------------------------------ client


def _localname(tag) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _xml_locs(text: str, *, expect: str, url: str) -> list[str]:
    """``<loc>`` values of a sitemap document, namespace-agnostic."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        # An HTML interstitial parses as XML only by accident: treat anything
        # else as a failed retrieval.
        raise SourceFetchError(f"RaiPlay returned a malformed sitemap for {url}") from exc
    root_name = _localname(root.tag)
    if root_name != expect:
        raise SourceFetchError(
            f"Unexpected RaiPlay sitemap root {root_name!r} for {url} (want {expect!r})"
        )
    return [
        element.text.strip()
        for element in root.iter()
        if _localname(element.tag) == "loc" and element.text and element.text.strip()
    ]


# ------------------------------------------------------------------ page


def _state_from_payload(payload: str, *, url: str = "") -> dict:
    """Decode one RaiPlay payload into its hydration object.

    Two shapes carry the same object, both observed live:

    * an HTML page embedding ``window.WashiContext = {…}`` (programme pages,
      and video pages when the server renders the player), and
    * a **bare JSON document** — ``.json`` endpoints increasingly answer with
      the record alone, starting with ``{`` and nothing else.

    Both values are strict JSON, so each goes straight to ``raw_decode``
    (which also reports where it stopped, leaving the rest of the document
    untouched). A payload that is neither shape (consent interstitial,
    template change) is a different page entirely, hence a retrieval failure —
    never an empty catalogue.
    """
    if not isinstance(payload, str):
        raise SourceFetchError(f"Unexpected RaiPlay page type for {url}")
    marker = payload.find(STATE_MARKER)
    if marker >= 0:
        start = payload.find("{", marker)
        if start < 0:
            raise SourceFetchError(
                f"RaiPlay {STATE_MARKER} carries no object for {url}"
            )
    else:
        # A bare JSON document and nothing else: a document that merely
        # *contains* a brace somewhere is an HTML page without the marker.
        start = len(payload) - len(payload.lstrip())
        if payload[start : start + 1] != "{":
            raise SourceFetchError(
                f"RaiPlay page carries no {STATE_MARKER} state for {url}"
            )
    try:
        state, _end = json.JSONDecoder().raw_decode(payload, start)
    except ValueError as exc:
        raise SourceFetchError(f"Malformed RaiPlay {STATE_MARKER} object for {url}") from exc
    if not isinstance(state, dict):
        raise SourceFetchError(f"RaiPlay {STATE_MARKER} is not an object for {url}")
    return state


# ------------------------------------------------------------------ fields


def _as_int(value) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def _seconds(value) -> Optional[int]:
    """``duration`` is a clock string (``01:32:44``) -> seconds."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = 0, 0, numbers[0]
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_timestamp(value) -> Optional[datetime]:
    """Provider window timestamp -> aware UTC datetime.

    Naive values (``2026-09-30T00:00:00.000``) are read as UTC, and the
    compact offset form RaiPlay writes (``+0000``) is normalized so parsing
    does not depend on the Python version.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    text = _TIMEZONE_RE.sub(r"\1:\2", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("rai_play_unparsable_timestamp value=%r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _video_url(value) -> Optional[str]:
    """``first_item`` -> absolute video URL, or ``None`` when unusable."""
    if not isinstance(value, str):
        return None
    path = value.strip()
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return BASE_URL + path
    # No observed RaiPlay page ships a bare relative ``first_item``; rather
    # than guess one, treat it as unobservable.
    return None


def _content_url(programme: dict, fallback_url: str) -> str:
    """Programme URL, exactly as observed (never constructed from a pattern)."""
    url = str(programme.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    relative = str(programme.get("relative_url") or "").strip()
    if relative.startswith("/"):
        return BASE_URL + relative
    # Last resort is the sitemap entry the page was actually fetched from —
    # also observed, never fabricated.
    return fallback_url


def _poster(programme: dict) -> str:
    images = programme.get("images")
    candidates = []
    if isinstance(images, dict):
        candidates.append(images.get("landscape"))
        candidates.append(images.get("square"))
    candidates.append(programme.get("image"))
    for candidate in candidates:
        path = str(candidate or "").strip()
        if not path:
            continue
        if path.startswith("//"):
            return f"https:{path}"
        if path.startswith("/"):
            return BASE_URL + path
        if path.startswith(("http://", "https://")):
            return path
    return ""


def _description(programme: dict) -> str:
    description = str(programme.get("description") or "").strip()
    return description or str(programme.get("text") or "").strip()


def _access_spec(video: dict) -> Optional[dict]:
    """The video's access model, or ``None`` when the page publishes none."""
    schema = video.get("schema")
    action = schema.get("potentialAction") if isinstance(schema, dict) else None
    spec = action.get("actionAccessibilityRequirement") if isinstance(action, dict) else None
    if not isinstance(spec, dict) or not spec:
        return None
    return spec


def _is_trailer(video: dict) -> bool:
    """Whether the video record is a trailer, by RaiPlay's own slug convention.

    Keyed on the record's own URL (``weblink``/``path_id``): observed trailer
    slugs are ``{title}---Trailer``, ``Trailer---{title}`` and ``…-trailer``.
    """
    for field in ("weblink", "path_id"):
        value = video.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        slug = PurePosixPath(urlparse(value.strip()).path).name
        slug = re.sub(r"\.(?:html|json)$", "", slug, flags=re.IGNORECASE)
        return bool(TRAILER_SLUG_RE.search(slug))
    return False


def _subtitle_languages(video: dict) -> Optional[list[str]]:
    """``[]`` when the page explicitly publishes none; ``None`` when unreported."""
    subtitles = video.get("subtitlesArray")
    if subtitles is None:
        return None
    if not isinstance(subtitles, dict):
        return None
    if not subtitles:
        return []
    names = []
    for value in subtitles.values():
        language = value.get("language") if isinstance(value, dict) else value
        if isinstance(language, str) and language.strip():
            names.append(language.strip())
    if not names:
        # A non-empty mapping we cannot read is "not reported", never "none".
        return None
    return normalize_languages(names)


def _quality(video: dict) -> str:
    """Structured quality only: ``uhd`` is null on every captured page."""
    return "4K" if video.get("uhd") else ""
