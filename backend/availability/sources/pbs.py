"""PBS dedicated collector — US public-media catalogue (Phase E).

First episode-granular catalogue :class:`AvailabilitySource` alongside the
CBC pattern: walk the full show list, batch-fetch each show's full-length
assets, emit series/season/episode offers, no login, no API key:

    allShows walk (``first: 100``, ``after`` cursor)
        -> showsBatch chunks of 50 slugs
        -> per asset: FULL_LENGTH + ``availability == "available"``
           + season/episode ordinals -> episode offer
           (isMvod -> subscription, else free_with_ads)
        -> series/season offers roll their assets' free-ness and expiry up
        -> AvailabilityRefreshService (catalogue path: ProviderContent +
           ContentMapping resolve show slug -> tracker series)

API contract (verified live against the gateway, 2026-10, see docs/PLAN.md
Phase E):

- ``POST https://content.services.pbs.org/graphql/`` with
  ``X-PBS-Platform: web`` and ``X-PBS-Platform-Version: 1.0`` — the exact
  headers pbs.org's own bundle sends (the bare host root returns 401; the
  GraphQL subpath is the public, keyless endpoint).
- ``allShows`` accepts ``after`` as a GraphQL **variable** but rejects
  ``first`` as one (deterministic 400), so ``first: 100`` ships as a literal
  in the document — verified, not assumed.
- ``showsBatch(showSlugs: [...])`` takes a ``[String]!`` variable; responses
  come back in an order unrelated to the request, so assets are mapped by
  ``show.slug`` and a show missing from the response counts as having no
  assets (it dropped out between requests).
- A GraphQL ``errors[]`` entry, a non-dict body, HTTP != 200, or a payload
  without the ``data -> allShows`` / ``data -> showsBatch`` shell are
  retrieval failures (:class:`SourceFetchError`) — never an empty catalogue.

Product semantics:

- **Country is US, fixed.** PBS is the US public-media catalogue; the content
  API serves the same catalogue from any egress (verified), so the honest
  offer country is the market the catalogue belongs to, not the observer's
  IP — same decision as CBC (CA) and Mediaset (IT).
- **Series, seasons, episodes — no movies.** One ``DiscoveredContent`` per
  show (the series row, exactly like CBC) with all of that show's offers
  sharing its slug as ``provider_content_id``; seasons are derived from
  distinct ``seasonOrdinal`` values, episodes from assets that carry both
  ordinals. Assets without ordinals (interviews, music videos, extras) are
  normal catalogue shape and are skipped silently; half-missing ordinals are
  anomalous and logged as warnings.
- **Only ``FULL_LENGTH`` + ``availability == "available"``** assets are
  watchable offers; ``CLIP``/``PREVIEW`` video types and non-available
  windows are observed but never persisted.
- **Rights vocabulary**: ``isMvod`` False -> ``free_with_ads`` (open
  streaming), True -> ``subscription`` (PBS Passport membership) — rolled up
  per group: a series/season is free if *any* of its watchable assets is.
- **Expiry windows are honoured**: an episode's ``expireDate`` becomes
  ``available_until`` and its ``premiereDate`` becomes ``available_from``
  (both ``YYYY-MM-DD`` parsed to aware UTC, mirroring CBC's date handling);
  a series/season offer carries the *latest* expiry among its assets — the
  group stays available while any member does.
- **Languages**: show/asset ``language`` (``en``) normalized through
  ``normalize_languages``.

Deliberate limitations, documented rather than papered over:

- **No playback URLs**: the content API exposes no signed stream; offers
  deep-link to the public watch page (``pbs.org/video/<asset slug>``,
  verified 200) which is what a viewer actually opens.
- **Bounded, checkpointed walk across runs**: the A–Z walk is a ~10-page
  ``allShows`` cursor walk, but the expensive half is ``showsBatch`` (one call
  per ``BATCH_SIZE`` shows, ~20 calls for the full catalogue). A fetch walks at
  most ``DEFAULT_BATCH_SHOWS`` shows and hands back a
  ``{"v": 1, "parts": {"shows": <last-walked-slug>}}`` checkpoint, so a late
  batch failure re-reads one bounded batch instead of the whole catalogue. The
  anchor is the last *show* the batch phase completed — PBS's own slug, never a
  page or an opaque ``endCursor``: the upstream cursor is only valid inside the
  paging session that produced it, so it cannot be persisted and replayed.
  ``supports_resumable_catalogue_sync=True`` describes this traversal progress;
  ``supports_incremental_sync`` stays ``False`` and means something else (PBS
  serves no offer delta).
- **Never authoritative**: absence in one run never deactivates offers
  (``supports_authoritative_snapshots=False``) — a batch that dropped a show
  would otherwise look like a removal.
- **Budget/rate limiting stay with the sweep**: one refresh = one shared
  rate-limiter token and one budget unit; the collector implements no quota
  accounting of its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from ..domain.enums import ContentType, OfferType, SourceType, SyncMode
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
from . import checkpoints

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://content.services.pbs.org/graphql/"
PLATFORM = "web"
PLATFORM_VERSION = "1.0"
SOURCE_VERSION = "pbs-content-graphql-v1"
VIDEO_URL = "https://www.pbs.org/video/{slug}"
SHOW_URL = "https://www.pbs.org/show/{slug}"
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: The one country PBS's catalogue belongs to (see module docstring).
COUNTRY = "US"

#: Pagination / batching guards: 982 shows fit in ~10 pages and ~20 batches;
#: the limit exists so a cursor loop can never spin forever.
SHOWS_PAGE_SIZE = 100
MAX_PAGES = 100
BATCH_SIZE = 50
#: Shows walked per fetch before the checkpoint is handed back. The
#: ``showsBatch`` calls are the expensive half of this walk (one per
#: ``BATCH_SIZE`` shows), so this — not the page cap — is what bounds a run.
DEFAULT_BATCH_SHOWS = 500
#: Bump when the traversal algorithm changes: a stored checkpoint from another
#: version restarts the walk instead of resuming a stale anchor.
CURSOR_VERSION = 1
#: The single part this walk checkpoints (it has one traversal dimension).
PART_SHOWS = "shows"

#: Only these asset states are watchable offers (see module docstring).
WATCHABLE_VIDEO_TYPE = "FULL_LENGTH"
WATCHABLE_AVAILABILITY = "available"

ALL_SHOWS_QUERY = """
query PbsAllShows($after: String) {
  allShows(first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges { node { slug title cid poster2x3ImageUrl descriptionShort descriptionLong language } }
  }
}
""".strip()

SHOWS_BATCH_QUERY = """
query PbsShowsBatch($slugs: [String]!) {
  showsBatch(showSlugs: $slugs) {
    show { slug title }
    fullLengthAssets {
      slug
      title
      isMvod
      availability
      videoType
      seasonOrdinal
      episodeOrdinal
      premiereDate
      expireDate
      language
    }
  }
}
""".strip()


class PbsClient:
    """Thin client for PBS's public content GraphQL API.

    ``http_post`` is an injectable ``(url, payload) -> dict`` callable; tests
    pass a fixture router so no test ever performs network I/O. Retrieval
    failures always surface as :class:`SourceFetchError`.
    """

    def __init__(self, *, http_post=None, timeout: int = DEFAULT_TIMEOUT):
        self.http_post = http_post
        self.timeout = timeout

    def all_shows(self, after: Optional[str]) -> dict:
        """One ``allShows`` page; ``after`` is ``None`` for the first page."""
        return self._graphql(
            "PbsAllShows", ALL_SHOWS_QUERY, {"after": after}
        )

    def shows_batch(self, slugs: list[str]) -> dict:
        """One ``showsBatch`` call for up to :data:`BATCH_SIZE` show slugs."""
        return self._graphql(
            "PbsShowsBatch", SHOWS_BATCH_QUERY, {"slugs": list(slugs)}
        )

    # ------------------------------------------------------------- transport

    def _graphql(self, operation: str, query: str, variables: dict) -> dict:
        body = {
            "operationName": operation,
            "query": query,
            "variables": variables,
        }
        if self.http_post is not None:
            try:
                payload = self.http_post(GRAPHQL_URL, body)
            except SourceFetchError:
                raise
            except Exception as exc:  # fixture routers raise plain exceptions
                raise SourceFetchError(
                    f"PBS {operation} request failed: {exc}"
                ) from exc
        else:
            payload = self._post(body)
        if not isinstance(payload, dict):
            raise SourceFetchError(
                f"Unexpected PBS {operation} response type"
            )
        errors = payload.get("errors")
        if errors:
            message = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("message") or "")
            raise SourceFetchError(
                f"PBS {operation} returned GraphQL errors: {message}"
            )
        return payload

    def _post(self, body: dict) -> dict:
        try:
            response = requests.post(
                GRAPHQL_URL,
                json=body,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/json",
                    "X-PBS-Platform": PLATFORM,
                    "X-PBS-Platform-Version": PLATFORM_VERSION,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SourceFetchError(f"PBS request failed: {exc}") from exc
        if response.status_code != 200:
            raise SourceFetchError(f"PBS returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError("PBS returned a non-JSON body") from exc


class PbsSource:
    """Catalogue-driven PBS source (``content=None`` fetches only)."""

    slug = "pbs"
    name = "PBS"
    source_type = SourceType.API
    capabilities = SourceCapabilities(
        supported_providers=frozenset({"pbs"}),
        supported_countries=frozenset({COUNTRY}),
        # PBS publishes no movies on this API; shows only, at series/season/
        # episode granularity.
        supported_content_types=frozenset(
            {ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE}
        ),
        # The walk starts from the top every run; no cursor persists across
        # runs (see module docstring).
        supports_incremental_sync=False,
        supports_catalogue_sync=True,
        # Bounded, checkpointed traversal (see module docstring).
        supports_resumable_catalogue_sync=True,
        # A batch that dropped a show must not read as a removal: absence
        # never deactivates offers.
        supports_authoritative_snapshots=False,
        # PBS exposes no external ids; mapping happens in the refresh service.
        supports_tmdb_ids=False,
        supports_seasons=True,
        supports_episodes=True,
        # Show/asset language metadata exists.
        supports_languages=True,
    )

    def __init__(self, client=None, batch_size=DEFAULT_BATCH_SHOWS):
        self.client = client or PbsClient()
        self.batch_size = max(1, int(batch_size))

    def fetch(self, content=None, *, provider=None, country=None, cursor=None) -> SourceFetchResult:
        if content is not None:
            raise ValueError(
                "pbs is catalogue-driven: fetch the catalogue with "
                "content=None (no title-targeted lookup exists)"
            )
        # provider/country are the caller's scope hints: this source has one
        # provider and one fixed market (see module docstring), so both are
        # accepted and not acted on.
        #
        # ``cursor`` is the durable walk checkpoint: the last show slug the
        # batch phase completed. One fetch walks at most ``batch_size`` shows
        # and hands the next checkpoint back, so a late ``showsBatch`` failure
        # cannot discard the whole catalogue's work.
        resume = checkpoints.decode(
            cursor, version=CURSOR_VERSION, parts=(PART_SHOWS,),
            log_prefix="pbs",
        )

        shows = self._walk_shows()
        after = resume.anchor(PART_SHOWS)
        if after:
            # Skip everything up to and including the checkpointed show. If it
            # left the catalogue the walk restarts from the top, which only
            # repeats already-persisted work — it never skips ahead.
            for position, show in enumerate(shows):
                if str(show.get("slug") or "") == after:
                    shows = shows[position + 1:]
                    logger.info("pbs_walk_resume after=%s remaining=%d",
                                after, len(shows))
                    break
            else:
                logger.warning(
                    "pbs_cursor_checkpoint_missing after=%s reason=restarting",
                    after,
                )

        contents: list[DiscoveredContent] = []
        offers: list[ProviderOffer] = []
        walked = 0
        position = 0
        last_slug = after
        # The chunk is capped by the remaining budget, not just by BATCH_SIZE:
        # asking for shows this fetch will not walk wastes a batch request's
        # worth of payload and makes the request's contents disagree with what
        # the checkpoint claims was processed.
        while position < len(shows) and walked < self.batch_size:
            limit = min(BATCH_SIZE, self.batch_size - walked)
            chunk = shows[position:position + limit]
            payload = self.client.shows_batch(
                [str(s.get("slug") or "") for s in chunk]
            )
            assets_by_show = _assets_by_show(payload, expected=len(chunk))
            for show in chunk:
                slug = str(show.get("slug") or "")
                walked += 1
                # The anchor moves as soon as the show is walked, so a show with
                # no watchable assets is still progress rather than an infinite
                # re-read.
                last_slug = slug or last_slug
                assets = assets_by_show.get(slug)
                if assets is None:
                    # The show dropped out of the catalogue between the walk
                    # and this batch: an observation of nothing, not an error.
                    logger.info(
                        "pbs_show_missing_from_batch show=%s", slug
                    )
                    continue
                content, show_offers = self._show_rows(show, assets)
                if content is None:
                    continue
                contents.append(content)
                offers.extend(show_offers)
            position += len(chunk)

        truncated = position < len(shows)

        if truncated:
            logger.warning(
                "pbs_catalogue_truncated shows_walked=%d batch_size=%d "
                "remaining=%d",
                walked, self.batch_size, max(0, len(shows) - walked),
            )
        # A checkpoint is only emitted while shows remain unwalked; an exhausted
        # walk returns None so the refresh service drops the row rather than
        # resuming a finished catalogue.
        next_cursor = (
            checkpoints.encode(CURSOR_VERSION, {PART_SHOWS: last_slug})
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

    def _walk_shows(self) -> list[dict]:
        """Every show on ``allShows``, first page to last, deduped by slug."""
        shows: list[dict] = []
        seen_slugs: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: Optional[str] = None
        pages = 0
        while True:
            payload = self.client.all_shows(cursor)
            nodes, has_next, end_cursor = _shows_page(payload)
            for node in nodes:
                slug = str(node.get("slug") or "").strip()
                if not slug:
                    continue
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                shows.append(node)
            pages += 1
            if not has_next:
                break
            if not end_cursor:
                raise SourceFetchError(
                    "PBS allShows reported hasNextPage without an endCursor"
                )
            if end_cursor in seen_cursors:
                # The paging window replayed a cursor; stop instead of
                # looping forever (same guard as the Pluto walk).
                logger.warning(
                    "pbs_repeated_cursor pages=%s cursor=%s", pages, end_cursor
                )
                break
            seen_cursors.add(end_cursor)
            if pages >= MAX_PAGES:
                logger.warning(
                    "pbs_page_limit pages=%s shows=%s", pages, len(shows)
                )
                break
            cursor = end_cursor
        return shows

    # ------------------------------------------------------------------ rows

    def _show_rows(self, show: dict, assets: list[dict]):
        """One series content row plus its series/season/episode offers.

        Returns ``(None, [])`` when the show has nothing watchable: a show
        whose assets are all clips, extras or outside their availability
        window is an observation of no offers, and no content row either (the
        row would claim a presence the offers do not back).
        """
        slug = str(show.get("slug") or "").strip()
        title = str(show.get("title") or "").strip()
        if not slug or not title:
            return None, []

        watchable: list[tuple[dict, int, int]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("videoType") or "") != WATCHABLE_VIDEO_TYPE:
                # CLIP/PREVIEW by design: never a full episode offer.
                continue
            if str(asset.get("availability") or "") != WATCHABLE_AVAILABILITY:
                logger.info(
                    "pbs_asset_skipped show=%s asset=%s reason=availability value=%r",
                    slug, asset.get("slug"), asset.get("availability"),
                )
                continue
            season = _as_int(asset.get("seasonOrdinal"))
            episode = _as_int(asset.get("episodeOrdinal"))
            if season is None and episode is None:
                # Interviews, music videos, extras: normal catalogue shape,
                # not episodes — skipped silently.
                continue
            if season is None or episode is None:
                logger.warning(
                    "pbs_asset_skipped show=%s asset=%s reason=half_missing_ordinal "
                    "season=%r episode=%r",
                    slug, asset.get("slug"), season, episode,
                )
                continue
            watchable.append((asset, season, episode))
        if not watchable:
            return None, []

        show_url = SHOW_URL.format(slug=slug)
        show_language = normalize_languages(
            [show.get("language")] if show.get("language") else None
        )

        offers: list[ProviderOffer] = []

        # Series-level offer: free if any watchable asset is, expiring with
        # the last one to expire.
        offers.append(
            ProviderOffer(
                provider_id=self.slug,
                provider_content_id=slug,
                country=normalize_country(COUNTRY),
                content_type=ContentType.SERIES,
                tmdb_id=None,
                season_number=None,
                episode_number=None,
                offer_type=_group_offer_type(
                    asset for asset, _, _ in watchable
                ),
                provider_url=show_url,
                quality="",
                audio_languages=show_language,
                subtitle_languages=None,
                available_from=None,
                available_until=_group_expiry(
                    asset for asset, _, _ in watchable
                ),
                source_record_id=slug,
                source_version=SOURCE_VERSION,
            )
        )

        # Season-level offers, derived from distinct season ordinals.
        by_season: dict[int, list[tuple[dict, int, int]]] = {}
        for entry in watchable:
            by_season.setdefault(entry[1], []).append(entry)
        for season_number in sorted(by_season):
            season_entries = by_season[season_number]
            offers.append(
                ProviderOffer(
                    provider_id=self.slug,
                    provider_content_id=slug,
                    country=normalize_country(COUNTRY),
                    content_type=ContentType.SEASON,
                    tmdb_id=None,
                    season_number=season_number,
                    episode_number=None,
                    offer_type=_group_offer_type(
                        asset for asset, _, _ in season_entries
                    ),
                    provider_url=show_url,
                    quality="",
                    audio_languages=show_language,
                    subtitle_languages=None,
                    available_from=None,
                    available_until=_group_expiry(
                        asset for asset, _, _ in season_entries
                    ),
                    source_record_id=f"{slug}:s{season_number}",
                    source_version=SOURCE_VERSION,
                )
            )

        # Episode-level offers, one per watchable asset.
        for asset, season_number, episode_number in watchable:
            asset_slug = str(asset.get("slug") or "")
            offers.append(
                ProviderOffer(
                    provider_id=self.slug,
                    provider_content_id=slug,
                    country=normalize_country(COUNTRY),
                    content_type=ContentType.EPISODE,
                    tmdb_id=None,
                    season_number=season_number,
                    episode_number=episode_number,
                    offer_type=(
                        OfferType.SUBSCRIPTION
                        if asset.get("isMvod")
                        else OfferType.FREE_WITH_ADS
                    ),
                    provider_url=VIDEO_URL.format(slug=asset_slug),
                    quality="",
                    audio_languages=normalize_languages(
                        [asset.get("language")] if asset.get("language") else None
                    ),
                    subtitle_languages=None,
                    available_from=_parse_date(asset.get("premiereDate")),
                    available_until=_parse_date(asset.get("expireDate")),
                    source_record_id=asset_slug or slug,
                    source_version=SOURCE_VERSION,
                )
            )

        content = DiscoveredContent(
            provider_id=self.slug,
            provider_content_id=slug,
            content_type=ContentType.SERIES,
            title=title,
            url=show_url,
            poster=str(show.get("poster2x3ImageUrl") or ""),
            description=str(
                show.get("descriptionLong") or show.get("descriptionShort") or ""
            ).strip(),
            external_ids={},
            source_id=self.slug,
        )
        return content, offers


# ----------------------------------------------------------------- parsing


def _shows_page(payload: dict) -> tuple[list[dict], bool, Optional[str]]:
    """``(nodes, hasNextPage, endCursor)`` of a well-formed ``allShows``.

    The shell exists even for the last page (``hasNextPage: false``), so a
    payload *without* it is an upstream shape change — loud failure, never a
    silently truncated catalogue.
    """
    try:
        connection = payload["data"]["allShows"]
        page_info = connection["pageInfo"]
        edges = connection["edges"]
    except (KeyError, TypeError) as exc:
        raise SourceFetchError("Unexpected PBS allShows payload shape") from exc
    if not isinstance(page_info, dict) or not isinstance(edges, list):
        raise SourceFetchError("Unexpected PBS allShows payload shape")
    nodes = [
        edge["node"]
        for edge in edges
        if isinstance(edge, dict)
        and isinstance(edge.get("node"), dict)
    ]
    has_next = bool(page_info.get("hasNextPage"))
    end_cursor = page_info.get("endCursor")
    return nodes, has_next, str(end_cursor) if end_cursor else None


def _assets_by_show(payload: dict, *, expected: int) -> dict[str, list[dict]]:
    """``show.slug -> fullLengthAssets`` of a well-formed ``showsBatch``.

    The shell exists even when every requested show is gone (the list is then
    simply shorter), so a payload *without* it is an upstream shape change.
    A response that is not a list, or that silently carries far fewer shows
    than asked for, likewise cannot masquerade as an empty observation.
    """
    try:
        batches = payload["data"]["showsBatch"]
    except (KeyError, TypeError) as exc:
        raise SourceFetchError("Unexpected PBS showsBatch payload shape") from exc
    if not isinstance(batches, list):
        raise SourceFetchError("Unexpected PBS showsBatch type")
    if len(batches) < expected:
        # Requests are chunked at BATCH_SIZE; a short response means entries
        # vanished mid-flight, which the caller logs per missing show — but a
        # wholesale shape change (e.g. a permanent truncation) must not read
        # as "those shows have no assets" without a trace.
        logger.warning(
            "pbs_batch_short expected=%s got=%s", expected, len(batches)
        )
    mapping: dict[str, list[dict]] = {}
    for entry in batches:
        if not isinstance(entry, dict):
            continue
        show = entry.get("show")
        slug = str((show or {}).get("slug") or "") if isinstance(show, dict) else ""
        if not slug:
            continue
        assets = entry.get("fullLengthAssets")
        mapping[slug] = [a for a in assets if isinstance(a, dict)] if isinstance(assets, list) else []
    return mapping


def _group_offer_type(assets) -> str:
    """Group free if *any* asset streams free; otherwise subscription."""
    for asset in assets:
        if not asset.get("isMvod"):
            return normalize_offer_type("free_with_ads")
    return normalize_offer_type("subscription")


def _group_expiry(assets) -> Optional[datetime]:
    """Latest ``expireDate`` among a group's assets (None if any never ends,
    or if none expire: the group is available while its last member is)."""
    latest: Optional[datetime] = None
    saw_expiry = False
    for asset in assets:
        parsed = _parse_date(asset.get("expireDate"))
        if parsed is None:
            continue
        saw_expiry = True
        if latest is None or parsed > latest:
            latest = parsed
    return latest if saw_expiry else None


def _as_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> Optional[datetime]:
    """``YYYY-MM-DD`` (PBS ``premiereDate``/``expireDate``) -> aware UTC."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc)
