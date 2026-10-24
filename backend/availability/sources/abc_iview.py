"""ABC iview dedicated collector — Australian public broadcaster (Phase E).

Second N+1 catalogue walker after CBC Gem: one request enumerates the whole
series index, then one detail request per series yields its episodes, and
each watchable episode becomes a series/season/episode offer. Keyless, no
login, no API key:

    GET /api/series                 -> the whole series index (1613 rows,
                                        verified live; no pagination params)
    GET /api/series/{slug}          -> seriesTitle, seriesDescription,
                                        seriesThumbnail, episodes[]
        per episode: "Series N Ep M ..." title -> season/episode numbers
        -> free_with_ads offers (iview is single-tier AVOD)

API contract (verified live against iview.abc.net.au, 2026-10, see
docs/PLAN.md Phase E):

- The index is a bare JSON **array** of ``{seriesTitle, latestEpisode,
  episodeCount?}``; ``latestEpisode`` is a ``programs/<slug>/<house id>``
  path — the only slug source (``episodeCount`` is absent on 729 of 1613
  rows, so it is never required).
- The detail endpoint answers per slug; an unknown slug is a real 404 (the
  catalogue churns), observed as *missing*, while any other HTTP status,
  a non-JSON body, a non-dict detail, or an ``episodes`` value that is
  neither null nor a list is a retrieval failure
  (:class:`SourceFetchError`) — never an empty-but-valid catalogue.
- ``pubDate`` (``"YYYY-MM-DD HH:MM:SS"``) is *local Sydney wall time* —
  parsed through ``Australia/Sydney`` (DST verified against live rows: Sep
  -> UTC-10, Jan and Oct -> UTC-11) and stored as aware UTC. If the runtime
  has no tz database the value falls back to UTC, never naive.
- Watch URLs: ``iview.abc.net.au/video/<house id>`` and
  ``iview.abc.net.au/shows/<slug>`` (both verified 200).

Product semantics:

- **Country is AU, fixed.** iview is the Australian catalogue, geo-fenced to
  Australia; the offer country is the market the catalogue belongs to, not
  the observer's IP — same decision as CBC (CA), Mediaset (IT), PBS (US).
- **Series, seasons, episodes — no movies.** One ``DiscoveredContent`` per
  series (the show row, exactly like CBC/PBS); all of its offers share the
  slug as ``provider_content_id``, seasons derive from the distinct
  ``Series N`` numbers, episodes from titles matching
  ``Series N Ep M``.
- **Episode numbering comes from the title alone** — the payload carries no
  structured season/episode fields (verified). Episodes whose title does not
  match (news segments, previews, trailers, ``Series 1 <topic>`` talk shows)
  are normal catalogue shape: skipped as episode offers, logged at info, but
  they still make the series itself watchable (the series-level offer is
  emitted when *any* non-live episode is playable). Live streams
  (``livestream == "1"``), and episodes with no house id anywhere, are not
  watchable at all and never make a series exist.
- **Single rights tier**: everything on iview is free with ads — no
  membership/paid tier exists in the payload, so every offer is
  ``free_with_ads``.
- **No expiry is exposed** by this endpoint: ``available_until`` stays None
  (iview does take episodes down, but the public catalogue says nothing
  about when — documented rather than guessed).
- **Languages**: the payload has no language field; iview streams are
  English, so offers carry ``["EN"]``.

Deliberate limitations, documented rather than papered over:

- **Politeness delay + batched walk**: one run walks the 1-request index plus
  at most ``DEFAULT_BATCH_SERIES`` detail requests, sleeping
  ``DEFAULT_DELAY`` (0.18s) between them, and stops at ``MAX_SERIES`` with a
  warning so a runaway index can never burn an unbounded number of requests.
- **Resumable checkpoint**: the walk returns the last successfully inspected
  slug in ``cursor``; the refresh service persists it after the batch's rows
  are committed, and the next run resumes after it. A late detail failure
  fails only that bounded batch — previous batches are already persisted and
  the checkpoint is not advanced, so the retry re-reads at most one batch
  instead of the whole catalogue. A complete walk returns no cursor
  (``supports_incremental_sync=False``: this is a bounded traversal, not an
  incremental offer sync).
- **Never authoritative**: absence in one run never deactivates offers
  (``supports_authoritative_snapshots=False``) — a 404'd series or a
  half-fetched run must not read as removals.
- **Budget/rate limiting stay with the sweep**: one refresh = one shared
  rate-limiter token and one budget unit; the collector implements no quota
  accounting of its own.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from ..domain.enums import ContentType, SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer
from ..services.normalize import (
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

BASE_URL = "https://iview.abc.net.au"
SERIES_LIST_URL = f"{BASE_URL}/api/series"
SERIES_DETAIL_URL = f"{BASE_URL}/api/series/{{slug}}"
VIDEO_URL = f"{BASE_URL}/video/{{house_id}}"
SHOW_URL = f"{BASE_URL}/shows/{{slug}}"
SOURCE_VERSION = "abc-iview-series-v1"
DEFAULT_TIMEOUT = 30
#: The index holds ~1613 series; the walk stops well above that so a
#: runaway index cannot turn one refresh into an unbounded request storm.
MAX_SERIES = 3000
#: Politeness delay between detail requests: ~1613 requests x 0.18s ~= 5
#: minutes per catalogue refresh (live endpoints tolerate this rate).
DEFAULT_DELAY = 0.18
#: Series detail pages one fetch walks before returning a resumable
#: checkpoint. 1613 pages in one request chain means a failure at page 1600
#: threw away the 1599 pages before it; a bounded batch persists what it
#: observed, and the next run continues from the checkpoint.
DEFAULT_BATCH_SERIES = 250
#: Bump when the checkpoint shape changes; an unknown version is ignored and
#: the walk restarts from the top instead of resuming from garbage.
CURSOR_VERSION = 1
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: The one country iview's catalogue belongs to (see module docstring).
COUNTRY = "AU"

#: ``pubDate`` is Sydney wall time; DST-verified live (see docstring).
try:
    from zoneinfo import ZoneInfo

    _PUB_DATE_TZ = ZoneInfo("Australia/Sydney")
except Exception:  # pragma: no cover - runtime without a tz database
    logger.warning(
        "abc_iview_no_tzdb pubDate parsed as UTC instead of Australia/Sydney"
    )
    _PUB_DATE_TZ = timezone.utc

#: The only episode numbering signal in the payload (no structured fields).
EPISODE_TITLE_RE = re.compile(r"^Series\s+(\d+)\s+Ep\s+(\d+)\b", re.IGNORECASE)

#: iview streams are English; the payload carries no language field.
_EN = normalize_languages(["en"])


class AbcIviewClient:
    """Thin client for ABC iview's public catalogue endpoints.

    ``requester`` is a ``url -> payload`` callable where ``None`` means
    *HTTP 404 / series gone*; tests inject a fixture router so no test ever
    performs network I/O. Every other failure (HTTP != 200, non-JSON body,
    transport error) surfaces as :class:`SourceFetchError` — never as an
    empty catalogue.
    """

    def __init__(self, *, requester=None, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = DEFAULT_DELAY, sleeper=None):
        self.requester = requester
        self.timeout = timeout
        self.delay = delay
        self._sleep = sleeper if sleeper is not None else time.sleep

    def series_list(self) -> list:
        """The whole series index (a bare JSON array, one request)."""
        payload = self._get(SERIES_LIST_URL)
        if not isinstance(payload, list):
            raise SourceFetchError("Unexpected ABC iview series list payload type")
        return payload

    def series_detail(self, slug: str) -> Optional[dict]:
        """One series' detail payload; ``None`` when the series is gone."""
        payload = self._get(SERIES_DETAIL_URL.format(slug=slug))
        # Politeness between the ~1613 detail requests of one walk, whether
        # or not this series existed (the 404 was still a request).
        if self.delay > 0:
            self._sleep(self.delay)
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise SourceFetchError(
                f"Unexpected ABC iview detail payload type for {slug!r}"
            )
        return payload

    # ------------------------------------------------------------- transport

    def _get(self, url: str) -> Optional[dict]:
        try:
            if self.requester is not None:
                payload = self.requester(url)
            else:
                payload = self._http(url)
        except SourceFetchError:
            raise
        except requests.RequestException as exc:
            raise SourceFetchError(f"ABC iview request failed: {exc}") from exc
        if payload is None:
            # Only the live transport (and fixture routers standing in for
            # it) produce None: the series is gone, an observed absence.
            return None
        if not isinstance(payload, (dict, list)):
            raise SourceFetchError(f"Unexpected ABC iview response type for {url}")
        return payload

    def _http(self, url: str) -> Optional[dict]:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise SourceFetchError(
                f"ABC iview returned HTTP {response.status_code} for {url}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError("ABC iview returned a non-JSON body") from exc


class AbcIviewSource:
    """Catalogue-driven ABC iview source (``content=None`` fetches only)."""

    slug = "abc_iview"
    name = "ABC iview"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"abc_iview"}),
        supported_countries=frozenset({COUNTRY}),
        # The index carries series only; movies live nowhere on iview.
        supported_content_types=frozenset(
            {ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        # The walk resumes from its own checkpoint; that is a traversal
        # position, not an incremental offer sync (see module docstring).
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Bounded, checkpointed traversal: this walk is resumable, which is a
        # *different* property from supports_incremental_sync (see base.py).
        # ABC iview resumes from its last-walked series slug (see module docstring).
        supports_resumable_catalogue_sync=True,
        # A series that 404s mid-run must not read as a removal: absence
        # never deactivates offers.
        supports_authoritative_snapshots=False,
        # iview exposes no external ids; mapping happens in the refresh
        # service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        # Offers carry the fixed English language (see module docstring).
        supports_languages=True,
    )

    def __init__(self, client=None, batch_size=DEFAULT_BATCH_SERIES):
        self.client = client or AbcIviewClient()
        self.batch_size = max(1, int(batch_size))

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "abc_iview is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints: this source has one
        # provider and one fixed market (see module docstring), so both are
        # accepted and not acted on.
        #
        # ``cursor`` is the durable walk checkpoint: the last series slug a
        # previous fetch persisted. One fetch walks at most ``batch_size``
        # detail pages and returns the checkpoint for the next one, so a late
        # request failure cannot discard a whole catalogue's worth of work.

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        seen_slugs: set[str] = set()
        details_fetched = 0
        after_slug = _decode_cursor(cursor)

        entries = self.client.series_list()
        start = 0
        if after_slug:
            for index, entry in enumerate(entries):
                if isinstance(entry, dict) and _entry_slug(entry) == after_slug:
                    start = index + 1
                    break
            else:
                # The checkpointed series left the index. Restarting re-reads
                # a window that was already persisted; it never skips ahead.
                logger.warning(
                    "abc_iview_cursor_checkpoint_missing after=%s reason=restarting",
                    after_slug,
                )

        window = entries[start:]
        last_slug = ""
        truncated = False
        for entry in window:
            if not isinstance(entry, dict):
                logger.info(
                    "abc_iview_entry_skipped reason=non_mapping value=%r", entry
                )
                continue
            slug = _entry_slug(entry)
            title = str(entry.get("seriesTitle") or "").strip()
            if not slug or not title:
                logger.info(
                    "abc_iview_entry_skipped reason=missing_identity title=%r",
                    entry.get("seriesTitle"),
                )
                continue
            if slug in seen_slugs:
                continue
            if details_fetched >= min(self.batch_size, MAX_SERIES):
                truncated = True
                if self.batch_size >= MAX_SERIES:
                    logger.warning(
                        "abc_iview_series_cap cap=%s series=%s",
                        MAX_SERIES, len(seen_slugs),
                    )
                break
            seen_slugs.add(slug)
            details_fetched += 1
            # A transport/parse failure here propagates: a catalogue batch that
            # cannot be completed is a failed fetch, never a smaller successful
            # one. The caller keeps the checkpoint it passed in, so the retry
            # re-reads at most this one bounded batch.
            detail = self.client.series_detail(slug)
            # A 404 is an observed absence, not a failure: it still advances
            # the checkpoint because the series was genuinely inspected.
            last_slug = slug
            if detail is None:
                logger.info("abc_iview_show_gone slug=%s", slug)
                continue
            parsed_content, parsed_offers = self._show_rows(slug, title, detail)
            if parsed_content is None:
                continue
            contents.append(parsed_content)
            offers.extend(parsed_offers)

        # A checkpoint is only emitted while there is more to walk; a complete
        # walk returns None so the refresh service can drop a stale checkpoint
        # rather than resume a finished catalogue.
        next_cursor = _encode_cursor(last_slug) if (truncated and last_slug) else None

        return SourceFetchResult(
            contents=contents,
            offers=offers,
            authoritative=False,
            complete_snapshot=False,
            sync_mode=SyncMode.FULL,
            cursor=next_cursor,
        )

    # ------------------------------------------------------------------ rows

    def _show_rows(self, slug: str, index_title: str, detail: dict):
        """One series content row plus its series/season/episode offers.

        Returns ``(None, [])`` when the series has nothing watchable: no
        episodes, only live streams, or only entries without a video id is
        an observation of no offers, and no content row either (the row
        would claim a presence the offers do not back).
        """
        title = str(detail.get("seriesTitle") or index_title or "").strip()
        if not title:
            return None, []

        episodes = detail.get("episodes")
        if episodes is None:
            episodes = []  # legitimately empty detail: series with nothing yet
        elif not isinstance(episodes, list):
            raise SourceFetchError(
                f"Unexpected ABC iview episodes payload for {slug!r}"
            )

        watchable: list[dict] = []
        numbered: list[tuple[dict, int, int]] = []
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            if str(episode.get("livestream") or "") == "1":
                # A 24/7 channel placeholder, not a watchable programme.
                logger.info(
                    "abc_iview_asset_skipped show=%s reason=livestream", slug
                )
                continue
            house_id = str(episode.get("episodeHouseNumber") or "").strip()
            if not house_id:
                href = str(episode.get("href") or "").strip()
                house_id = href.rsplit("/", 1)[-1] if href else ""
            if not house_id:
                # No video id anywhere: nowhere to point a viewer.
                logger.info(
                    "abc_iview_asset_skipped show=%s title=%r "
                    "reason=missing_video_id",
                    slug, episode.get("title"),
                )
                continue
            watchable.append(episode)
            match = EPISODE_TITLE_RE.match(
                str(episode.get("title") or "").strip()
            )
            if match is None:
                # News segments, previews, talk-show topics: real catalogue
                # shape, just not numbered episodes — the series-level offer
                # below still counts them as watchable.
                logger.info(
                    "abc_iview_asset_skipped show=%s title=%r "
                    "reason=unnumbered_title",
                    slug, episode.get("title"),
                )
                continue
            numbered.append((episode, int(match.group(1)), int(match.group(2))))
        if not watchable:
            return None, []

        show_url = SHOW_URL.format(slug=slug)

        offers: list[ProviderOffer] = []

        # Series-level offer: iview is a single free tier, and the series
        # exists as long as any non-live episode is playable (the unnumbered
        # ones included).
        offers.append(
            ProviderOffer(
                provider_id=self.slug,
                provider_content_id=slug,
                country=normalize_country(COUNTRY),
                content_type=ContentType.SERIES,
                tmdb_id=None,
                season_number=None,
                episode_number=None,
                offer_type=normalize_offer_type("free_with_ads"),
                provider_url=show_url,
                quality="",
                audio_languages=_EN,
                subtitle_languages=None,
                available_from=None,
                available_until=None,
                source_record_id=slug,
                source_version=SOURCE_VERSION,
            )
        )

        # Season-level offers, derived from distinct ``Series N`` numbers.
        by_season: dict[int, list[tuple[dict, int, int]]] = {}
        for entry in numbered:
            by_season.setdefault(entry[1], []).append(entry)
        for season_number in sorted(by_season):
            offers.append(
                ProviderOffer(
                    provider_id=self.slug,
                    provider_content_id=slug,
                    country=normalize_country(COUNTRY),
                    content_type=ContentType.SEASON,
                    tmdb_id=None,
                    season_number=season_number,
                    episode_number=None,
                    offer_type=normalize_offer_type("free_with_ads"),
                    provider_url=show_url,
                    quality="",
                    audio_languages=_EN,
                    subtitle_languages=None,
                    available_from=None,
                    available_until=None,
                    source_record_id=f"{slug}:s{season_number}",
                    source_version=SOURCE_VERSION,
                )
            )

        # Episode-level offers, one per numbered episode.
        for episode, season_number, episode_number in numbered:
            house_id = str(episode.get("episodeHouseNumber") or "").strip()
            if not house_id:
                href = str(episode.get("href") or "").strip()
                house_id = href.rsplit("/", 1)[-1] if href else ""
            offers.append(
                ProviderOffer(
                    provider_id=self.slug,
                    provider_content_id=slug,
                    country=normalize_country(COUNTRY),
                    content_type=ContentType.EPISODE,
                    tmdb_id=None,
                    season_number=season_number,
                    episode_number=episode_number,
                    offer_type=normalize_offer_type("free_with_ads"),
                    provider_url=VIDEO_URL.format(house_id=house_id),
                    quality="",
                    audio_languages=_EN,
                    subtitle_languages=None,
                    available_from=_parse_pub_date(episode.get("pubDate")),
                    available_until=None,
                    source_record_id=house_id,
                    source_version=SOURCE_VERSION,
                )
            )

        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=slug,
            content_type=ContentType.SERIES,
            title=title,
            url=show_url,
            poster=str(detail.get("seriesThumbnail") or ""),
            description=str(detail.get("seriesDescription") or "").strip(),
            external_ids={},
            source_id=self.slug,
        )
        return content, offers


# ----------------------------------------------------------------- parsing


def _entry_slug(entry: dict) -> str:
    """Series slug from the index's ``programs/<slug>/<house id>`` path."""
    path = str(entry.get("latestEpisode") or "").strip()
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "programs":
        return parts[1].strip()
    return ""


def _parse_pub_date(value) -> Optional[datetime]:
    """``"YYYY-MM-DD HH:MM:SS"`` Sydney wall time -> aware UTC (see docstring)."""
    if not value:
        return None
    try:
        naive = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=_PUB_DATE_TZ).astimezone(timezone.utc)


def _decode_cursor(cursor) -> str:
    """The last successfully walked slug from a stored checkpoint, else ``""``.

    Any unreadable or foreign checkpoint means "start from the top": a broken
    cursor is never a reason to look like a finished walk.
    """
    if not cursor:
        return ""
    data = cursor
    if not isinstance(cursor, dict):
        try:
            data = json.loads(str(cursor))
        except (TypeError, ValueError):
            logger.warning("abc_iview_cursor_invalid cursor=%r", str(cursor)[:160])
            return ""
    if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
        logger.warning("abc_iview_cursor_reset version=%r", data.get("v") if isinstance(data, dict) else None)
        return ""
    after = data.get("after")
    return str(after) if after else ""


def _encode_cursor(after: str) -> str:
    """Durable checkpoint: the last slug this walk observed successfully."""
    return json.dumps({"v": CURSOR_VERSION, "after": after}, sort_keys=True,
                      separators=(",", ":"))
