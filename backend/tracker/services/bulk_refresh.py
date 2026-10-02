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
``availability.Offer.last_seen`` **per source**: every scope is measured
against its own declared window (``availability.freshness``), never a single
global timeout, so two sources for the same title go stale independently. The
legacy ``AvailabilityCache`` no longer owns availability persistence here. The
legacy tracker ``RefreshRun`` audit row is kept for run bookkeeping, while
per-group lifecycle details live on ``availability.SyncRun`` rows created by
the refresh service.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from django.db import connection
from django.db.models import Max
from django.utils import timezone

from availability.freshness import freshness_for, source_is_stale, stale_sources
from availability.models import Country, Offer, Source
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry

from ..models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from .rate_limit import RateLimitExceeded, RateLimiter

logger = logging.getLogger(__name__)


def is_stale(
    movie: Movie,
    *,
    source: str = "",
    now=None,
) -> bool:
    """True when *source*'s observation of the movie is missing or too old.

    Freshness is evaluated **per source** against that source's declared
    window (``availability.freshness``) — there is no shared global timeout:
    the default scope and a catalogue source for the same title go stale
    independently, so a fresh Prime Video observation never hides a stale
    Streaming Availability one (and vice versa).

    ``source=""`` is the default title-targeted scope
    (``DEFAULT_SOURCE_SLUG``) — what this module's sweep and a default
    ``RefreshRequest`` refresh. The newest ``Offer.last_seen`` of that scope
    counts (season/episode rows included, so a series refreshed through
    season-level offers is not re-fetched). A scope the canonical path has
    never observed is always stale.
    """
    return source_is_stale(movie.tmdb_id, source or DEFAULT_SOURCE_SLUG, now=now)


def refreshable_source_slugs() -> list[str]:
    """Sources the per-title sweep may refresh.

    Capability-driven, never slug-driven: a source that syncs its own
    catalogue is refreshed by ``process_catalogue_refresh`` (a whole-catalogue
    fetch, no budget), while everything else needs a per-title fetch here —
    today that is the default title-targeted scope. Unknown slugs are not
    refreshable by construction (they are not registered).
    """
    return [
        slug
        for slug in source_registry.slugs()
        if not source_registry.get(slug).capabilities.supports_catalogue_sync
    ]


def stale_refresh_scopes(tmdb_id, *, now=None) -> list[str]:
    """Refreshable sources whose observation of the title is stale.

    Each candidate is evaluated on its own freshness window, so one stale
    source is selected while a fresh one for the same title is left alone —
    and a fresh source never triggers a refresh of another scope. Catalogue
    sources are classified by :func:`stale_sources` but dropped here: their
    staleness belongs to the catalogue run, not the budgeted per-title sweep.
    """
    refreshable = set(refreshable_source_slugs())
    return [
        slug for slug in stale_sources(tmdb_id, now=now) if slug in refreshable
    ]


def _latest_checks(tmdb_ids) -> dict:
    """Map tmdb_id -> newest default-scope ``Offer.last_seen`` for those ids.

    Ordering follows the scope the sweep refreshes: a title another source
    refreshed an hour ago is still worth ordering by *its own* age here.
    """
    rows = (
        Offer.objects.filter(
            tmdb_id__in=list(tmdb_ids), source__slug=DEFAULT_SOURCE_SLUG
        )
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
    3. Watchlist movies with the oldest default-scope ``Offer.last_seen``.

    One unit of work = one ``AvailabilityRefreshService.refresh_content``
    call (one external API request + one transactional canonical write).

    Staleness is per source (``stale_refresh_scopes``): each source's
    observation of a title is evaluated against that source's own freshness
    window, only refreshable scopes are refreshed, and a fresh source for the
    same title never triggers — never suppresses — another scope's refresh.

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
    limiter = limiter or RateLimiter(DEFAULT_SOURCE_SLUG)

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)

    # Check upfront if rate limit is already exhausted
    if limiter.remaining() <= 0:
        summary.rate_limited = True

    run = RefreshRun.objects.create(
        service=DEFAULT_SOURCE_SLUG,
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

        # Per-source staleness: each source's own window decides, so a fresh
        # source neither triggers nor suppresses another scope's refresh.
        scopes = stale_refresh_scopes(tmdb_id)
        if not scopes:
            summary.skipped_cached += 1
            continue

        # The stale scopes we may refresh, with budget and rate capacity.
        for slug in scopes:
            if rate_limit_hit:
                summary.skipped_rate_limited += 1
                break
            if summary.remaining_budget <= 0:
                summary.skipped_budget_exhausted += 1
                break
            scope_service = (
                service
                if slug == DEFAULT_SOURCE_SLUG
                else AvailabilityRefreshService(source=source_registry.get(slug))
            )
            try:
                limiter.acquire()
                scope_service.refresh_content(movie)
                summary.refreshed += 1
                summary.remaining_budget -= 1
            except RateLimitExceeded:
                rate_limit_hit = True
                summary.rate_limited = True
                summary.skipped_rate_limited += 1
            except Exception as exc:
                logger.exception(
                    "Unexpected error refreshing movie tmdb_id=%s source=%s: %s",
                    movie.tmdb_id, slug, exc,
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


def stale_catalogue_sources(now=None) -> list[str]:
    """Catalogue-capable slugs whose last successful sync left its window.

    Evidence is the legacy ``RefreshRun(service=slug)`` audit row
    ``process_catalogue_refresh`` writes for every attempted source (``error``
    set when the attempt failed): the newest error-free ``finished_at`` per
    slug — a slug with no such row has never synced and is stale by
    definition. Each slug is measured against its own freshness window
    (``availability.freshness``), the same policy the title scopes use, and
    disabled sources are excluded exactly like the bare catalogue run does.

    One aggregate query regardless of how many catalogue sources exist —
    the watchlist's bulk refresh calls it to decide whether to press the
    catalogue marker.
    """
    slugs, _disabled = _catalogue_source_slugs()
    last_success = {
        row["service"]: row["last"]
        for row in RefreshRun.objects.filter(
            service__in=slugs, error="", finished_at__isnull=False
        )
        .values("service")
        .annotate(last=Max("finished_at"))
    }
    moment = now or timezone.now()
    return sorted(
        slug
        for slug in slugs
        if last_success.get(slug) is None
        or moment - last_success[slug] > freshness_for(slug)
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
        summary.attempted, summary.refreshed, summary.errors,
        summary.locked, summary.disabled, summary.discovered,
        summary.created + summary.updated, summary.unresolved,
        summary.duration_seconds,
    )
    return summary.to_dict()


def process_pending_catalogue_refresh() -> Optional[dict]:
    """Run the queued catalogue-sync marker, if any (refresh worker tick).

    The counterpart of ``request_catalogue_refresh``: one pending marker =
    one pass over exactly the sources that read stale when the watchlist's
    "Refresh all" pressed it, each under its own advisory lock — so an
    overlapping scheduled cron simply skips that source as ``locked``, which
    is success by definition (someone else is syncing it right now). Only
    genuine per-source errors (or usage errors escaping
    ``process_catalogue_refresh``) fail the marker; it stays consumed either
    way — a later button press opens a fresh job.

    Returns the marker's JSON-able summary, or ``None`` when no marker is
    pending (the common idle tick). Catalogue syncs spend no API budget, so
    this runs regardless of the daily rate limit.
    """
    marker = (
        CatalogueRefreshRequest.objects.filter(
            status=CatalogueRefreshStatus.PENDING
        )
        .order_by("requested_at")
        .first()
    )
    if marker is None:
        return None

    sources = list(marker.sources)
    failed: list[str] = []
    for slug in sources:
        try:
            result = process_catalogue_refresh(source_slug=slug)
            if result.get("errors"):
                failed.append(slug)
        except Exception:  # noqa: BLE001 - one broken source must not stop the rest
            logger.exception("catalogue_marker_source_failed source=%s", slug)
            failed.append(slug)

    marker.completed_at = timezone.now()
    marker.status = (
        CatalogueRefreshStatus.FAILED if failed else CatalogueRefreshStatus.DONE
    )
    marker.last_error = ", ".join(failed)
    marker.save(update_fields=["status", "completed_at", "last_error"])
    logger.info(
        "catalogue_marker_processed sources=%s failed=%s status=%s",
        ",".join(sources) or "none",
        ",".join(failed) or "none",
        marker.status,
    )
    return {
        "sources": sources,
        "failed_sources": failed,
        "status": marker.status,
    }
