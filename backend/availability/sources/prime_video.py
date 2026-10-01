"""Prime Video dedicated collector — storefront index -> SSR detail pages -> offers (Phase 11).

Fourth provider-specific :class:`AvailabilitySource`, and the first whose
discovery walks Prime's own storefront instead of a sitemap or a partner API:

    /categories  -> genre links (round-robin seeds)
    /store, /movie, /tv, /genre/<slug>
        -> ``body.containers[].entities[]`` (each card links ``/detail/<compactGTI>``)
        -> ``body.pagination.url`` when the index offers a next page
        -> one detail page per title (``init.route.name == "detail"``)
        -> ``atf.state`` + ``btf.state.detail.detail[pageTitleId]``
        -> one ``DiscoveredContent`` per title + ``ProviderOffer`` at every
           granularity the page actually carries
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

Research basis (every claim below was verified against captured pages; captures
live in the research notes and the fixtures under
``backend/availability/tests/fixtures/prime_video/`` — nothing here is guessed):

- **SSR payload.** Both index and detail pages embed one strict-JSON
  ``<script id="dv-web-page-hydration-data" type="application/json">`` holding
  ``{"init": {"path": ..., "route": ..., "preparations": {"head": ..., "body": ...}}}``,
  plus a small ``<script id="dv-web-global-store-data">`` with the request
  context. Index pages carry ``route.name == "landing"`` and the data in
  ``preparations.body``; detail pages carry ``route.name == "detail"`` and the
  data in ``preparations.body.atf.state`` / ``.btf.state``. A page without the
  marker, or whose payload is not the expected shape, is a retrieval failure
  (:class:`SourceFetchError`) — never an empty catalogue.
- **Territory is served by egress IP.** ``/region/us``, ``/region/gb`` and
  ``/region/nl`` all answered with ``RequestContext.currentTerritory == "NL"``
  and marketplace ``A3K6Y4MI8GDYMT`` for the research egress: the region path
  does *not* select the catalogue, the client's IP does. The source therefore
  declares the one territory it was researched against (``COUNTRY``), reads
  ``currentTerritory`` from every page, and **raises** on a mismatch instead of
  silently reporting another country's catalogue. Same loud-failure contract as
  Tubi's geo-lock (see ``docs/DEPLOYMENT.md``).
- **Index pagination chains.** ``body.pagination.url`` is a ready-made GET URL
  (signed ``serviceToken``, ``startIndex``, ``pageSize``, ``page``) that the
  server honours even though ``pagination.requestMethod`` says ``"POST"``:
  ``/store?page=2`` answered 289 entities plus the page-3 URL, ``/movie?page=2``
  440 movies, ``/tv?page=2`` 350 TV shows. **Genre pages do not chain**: their
  first-page ``pagination.url`` is ``""``, and rebuilding one from
  ``queryParameters`` returns a widget-targeted page (20 entities, no
  ``pagination``). Genre seeds therefore contribute page 1 only.
- **Cards.** Each entity has ``entityType`` (``Movie``, ``TV Show``,
  ``Short Film``, ``Collection``, ...), ``titleID`` (``amzn1.dv.gti.<uuid>``),
  ``link.url`` (``/detail/<compactGTI>?jic=...&ref_=...``), ``title``,
  ``releaseYear``, ``runtime``, ``synopsis``, ``images``, and
  ``entitlementCues`` (``focusMessage.message``, ``glanceMessage.message``,
  ``entitlementType``). Only ``/detail/``-linked Movie/TV Show/Short Film cards
  are catalogue items; collections and ``/genre/`` tiles are never fetched.
  The card cue is kept as a *fallback* for the detail page's own cue.
- **Detail page identity.** ``atf.state.pageTitleId`` (long ``amzn1.dv.gti.<uuid>``)
  indexes ``atf.state.self`` — which carries ``titleType`` (``movie`` /
  ``season``), ``compactGTI`` and ``link`` — and ``btf.state.detail.detail``
  — which carries the metadata (``title``, ``titleType``, ``entityType``,
  ``seasonNumber``, ``parentTitle``, ``releaseDate``, ``releaseYear``,
  ``duration`` (seconds), ``runtime`` (display string), ``isPrime``, ``isUhd``,
  ``isHdr``, ``isHdr10Plus``, ``isDolbyVision``, ``audioTracks``, ``subtitles``,
  ``synopsis``, ``images.packshot``). The detail page's own commercial message
  sits in ``atf.state.action.atf[pageTitleId].messages`` (``focusMessage.dvMessage.string``).
- **TV has no series-level id.** Every captured TV detail page reports
  ``pageContext.subPageType == "Season"`` and ``titleType == "season"``;
  ``titleType == "series"`` appears nowhere in the SSR payload, ``parentTitle``
  is a plain string, and the season ASINs/gtis are per-season. The only
  Prime-owned identifier for the *show* is the ``compactGTI`` of its canonical
  first season, which ``atf.state.seasons`` always lists
  (``sequenceNumber`` 1 first, ``seasonLink`` = ``/detail/<compactGTI>?ref_=atv_dp_season_select_s1``).
  That id is what the SERIES ``DiscoveredContent``, the SERIES offer, the
  SEASON offers and every EPISODE offer share — one ``ContentMapping`` per
  show, exactly like CBC/BBC/Tubi — and season/episode rows stay distinct
  through the offer identity's season/episode slots.
- **Episodes** come from ``btf.state.episodeList.cardTitleIds`` (long gtis) with
  the record in ``btf.state.self`` (``compactGTI``, ``link``,
  ``sequenceNumber``) and the metadata in ``btf.state.detail.detail`` (which
  carries ``episodeNumber``, ``duration``, per-episode quality flags and the
  audio/subtitle lists the *season* record leaves empty).
- **Commercial state.** ``buyBox`` is ``{}`` in SSR (no prices), and
  ``seasonSelectorIcon`` is always ``"Unentitled"`` regardless of
  ``isPrime`` — signed-out state, never a commercial signal. What is observable
  is the detail page's ``focusMessage`` vocabulary (observed: *"Watch with a
  30 day free Prime trial..."*, *"Auto-renews at €4.99/month after trial"*,
  *"Available to rent or buy"*, *"Available to buy"*, *"First episode free"*,
  *"Subscribe to HBO Max, rent, or buy"*, *"Free 7-day trial of SkyShowtime..."*,
  *"Available to pre-order"*) together with the page's ``isPrime`` flag.
  ``_offer_types`` turns that pair into canonical offer types; anything it
  cannot classify becomes ``unknown`` rather than being dropped, and the read
  path keeps showing non-qualifying offers as *excluded* reasons (only
  ``subscription``/``free`` qualify in ``tracker.services.watchability``).
  A pre-order page is transactional — its own buy action reads
  ``"Pre-order movie UHD €17.99"`` — so the pre-order wording classifies as
  ``buy``: recorded, like rent/buy, and never watchable now (the page's
  ``highValueMessage`` release date is prose, not an offer window, so no
  ``available_from`` is claimed).

Product semantics, all documented rather than papered over:

- **``isPrime`` is per-page, not per-episode**: it is ``True`` exactly for the
  seven captured Prime-included seasons and ``False`` for the movies and
  channel-backed seasons; the 70 captured episode records report ``isPrime``
  ``False`` in *every* case, including episodes of Prime-included seasons.
  Episode offers therefore inherit the season page's offer types (the same way
  CBC lets series languages cover their episodes) and the per-episode flag is
  recorded nowhere as a claim.
- **Languages**: movie and episode records carry ``audioTracks``/``subtitles``;
  season records ship ``[]`` in every capture. An empty list means "the source
  publishes no language metadata here" and is stored as ``None`` (never ``[]``,
  which would claim "no languages exist"); season/series offers fall back to
  the first episode record that does publish a list. Names arrive with display
  qualifiers (``"English [CC]"``, ``"Deutsch Dialogue Boost: Medium"``,
  ``"Español (España)"``) which are stripped before canonical normalization.
- **Quality** maps the observed boolean flags onto the shared ranking
  vocabulary (``4K`` / ``HDR`` / ``""``): ``isUhd`` wins, then any HDR flag.
- **Country** is always the source's own declared territory (``COUNTRY``) —
  never the title's production country, never the marketplace id.
- **No windows**: Prime publishes no ``available_from``/``available_until``
  here, so offers are written with none.
- **The catalogue is never an authoritative snapshot**: index pages are
  editorially ordered, the walk is bounded by ``max_contents``, genre pages do
  not chain, and Prime exposes no per-item removal signal. Results are always
  ``authoritative=False`` / ``complete_snapshot=False`` (absence never
  deactivates) even though ``sync_mode`` is ``FULL``.
- **No TMDb ids** (``supports_tmdb_ids=False``): Prime publishes ASINs/gtis
  only, so mapping happens in the refresh service by local title/year
  matching — series resolve title-only (no first-air year is observable), the
  ``TITLE_ONLY`` tier.
- **Runtime bound**: ``/store``, ``/movie``, ``/tv`` and every genre index
  paginate far past what one run should scan, and each detail page ships
  140 kB..1 MB of hydration JSON. ``DEFAULT_MAX_CONTENTS`` bounds one run
  (index pages are round-robined so a capped run still mixes movies, series
  and genres) and the truncation is logged; see ``docs/CRON.md``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlsplit

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

BASE_URL = "https://www.primevideo.com"
PROVIDER = "prime"
# The Provider row this source reports (the same canonical provider the
# Streaming Availability source reports), so both converge on one Provider
# while their provenance — Source.slug — stays distinct.
COUNTRY = "NL"
# The territory the source was researched against *and* the only one the
# catalogue is accepted from (``currentTerritory`` is checked per page).
INDEX_PATHS = ("/store", "/movie", "/tv")
CATEGORIES_PATH = "/categories"
DEFAULT_TIMEOUT = 30
# Politeness gap between live requests only (tests route through ``requester``
# and never sleep): one walk fetches index pages plus a detail page of 140 kB..1 MB
# each.
DEFAULT_DELAY = 0.2
# Safety bound on one run. Prime's storefront indexes paginate to hundreds of
# pages and every detail page is large; a capped walk still mixes movies,
# series and genres because the seeds are round-robined. ``None`` disables the
# bound (tests use it; production must not).
DEFAULT_MAX_CONTENTS = 500
SOURCE_VERSION = "prime-ssr-v1"
HYDRATION_MARKER = "dv-web-page-hydration-data"
STORE_MARKER = "dv-web-global-store-data"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Cards that are catalogue items; collections, trailers and the ``/genre/``
# tiles on ``/categories`` are not titles and are never requested.
TITLE_ENTITY_TYPES = frozenset({"Movie", "TV Show", "Short Film"})
DETAIL_PATH_RE = re.compile(r"^/detail/(?P<gti>[0-9A-Za-z]+)")
GENRE_HREF_RE = re.compile(
    r"""href\s*=\s*["'](?:https?://(?:www\.)?primevideo\.com)?"""
    r"""(?P<path>/genre/[a-z0-9-]+)""",
    re.IGNORECASE,
)
_RELEASE_DATE_RE = re.compile(r"^(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})$")
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
# Display qualifiers Prime appends to track names; the bare language name is
# what the canonical normalizer knows.
_LANGUAGE_QUALIFIER_RE = re.compile(
    r"\s*\[[^\]]*\]|\s*[（(][^）)]*[）)]|\s+Dialogue Boost:\s*\w+"
)
_PRIME_WORD_RE = re.compile(r"\bprime\b", re.IGNORECASE)
_SUBSCRIPTION_WORD_RE = re.compile(
    r"subscribe|subscrib|auto-renews at|\btrial\b", re.IGNORECASE
)
_RENT_WORD_RE = re.compile(r"\brent", re.IGNORECASE)
_BUY_WORD_RE = re.compile(r"\b(buy|buying|purchase)\b", re.IGNORECASE)
# Observed live: a not-yet-released title answers *"Available to pre-order"*
# while its buy action reads *"Pre-order movie UHD €17.99"* — a purchase.
_PREORDER_WORD_RE = re.compile(r"\bpre-?order(ed|ing)?\b", re.IGNORECASE)
_FREE_WITH_EPISODE_RE = re.compile(r"first episode free", re.IGNORECASE)


class PrimeVideoClient:
    """Thin client for Prime Video's public storefront and detail pages.

    ``requester`` is a ``url -> html`` callable so tests inject a fixture router
    and never touch the network. Retrieval failures (HTTP error, transport
    error, a non-HTML answer) always surface as :class:`SourceFetchError` —
    never as an empty-but-valid result. Page-level interpretation (hydration
    decoding, territory, title parsing) belongs to the module helpers, not to
    the client.
    """

    def __init__(self, requester=None, timeout=DEFAULT_TIMEOUT, delay=DEFAULT_DELAY):
        self.requester = requester
        self.timeout = timeout
        self.delay = delay

    def categories(self) -> str:
        """Raw HTML of ``/categories`` (the genre link farm)."""
        return self._get(f"{BASE_URL}{CATEGORIES_PATH}")

    def storefront(self, url: str) -> str:
        """Raw HTML of an index page (or of a ``pagination.url`` follow-up)."""
        return self._get(url)

    def detail(self, url: str) -> str:
        """Raw HTML of one ``/detail/`` page."""
        return self._get(url)

    def _get(self, url: str) -> str:
        try:
            if self.requester is not None:
                payload = self.requester(url)
            else:
                payload = self._http(url)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"Prime Video request failed: {exc}") from exc
        if not isinstance(payload, str):
            raise SourceFetchError(f"Unexpected Prime Video response type for {url}")
        return payload

    def _http(self, url: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SourceFetchError(
                f"Prime Video returned HTTP {response.status_code} for {url}"
            )
        return response.text


class PrimeVideoSource:
    """Catalogue-driven Prime Video source (``content=None`` fetches only)."""

    slug = "prime_video"
    name = "Prime Video"
    source_type = SourceType.WEB
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"prime"}),
        supported_countries=frozenset({COUNTRY}),
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Index pages are editorially ordered, genre pages do not chain, the
        # walk is bounded, and Prime publishes no removal signal: absence can
        # never be evidence of unavailability.
        supports_authoritative_snapshots=False,
        # Prime publishes gtis/ASINs only (``external_ids`` records them for
        # reference); mapping happens in the refresh service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        supports_languages=True,
    )

    def __init__(self, client=None, max_contents=DEFAULT_MAX_CONTENTS):
        self.client = client or PrimeVideoClient()
        self.max_contents = max_contents

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "prime_video is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints, already validated
        # against ``capabilities`` by the refresh service; Prime Video only ever
        # reports its own (prime, NL) — and says so loudly when the page it
        # fetched belongs to another territory. cursor is accepted but unused:
        # the storefront exposes no resumable server-side cursor, so one fetch
        # reports the whole configured window instead of inventing a token.
        discovered, cues = self._discover()
        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        observed = 0
        skipped = 0
        for path in discovered:
            url = f"{BASE_URL}{path}"
            html = self.client.detail(url)
            _check_territory(html, url=url)
            page = _detail_page(html, url=url)
            if page is None:
                skipped += 1
                continue
            page_contents, page_offers = self._from_detail(page, cue=cues.get(path, ""))
            contents.extend(page_contents)
            offers.extend(page_offers)
            observed += 1

        if discovered and observed == 0:
            # Everything came back as an unrecognised page type: that reads
            # like a broken fetch path (consent interstitial, template change)
            # rather than an empty catalogue. Log it loudly; still never raise,
            # because the walk itself succeeded and nothing gets deactivated
            # either way.
            logger.warning(
                "prime_video_catalogue_nothing_observed fetched=%d skipped=%d",
                len(discovered), skipped,
            )
        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=None,
        )

    # ------------------------------------------------------------------ walk

    def _discover(self) -> tuple[list[str], dict[str, str]]:
        """Detail paths from the storefront indexes, interleaved and bounded.

        Returns the ordered, de-duplicated ``/detail/<compactGTI>`` paths plus
        the card cue for each one (the detail page's own cue wins when it has
        one). Index pages are round-robined — one page per seed at a time — so
        a capped run mixes movies, series and genres instead of exhausting the
        first carousel of ``/store``.
        """
        queue = deque(self._seeds())
        seen_pages: set[str] = set()
        paths: list[str] = []
        seen_paths: set[str] = set()
        cues: dict[str, str] = {}
        while queue and (self.max_contents is None or len(paths) < self.max_contents):
            url = queue.popleft()
            if url in seen_pages:
                continue
            seen_pages.add(url)
            html = self.client.storefront(url)
            _check_territory(html, url=url)
            head, body = _page(html, url=url)
            entities, next_url = _entities(body)
            for entity in entities:
                path = _detail_path(entity)
                if not path or path in seen_paths:
                    continue
                if (
                    self.max_contents is not None
                    and len(paths) >= self.max_contents
                ):
                    break
                seen_paths.add(path)
                paths.append(path)
                cue = _card_cue(entity)
                if cue:
                    cues.setdefault(path, cue)
            if next_url and (
                self.max_contents is None or len(paths) < self.max_contents
            ):
                # ``pagination.url`` ships path-absolute ("/store?page=3&...").
                queue.append(
                    next_url if next_url.startswith("http") else f"{BASE_URL}{next_url}"
                )
            _log_page(head, url=url)

        if self.max_contents is not None and queue:
            logger.warning(
                "prime_video_catalogue_truncated discovered=%d max_contents=%d "
                "pending_index_pages=%d",
                len(paths), self.max_contents, len(queue),
            )
        if not paths:
            logger.warning(
                "prime_video_catalogue_empty discovered=0 index_pages=%d",
                len(seen_pages),
            )
        return paths, cues

    def _seeds(self) -> list[str]:
        """Starting index URLs: the three core storefronts plus genre page 1.

        ``/categories`` failing is logged, not fatal: the genre seeds only
        widen the walk, the core catalogue lives on ``/store``/``/movie``/``/tv``.
        """
        seeds = [f"{BASE_URL}{path}" for path in INDEX_PATHS]
        try:
            html = self.client.categories()
            _check_territory(html, url=f"{BASE_URL}{CATEGORIES_PATH}")
            genre_paths = _genre_paths(html)
        except SourceFetchError as exc:
            logger.warning("prime_video_categories_unavailable error=%s", exc)
        else:
            if not genre_paths:
                logger.warning("prime_video_categories_no_genres")
            seeds.extend(f"{BASE_URL}{path}" for path in genre_paths)
        return seeds

    # ------------------------------------------------------------------ parse

    def _from_detail(
        self, page: _DetailPage, *, cue: str
    ) -> tuple[list[DiscoveredContent], list[ProviderOffer]]:
        """One parsed detail page -> (contents, offers) at every granularity."""
        message = page.cue or cue
        offer_types = _offer_types(page.meta.get("isPrime"), message)
        quality = _quality(page.meta)
        audio = _languages(page.meta.get("audioTracks"))
        subtitles = _languages(page.meta.get("subtitles"))

        if page.title_type == "movie":
            path = page.path
            contents = [
                DiscoveredContent(
                    provider_id=PROVIDER,
                    provider_content_id=page.compact,
                    content_type=normalize_content_type(ContentType.MOVIE),
                    title=page.meta.get("title") or "",
                    # Movies: exact premiere date when Prime publishes one,
                    # otherwise Jan 1 of the release year (year precision),
                    # like CBC's movies. Season pages deliberately carry no
                    # date: their ``releaseDate`` belongs to the observed
                    # season, not to the show.
                    release_date=_release_date(page.meta),
                    duration=_as_int(page.meta.get("duration")),
                    url=f"{BASE_URL}{path}",
                    poster=_poster(page.meta),
                    description=page.meta.get("synopsis") or "",
                    source_id=self.slug,
                )
            ]
            offers = [
                self._offer(
                    provider_content_id=page.compact,
                    content_type=ContentType.MOVIE,
                    offer_type=offer_type,
                    provider_url=f"{BASE_URL}{path}",
                    quality=quality,
                    audio=audio,
                    subtitles=subtitles,
                    record_id=page.compact,
                )
                for offer_type in offer_types
            ]
            return contents, offers

        # season / series: one show-level content row keyed on the canonical
        # first season's compactGTI, shared by every offer of the page.
        show_id, show_path = page.show
        show_title = page.meta.get("parentTitle") or page.meta.get("title") or ""
        contents = [
            DiscoveredContent(
                provider_id=PROVIDER,
                provider_content_id=show_id,
                content_type=normalize_content_type(ContentType.SERIES),
                title=show_title,
                release_date=None,
                url=f"{BASE_URL}{show_path}",
                poster=_poster(page.meta),
                description=page.meta.get("synopsis") or "",
                source_id=self.slug,
            )
        ]
        season_number = _as_int(page.meta.get("seasonNumber"))

        # Prime publishes the language sets on episode records; when the
        # season record ships an empty list (every capture does), the show
        # inherits them from its first episode that publishes any.
        if audio is None and subtitles is None:
            audio, subtitles = page.first_episode_languages()

        offers = [
            self._offer(
                provider_content_id=show_id,
                content_type=ContentType.SERIES,
                offer_type=offer_type,
                provider_url=f"{BASE_URL}{show_path}",
                quality=quality,
                audio=audio,
                subtitles=subtitles,
                record_id=show_id,
            )
            for offer_type in offer_types
        ]
        if page.title_type == "season" and season_number is not None:
            offers.extend(
                self._offer(
                    provider_content_id=show_id,
                    content_type=ContentType.SEASON,
                    offer_type=offer_type,
                    provider_url=f"{BASE_URL}{page.path}",
                    season_number=season_number,
                    quality=quality,
                    audio=audio,
                    subtitles=subtitles,
                    record_id=f"{show_id}/s{season_number}",
                )
                for offer_type in offer_types
            )

        for episode in page.episodes:
            # Episodes inherit the page's offer types: Prime's per-episode
            # ``isPrime`` is False on every captured episode, including
            # episodes of Prime-included seasons, so it carries no signal.
            episode_audio = _languages(episode.meta.get("audioTracks"))
            episode_subtitles = _languages(episode.meta.get("subtitles"))
            offers.extend(
                self._offer(
                    provider_content_id=show_id,
                    content_type=ContentType.EPISODE,
                    offer_type=offer_type,
                    provider_url=f"{BASE_URL}{episode.path}",
                    season_number=season_number,
                    episode_number=episode.number,
                    quality=_quality(episode.meta),
                    audio=episode_audio if episode_audio is not None else audio,
                    subtitles=(
                        episode_subtitles if episode_subtitles is not None else subtitles
                    ),
                    record_id=_record_id(
                        show_id, season_number, episode.number
                    ),
                )
                for offer_type in offer_types
            )
        return contents, offers

    def _offer(
        self,
        *,
        provider_content_id: str,
        content_type: ContentType,
        offer_type: str,
        provider_url: str,
        record_id: str,
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        quality: str = "",
        audio: Optional[list[str]] = None,
        subtitles: Optional[list[str]] = None,
    ) -> ProviderOffer:
        return ProviderOffer(
            provider_id=PROVIDER,
            provider_content_id=provider_content_id,
            country=normalize_country(COUNTRY),
            content_type=normalize_content_type(content_type),
            tmdb_id=None,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=offer_type,
            provider_url=provider_url,
            quality=quality,
            audio_languages=audio,
            subtitle_languages=subtitles,
            available_from=None,
            available_until=None,
            source_record_id=record_id,
            source_version=SOURCE_VERSION,
        )


# --------------------------------------------------------------------- pages


@dataclass(frozen=True)
class _Episode:
    """One episode card of a season page: position plus its own metadata."""

    number: int
    path: str
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class _DetailPage:
    """The parts of one ``/detail/`` SSR payload the source reads."""

    compact: str
    path: str
    title_type: str
    meta: dict
    cue: str
    show: tuple[str, str]
    episodes: tuple[_Episode, ...] = ()
    languages: tuple[tuple[list[str], list[str]], ...] = ()

    def first_episode_languages(self) -> tuple[Optional[list[str]], Optional[list[str]]]:
        """Audio/subtitles of the first episode record that publishes any."""
        for audio, subtitles in self.languages:
            if audio or subtitles:
                return audio, subtitles
        return None, None


def _json_script(html: str, marker: str, *, url: str) -> dict:
    """Decode one ``<script id="marker" type="application/json">`` payload.

    Missing or malformed payloads are retrieval failures: a page that answers
    200 without the payload we came for (consent interstitial, template
    change, transport garbage) must not read as "no titles here".
    """
    match = re.search(
        r"""<script[^>]*\bid=["']%s["'][^>]*>(.*?)</script>""" % re.escape(marker),
        html,
        re.S | re.I,
    )
    if match is None:
        raise SourceFetchError(f"Prime Video page carries no {marker} for {url}")
    try:
        data = json.loads(match.group(1))
    except ValueError as exc:
        raise SourceFetchError(
            f"Prime Video {marker} payload is not valid JSON for {url}"
        ) from exc
    if not isinstance(data, dict):
        raise SourceFetchError(
            f"Prime Video {marker} payload is not an object for {url}"
        )
    return data


def _page(html: str, *, url: str) -> tuple[dict, dict]:
    """``(head, body)`` of a hydrated page, whatever route it belongs to."""
    hydration = _json_script(html, HYDRATION_MARKER, url=url)
    init = hydration.get("init")
    if not isinstance(init, dict):
        raise SourceFetchError(f"Prime Video hydration carries no init for {url}")
    preparations = init.get("preparations")
    if not isinstance(preparations, dict):
        raise SourceFetchError(f"Prime Video hydration carries no preparations for {url}")
    head, body = preparations.get("head"), preparations.get("body")
    if not isinstance(head, dict) or not isinstance(body, dict):
        raise SourceFetchError(
            f"Prime Video hydration carries no head/body for {url}"
        )
    return head, body


def _check_territory(html: str, *, url: str) -> str:
    """Territory the page was rendered for; raises on a foreign catalogue.

    Prime serves the catalogue of the client's egress region, so a mismatch
    means every subsequent offer would be attributed to the wrong country.
    The failure is loud by design (Tubi's geo-lock contract).
    """
    store = _json_script(html, STORE_MARKER, url=url)
    context = store.get("RequestContext")
    territory = context.get("currentTerritory") if isinstance(context, dict) else None
    if not isinstance(territory, str) or not territory:
        raise SourceFetchError(
            f"Prime Video page reports no territory for {url}"
        )
    if territory.upper() != COUNTRY:
        raise SourceFetchError(
            f"Prime Video region mismatch: page served {territory!r} but "
            f"{COUNTRY} is the declared scope (egress IP selects the catalogue)"
        )
    return territory


def _entities(body: dict) -> tuple[list[dict], Optional[str]]:
    """Every entity of an index page plus its next-page URL, if any."""
    entities: list[dict] = []
    for container in body.get("containers") or []:
        if not isinstance(container, dict):
            continue
        for entity in container.get("entities") or []:
            if isinstance(entity, dict):
                entities.append(entity)
    pagination = body.get("pagination")
    next_url = pagination.get("url") if isinstance(pagination, dict) else None
    if not isinstance(next_url, str) or not next_url.strip():
        next_url = None
    return entities, next_url


def _detail_path(entity: dict) -> str:
    """``/detail/<compactGTI>`` of a title card, or ``""`` when not a title."""
    if entity.get("entityType") not in TITLE_ENTITY_TYPES:
        return ""
    link = entity.get("link")
    raw = link.get("url") if isinstance(link, dict) else None
    if not isinstance(raw, str):
        return ""
    path = urlsplit(raw).path
    match = DETAIL_PATH_RE.match(path)
    return f"/detail/{match.group('gti')}" if match else ""


def _card_cue(entity: dict) -> str:
    """Commercial message Prime prints on the card (detail cue wins later)."""
    cues = entity.get("entitlementCues")
    if not isinstance(cues, dict):
        return ""
    for key in ("focusMessage", "glanceMessage", "compactFocusMessage"):
        message = cues.get(key)
        if isinstance(message, dict):
            text = message.get("message")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _genre_paths(html: str) -> list[str]:
    """Genre index paths linked from ``/categories``, in page order."""
    return list(dict.fromkeys(GENRE_HREF_RE.findall(html)))


def _log_page(head: dict, *, url: str) -> None:
    """Log which index page we actually walked (pagination is signed)."""
    page_metadata = head.get("pageMetadata")
    if not isinstance(page_metadata, dict):
        return
    logger.debug(
        "prime_video_index_page url=%s page_type=%s sub_page_type=%s",
        url, page_metadata.get("pageType"), page_metadata.get("subPageType"),
    )


def _detail_page(html: str, *, url: str) -> Optional[_DetailPage]:
    """Parse one ``/detail/`` payload, or ``None`` when it is not a title page.

    A page that hydrates but is not a detail page (the storefront answering a
    title URL) is skipped and logged — the caller reports the walk as
    "nothing observed" when that happens to every page. A detail page whose
    payload we cannot make sense of is a failure, not an empty title.
    """
    _head, body = _page(html, url=url)
    atf_state = (body.get("atf") or {}).get("state")
    if not isinstance(atf_state, dict):
        logger.info(
            "prime_video_content_skipped url=%s reason=not_a_detail_page", url
        )
        return None
    page_title_id = atf_state.get("pageTitleId")
    if not isinstance(page_title_id, str) or not page_title_id:
        raise SourceFetchError(f"Prime Video detail page has no pageTitleId for {url}")

    self_entry = atf_state.get("self")
    self_entry = self_entry.get(page_title_id) if isinstance(self_entry, dict) else {}
    if not isinstance(self_entry, dict):
        self_entry = {}
    link = self_entry.get("link")
    page_path = urlsplit(link if isinstance(link, str) else url).path
    compact = str(self_entry.get("compactGTI") or "") or _path_gti(page_path, url=url)

    btf_state = (body.get("btf") or {}).get("state")
    btf_state = btf_state if isinstance(btf_state, dict) else {}
    detail_block = btf_state.get("detail")
    detail_block = detail_block.get("detail") if isinstance(detail_block, dict) else {}
    detail_block = detail_block if isinstance(detail_block, dict) else {}
    meta = detail_block.get(page_title_id)
    meta = meta if isinstance(meta, dict) else {}

    title_type = str(meta.get("titleType") or self_entry.get("titleType") or "").lower()
    if title_type not in {"movie", "season", "series"}:
        # Collections and other storefront surfaces link ``/detail/`` too.
        logger.info(
            "prime_video_content_skipped url=%s title_type=%r reason=unsupported_title_type",
            url, title_type or None,
        )
        return None

    show = _show_identity(atf_state, meta, compact=compact, path=page_path)
    episodes = _episodes(btf_state, detail_block)
    languages = tuple(
        (_languages(episode.meta.get("audioTracks")),
         _languages(episode.meta.get("subtitles")))
        for episode in episodes
    )
    return _DetailPage(
        compact=compact,
        path=page_path,
        title_type=title_type,
        meta=meta,
        cue=_detail_cue(atf_state, page_title_id),
        show=show,
        episodes=tuple(episodes),
        languages=languages,
    )


def _show_identity(
    atf_state: dict, meta: dict, *, compact: str, path: str
) -> tuple[str, str]:
    """``(compactGTI, path)`` of the *show* a detail page belongs to.

    For a movie it is the page itself. For a season it is the canonical first
    season's compactGTI from ``atf.state.seasons`` (the only Prime-owned id for
    the show — see the module docstring); when the page lists no seasons the
    observed page is the best identity available and is used as-is.
    """
    seasons_state = atf_state.get("seasons")
    candidates: list[tuple[int, str]] = []
    if isinstance(seasons_state, dict):
        for entries in seasons_state.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                link = entry.get("seasonLink")
                gti = _gti_of(urlsplit(link).path) if isinstance(link, str) else ""
                if not gti:
                    continue
                sequence = _as_int(entry.get("sequenceNumber"))
                candidates.append(
                    (sequence if sequence is not None else 1 << 30, gti)
                )
    if not candidates:
        return compact, path
    _sequence, show_id = min(candidates, key=lambda item: item[0])
    return show_id, f"/detail/{show_id}"


def _episodes(btf_state: dict, detail_block: dict) -> list[_Episode]:
    """Episode records of a season page, in the order the page lists them."""
    episode_list = btf_state.get("episodeList")
    ids = episode_list.get("cardTitleIds") if isinstance(episode_list, dict) else None
    if not isinstance(ids, list):
        return []
    records = btf_state.get("self")
    records = records if isinstance(records, dict) else {}
    episodes: list[_Episode] = []
    for gti in ids:
        record = records.get(gti) if isinstance(gti, str) else None
        record = record if isinstance(record, dict) else {}
        meta = detail_block.get(gti) if isinstance(gti, str) else None
        meta = meta if isinstance(meta, dict) else {}
        number = _as_int(meta.get("episodeNumber"))
        path = urlsplit(str(record.get("link") or "")).path
        if number is None or not DETAIL_PATH_RE.match(path or ""):
            logger.info(
                "prime_video_episode_skipped gti=%s reason=unparseable_position", gti
            )
            continue
        episodes.append(_Episode(number=number, path=path, meta=meta))
    return episodes


def _detail_cue(atf_state: dict, page_title_id: str) -> str:
    """Detail page's own commercial message (``focusMessage``)."""
    action = atf_state.get("action")
    action = action.get("atf") if isinstance(action, dict) else {}
    if not isinstance(action, dict):
        return ""
    for entry in action.values():
        if not isinstance(entry, dict):
            continue
        messages = entry.get("messages")
        if not isinstance(messages, dict):
            continue
        focus = messages.get("focusMessage")
        if not isinstance(focus, dict):
            continue
        dv_message = focus.get("dvMessage")
        text = dv_message.get("string") if isinstance(dv_message, dict) else None
        if isinstance(text, str) and text.strip():
            return text.strip()
        text = focus.get("message")
        if isinstance(text, str) and text.strip():
            return text.strip()
    _ = page_title_id
    return ""


def _gti_of(path: str) -> str:
    """``compactGTI`` of a ``/detail/...`` path, or ``""`` for anything else."""
    match = DETAIL_PATH_RE.match(path or "")
    return match.group("gti") if match else ""


def _record_id(show_id: str, season_number, episode_number) -> str:
    """Provenance key of one offer: show id plus the positions it applies to."""
    parts = [show_id]
    if season_number is not None:
        parts.append(f"s{season_number}")
    if episode_number is not None:
        parts.append(f"e{episode_number}")
    return "/".join(parts)


def _path_gti(path: str, *, url: str) -> str:
    gti = _gti_of(path)
    if not gti:
        raise SourceFetchError(f"Prime Video link carries no detail id for {url}")
    return gti


# ------------------------------------------------------------------ semantics


def _offer_types(is_prime, cue: str) -> list[str]:
    """Canonical offer types Prime implies for one title, in stable order.

    Two observed signals, neither of them a price (``buyBox`` is empty in SSR):

    - ``isPrime`` on the page (``True`` for Prime-included titles, ``False``
      for rent/buy and channel-backed ones) together with the page's own
      ``focusMessage``;
    - the message vocabulary itself: Prime trial wording marks a membership
      offer, channel trial / "auto-renews at ..." wording marks an add-on
      subscription, "first episode free" marks a free window, rent/buy
      wording marks transactional offers, and "pre-order" wording marks the
      purchase of a title that is not released yet.

    Every derived type is kept (a title can be includable *and* purchasable),
    and a title nothing can be derived from keeps a single ``unknown`` offer
    rather than disappearing — the read path already reports non-qualifying
    offer types as excluded reasons.
    """
    text = str(cue or "")
    types: list[str] = []
    if _FREE_WITH_EPISODE_RE.search(text):
        types.append(normalize_offer_type("free"))
    if bool(is_prime) or bool(_PRIME_WORD_RE.search(text)):
        types.append(normalize_offer_type("subscription"))
    elif _SUBSCRIPTION_WORD_RE.search(text):
        types.append(normalize_offer_type("addon"))
    if _RENT_WORD_RE.search(text):
        types.append(normalize_offer_type("rent"))
    if _BUY_WORD_RE.search(text) or _PREORDER_WORD_RE.search(text):
        types.append(normalize_offer_type("buy"))
    if not types:
        types.append(normalize_offer_type("unknown"))
    return list(dict.fromkeys(types))


def _quality(meta: dict) -> str:
    """Highest rendition Prime states for the title, in the shared vocabulary."""
    if meta.get("isUhd"):
        return "4K"
    if meta.get("isHdr") or meta.get("isHdr10Plus") or meta.get("isDolbyVision"):
        return "HDR"
    return ""


def _languages(values) -> Optional[list[str]]:
    """Canonical language codes, or ``None`` when the page publishes none.

    ``None`` (unknown to the source) stays distinct from ``[]`` (the title has
    no audio/subtitles): Prime ships empty lists on season records, which
    means "not published here", not "no languages exist".
    """
    if not isinstance(values, (list, tuple)) or not values:
        return None
    names = [name for name in (_clean_language(value) for value in values) if name]
    if not names:
        return None
    return normalize_languages(names) or None


def _clean_language(value) -> str:
    """Strip the display qualifiers Prime appends to track names."""
    return _LANGUAGE_QUALIFIER_RE.sub("", str(value)).strip()


def _release_date(meta: dict) -> Optional[date]:
    """Premiere date of a movie: exact when published, else year precision."""
    raw = meta.get("releaseDate")
    if isinstance(raw, str):
        match = _RELEASE_DATE_RE.match(raw.strip())
        if match:
            month = _MONTHS.get(match.group("month").title())
            if month is not None:
                try:
                    return date(int(match.group("year")), month, int(match.group("day")))
                except ValueError:
                    pass
    year = _as_int(meta.get("releaseYear"))
    if year is not None and 1800 <= year <= 2100:
        return date(year, 1, 1)
    return None


def _poster(meta: dict) -> str:
    images = meta.get("images")
    if not isinstance(images, dict):
        return ""
    for key in ("packshot", "covershot"):
        url = images.get(key)
        if isinstance(url, str) and url.strip():
            return url.strip()
    return ""


def _as_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None
