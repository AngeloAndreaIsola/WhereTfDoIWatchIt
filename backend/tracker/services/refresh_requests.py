"""Event-driven availability refresh requests (Phase B).

The event side of the canonical refresh architecture:

    user/system event
        -> request_availability_refresh(...)    # local INSERT, idempotent
        -> tracker.RefreshRequest (pending, prioritized, deduplicated)
        -> drain_refresh_requests(...)          # called by process_watchlist_refresh
        -> AvailabilityRefreshService           # budgeted, rate-limited
        -> canonical Offer / ProviderContent / ContentMapping rows
        -> local PostgreSQL read path

Contracts:

- **Requesting a refresh never performs network I/O.** It is a local
  database operation executed inside the originating transaction, so it
  commits or rolls back with the user action and the HTTP response never
  waits for an external provider. Actual refresh work happens later, in the
  existing refresh run (cron or ``POST /api/watchlist/refresh/``).
- **Requests are idempotent.** At most one pending request exists per
  (movie, source scope), enforced by a partial unique constraint; repeated
  events collapse into the same row and may only raise its priority.
- **Draining reuses the existing refresh infrastructure**: the same
  ``RateLimiter``/daily budget, the same per-source freshness policy
  (``availability.freshness``, evaluated for the request's own source scope —
  a fresh pending request settles without spending budget, while an
  ``EXPLICIT`` one — the manual "Refresh availability" action — forces the
  new check even when the current data still reads fresh), the same
  ``RefreshRun`` audit row, and the same ``AvailabilityRefreshService`` as
  the scheduled stale sweep — so event triggers can never bypass rate limits
  or duplicate freshness/reconciliation logic.
- **Failures never propagate to the originating user action.** A failed
  drain records ``attempts``/``last_error`` on the request row and retries at
  most ``MAX_ATTEMPTS`` times before being marked ``failed`` permanently.
- **Provider vs Source stays intact**: a request's ``source`` field scopes
  *where the information comes from* (an ``availability.Source.slug``, empty
  for the default title-targeted refresh). It never selects *where the user
  watches* — providers continue to contribute through every registered
  source that reports them.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import IntegrityError
from django.utils import timezone

from availability.models import Offer
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry

from ..models import (
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from .bulk_refresh import BulkRefreshSummary, is_stale
from .rate_limit import RateLimitExceeded, RateLimiter

logger = logging.getLogger(__name__)

# A request is retried at most this many times before it is marked failed
# and left alone (a later event may legitimately open a fresh request).
MAX_ATTEMPTS = 3

# After a completed attempt (done or failed), the same (movie, source) scope
# is left alone for this long even when the title still reads as stale. A
# completed refresh that found nothing is *not* "fresh" (absence is not
# evidence), so without this window every detail view or frontend poll of an
# unavailable title would enqueue — and the worker would spend — a brand-new
# external fetch on each read.
RECENT_ATTEMPT_WINDOW = dt.timedelta(minutes=10)


def has_canonical_offers(movie) -> bool:
    """True when the canonical path has ever observed this title."""
    return Offer.objects.filter(tmdb_id=movie.tmdb_id).exists()


def recently_attempted(movie, source: str = "") -> bool:
    """True when this scope already had a completed attempt within the window.

    Guards the enqueue side against churn: a drain that found nothing leaves
    the title stale, and this read-side check is what stops the next detail
    view (or frontend poll) from immediately opening another attempt for the
    very same observation window.
    """
    return RefreshRequest.objects.filter(
        movie=movie,
        source=source,
        status__in=[RefreshRequestStatus.DONE, RefreshRequestStatus.FAILED],
        completed_at__gte=timezone.now() - RECENT_ATTEMPT_WINDOW,
    ).exists()


def request_availability_refresh(
    movie,
    priority: int = RefreshPriority.STALE,
    *,
    source: str = "",
    if_stale: bool = True,
) -> RefreshRequest | None:
    """Request an asynchronous canonical availability refresh for *movie*.

    Idempotent and local-only: creates at most one pending
    ``RefreshRequest`` per (movie, source scope), upgrading its priority if a
    later event cares more. Titles the canonical path has never seen are
    always bumped to ``RefreshPriority.NEW``.

    ``if_stale`` (default) skips titles whose canonical offers *for the
    requested source scope* are fresh (that scope's own freshness window, see
    ``availability.freshness``) and titles whose same scope completed an
    attempt within ``RECENT_ATTEMPT_WINDOW``, so repeated events (detail
    views, frontend polls, re-adds) do not churn the queue — and one source's
    fresh observation never makes another source's stale scope look fresh.
    Never raises: an enqueue failure is logged and swallowed so it cannot
    break the user action that triggered it.
    """
    try:
        if if_stale and not is_stale(movie, source=source):
            return None
        if if_stale and recently_attempted(movie, source):
            # Already fetched (or failed) moments ago for this scope — the
            # title may legitimately still be stale because the provider has
            # nothing for it; do not open a new attempt on every read.
            return None

        effective_priority = priority
        if not has_canonical_offers(movie):
            effective_priority = min(priority, RefreshPriority.NEW)

        try:
            request, created = RefreshRequest.objects.get_or_create(
                movie=movie,
                source=source,
                status=RefreshRequestStatus.PENDING,
                defaults={"priority": effective_priority},
            )
        except IntegrityError:
            # Concurrent event won the partial unique constraint; the row we
            # want already exists — fall through to the upgrade check below.
            request, created = RefreshRequest.objects.get(
                movie=movie, source=source, status=RefreshRequestStatus.PENDING
            ), False

        if not created and effective_priority < request.priority:
            request.priority = effective_priority
            request.save(update_fields=["priority"])
        return request
    except Exception:  # noqa: BLE001 - enqueue must never break the caller
        logger.exception(
            "refresh_request_enqueue_failed tmdb_id=%s priority=%s",
            getattr(movie, "tmdb_id", None),
            priority,
        )
        return None


def request_watchlist_refresh(
    user_id: int = 1,
    *,
    priority: int = RefreshPriority.EXPLICIT,
    if_stale: bool = False,
) -> dict:
    """Queue an availability refresh for every title on a user's watchlist.

    Bulk counterpart of the detail-page refresh button and the enqueue side
    of the watchlist's "Refresh all availability" action. One local pass over
    the watchlist calls the regular ``request_availability_refresh`` per
    title, so deduplication (one pending row per title), priority upgrades,
    the never-fetched bump and the swallow-on-failure contract stay exactly
    the per-title ones — repeated clicks and titles already queued by an
    event collapse into the same rows instead of multiplying them. No
    network I/O happens here; the queue is drained later by the existing
    ``refresh_worker`` under the shared rate limit and daily budget.

    ``if_stale=False`` by default (mirroring the single-title explicit
    endpoint): a deliberate "refresh everything" must check every title even
    while its current offers still read fresh.

    Returns ``{"queued": <pending rows for those titles right now>,
    "total_watchlist": <titles covered>}``.
    """
    movie_ids = list(
        WatchlistItem.objects.filter(user_id=user_id)
        .values_list("movie_id", flat=True)
        .distinct()
    )
    for movie_id, movie in Movie.objects.in_bulk(movie_ids).items():
        request_availability_refresh(movie, priority=priority, if_stale=if_stale)
    queued = RefreshRequest.objects.filter(
        movie_id__in=movie_ids,
        source="",
        status=RefreshRequestStatus.PENDING,
    ).count()
    return {"queued": queued, "total_watchlist": len(movie_ids)}


def request_import_refresh(
    movie,
    priority: int = RefreshPriority.WATCHLIST,
    *,
    entering: bool = False,
) -> RefreshRequest | None:
    """Refresh-request rule for imported titles.

    Only requests refresh where it is useful:

    - ``entering`` — the title just entered the catalogue (new content row or
      new watchlist item), or
    - the title has **no canonical availability data at all** (never fetched).

    Existing titles that merely happen to be stale are left to the scheduled
    sweep, and fresh titles are skipped by the ``if_stale`` guard inside
    ``request_availability_refresh`` — so re-importing a 500-row backup never
    floods the queue with uncontrolled requests.
    """
    if not entering and has_canonical_offers(movie):
        return None
    return request_availability_refresh(movie, priority=priority, if_stale=True)


def _service_for_scope(source: str, default_service: AvailabilityRefreshService):
    """Resolve the refresh service for a request's source scope."""
    if not source:
        return default_service
    return AvailabilityRefreshService(source=source_registry.get(source))


def _finish(request: RefreshRequest, status: RefreshRequestStatus) -> None:
    request.status = status
    request.completed_at = timezone.now()
    request.last_error = ""
    request.save(update_fields=["status", "completed_at", "last_error"])


def _drain_one(
    request: RefreshRequest,
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
    rate_limit_hit: bool,
) -> bool:
    """Process one pending request under the run's guards.

    The very same guards as the background stale sweep, in order:
    freshness first — evaluated for *this request's source scope* on that
    source's own window, so a title that became fresh meanwhile is settled as
    ``done`` without spending budget (no external call) while a scope that is
    still stale for its own source still refreshes — then rate-limit capacity
    (``limiter.acquire()``), which priority never bypasses, then the remaining
    run budget.

    The one guard an ``EXPLICIT`` request does not take: the freshness
    short-circuit. Every other priority settles fresh scopes without
    spending budget, but ``EXPLICIT`` is the manual "Refresh availability"
    action — a deliberate *"check now"* that must perform a new availability
    check even while the current observation still reads as fresh (the
    enqueue side mirrors this with ``if_stale=False``). Rate limiting and
    the run budget are never bypassed.

    Outcomes:

    - refreshed / already fresh -> ``done``;
    - refresh raised -> ``attempts`` + ``last_error`` recorded, retried on a
      later run up to ``MAX_ATTEMPTS`` times, then ``failed`` permanently;
    - budget or rate limit exhausted -> stays ``pending`` for the next run.

    Returns the (possibly updated) ``rate_limit_hit`` flag: once the
    limiter refuses, every later request of this run skips its acquire.
    """
    movie = request.movie

    # Freshness is free: settle fresh scopes without touching the budget —
    # unless the user explicitly asked for a new check right now.
    if (
        request.priority != RefreshPriority.EXPLICIT
        and not is_stale(movie, source=request.source)
    ):
        _finish(request, RefreshRequestStatus.DONE)
        summary.skipped_cached += 1
        summary.requests_drained += 1
        return rate_limit_hit

    if rate_limit_hit:
        summary.skipped_rate_limited += 1
        return rate_limit_hit

    if summary.remaining_budget <= 0:
        summary.skipped_budget_exhausted += 1
        return rate_limit_hit

    try:
        limiter.acquire()
        _service_for_scope(request.source, service).refresh_content(movie)
    except RateLimitExceeded:
        summary.rate_limited = True
        summary.skipped_rate_limited += 1
        return True
    except Exception as exc:  # noqa: BLE001 - one bad title must not stop the run
        summary.errors += 1
        request.attempts += 1
        request.last_error = f"{type(exc).__name__}: {exc}"
        if request.attempts >= MAX_ATTEMPTS:
            request.status = RefreshRequestStatus.FAILED
            request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "completed_at"]
        )
        summary.requests_drained += 1
        logger.error(
            "refresh_request_failed tmdb_id=%s attempts=%s error=%s",
            movie.tmdb_id, request.attempts, request.last_error,
            exc_info=True,
        )
        return rate_limit_hit
    _finish(request, RefreshRequestStatus.DONE)
    summary.refreshed += 1
    summary.remaining_budget -= 1
    summary.requests_drained += 1
    return rate_limit_hit


def drain_refresh_requests(
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
) -> set[int]:
    """Process pending refresh requests in priority order, budget permitting.

    Called by ``process_watchlist_refresh`` before its background stale
    sweep. Every request goes through the very same guards as the sweep
    (see ``_drain_one``), and priority never bypasses the rate limiter or
    the daily budget. Freshness is evaluated per request: each scope is
    checked against its own source's window, so the drain settles what is
    fresh for *that* scope and refreshes what is not.

    **Fairness (bounded, one slot):** when the run has budget to spare
    (``remaining_budget >= 2``), one unit is held back while the strict
    priority pass runs and is then spent — last, and only by — the
    *longest waiting* pending request, but only when that request is not
    already in the top priority tier present. Without this, a steady stream
    of high-priority events (detail views, watchlist adds) could starve the
    low-priority backlog forever, since the queue is re-read fresh every
    run. The reserved unit goes through the same freshness and rate-limit
    guards as everything else; if the reserved request settles without
    spending it (already fresh, refused, or failed), the unit simply stays
    with the run. With ``budget < 2`` nothing is reserved: plain priority
    wins outright.

    Returns every ``tmdb_id`` the request queue owns this run — settled
    (``done``/``failed``) **and** still-pending requests — so the caller's
    stale sweep neither re-attempts nor double-counts those titles (a pending
    request left for the next run is the queue's responsibility, not the
    sweep's).
    """
    excluded: set[int] = set()
    rate_limit_hit = summary.rate_limited

    pending = list(
        RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING)
        .select_related("movie")
        .order_by("priority", "requested_at")
    )
    if not pending:
        return excluded

    # Fairness slot: the globally oldest request, when it sits below the
    # top tier, gets one budget unit held back for the reserved pass.
    oldest = min(pending, key=lambda row: (row.requested_at, row.pk))
    reserved = None
    if summary.remaining_budget >= 2 and oldest.priority > pending[0].priority:
        reserved = oldest
        summary.remaining_budget -= 1

    strict = pending if reserved is None else [r for r in pending if r.pk != reserved.pk]

    for request in strict:
        excluded.add(request.movie.tmdb_id)
        rate_limit_hit = _drain_one(
            request,
            summary=summary,
            service=service,
            limiter=limiter,
            rate_limit_hit=rate_limit_hit,
        )

    if reserved is not None:
        # Release the held-back unit for the reserved pass: the strict pass
        # spent from everything except this unit, which now becomes spendable
        # again — only by this one request, and only under the same guards.
        summary.remaining_budget += 1
        excluded.add(reserved.movie.tmdb_id)
        rate_limit_hit = _drain_one(
            reserved,
            summary=summary,
            service=service,
            limiter=limiter,
            rate_limit_hit=rate_limit_hit,
        )

    return excluded


def process_refresh_queue(
    *,
    budget: int = 10,
    service: AvailabilityRefreshService | None = None,
    limiter: RateLimiter | None = None,
) -> dict | None:
    """Drain the pending ``RefreshRequest`` queue exactly once (worker tick).

    The queue-only counterpart to ``process_watchlist_refresh``: it settles
    pending event-driven requests in priority order under the same
    ``RateLimiter``/daily quota, the same per-source freshness policy and the
    same ``RefreshRun`` audit row — but it never runs the background stale
    sweep. The refresh worker loops on this function, so a tick must not be
    able to spend the daily API budget on maintenance the scheduled
    ``refresh_availability`` run already owns.

    Returns the run summary dict, or ``None`` when the queue is empty (the
    common idle tick: no ``RefreshRun`` row, no work). When the daily quota
    is already exhausted the queue is left untouched and a summary with
    ``rate_limited=True`` is returned without creating a ``RefreshRun`` —
    there is nothing to audit when not a single request can be attempted.
    """
    pending = RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING)
    if not pending.exists():
        return None

    service = service or AvailabilityRefreshService()
    limiter = limiter or RateLimiter(DEFAULT_SOURCE_SLUG)

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)
    if limiter.remaining() <= 0:
        summary.rate_limited = True
        summary.requests_pending = pending.count()
        return summary.to_dict()

    run = RefreshRun.objects.create(
        service=DEFAULT_SOURCE_SLUG,
        started_at=timezone.now(),
    )
    drain_refresh_requests(summary=summary, service=service, limiter=limiter)
    run.rows_refreshed = summary.refreshed
    run.rows_skipped = (
        summary.skipped_cached
        + summary.skipped_budget_exhausted
        + summary.skipped_rate_limited
    )
    run.rate_limited = summary.rate_limited
    run.finished_at = timezone.now()
    run.save()

    summary.requests_pending = pending.count()
    return summary.to_dict()


def has_pending_request(tmdb_id: int) -> bool:
    """True when any source scope for this title still waits in the queue."""
    return RefreshRequest.objects.filter(
        movie__tmdb_id=tmdb_id, status=RefreshRequestStatus.PENDING
    ).exists()
