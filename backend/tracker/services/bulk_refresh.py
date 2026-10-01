"""Bulk availability refresh services.

``process_watchlist_refresh`` (Phase 3): selects watchlist movies needing
availability refresh, respecting daily API quota and caller-provided budget
limit. Phase B extended it with an event-driven request drain
(``tracker.services.refresh_requests``): pending ``RefreshRequest`` rows —
created by watchlist adds, imports, and detail views — are processed first,
in priority order, through the exact same budget, rate limiter, freshness
policy and audit row as the background sweep.

``process_catalogue_refresh`` (Phases 5/6): refreshes the full catalogue of
catalogue-capable sources (CBC Gem first) with no budget selection at all —
one service call per source, each internally fetching its whole configured
catalogue. It is the cron-facing entry point: capability-driven source
discovery (registry + enabled ``Source`` rows), a per-source advisory lock
against overlapping runs, declared-country initialization, per-source failure
isolation, and a summary that distinguishes succeeded / failed / locked /
disabled sources.

Write path:

    bulk_refresh
        -> AvailabilityRefreshService (refresh_content / refresh_catalogue)
        -> AvailabilitySource interface (registry: streaming_availability,
           cbc_gem)
        -> OfferRepository (canonical availability.Offer rows)

Staleness and candidate ordering are evaluated against canonical
``availability.Offer.last_seen``; the legacy ``AvailabilityCache`` no longer
owns availability persistence here. The legacy tracker ``RefreshRun`` audit
row is kept for run bookkeeping, while per-group lifecycle details live on
``availability.SyncRun`` rows created by the refresh service.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from django.db import connection
from django.db.models import Max
from django.utils import timezone

from availability.models import Country, Offer, Source
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import source_registry

from ..models import Movie, RefreshRequest, RefreshRequestStatus, RefreshRun, WatchlistItem
from .rate_limit import RateLimitExceeded, RateLimiter

logger = logging.getLogger(__name__)

STALE_AFTER = dt.timedelta(hours=24)


def is_stale(movie: Movie, stale_after: dt.timedelta = STALE_AFTER) -> bool:
    """True when the movie has no canonical offer observed within stale_after.

    Freshness is ``max(Offer.last_seen)`` across all offers of the movie's
    TMDb id (season/episode rows count too), so a series refreshed through
    season-level offers is not re-fetched. A movie the canonical path has
    never seen is always stale — same behavior the legacy cache had for
    movies with no availability rows.
    """
    latest = (
        Offer.objects.filter(tmdb_id=movie.tmdb_id)
        .aggregate(latest=Max("last_seen"))["latest"]
    )
    if latest is None:
        return True
    return (timezone.now() - latest) > stale_after


def _latest_checks(tmdb_ids) -> dict:
    """Map tmdb_id -> newest canonical ``Offer.last_seen`` for those ids."""
    rows = (
        Offer.objects.filter(tmdb_id__in=list(tmdb_ids))
        .values("tmdb_id")
        .annotate(latest=Max("last_seen"))
    )
    return {row["tmdb_id"]: row["latest"] for row in rows}


def _candidate_pairs(user_id: int, content_types) -> list[tuple[int, int]]:
    """(movie_id, tmdb_id) pairs to consider, watchlist first, no duplicates."""
    items = WatchlistItem.objects.filter(
        user_id=user_id,
        movie__content_type__in=list(content_types),
    ).select_related("movie")

    pairs: list[tuple[int, int]] = []
    seen: set[int] = set()
    for movie_id, tmdb_id in items.values_list("movie_id", "movie__tmdb_id"):
        if movie_id not in seen:
            seen.add(movie_id)
            pairs.append((movie_id, tmdb_id))

    if pairs:
        return pairs

    # Fallback to querying catalog movies and series directly
    for movie_id, tmdb_id in Movie.objects.filter(
        content_type__in=list(content_types)
    ).values_list("id", "tmdb_id"):
        if movie_id not in seen:
            seen.add(movie_id)
            pairs.append((movie_id, tmdb_id))
    return pairs


def _order_candidates(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Never-checked first, then oldest canonical check first."""
    latest = _latest_checks(tmdb_id for _movie_id, tmdb_id in pairs)
    return sorted(
        pairs,
        key=lambda pair: (
            pair[1] in latest,
            latest.get(pair[1]) or timezone.now(),
        ),
    )


@dataclass
class BulkRefreshSummary:
    attempted: int = 0
    refreshed: int = 0
    skipped_cached: int = 0
    skipped_budget_exhausted: int = 0
    skipped_rate_limited: int = 0
    errors: int = 0
    budget: int = 0
    remaining_budget: int = 0
    rate_limited: bool = False
    # Event-driven refresh requests (Phase B): how many pending
    # RefreshRequest rows were settled (done or failed) by this run, and how
    # many are still queued for a later run.
    requests_drained: int = 0
    requests_pending: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def process_watchlist_refresh(
    budget: int = 90,
    user_id: int = 1,
    refresh_service: Optional[AvailabilityRefreshService] = None,
    limiter: Optional[RateLimiter] = None,
    content_types: tuple[str, ...] | list[str] = ("movie", "series"),
    stale_after: dt.timedelta = STALE_AFTER,
) -> dict:
    """Batch refresh watchlist movie availability up to budget limit.

    Candidates selection & priority:
    1. Pending event-driven refresh requests (``tracker.RefreshRequest``),
       drained first in their deterministic priority order — the user-intent
       ladder (``RefreshPriority``: explicit action, never-fetched, recent
       view, watchlist, watched, stale, background) outranks background
       maintenance, with one bounded fairness slot so the oldest waiting
       request cannot be starved (see ``drain_refresh_requests``). Draining
       shares this run's budget, rate limiter and ``RefreshRun`` audit row,
       so event triggers can never bypass the daily API quota.
    2. Watchlist movies for user_id with no canonical availability yet.
    3. Watchlist movies with oldest/stalest canonical Offer.last_seen.

    One unit of work = one ``AvailabilityRefreshService.refresh_content``
    call (one external API request + one transactional canonical write).

    Summary fields:
    - attempted: number of sweep candidates considered
    - refreshed: number of titles successfully refreshed (requests + sweep)
    - skipped_cached: number skipped because existing cache was fresh
    - skipped_budget_exhausted: number skipped because no refresh budget remained
    - skipped_rate_limited: number skipped because rate limit/quota prevented further refreshes
    - errors: number of unexpected per-title errors
    - budget: input budget for the run
    - remaining_budget: unused live-refresh budget after the run
    - rate_limited: boolean indicating whether quota/rate limiting stopped the run
    - requests_drained: pending RefreshRequest rows settled (done/failed) this run
    - requests_pending: RefreshRequest rows still queued after the run
    """
    service = refresh_service or AvailabilityRefreshService()
    limiter = limiter or RateLimiter("streaming_availability")

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)

    # Check upfront if rate limit is already exhausted
    if limiter.remaining() <= 0:
        summary.rate_limited = True

    run = RefreshRun.objects.create(
        service="streaming_availability",
        started_at=timezone.now(),
    )

    # Phase B: drain event-driven refresh requests first (local intent
    # outranks the background sweep). Local import: refresh_requests imports
    # this module's freshness helpers, so the dependency stays one-way at
    # module load time. The returned set owns those titles for this run —
    # settled or still queued — so the sweep below neither re-attempts nor
    # double-counts them.
    from .refresh_requests import drain_refresh_requests

    queue_owned = drain_refresh_requests(
        summary=summary,
        service=service,
        limiter=limiter,
        stale_after=stale_after,
    )

    pairs = _order_candidates(_candidate_pairs(user_id, content_types))
    summary.attempted = len(pairs)
    movies_by_id = {
        m.id: m for m in Movie.objects.filter(id__in=[movie_id for movie_id, _ in pairs])
    }

    rate_limit_hit = summary.rate_limited
    for movie_id, tmdb_id in pairs:
        if tmdb_id in queue_owned:
            # The request drain owns this title this run (settled, or still
            # queued for a later run) — one title, one attempt per run.
            continue

        movie = movies_by_id.get(movie_id)
        if not movie:
            continue

        if rate_limit_hit:
            summary.skipped_rate_limited += 1
            continue

        if summary.remaining_budget <= 0:
            summary.skipped_budget_exhausted += 1
            continue

        if not is_stale(movie, stale_after):
            summary.skipped_cached += 1
            continue

        # Movie is stale/missing, we have budget and rate limit capacity.
        try:
            limiter.acquire()
            service.refresh_content(movie)
            summary.refreshed += 1
            summary.remaining_budget -= 1
        except RateLimitExceeded:
            rate_limit_hit = True
            summary.rate_limited = True
            summary.skipped_rate_limited += 1
        except Exception as exc:
            logger.exception(
                "Unexpected error refreshing movie tmdb_id=%s: %s", movie.tmdb_id, exc
            )
            summary.errors += 1

    run.rows_refreshed = summary.refreshed
    run.rows_skipped = summary.skipped_cached + summary.skipped_budget_exhausted + summary.skipped_rate_limited
    run.rate_limited = summary.rate_limited
    run.finished_at = timezone.now()
    run.save()

    summary.requests_pending = RefreshRequest.objects.filter(
        status=RefreshRequestStatus.PENDING
    ).count()

    return summary.to_dict()


@dataclass
class CatalogueSourceSummary:
    """Outcome of one source inside a scheduled catalogue refresh.

    ``status`` is one of:

    - ``succeeded``: fetch + mapping + persistence completed (an empty
      catalogue is still a success);
    - ``failed``: fetch/mapping/persistence raised — recorded on the source's
      ``RefreshRun.error``, never disguised as a successful empty run;
    - ``locked``: another refresh for the same source holds the lock, so this
      run skipped cleanly without touching the database;
    - ``disabled``: the database ``Source`` row is disabled, so the source
      was never scheduled (the row is never deleted).
    """

    source: str
    status: str = "succeeded"
    discovered: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    unresolved: int = 0
    error: str = ""
    started_at: str = ""
    duration_seconds: float = 0.0

    @property
    def attempted(self) -> bool:
        """True when a refresh was actually invoked for this source."""
        return self.status in ("succeeded", "failed")


@dataclass
class CatalogueRefreshSummary:
    attempted: int = 0
    refreshed: int = 0
    errors: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    discovered: int = 0
    unresolved: int = 0
    locked: int = 0
    disabled: int = 0
    duration_seconds: float = 0.0
    sources: list = field(default_factory=list)
    results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _catalogue_lock_key(slug: str) -> int:
    """Deterministic signed 64-bit advisory-lock key for a source slug."""
    digest = hashlib.sha256(slug.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _try_source_lock(slug: str) -> bool:
    """Try to take the per-source refresh lock; True when this run owns it.

    Uses a Postgres session advisory lock keyed by the source slug, so two
    overlapping cron runs of the *same* source cannot refresh concurrently —
    the second one skips cleanly — while two *different* sources each take
    their own key and never block one another. The lock is released in a
    ``finally`` (and automatically if the connection dies), so a crashed run
    cannot leave the source locked. Backends without advisory locks (local
    sqlite) simply proceed; the production/dev stack is Postgres.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_catalogue_lock_key(slug)])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_source_lock(slug: str) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_catalogue_lock_key(slug)])


def _ensure_declared_countries(slug: str, capabilities) -> None:
    """Initialize the source's declared country scope before it is written to.

    The refresh service never creates countries itself: an offer whose
    ``Country`` row is missing used to be skipped silently (the Phase 5
    discovery that left CBC writes empty). A scheduled run therefore makes
    sure every capability-declared country exists first — derived from
    ``SourceCapabilities``, never from a hardcoded country list, and never
    for sources whose country scope is unrestricted (nothing to initialize).

    Rows that already exist are untouched: a *disabled* country keeps its
    documented semantics (offers skipped, refresh succeeds) and is only
    logged so the situation is visible in cron output.
    """
    for code in sorted(capabilities.supported_countries or ()):
        normalized = str(code).upper()
        country, created = Country.objects.get_or_create(
            code=normalized, defaults={"name": normalized}
        )
        if created:
            logger.warning(
                "catalogue_refresh_country_initialized source=%s country=%s",
                slug, normalized,
            )
        elif not country.enabled:
            logger.warning(
                "catalogue_refresh_country_disabled source=%s country=%s "
                "offers for it will be skipped",
                slug, normalized,
            )


def _catalogue_source_slugs(
    source_slug: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    """Discover scheduled sources: (slugs to refresh, disabled slugs).

    Discovery is capability-driven end to end — the registry owns the code
    implementations, the database ``Source`` row owns configuration/state,
    and the scheduler never learns any provider's name:

        registered sources -> supports_catalogue_sync -> enabled in the DB

    An explicit ``source_slug`` is a manual override (``--source``): it still
    validates against the registry/capabilities — an unknown slug raises
    ``UnknownSourceError`` and a non-catalogue slug raises ``ValueError``,
    both before anything is recorded — but it bypasses the enabled filter so
    an operator can refresh a deliberately disabled source on purpose.
    """
    if source_slug is not None:
        source = source_registry.get(source_slug)
        if not source.capabilities.supports_catalogue_sync:
            raise ValueError(
                f"Source {source_slug!r} does not support catalogue refresh"
            )
        if Source.objects.filter(slug=source_slug, enabled=False).exists():
            logger.warning(
                "catalogue_refresh_source_disabled source=%s reason=manual_override",
                source_slug,
            )
        return [source_slug], []

    candidates = [
        slug for slug in source_registry.slugs()
        if source_registry.get(slug).capabilities.supports_catalogue_sync
    ]
    disabled = set(
        Source.objects.filter(enabled=False).values_list("slug", flat=True)
    )
    return (
        [slug for slug in candidates if slug not in disabled],
        [slug for slug in candidates if slug in disabled],
    )


def _refresh_one_source(
    slug: str,
    source,
    refresh_service: Optional[AvailabilityRefreshService] = None,
) -> CatalogueSourceSummary:
    """Refresh one source's catalogue, isolated from every other source.

    Failure isolation: whatever this source raises (fetch, normalization,
    mapping, persistence) is recorded on its own ``RefreshRun`` and returned
    as ``failed`` — the caller keeps going with the remaining sources, and
    no offer is ever deactivated because a refresh failed.
    """
    started_at = timezone.now()
    result = CatalogueSourceSummary(source=slug, started_at=started_at.isoformat())
    t0 = time.monotonic()

    if not _try_source_lock(slug):
        result.status = "locked"
        logger.warning(
            "catalogue_refresh_skipped source=%s reason=already_running "
            "started_at=%s",
            slug, result.started_at,
        )
        return result

    run = RefreshRun.objects.create(service=slug, started_at=started_at)
    try:
        _ensure_declared_countries(slug, source.capabilities)
        logger.info(
            "catalogue_refresh_started source=%s providers=%s countries=%s "
            "started_at=%s",
            slug,
            ",".join(sorted(source.capabilities.supported_providers or ())) or "any",
            ",".join(sorted(source.capabilities.supported_countries or ())) or "any",
            result.started_at,
        )
        service = refresh_service or AvailabilityRefreshService(source=source)
        refresh_result = service.refresh_catalogue()
    except Exception as exc:
        duration = round(time.monotonic() - t0, 3)
        logger.exception(
            "catalogue_refresh_failed source=%s duration_s=%s error=%s",
            slug, duration, exc,
        )
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        result.duration_seconds = duration
        run.error = result.error
        run.finished_at = timezone.now()
        run.save()
        return result
    else:
        duration = round(time.monotonic() - t0, 3)
        result.status = "succeeded"
        result.discovered = refresh_result.discovered
        result.processed = refresh_result.processed
        result.created = refresh_result.created
        result.updated = refresh_result.updated
        result.skipped = refresh_result.skipped
        result.unresolved = refresh_result.unresolved
        result.duration_seconds = duration
        run.rows_refreshed = refresh_result.processed
        run.rows_skipped = refresh_result.skipped
        run.finished_at = timezone.now()
        run.save()
        logger.info(
            "catalogue_refresh_completed source=%s discovered=%s mapped=%s "
            "unresolved=%s offers=%s created=%s updated=%s skipped=%s "
            "duration_s=%s finished_at=%s",
            slug, refresh_result.discovered,
            refresh_result.discovered - refresh_result.unresolved,
            refresh_result.unresolved,
            refresh_result.processed,
            refresh_result.created, refresh_result.updated,
            refresh_result.skipped,
            duration, run.finished_at.isoformat(),
        )
        return result
    finally:
        # Always release, on success, failure, or an unexpected record-time
        # error, so a crashed run can never wedge the source's schedule.
        try:
            _release_source_lock(slug)
        except Exception:
            logger.exception("catalogue_refresh_unlock_failed source=%s", slug)


def process_catalogue_refresh(
    source_slug: Optional[str] = None,
    refresh_service: Optional[AvailabilityRefreshService] = None,
) -> dict:
    """Refresh whole catalogues for catalogue-capable sources.

    One unit of work = one ``AvailabilityRefreshService.refresh_catalogue``
    call: one source fetch (a source may internally walk many upstream pages)
    and one transactional canonical write. Unlike ``process_watchlist_refresh``
    there is no staleness selection and no API budget — a catalogue source
    reports its full configured scope in a single fetch (CBC Gem: one pass
    over its category indices), so budget accounting would be meaningless.

    Scope selection:
    - ``refresh_service`` (tests / callers with an injected source) runs alone;
    - ``source_slug`` restricts the run to one registered source — an unknown
      slug raises ``UnknownSourceError`` and a non-catalogue source raises
      ``ValueError`` *before* any run is recorded, because both are usage
      errors rather than per-source refresh failures;
    - default: every registered source whose capabilities claim
      ``supports_catalogue_sync`` **and** whose database ``Source`` row is not
      disabled (a missing row means "not configured yet" and counts as
      enabled, because ``Source.enabled`` defaults to True).

    Scheduling contract (Phase 6): each source is attempted under its own
    advisory lock (overlapping runs of the same source skip cleanly), its
    declared country scope is initialized first, and one failure never stops
    the remaining sources. Each attempted source gets a legacy
    ``RefreshRun(service=slug)`` audit row whose ``error`` field is set when
    the attempt failed; per-group lifecycle details stay on
    ``availability.SyncRun`` rows created by the refresh service (a failed
    fetch creates no SyncRun at all — nothing was written).
    """
    started_at = time.monotonic()
    if refresh_service is not None:
        slugs = [refresh_service.source.slug]
        sources = {refresh_service.source.slug: refresh_service.source}
        disabled: list[str] = []
    else:
        slugs, disabled = _catalogue_source_slugs(source_slug)
        sources = {slug: source_registry.get(slug) for slug in slugs}

    results: list[CatalogueSourceSummary] = []
    for slug in disabled:
        logger.warning("catalogue_refresh_skipped source=%s reason=disabled", slug)
        results.append(CatalogueSourceSummary(source=slug, status="disabled"))
    for slug in slugs:
        results.append(_refresh_one_source(slug, sources[slug], refresh_service))

    summary = CatalogueRefreshSummary(
        attempted=sum(1 for item in results if item.attempted),
        refreshed=sum(1 for item in results if item.status == "succeeded"),
        errors=sum(1 for item in results if item.status == "failed"),
        locked=sum(1 for item in results if item.status == "locked"),
        disabled=sum(1 for item in results if item.status == "disabled"),
        created=sum(item.created for item in results),
        updated=sum(item.updated for item in results),
        skipped=sum(item.skipped for item in results),
        discovered=sum(item.discovered for item in results),
        unresolved=sum(item.unresolved for item in results),
        duration_seconds=round(time.monotonic() - started_at, 3),
        sources=[item.source for item in results if item.status == "succeeded"],
        results=results,
    )
    logger.info(
        "catalogue_refresh_summary attempted=%s refreshed=%s errors=%s "
        "locked=%s disabled=%s discovered=%s offers=%s unresolved=%s "
        "duration_s=%s",
        summary.attempted, summary.refreshed, summary.errors, summary.locked,
        summary.disabled, summary.discovered,
        summary.created + summary.updated, summary.unresolved,
        summary.duration_seconds,
    )
    return summary.to_dict()
