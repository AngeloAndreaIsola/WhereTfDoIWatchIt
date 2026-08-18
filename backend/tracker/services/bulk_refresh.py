"""Bulk availability refresh service for watchlist items.

Selects watchlist movies needing availability refresh, respecting daily API quota
and caller-provided budget limit.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Optional

from django.db.models import F, Max
from django.utils import timezone

from ..models import Movie, RefreshRun, WatchlistItem
from .cache import AvailabilityCache
from .rate_limit import RateLimitExceeded, RateLimiter

logger = logging.getLogger(__name__)


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

    def to_dict(self) -> dict:
        return asdict(self)


def process_watchlist_refresh(
    budget: int = 90,
    user_id: int = 1,
    cache: Optional[AvailabilityCache] = None,
) -> dict:
    """Batch refresh watchlist movie availability up to budget limit.

    Candidates selection & priority:
    1. Watchlist movies for user_id with no availability records (Max(last_checked_at) is None).
    2. Watchlist movies with oldest/stalest Max(last_checked_at).

    Summary fields:
    - attempted: number of candidates considered
    - refreshed: number of movies successfully refreshed
    - skipped_cached: number skipped because existing cache was fresh
    - skipped_budget_exhausted: number skipped because no refresh budget remained
    - skipped_rate_limited: number skipped because rate limit/quota prevented further refreshes
    - errors: number of unexpected per-movie errors
    - budget: input budget for the run
    - remaining_budget: unused live-refresh budget after the run
    - rate_limited: boolean indicating whether quota/rate limiting stopped the run
    """
    if cache is None:
        cache = AvailabilityCache()

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)

    # Check upfront if rate limit is already exhausted
    if cache.limiter.remaining() <= 0:
        summary.rate_limited = True

    run = RefreshRun.objects.create(
        service="streaming_availability",
        started_at=timezone.now(),
    )

    # Watchlist items for user_id
    items = WatchlistItem.objects.filter(user_id=user_id).select_related("movie")

    # Order candidates: NULL last_checked_at first, then oldest last_checked_at
    candidates_qs = (
        items.values("movie_id")
        .annotate(latest_checked=Max("movie__availabilities__last_checked_at"))
        .order_by(F("latest_checked").asc(nulls_first=True))
    )

    candidate_movie_ids = [row["movie_id"] for row in candidates_qs]
    summary.attempted = len(candidate_movie_ids)

    if candidate_movie_ids:
        # Load movies preserving the candidate_movie_ids order
        movies_by_id = {
            m.id: m for m in Movie.objects.filter(id__in=candidate_movie_ids)
        }

        budget_exhausted = False
        rate_limit_hit = summary.rate_limited

        for movie_id in candidate_movie_ids:
            movie = movies_by_id.get(movie_id)
            if not movie:
                continue

            if rate_limit_hit:
                summary.skipped_rate_limited += 1
                continue

            if summary.remaining_budget <= 0:
                budget_exhausted = True
                summary.skipped_budget_exhausted += 1
                continue

            if not cache.is_stale(movie):
                summary.skipped_cached += 1
                continue

            # Movie is stale/missing and we have budget and rate limit capacity
            try:
                status = cache.get_or_refresh(movie)
                if status.reason == "rate_limited":
                    rate_limit_hit = True
                    summary.rate_limited = True
                    summary.skipped_rate_limited += 1
                elif status.refreshed:
                    summary.refreshed += 1
                    summary.remaining_budget -= 1
                else:
                    summary.skipped_cached += 1
            except RateLimitExceeded:
                rate_limit_hit = True
                summary.rate_limited = True
                summary.skipped_rate_limited += 1
            except Exception as exc:
                logger.exception("Unexpected error refreshing movie tmdb_id=%s: %s", movie.tmdb_id, exc)
                summary.errors += 1

    run.rows_refreshed = summary.refreshed
    run.rows_skipped = summary.skipped_cached + summary.skipped_budget_exhausted + summary.skipped_rate_limited
    run.rate_limited = summary.rate_limited
    run.finished_at = timezone.now()
    run.save()

    return summary.to_dict()
