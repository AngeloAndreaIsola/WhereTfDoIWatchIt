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
  ``RateLimiter``/daily budget, the same ``is_stale`` freshness policy, the
  same ``RefreshRun`` audit row, and the same ``AvailabilityRefreshService``
  as the scheduled stale sweep — so event triggers can never bypass rate
  limits or duplicate freshness/reconciliation logic.
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
from availability.sources import source_registry

from ..models import RefreshPriority, RefreshRequest, RefreshRequestStatus
from .bulk_refresh import is_stale
from .rate_limit import RateLimitExceeded

logger = logging.getLogger(__name__)

# A request is retried at most this many times before it is marked failed
# and left alone (a later event may legitimately open a fresh request).
MAX_ATTEMPTS = 3


def has_canonical_offers(movie) -> bool:
    """True when the canonical path has ever observed this title."""
    return Offer.objects.filter(tmdb_id=movie.tmdb_id).exists()


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

    ``if_stale`` (default) skips titles whose canonical offers are fresh, so
    repeated events (detail views, re-adds) do not churn the queue. Never
    raises: an enqueue failure is logged and swallowed so it cannot break the
    user action that triggered it.
    """
    try:
        if if_stale and not is_stale(movie):
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


def drain_refresh_requests(
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
    stale_after: dt.timedelta,
) -> set[int]:
    """Process pending refresh requests in priority order, budget permitting.

    Called by ``process_watchlist_refresh`` before its background stale
    sweep. Every request goes through the very same guards as the sweep:

    - freshness first (a title that became fresh meanwhile is settled as
      ``done`` without spending budget — no external call),
    - then rate-limit capacity (``limiter.acquire()``), which priority never
      bypasses,
    - then remaining run budget.

    Outcomes:

    - refreshed / already fresh -> ``done``;
    - refresh raised -> ``attempts`` + ``last_error`` recorded, retried on a
      later run up to ``MAX_ATTEMPTS`` times, then ``failed`` permanently;
    - budget or rate limit exhausted -> stays ``pending`` for the next run.

    Returns every ``tmdb_id`` the request queue owns this run — settled
    (``done``/``failed``) **and** still-pending requests — so the caller's
    stale sweep neither re-attempts nor double-counts those titles (a pending
    request left for the next run is the queue's responsibility, not the
    sweep's).
    """
    excluded: set[int] = set()
    rate_limit_hit = summary.rate_limited

    pending = (
        RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING)
        .select_related("movie")
        .order_by("priority", "requested_at")
    )

    for request in pending:
        movie = request.movie

        # Freshness is free: settle fresh titles without touching the budget.
        if not is_stale(movie, stale_after):
            _finish(request, RefreshRequestStatus.DONE)
            summary.skipped_cached += 1
            summary.requests_drained += 1
            excluded.add(movie.tmdb_id)
            continue

        if rate_limit_hit:
            summary.skipped_rate_limited += 1
            excluded.add(movie.tmdb_id)
            continue

        if summary.remaining_budget <= 0:
            summary.skipped_budget_exhausted += 1
            excluded.add(movie.tmdb_id)
            continue

        try:
            limiter.acquire()
            _service_for_scope(request.source, service).refresh_content(movie)
        except RateLimitExceeded:
            rate_limit_hit = True
            summary.rate_limited = True
            summary.skipped_rate_limited += 1
            excluded.add(movie.tmdb_id)
            continue
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
            excluded.add(movie.tmdb_id)
            logger.error(
                "refresh_request_failed tmdb_id=%s attempts=%s error=%s",
                movie.tmdb_id, request.attempts, request.last_error,
                exc_info=True,
            )
            continue
        else:
            _finish(request, RefreshRequestStatus.DONE)
            summary.refreshed += 1
            summary.remaining_budget -= 1
            summary.requests_drained += 1
            excluded.add(movie.tmdb_id)

    return excluded
