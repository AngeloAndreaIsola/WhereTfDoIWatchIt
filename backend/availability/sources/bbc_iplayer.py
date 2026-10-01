"""BBC iPlayer dedicated collector — catalogue -> provider content -> offers (Phase 9).

Second provider-specific :class:`AvailabilitySource`, proving the Phase 4-8
architecture against a catalogue shape that differs materially from CBC Gem:
public HTML pages with an embedded JSON state instead of a JSON API, per-brand
episode *slices* instead of lineups, no external ids, and no language metadata
at all:

    BBC iPlayer A-Z index (one page per letter)
        -> brand detail page(s) or single-programme detail page
        -> DiscoveredContent (one entry per *title*, provider-native id = PID)
        -> ProviderOffer rows (series / season / episode / movie granularity)
        -> AvailabilityRefreshService (ProviderContent + ContentMapping + OfferRepository)

Interface contract (public pages, no auth, geo-fenced to the UK):

- ``GET {BASE}/iplayer/a-z/{letter}`` for each of the 27 letters (``0-9`` plus
  ``a``..``z``) -> HTML embedding
  ``<script id="tvip-script-app-store">window.__IPLAYER_REDUX_STATE__ = {json}``.
  The state carries ``programmes[letter] = {count, entities[]}`` and a
  top-level ``pagination = {currentPage, totalPages, perPage}`` (``perPage``
  200). Each entity is ``{props: {href, imageTemplate, title, synopsis},
  meta: {episodesAvailable}}``; hrefs are either
  ``/iplayer/episodes/{pid}/{slug}`` (a brand: series, box set, ongoing
  programme) or ``/iplayer/episode/{pid}/{slug}`` (one single self-contained
  programme: film, one-off drama, news bulletin).
- ``GET {BASE}/iplayer/episodes/{pid}[?seriesId={sliceId}&page={N}]`` -> brand
  page: ``header`` (``title``, ``subtitle`` = tagline, ``imageTemplate``,
  ``availableSlices[]``, ``episodeCount``, ``comingSoon``), ``page.seriesPagination``
  (``{perPage, page, count, sliceId}``), top-level ``pagination``
  (``{currentPage, totalEpisodes, totalPages, perPage, pageUrl}``) and
  ``entities.results[].episode`` (``{id, title{default}, subtitle{default,
  slice}, synopsis{small}, image{default}, labels, versions[]}``). Slices are
  ``availableSlices`` minus ``more-like-this``; ``?seriesId=`` selects one and
  ``page=`` paginates it — verified live: ``?seriesId=unsliced&page=2`` on a
  996-episode slice returns distinct results with ``currentPage: 2``.
- ``GET {BASE}/iplayer/episode/{pid}`` -> 301 to the canonical slug URL. Its
  state carries ``episode`` (``{id, tleoType, title, synopses, images,
  labels{category}, programmeType, releaseDate, releaseDateTime,
  firstVersionDuration, requiresSignIn, requiresTvLicence}``) plus a
  top-level ``versions`` list (``{id, kind, duration{seconds},
  isoStartTime, isoEndTime, availability{end}}``).

Deliberate limitations, all documented rather than papered over:

- **The A-Z index cannot paginate.** The origin ignores ``page`` on
  ``/iplayer/a-z/*`` — seven parameter spellings, cache-busting headers and a
  cookie session all return byte-identical page 1 while the page advertises
  ``totalPages: 2`` — so a letter contributes at most its first 200 entries
  (~83% of the index overall; letters a, b, c, m, s and t are truncated).
  Truncation is logged per letter, the result is never ``authoritative`` and
  never a ``complete_snapshot``, so unseen offers are never deactivated —
  absence is not evidence, exactly as with CBC Gem. Adding pagination is a
  non-breaking change if the route starts honouring the parameter.
- **No external ids**: iPlayer publishes no TMDb ids
  (``supports_tmdb_ids=False``), so mapping happens in the refresh service via
  local title/year matching; an unmapped title yields no ``Offer`` rows
  (``Offer.tmdb_id`` is non-null) but is still stored as ``ProviderContent``.
- **No language metadata**: nothing in the catalogue payloads states an audio
  or subtitle language (``player.subtitles`` and ``footer.languages`` are UI
  preferences), so both stay ``None`` (unknown) and the source declares
  ``supports_languages=False``. iPlayer's ``audio-described`` versions are an
  accessibility track, not another language track.
- **Offer type is always ``free``**: iPlayer has no paid tier.
  ``requiresSignIn`` is a free BBC account and ``requiresTvLicence`` is a UK
  legal obligation, not a payment. Geo-fencing is not observable from the
  payloads, so offers are recorded for the source's own country scope, ``GB``.
- **Availability windows come from version payloads only**: single programmes
  expose ``isoStartTime``/``isoEndTime`` on their original version; brand pages
  expose only ``versions[].availability.end``, so episode offers get
  ``available_until`` when present and never a start, and series/season offers
  carry no window at all. Dates are never inferred from publish or crawl times.
- **Single programmes are typed ``movie``** — the canonical shape for one
  self-contained watchable title. iPlayer's own ``programmeType``,
  ``labels.category`` and ``tleoType`` are deliberately not persisted: they
  have no consumer (identity, matching and ranking only see the canonical
  content type) and ``DiscoveredContent`` carries no free-form metadata field.
- **One content row per title, offers underneath**: a brand PID becomes one
  series entry whose season/episode offers hang off that same id, so a mapping
  resolved for the title resolves every offer below it (the CBC Gem shape).
  Episodes are never separate catalogue rows — an episode title matched against
  the local movie catalogue would invent mappings. Numbering that cannot be
  parsed (a soap opera dated ``30/09/2026``, a slice called ``Specials``) is
  skipped, never invented, and the episodes stay reachable through the
  series-level offer.
- **Durations are per version, not per catalogue entry** (ISO-8601 on single
  pages, ``"11 mins"`` text on brand pages), so no ``duration`` is recorded.
"""
from __future__ import annotations

import json
import logging
import re
import string
import time
from datetime import date, datetime, timezone
from typing import Optional

import requests

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import normalize_country, normalize_offer_type
from .base import (
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bbc.co.uk"
ATOZ_URL = BASE_URL + "/iplayer/a-z/{letter}"
EPISODES_URL = BASE_URL + "/iplayer/episodes/{pid}"
EPISODE_URL = BASE_URL + "/iplayer/episode/{pid}"

COUNTRY = "GB"
# The A-Z route: one page per bucket, ``0-9`` first, then a..z.
LETTERS = ("0-9",) + tuple(string.ascii_lowercase)
# The editorial "More Like This" pseudo-slice is not a season.
MORE_LIKE_THIS = "more-like-this"
# Safety net against a runaway slice (the largest observed real slice is 996
# episodes at 36 per page = 28 pages).
MAX_PAGES_PER_SLICE = 60
DEFAULT_TIMEOUT = 30
# Politeness gap between live requests only (tests route through ``requester``
# and never sleep): one catalogue refresh walks thousands of detail pages.
DEFAULT_DELAY = 0.1
# Revision of the embedded-state contract this parser is written against.
SOURCE_VERSION = "iplayer-state-v1"
# iPlayer image URLs carry a ``{recipe}`` placeholder; 16:9 is what the
# catalogue cards render (verified live: /images/ic/640x360/... -> 200).
POSTER_RECIPE = "640x360"
STATE_MARKER = "window.__IPLAYER_REDUX_STATE__"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

FREE = normalize_offer_type("free")

# ``episode.subtitle.default`` on a sliced brand: "Series 1: 1. Acair/Anchor".
_EPISODE_SUBTITLE_RE = re.compile(r"^Series\s+(\d+)\s*:\s*(\d+)\s*[.)]", re.I)
# Slice titles are the provider's own season labels: "Series 2".
_SEASON_SLICE_RE = re.compile(r"^Series\s+(\d+)$", re.I)
# ``episode.subtitle.slice`` may carry only the position: "1. Acair/Anchor".
_SLICE_POSITION_RE = re.compile(r"^(\d+)\s*[.)]")
# A-Z href -> (kind, pid).
_HREF_RE = re.compile(r"^/iplayer/(episodes|episode)/([0-9a-z]+)(?:/|$)")


class BBCIPlayerClient:
    """Thin client for the public BBC iPlayer pages.

    Every page embeds its state as JSON inside a ``tvip-script-app-store``
    script tag; ``requester`` is an ``url -> html`` callable so tests inject a
    fixture router and never touch the network. Retrieval failures (HTTP error,
    transport error, missing or malformed state) always surface as
    :class:`SourceFetchError` — never as an empty-but-valid page.
    """

    def __init__(self, requester=None, timeout=DEFAULT_TIMEOUT, delay=DEFAULT_DELAY):
        self.requester = requester
        self.timeout = timeout
        self.delay = delay

    def atoz(self, letter: str) -> dict:
        """One A-Z letter index (page 1 — see the pagination limitation above)."""
        return self._get(ATOZ_URL.format(letter=letter))

    def brand_page(self, pid: str, *, series_id: Optional[str] = None,
                   page: Optional[int] = None) -> dict:
        """A brand page, optionally scoped to one slice and one page of it."""
        url = EPISODES_URL.format(pid=pid)
        query = []
        if series_id:
            query.append(f"seriesId={series_id}")
        if page:
            query.append(f"page={page}")
        if query:
            url = f"{url}?{'&'.join(query)}"
        return self._get(url)

    def episode_page(self, pid: str) -> dict:
        """One single-programme page (redirects to the canonical slug URL)."""
        return self._get(EPISODE_URL.format(pid=pid))

    def _get(self, url: str) -> dict:
        try:
            if self.requester is not None:
                html = self.requester(url)
            else:
                html = self._http(url)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"BBC iPlayer request failed: {exc}") from exc
        return _state_from_html(html)

    def _http(self, url: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise SourceFetchError(
                f"BBC iPlayer returned HTTP {response.status_code} for {url}"
            )
        return response.text


class BBCIPlayerSource:
    """Catalogue-driven BBC iPlayer source (``content=None`` fetches only)."""

    slug = "bbc_iplayer"
    name = "BBC iPlayer"
    source_type = SourceType.WEB
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"bbc_iplayer"}),
        supported_countries=frozenset({COUNTRY}),
        supported_content_types=frozenset(
            {ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # The A-Z index is capped at 200 entries per letter and iPlayer exposes
        # no per-item removal signal: absence must never deactivate offers.
        supports_authoritative_snapshots=False,
        # iPlayer exposes no external ids; mapping happens in the refresh service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        # No catalogue payload states an audio or subtitle language.
        supports_languages=False,
    )

    def __init__(self, client=None):
        self.client = client or BBCIPlayerClient()

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "bbc_iplayer is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints (iPlayer only ever
        # reports bbc_iplayer/GB); cursor is accepted but unused — the pages
        # expose letter and slice coordinates, not a resumable cursor token.
        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        seen_pids: set[str] = set()

        for letter in LETTERS:
            store = self.client.atoz(letter)
            bucket = (store.get("programmes") or {}).get(letter)
            if not isinstance(bucket, dict):
                raise SourceFetchError(
                    f"Unexpected BBC iPlayer A-Z payload for letter {letter!r}"
                )
            pagination = store.get("pagination") if isinstance(store.get("pagination"), dict) else {}
            total_pages = _as_int(pagination.get("totalPages"), default=1)
            if total_pages > 1:
                # Page 2 of the A-Z route is not reachable (see module docs):
                # everything past the first 200 entries of this letter is
                # missing from this run, which is why the result is never
                # authoritative.
                logger.warning(
                    "bbc_iplayer_letter_truncated letter=%s page=1 of %s per_page=%s",
                    letter, total_pages, _as_int(pagination.get("perPage"), default=None),
                )
            for entity in bucket.get("entities") or []:
                props = entity.get("props") if isinstance(entity, dict) else None
                props = props if isinstance(props, dict) else {}
                href = str(props.get("href") or "").strip()
                match = _HREF_RE.match(href)
                if match is None:
                    logger.info(
                        "bbc_iplayer_entry_skipped href=%s reason=unsupported_href", href,
                    )
                    continue
                kind, pid = match.group(1), match.group(2)
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                if kind == "episodes":
                    parsed_content, parsed_offers = self._parse_brand(pid, href, props)
                else:
                    parsed_content, parsed_offers = self._parse_single(pid, href, props)
                if parsed_content is not None:
                    contents.append(parsed_content)
                offers.extend(parsed_offers)

        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=None,
        )

    # ------------------------------------------------------------------ parse

    def _parse_single(self, pid, href, props):
        """One ``/iplayer/episode/`` entry -> (movie content, offers)."""
        store = self.client.episode_page(pid)
        episode = store.get("episode")
        if not isinstance(episode, dict):
            raise SourceFetchError(f"Unexpected BBC iPlayer programme payload for {pid}")
        title = _text(episode.get("title")) or _text(props.get("title"))
        if not title:
            logger.warning(
                "bbc_iplayer_entry_skipped pid=%s reason=missing_title", pid
            )
            return None, []
        url = BASE_URL + href
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=pid,
            # One self-contained watchable title: the only modelled shape.
            content_type=ContentType.MOVIE,
            title=title,
            release_date=_release_date(episode),
            url=url,
            poster=_poster(episode.get("images"), props.get("imageTemplate")),
            description=_description(episode.get("synopses"))
            or _description(props.get("synopsis")),
            external_ids={},
            source_id=self.slug,
        )
        # Listed on the index but with no playable version (a title that is
        # announced but not yet watchable) is a catalogue entry, not an offer.
        versions = store.get("versions")
        if not isinstance(versions, list):
            versions = episode.get("versions")
        version = _original_version(versions if isinstance(versions, list) else None)
        if version is None:
            logger.info(
                "bbc_iplayer_offer_skipped pid=%s reason=no_versions", pid
            )
            return content, []
        offer = self._offer(
            provider_content_id=pid,
            content_type=ContentType.MOVIE,
            provider_url=url,
            available_from=_iso_datetime(version.get("isoStartTime")),
            available_until=_iso_datetime(version.get("isoEndTime")),
            source_record_id=pid,
        )
        return content, [offer]

    def _parse_brand(self, pid, href, props):
        """One ``/iplayer/episodes/`` entry -> (series content, offers)."""
        store = self.client.brand_page(pid)
        header = store.get("header")
        if not isinstance(header, dict):
            raise SourceFetchError(f"Unexpected BBC iPlayer brand payload for {pid}")
        title = _text(header.get("title")) or _text(props.get("title"))
        if not title:
            logger.warning(
                "bbc_iplayer_entry_skipped pid=%s reason=missing_title", pid
            )
            return None, []
        url = BASE_URL + href
        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=pid,
            content_type=ContentType.SERIES,
            title=title,
            # Brand pages publish no first-air year (the JSON-LD carries name,
            # description and episode count only), so series stay unmatched by
            # year and resolve title-only, like CBC's series.
            release_date=None,
            url=url,
            poster=_poster(header.get("imageTemplate"), props.get("imageTemplate")),
            description=_description(header.get("subtitle"))
            or _description(props.get("synopsis")),
            external_ids={},
            source_id=self.slug,
        )

        default_slice = _slice_id(store)
        slices = _slices(header)
        collected = []  # (slice_id, season_number, episodes[])
        seen_episodes: set[str] = set()
        for position, slice_ in enumerate(slices):
            slice_id = str(slice_.get("id") or "").strip()
            if not slice_id:
                continue
            payload = store if (position == 0 and slice_id == default_slice) else None
            season, episodes = self._collect_slice(pid, slice_id, payload, seen_episodes)
            if episodes:
                collected.append((slice_id, season, episodes))

        offers = []
        if collected:
            # The brand itself is watchable: one series-level offer, then one
            # offer per slice and per numbered episode underneath it.
            offers.append(
                self._offer(
                    provider_content_id=pid,
                    content_type=ContentType.SERIES,
                    provider_url=url,
                    source_record_id=pid,
                )
            )
            for slice_id, season, episodes in collected:
                if season is not None:
                    offers.append(
                        self._offer(
                            provider_content_id=pid,
                            content_type=ContentType.SEASON,
                            provider_url=url if slice_id == "unsliced"
                            else f"{url}?seriesId={slice_id}",
                            season_number=season,
                            source_record_id=f"{pid}/{slice_id}",
                        )
                    )
                for episode in episodes:
                    episode_season, episode_number = _episode_numbers(episode, season)
                    if episode_season is None or episode_number is None:
                        logger.info(
                            "bbc_iplayer_offer_skipped pid=%s episode=%s "
                            "reason=unparseable_numbering",
                            pid, episode.get("id"),
                        )
                        continue
                    episode_pid = str(episode.get("id") or "").strip()
                    offers.append(
                        self._offer(
                            provider_content_id=pid,
                            content_type=ContentType.EPISODE,
                            provider_url=EPISODE_URL.format(pid=episode_pid),
                            season_number=episode_season,
                            episode_number=episode_number,
                            # Brand pages publish only the end of the window.
                            available_until=_episode_end(episode),
                            source_record_id=episode_pid,
                        )
                    )
        return content, offers

    def _collect_slice(self, pid, slice_id, payload, seen_episodes):
        """Walk one slice's pages -> (season_number, episodes).

        Termination comes from each page's own ``pagination`` plus a stall
        guard: if the origin stops advancing ``currentPage`` the walk stops
        instead of looping (the same defence CBC Gem applies to repeated
        pages).
        """
        episodes = []
        page_number = 1
        while True:
            if payload is None:
                payload = self.client.brand_page(pid, series_id=slice_id, page=page_number)
            results = _results(payload)
            for item in results:
                episode = item.get("episode") if isinstance(item, dict) else None
                if not isinstance(episode, dict):
                    continue
                episode_pid = str(episode.get("id") or "").strip()
                if not episode_pid or episode_pid in seen_episodes:
                    continue
                seen_episodes.add(episode_pid)
                episodes.append(episode)

            pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
            total_pages = _as_int(pagination.get("totalPages"), default=1)
            current = _as_int(pagination.get("currentPage"), default=page_number)
            if current != page_number:
                logger.warning(
                    "bbc_iplayer_slice_stalled pid=%s slice=%s requested_page=%s "
                    "upstream_page=%s",
                    pid, slice_id, page_number, current,
                )
                return _season_from_slice(_slice_title(payload, slice_id)), episodes
            if page_number >= total_pages or page_number >= MAX_PAGES_PER_SLICE:
                return _season_from_slice(_slice_title(payload, slice_id)), episodes
            page_number += 1
            payload = None

    def _offer(self, *, provider_content_id, content_type, provider_url,
               season_number=None, episode_number=None, available_from=None,
               available_until=None, source_record_id):
        return ProviderOffer(
            provider_id=self.slug,
            provider_content_id=provider_content_id,
            country=normalize_country(COUNTRY),
            content_type=content_type,
            tmdb_id=None,
            season_number=season_number,
            episode_number=episode_number,
            offer_type=FREE,
            provider_url=provider_url,
            quality="",
            # iPlayer publishes no audio/subtitle language metadata at all.
            audio_languages=None,
            subtitle_languages=None,
            available_from=available_from,
            available_until=available_until,
            source_record_id=source_record_id,
            source_version=SOURCE_VERSION,
        )


# ---------------------------------------------------------------------- html


def _state_from_html(html) -> dict:
    """Extract the embedded ``__IPLAYER_REDUX_STATE__`` object from a page."""
    if not isinstance(html, str):
        raise SourceFetchError(
            f"Unexpected BBC iPlayer response type: {type(html).__name__}"
        )
    marker = html.find(STATE_MARKER)
    if marker != -1:
        assignment = html[marker + len(STATE_MARKER):].lstrip()
        if assignment.startswith("="):
            # The assignment is terminated by ``;`` and then the rest of the
            # document (``</script>...``), so decode the first JSON value
            # rather than expecting the remainder of the string to be JSON.
            try:
                state, _end = json.JSONDecoder().raw_decode(assignment[1:].lstrip())
            except ValueError as exc:
                raise SourceFetchError(
                    "BBC iPlayer page embedded a malformed "
                    "__IPLAYER_REDUX_STATE__ payload"
                ) from exc
            if isinstance(state, dict):
                return state
    raise SourceFetchError(
        "BBC iPlayer page did not embed a usable __IPLAYER_REDUX_STATE__ payload"
    )


# -------------------------------------------------------------------- helpers


def _text(value) -> str:
    """A page field that is either a string or a ``{default: ...}`` object."""
    if isinstance(value, dict):
        return str(value.get("default") or "").strip()
    return str(value or "").strip()


def _description(value) -> str:
    """Synopsis-shaped field: a string, ``{default: ...}`` or ``{small: ...}``."""
    if isinstance(value, dict):
        for key in ("small", "medium", "editorial", "default"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _poster(*candidates) -> str:
    """First usable image URL, with iPlayer's ``{recipe}`` slot resolved."""
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("standard") or candidate.get("default")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().replace("{recipe}", POSTER_RECIPE)
    return ""


def _slices(header) -> list[dict]:
    raw = header.get("availableSlices") if isinstance(header, dict) else None
    return [
        slice_
        for slice_ in (raw or [])
        if isinstance(slice_, dict)
        and str(slice_.get("id") or "").strip() not in ("", MORE_LIKE_THIS)
    ]


def _slice_id(store) -> str:
    page = store.get("page") if isinstance(store.get("page"), dict) else {}
    series_pagination = page.get("seriesPagination")
    if not isinstance(series_pagination, dict):
        return ""
    return str(series_pagination.get("sliceId") or "").strip()


def _slice_title(store, fallback: str) -> str:
    """The title of the slice this payload rendered (``header`` is per page)."""
    header = store.get("header") if isinstance(store.get("header"), dict) else {}
    for slice_ in _slices(header):
        if str(slice_.get("id") or "").strip() == fallback:
            return _text(slice_.get("title"))
    return ""


def _season_from_slice(title: str) -> Optional[int]:
    match = _SEASON_SLICE_RE.match(title or "")
    return int(match.group(1)) if match else None


def _episode_numbers(episode, slice_season: Optional[int]):
    """(season, episode) as the provider states them, or (None, None).

    Never invented: an episode that cannot be read as "Series N: M." (a dated
    soap-opera instalment, a "Christmas Special") yields nothing and is
    skipped by the caller.
    """
    subtitle = episode.get("subtitle") if isinstance(episode.get("subtitle"), dict) else {}
    match = _EPISODE_SUBTITLE_RE.match(_text(subtitle.get("default")))
    if match:
        return int(match.group(1)), int(match.group(2))
    if slice_season is not None:
        match = _SLICE_POSITION_RE.match(_text(subtitle.get("slice")))
        if match:
            return slice_season, int(match.group(1))
    return None, None


def _results(store) -> list[dict]:
    entities = store.get("entities") if isinstance(store.get("entities"), dict) else {}
    results = entities.get("results")
    return [item for item in (results or []) if isinstance(item, dict)]


def _original_version(versions):
    """The version whose window describes the programme itself."""
    if not versions:
        return None
    for version in versions:
        if isinstance(version, dict) and str(version.get("kind") or "") == "original":
            return version
    return next((v for v in versions if isinstance(v, dict)), None)


def _episode_end(episode) -> Optional[datetime]:
    versions = episode.get("versions") if isinstance(episode.get("versions"), list) else None
    version = _original_version(versions)
    if not isinstance(version, dict):
        return None
    availability = version.get("availability")
    availability = availability if isinstance(availability, dict) else {}
    return _iso_datetime(availability.get("end"))


def _iso_datetime(value) -> Optional[datetime]:
    """An ISO-8601 timestamp (``...Z``) -> aware datetime, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _release_date(episode) -> Optional[date]:
    """``releaseDateTime`` (precise) or ``releaseDate`` (year or "14 Sep 2025").

    This is the programme's own release/first-broadcast statement, never the
    availability window (iPlayer shows both, e.g. release 2024 vs window
    starting 2026-09-29), so it is what title/year matching may use.
    """
    parsed = _iso_datetime(episode.get("releaseDateTime"))
    if parsed is not None:
        return parsed.date()
    value = _text(episode.get("releaseDate"))
    if not value:
        return None
    year = None
    if re.fullmatch(r"\d{4}", value):
        year = int(value)
    else:
        for pattern in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
            try:
                year = datetime.strptime(value, pattern).year
                break
            except ValueError:
                continue
    if year is None or not 1800 <= year <= 2200:
        return None
    return date(year, 1, 1)


def _as_int(value, *, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
