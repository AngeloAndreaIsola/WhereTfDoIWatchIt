"""Tubi dedicated collector — sitemap discovery -> SSR detail pages -> offers (Phase 10).

Third provider-specific :class:`AvailabilitySource`, and the first whose
discovery is *sitemap-driven* rather than API- or index-driven:

    https://tubitv.com/sitemap.xml (sitemap index)
        -> only the title sitemaps  sitemaps/movies-*.xml, sitemaps/tv-shows-*.xml
        -> one detail URL per entry (round-robin across sitemaps, bounded)
        -> ``window.__data`` embedded in each detail page's SSR HTML
        -> one ``DiscoveredContent`` per title + ``ProviderOffer`` at every
           granularity the embedded state actually carries
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

Research basis (every claim below was verified against captured pages, never
guessed; the host used for research egresses from the EU and Tubi geo-blocks
that egress, so no live request is possible from it and no endpoint here was
probed live):

- The index lists ``sitemaps/movies-1..8.xml`` and ``sitemaps/tv-shows-1..2.xml``
  among other children (``category-pages``, ``linear-series-pages``,
  ``static-pages``, ``upcoming-pages``, ``category/*``); only the movies/tv-shows
  children hold titles, so the rest are filtered out by URL shape. Category
  sitemaps overlap the title sitemaps and would double the walk for no new
  titles.
- Each title sitemap holds ~10,000 ``<url><loc>`` entries (``<lastmod>`` is the
  *generation* time, not a revision of the item, so ``lastmod`` cannot drive
  incremental sync — the source declares ``supports_incremental_sync=False``
  and walks the whole configured window every run).
- Detail pages are server-rendered: the state sits in
  ``window.__data={...};`` as a **JavaScript object literal, not JSON** — bare
  ``undefined`` values (27..143 per observed page), occasionally
  ``new Date("...")``, trailing commas, and no spaces around ``=``. Strict JSON
  parsing fails at ~char 498 on a real page, so the literal is scanned into
  strict JSON and handed to ``json.JSONDecoder.raw_decode`` (see
  :func:`_decode_object`).
- ``window.__data.video.byId`` holds the title, keyed by Tubi's own id —
  **zero-padded on some pages and unpadded on others** (``02185`` vs ``2185``,
  ``0300016468`` vs ``300016468``), while the URL path, the ``series_id`` on
  episodes and the page's own links mix both shapes. Every id is therefore
  normalized with ``str(int(id))`` before it is used as
  ``provider_content_id``; that normalized id is what the offers, the content
  row and the ``ContentMapping`` key all share (CBC-style: season/episode rows
  stay distinct through the offer identity's season/episode slots).
- A ``200`` answer can still be the SPA shell: ``"byId": {}``, no ``<title>``,
  no ``og:`` tags (~41.7 kB). It means "nothing observable about that title
  right now" — either the sitemap entry went stale (confirmed: several captured
  entries return only the shell) or the page did not render. It is logged
  (``tubi_content_missing``) and skipped; it is never converted into an empty
  result *for that title* and it never deactivates anything. A page without
  the ``window.__data`` marker at all (the GDPR interstitial served to EU
  egress, transport garbage) is a retrieval failure and raises
  :class:`SourceFetchError`.
- Per-title metadata actually present in ``byId``: ``title``, ``year``,
  ``duration`` (seconds), ``detailed_type`` (``movie``/``series``/``episode``),
  ``lang`` (audio language name), ``subtitles`` (list of ``{lang, lang_alpha3}``,
  ``[]`` when the title has none), ``availability_starts``/``availability_ends``
  (ISO-8601; ``availability_ends`` is often ``null``), ``imdb_id``,
  ``posterarts`` (scheme-relative ``//canvas-lb.tubitv.com/...``),
  ``description``, ``needs_login``, and per-entry ``video_resources[].resolution``
  (``720P``, ``576P``, ...) which is the only quality signal.
- A series page carries the series entry plus its **default season** episodes
  in ``byId``; the remaining seasons appear as ``seasons[].episodes[]`` wrappers
  — full episode objects for the default season, bare ``{id, num}`` stubs for
  the others (and sometimes stubs only, with no field beyond id/order). Only
  episodes that arrive as full records can be offered; stubs are used solely to
  attribute a season number to those records. Season ``N>default`` pages exist
  in the wild but are **not** listed in the sitemaps, so they are never
  fetched: seasons beyond the default one produce no offers, which is a
  coverage limit, not a claim about availability.
- Episode watch URLs come from the page's own links
  (``href="/tv-shows/{id}/{slug}"`` — 20/20 episodes had one on every captured
  series page). When a page links none, the offer is written without a URL
  rather than with a guessed one.

Product semantics, all documented rather than papered over:

- Offer type is always **free with ads** (``OfferType.FREE_WITH_ADS``): Tubi is
  an ad-supported service. ``needs_login`` (a free Tubi account for
  resume/sync) is observed but deliberately ignored — it does not gate playback.
- ``country`` inside the payload (``"United States"``) is the *production*
  country of the title and is never used for offers; the source only ever
  reports its own scope, ``US``.
- An offer whose provider-stated window is already closed, or has not opened
  yet, is skipped and logged (``tubi_offer_expired`` /
  ``tubi_offer_not_yet_available``): the read path treats stored offers as
  current regardless of ``available_until``, so publishing a window we know is
  over would offer something Tubi has said it will not. Offers written while
  the window was open keep their stored ``available_until``.
- The catalogue is **never an authoritative snapshot**: sitemap entries go
  stale, shell pages hide titles, the walk is bounded by ``max_contents``, and
  Tubi exposes no per-item removal signal. Results are therefore always
  ``authoritative=False`` / ``complete_snapshot=False`` (absence never
  deactivates) even though ``sync_mode`` is ``FULL`` — one fetch reports the
  whole configured window.
- **No TMDb ids** (``supports_tmdb_ids=False``): Tubi publishes ``imdb_id``,
  recorded in the content metadata as an external id for reference, while
  mapping happens in the refresh service by local title/year matching.
- **Runtime bound**: at 10,000 entries per sitemap the full catalogue is ~100k
  detail pages of 194 kB..2 MB each. ``DEFAULT_MAX_CONTENTS`` therefore bounds
  one run (entries are interleaved across sitemaps so a capped run still mixes
  movies and series) and the truncation is logged; see ``docs/CRON.md``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

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

BASE_URL = "https://tubitv.com"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemap.xml"
COUNTRY = "US"
DEFAULT_TIMEOUT = 30
# Politeness gap between live requests only (tests route through ``requester``
# and never sleep): one catalogue walk hits hundreds of detail pages.
DEFAULT_DELAY = 0.1
# Safety bound on one run. The full title catalogue is ~100k detail pages of
# 194 kB..2 MB; the sitemaps are ordered by the provider, newest ``lastmod``
# first, so a capped walk still covers current titles. ``None`` disables the
# bound (tests use it; production must not).
DEFAULT_MAX_CONTENTS = 500
SOURCE_VERSION = "tubi-ssr-v1"
STATE_MARKER = "window.__data"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Only title sitemaps; every other child of the index is editorial/structural.
TITLE_SITEMAP_RE = re.compile(
    r"^https://tubitv\.com/sitemaps/(movies-\d+|tv-shows-\d+)\.xml$"
)
DETAIL_PATH_RE = re.compile(r"^/(movies|series)/(\d+)(?:/|$)")
_EPISODE_HREF_RE = re.compile(
    r"""href\s*=\s*["'](?:https?://(?:www\.)?tubitv\.com)?"""
    r"""(?P<path>/tv-shows/(?P<id>\d+)[^"']*)["']""",
    re.IGNORECASE,
)
_RESOLUTION_RE = re.compile(r"^(\d{3,4})[Pp]?")
# Episode titles are provider-numbered, e.g. "S01:E02 - Winners and Losers".
_TITLE_POSITION_RE = re.compile(r"^S(\d+):E(\d+)\b")

FREE_WITH_ADS: str = normalize_offer_type("free_with_ads")
# Tubi advertises max-resolution renditions; the ranking vocabulary is the
# shared ``tracker.services.score.QUALITY_RANK`` set (4k/uhd/hdr/qhd/hd/sd).
_HD_FLOOR = 720
_4K_FLOOR = 2160


class TubiClient:
    """Thin client for Tubi's public sitemap and server-rendered detail pages.

    ``requester`` is a ``url -> html`` callable so tests inject a fixture router
    and never touch the network. Retrieval failures (HTTP error, transport
    error, a payload that is not the expected XML or page) always surface as
    :class:`SourceFetchError` — never as an empty-but-valid result. Page-level
    interpretation (state decoding, shell detection) belongs to
    :func:`_state_from_html`, not to the client.
    """

    def __init__(self, requester=None, timeout=DEFAULT_TIMEOUT, delay=DEFAULT_DELAY):
        self.requester = requester
        self.timeout = timeout
        self.delay = delay

    def sitemap_index(self) -> list[str]:
        """Child ``<loc>`` URLs of the sitemap index."""
        return _xml_locs(self._get(SITEMAP_INDEX_URL), expect="sitemapindex",
                         url=SITEMAP_INDEX_URL)

    def sitemap_entries(self, url: str) -> list[str]:
        """``<loc>`` URLs of one sitemap (title order, provider's own order)."""
        return _xml_locs(self._get(url), expect="urlset", url=url)

    def detail_page(self, url: str) -> str:
        """Raw HTML of one detail page (shell pages included)."""
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
            raise SourceFetchError(f"Tubi request failed: {exc}") from exc
        if not isinstance(payload, str):
            raise SourceFetchError(f"Unexpected Tubi response type for {url}")
        return payload

    def _http(self, url: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT,
                     "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SourceFetchError(f"Tubi returned HTTP {response.status_code} for {url}")
        return response.text


class TubiSource:
    """Catalogue-driven Tubi source (``content=None`` fetches only)."""

    slug = "tubi"
    name = "Tubi"
    source_type = SourceType.WEB
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"tubi"}),
        supported_countries=frozenset({COUNTRY}),
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Sitemap entries go stale, shell pages hide titles, the walk is
        # bounded: absence can never be evidence of unavailability.
        supports_authoritative_snapshots=False,
        # Tubi publishes no TMDb ids (``imdb_id`` is recorded as metadata);
        # mapping happens in the refresh service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        supports_languages=True,
    )

    def __init__(self, client=None, max_contents=DEFAULT_MAX_CONTENTS):
        self.client = client or TubiClient()
        self.max_contents = max_contents

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "tubi is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints, already validated
        # against ``capabilities`` by the refresh service; Tubi only ever
        # reports its own (tubi, US). cursor is accepted but unused: the
        # sitemaps offer no resumable server-side cursor, so one fetch reports
        # the whole configured window instead of inventing a token.
        entries = self._entries()
        scanned = entries if self.max_contents is None else entries[: self.max_contents]
        if len(scanned) < len(entries):
            logger.warning(
                "tubi_catalogue_truncated entries=%d scanned=%d max_contents=%d",
                len(entries), len(scanned), self.max_contents,
            )

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        observed = 0
        skipped = 0
        for url in scanned:
            match = DETAIL_PATH_RE.match(urlparse(url).path)
            if match is None:
                # A non-title URL in the index: editorial surface, not a
                # catalogue item. It is never even requested.
                logger.info("tubi_entry_skipped url=%s reason=not_a_title_url", url)
                skipped += 1
                continue
            html = self.client.detail_page(url)
            state = _state_from_html(html, url=url)
            if state is None:
                # 200 SPA shell: the title is not observable right now.
                logger.info("tubi_content_missing url=%s reason=spa_shell", url)
                skipped += 1
                continue
            parsed = self._parse_page(
                kind=match.group(1),
                raw_id=match.group(2),
                url=url,
                html=html,
                state=state,
            )
            if parsed is None:
                skipped += 1
                continue
            discovered, page_offers = parsed
            contents.append(discovered)
            offers.extend(page_offers)
            observed += 1

        if scanned and observed == 0:
            # Everything came back as a shell/unsupported page: that reads like
            # a broken fetch path (EU interstitial, template change), not like
            # an empty catalogue. Log it loudly; still never raise, because the
            # walk itself succeeded and nothing gets deactivated either way.
            logger.warning(
                "tubi_catalogue_nothing_observed fetched=%d skipped=%d",
                len(scanned), skipped,
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

    def _entries(self) -> list[str]:
        """Title URLs from the index, interleaved and de-duplicated."""
        index = self.client.sitemap_index()
        sitemaps = [url for url in index if TITLE_SITEMAP_RE.match(url)]
        if not sitemaps:
            raise SourceFetchError("Tubi sitemap index lists no movie/series sitemaps")
        queues = [self.client.sitemap_entries(url) for url in sitemaps]
        return list(dict.fromkeys(_interleave(queues)))

    # ------------------------------------------------------------------ parse

    def _parse_page(self, *, kind: str, raw_id: str, url: str, html: str, state: dict):
        """One detail page -> (DiscoveredContent, list[ProviderOffer])."""
        by_id: dict[str, dict] = {}
        for key, value in (state["video"]["byId"] or {}).items():
            if isinstance(value, dict):
                by_id[_norm_id(key)] = value

        provider_content_id = _norm_id(raw_id)
        entry = by_id.get(provider_content_id)
        if entry is None:
            # Shell-free but this title is not in the state (stale entry that
            # still renders, or a page that hydrated something else).
            logger.warning("tubi_content_missing url=%s reason=entry_not_in_state", url)
            return None

        detailed_type = str(entry.get("detailed_type") or "").strip().lower()
        if detailed_type not in ("movie", "series"):
            logger.info("tubi_unsupported_type url=%s type=%r", url, detailed_type)
            return None
        expected = "movie" if kind == "movies" else "series"
        if detailed_type != expected:
            logger.warning(
                "tubi_content_missing url=%s reason=type_mismatch expected=%s got=%s",
                url, expected, detailed_type,
            )
            return None

        content = self._content(provider_content_id, entry, url, detailed_type)
        if detailed_type == "movie":
            offer = self._offer(
                provider_content_id=provider_content_id,
                content_type=ContentType.MOVIE,
                entry=entry,
                provider_url=url,
                source_record_id=provider_content_id,
            )
            return content, [] if offer is None else [offer]
        return content, self._series_offers(
            provider_content_id=provider_content_id,
            entry=entry,
            url=url,
            html=html,
            by_id=by_id,
        )

    def _content(self, provider_content_id: str, entry: dict, url: str,
                 detailed_type: str) -> DiscoveredContent:
        content_type = ContentType.MOVIE if detailed_type == "movie" else ContentType.SERIES
        year = _as_int(entry.get("year"))
        imdb_id = str(entry.get("imdb_id") or "").strip()
        return DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=provider_content_id,
            content_type=normalize_content_type(content_type),
            title=str(entry.get("title") or "").strip(),
            # Movies: production year, recorded as Jan 1 of that year (year
            # precision), like CBC's movies. Series deliberately get none —
            # Tubi's series ``year`` is not a dependable first-air year
            # (Ancient Aliens carries 2020 against a 2010 premiere), so series
            # stay unmatched by year and resolve title-only.
            release_date=date(year, 1, 1) if (year and detailed_type == "movie") else None,
            # Tubi's ``duration`` is seconds (6882 for a 114-minute film).
            duration=_as_int(entry.get("duration")),
            url=url,
            poster=_poster(entry),
            description=str(entry.get("description") or "").strip(),
            external_ids={"imdb_id": imdb_id} if imdb_id else {},
            source_id=self.slug,
        )

    def _series_offers(self, *, provider_content_id: str, entry: dict, url: str,
                       html: str, by_id: dict[str, dict]) -> list[ProviderOffer]:
        """Series + season + episode offers for one series page.

        Only episodes that arrive as full records in ``byId`` become offers;
        the ``seasons[]`` wrappers attribute a season number to them (and, for
        the non-default seasons, carry stubs with nothing but id/order).
        """
        wrappers = [w for w in (entry.get("seasons") or []) if isinstance(w, dict)]
        season_of: dict[str, int] = {}
        for wrapper in wrappers:
            season_number = _as_int(wrapper.get("number"))
            if season_number is None:
                continue
            for item in (wrapper.get("episodes") or []):
                if isinstance(item, dict) and item.get("id") is not None:
                    season_of.setdefault(_norm_id(item["id"]), season_number)
        default_season = _as_int(wrappers[0].get("number")) if wrappers else None

        episode_urls = _episode_urls(html)
        episode_offers: list[ProviderOffer] = []
        for episode_id, episode in _episode_entries(by_id, provider_content_id):
            season_number = season_of.get(episode_id)
            episode_number = _as_int(episode.get("episode_number"))
            if season_number is None or episode_number is None:
                position = _TITLE_POSITION_RE.match(str(episode.get("title") or ""))
                if position:
                    if season_number is None:
                        season_number = int(position.group(1))
                    if episode_number is None:
                        episode_number = int(position.group(2))
            if season_number is None or episode_number is None:
                logger.warning(
                    "tubi_offer_skipped content=%s episode=%s "
                    "reason=unparseable_numbering",
                    provider_content_id, episode_id,
                )
                continue
            offer = self._offer(
                provider_content_id=provider_content_id,
                content_type=ContentType.EPISODE,
                entry=episode,
                provider_url=episode_urls.get(episode_id, ""),
                season_number=season_number,
                episode_number=episode_number,
                source_record_id=episode_id,
            )
            if offer is not None:
                episode_offers.append(offer)

        if not episode_offers:
            # Listed, but nothing watchable was observable on this page — the
            # catalogue row still stands (unresolved != discarded), offers do
            # not. Same rule CBC applies to a lineup with nothing playable.
            logger.info(
                "tubi_content_only content=%s reason=no_episodes_observed",
                provider_content_id,
            )
            return []

        offers = [
            self._offer(
                provider_content_id=provider_content_id,
                content_type=ContentType.SERIES,
                entry=entry,
                provider_url=url,
                source_record_id=provider_content_id,
            )
        ]
        for season_number in sorted({o.season_number for o in episode_offers}):
            # The default season *is* the page; every other season URL follows
            # the pattern the page itself links.
            season_url = url if season_number == default_season else f"{url}/season-{season_number}"
            offers.append(
                self._offer(
                    provider_content_id=provider_content_id,
                    content_type=ContentType.SEASON,
                    entry=entry,
                    provider_url=season_url,
                    season_number=season_number,
                    source_record_id=f"{provider_content_id}/{season_number}",
                )
            )
        offers.extend(episode_offers)
        return [offer for offer in offers if offer is not None]

    def _offer(self, *, provider_content_id: str, content_type: str, entry: dict,
               provider_url: str, season_number: Optional[int] = None,
               episode_number: Optional[int] = None,
               source_record_id: str) -> Optional[ProviderOffer]:
        """One provider-stated offer, or ``None`` when its window is closed.

        Season/episode offers take their availability and language metadata
        from the entry they are derived from: a season is offered through the
        series entry, an episode through its own record (Tubi publishes no
        season-level availability of its own).
        """
        available_from = _parse_timestamp(entry.get("availability_starts"))
        available_until = _parse_timestamp(entry.get("availability_ends"))
        now = datetime.now(timezone.utc)
        if available_from is not None and available_from > now:
            logger.info(
                "tubi_offer_not_yet_available content=%s type=%s season=%s "
                "episode=%s available_from=%s",
                provider_content_id, content_type, season_number, episode_number,
                available_from.isoformat(),
            )
            return None
        if available_until is not None and available_until <= now:
            logger.info(
                "tubi_offer_expired content=%s type=%s season=%s episode=%s "
                "available_until=%s",
                provider_content_id, content_type, season_number, episode_number,
                available_until.isoformat(),
            )
            return None
        return ProviderOffer(
            provider_id=self.slug,
            provider_content_id=provider_content_id,
            country=normalize_country(COUNTRY),
            content_type=normalize_content_type(content_type),
            tmdb_id=None,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=FREE_WITH_ADS,
            provider_url=provider_url,
            quality=_quality(entry),
            audio_languages=_audio_languages(entry),
            subtitle_languages=_subtitle_languages(entry),
            available_from=available_from,
            available_until=available_until,
            source_record_id=source_record_id,
            source_version=SOURCE_VERSION,
        )


# ------------------------------------------------------------------ walk


def _interleave(queues: list[list[str]]) -> list[str]:
    """Round-robin across sitemaps so a capped walk mixes movies and series."""
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
        # An HTML interstitial (GDPR consent, challenge page) parses as XML
        # only by accident: treat anything else as a failed retrieval.
        raise SourceFetchError(f"Tubi returned a malformed sitemap for {url}") from exc
    root_name = _localname(root.tag)
    if root_name != expect:
        raise SourceFetchError(
            f"Unexpected Tubi sitemap root {root_name!r} for {url} (want {expect!r})"
        )
    locs = [
        element.text.strip()
        for element in root.iter()
        if _localname(element.tag) == "loc" and element.text and element.text.strip()
    ]
    return locs


# ------------------------------------------------------------------ page


def _state_from_html(html: str, *, url: str = "") -> Optional[dict]:
    """Decode ``window.__data`` from one detail page.

    Returns ``None`` when the page is the ``200`` SPA shell (empty ``byId``):
    the title is not observable right now. Raises :class:`SourceFetchError`
    when there is no state at all or it cannot be decoded — a missing marker is
    a different page entirely (consent interstitial, challenge), not an empty
    catalogue.
    """
    if not isinstance(html, str):
        raise SourceFetchError(f"Unexpected Tubi page type for {url}")
    marker = html.find(STATE_MARKER)
    if marker < 0:
        raise SourceFetchError(f"Tubi page carries no {STATE_MARKER} state for {url}")
    start = html.find("{", marker)
    if start < 0:
        raise SourceFetchError(f"Tubi {STATE_MARKER} carries no object for {url}")
    state, _end = _decode_object(html, start, url=url)
    if not isinstance(state, dict):
        raise SourceFetchError(f"Tubi {STATE_MARKER} is not an object for {url}")
    video = state.get("video")
    by_id = video.get("byId") if isinstance(video, dict) else None
    if not isinstance(by_id, dict):
        raise SourceFetchError(f"Tubi {STATE_MARKER} has no video.byId for {url}")
    if not by_id:
        return None
    return state


def _decode_object(text: str, start: int, *, url: str = "") -> tuple[dict, int]:
    """Decode the JS object literal starting at ``start`` (index after it).

    ``window.__data={...}`` is a JavaScript literal, not JSON: values may be
    bare ``undefined``/``NaN``/``Infinity`` or ``new Date("...")`` calls,
    trailing commas are legal and ``=`` has no surrounding spaces. The literal
    is therefore scanned token by token into strict JSON (strings are copied
    verbatim, unknown identifiers become ``null``, ``new Date("...")`` becomes
    its quoted argument) and handed to ``raw_decode``, which also reports where
    it stopped so the caller can keep the remainder of the page untouched.
    """
    out: list[str] = []
    length = len(text)
    index = start
    depth = 0
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char in "{[":
            depth += 1
            out.append(char)
            index += 1
            continue
        if char in "}]":
            depth -= 1
            out.append(char)
            index += 1
            if depth == 0:
                break
            continue
        if char == ",":
            # Drop a trailing comma before a closer: legal JS, illegal JSON.
            look = index + 1
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look < length and text[look] in "}]":
                index += 1
                continue
            out.append(char)
            index += 1
            continue
        if char == "-" and text[index + 1:].startswith("Infinity"):
            out.append("null")
            index += len("Infinity") + 1
            continue
        if char.isalpha() or char in "_$":
            end = index
            while end < length and (text[end].isalnum() or text[end] in "_$."):
                end += 1
            word = text[index:end]
            if word in ("undefined", "NaN", "Infinity"):
                out.append("null")
                index = end
                continue
            if word in ("true", "false", "null"):
                out.append(word)
                index = end
                continue
            if word == "new":
                index = _consume_call(text, end, out)
                continue
            look = end
            while look < length and text[look] in " \t\r\n":
                look += 1
            if look < length and text[look] == ":":
                # JS allows unquoted keys; the state is JSON-shaped but does
                # not have to be strict JSON.
                out.append(json.dumps(word))
                index = end
                continue
            # Any other bare identifier is not a JSON value we can vouch for.
            raise SourceFetchError(
                f"Unsupported value {word!r} in Tubi {STATE_MARKER} for {url}"
            )
        out.append(char)
        index += 1

    if in_string or depth != 0:
        raise SourceFetchError(f"Unterminated Tubi {STATE_MARKER} object for {url}")
    try:
        value, _consumed = json.JSONDecoder().raw_decode("".join(out))
    except ValueError as exc:
        raise SourceFetchError(f"Malformed Tubi {STATE_MARKER} object for {url}") from exc
    return value, index


def _consume_call(text: str, index: int, out: list[str]) -> int:
    """Emit ``new X(...)`` as its argument's JSON value; return the next index.

    Only ``new Date("2026-01-01T00:00:00Z")`` was observed (a timestamp in a
    hydration payload): the quoted argument is emitted verbatim, anything else
    collapses to ``null`` rather than being guessed at.
    """
    length = len(text)
    while index < length and text[index] in " \t\r\n":
        index += 1
    name_end = index
    while name_end < length and (text[name_end].isalnum() or text[name_end] in "_$"):
        name_end += 1
    while name_end < length and text[name_end] in " \t\r\n":
        name_end += 1
    if name_end >= length or text[name_end] != "(":
        out.append("null")
        return name_end
    depth = 0
    cursor = name_end
    in_string = False
    while cursor < length:
        char = text[cursor]
        if in_string:
            if char == "\\":
                cursor += 2
                continue
            if char == '"':
                in_string = False
            cursor += 1
            continue
        if char == '"':
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                cursor += 1
                break
        cursor += 1
    argument = text[name_end:cursor]
    quoted = re.search(r'"([^"]*)"', argument)
    out.append(json.dumps(quoted.group(1)) if quoted else "null")
    return cursor


# ------------------------------------------------------------------ fields


def _norm_id(value) -> str:
    """Provider id as the offers and content rows key it (``02185`` -> ``2185``)."""
    text = str(value if value is not None else "").strip()
    if text.isdigit():
        return str(int(text))
    return text


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


def _parse_timestamp(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("tubi_unparsable_timestamp value=%r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _poster(entry: dict) -> str:
    arts = entry.get("posterarts")
    first = arts[0] if isinstance(arts, list) and arts and isinstance(arts[0], str) else ""
    if not first:
        return ""
    if first.startswith("//"):
        # Canvas URLs are protocol-relative; the offer URL is https.
        return f"https:{first}"
    if first.startswith(("http://", "https://")):
        return first
    return ""


def _episode_urls(html: str) -> dict[str, str]:
    """Episode id -> watch URL, from the page's own links."""
    urls: dict[str, str] = {}
    for match in _EPISODE_HREF_RE.finditer(html):
        urls.setdefault(_norm_id(match.group("id")), BASE_URL + match.group("path"))
    return urls


def _episode_entries(by_id: dict[str, dict], provider_content_id: str):
    """Episode records of one series, newest-looking order kept as-is."""
    for episode_id, entry in by_id.items():
        if str(entry.get("detailed_type") or "").strip().lower() != "episode":
            continue
        series_id = entry.get("series_id")
        if series_id is not None and _norm_id(series_id) != provider_content_id:
            continue
        yield episode_id, entry


def _audio_languages(entry: dict) -> Optional[list[str]]:
    """The single audio language Tubi publishes (``None`` = not reported)."""
    language = entry.get("lang")
    if not isinstance(language, str) or not language.strip():
        return None
    return normalize_languages([language])


def _subtitle_languages(entry: dict) -> Optional[list[str]]:
    """``[]`` when the title explicitly has none; ``None`` when unreported."""
    subtitles = entry.get("subtitles")
    if subtitles is None:
        return None
    if not isinstance(subtitles, list) or not subtitles:
        return [] if isinstance(subtitles, list) else None
    names = []
    for item in subtitles:
        if isinstance(item, dict):
            name = item.get("lang")
        else:
            name = item
        if isinstance(name, str) and name.strip():
            names.append(name)
    if not names:
        # A non-empty list we cannot read is "not reported", never "none".
        return None
    return normalize_languages(names)


def _quality(entry: dict) -> str:
    """Highest rendition of the entry, mapped to the shared ranking vocabulary."""
    best = 0
    resources = entry.get("video_resources")
    if isinstance(resources, list):
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            match = _RESOLUTION_RE.match(str(resource.get("resolution") or "").strip())
            if match:
                best = max(best, int(match.group(1)))
    if best >= _4K_FLOOR:
        return "4K"
    if best >= _HD_FLOOR:
        return "HD"
    if best > 0:
        return "SD"
    return ""
