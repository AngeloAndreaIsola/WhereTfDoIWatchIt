"""Daily refresh of stale Availability rows within API budget.

Default budget: 90 calls/day (10 reserve for ad-hoc user-initiated fetches).
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ...models import Movie
from ...services.cache import AvailabilityCache
from ...services.rate_limit import RateLimitExceeded

logger = logging.getLogger(__name__)

DAILY_BUDGET = 90


class Command(BaseCommand):
    help = "Refresh stalest Availability rows within the daily API budget."

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget",
            type=int,
            default=DAILY_BUDGET,
            help=f"Maximum API calls to make (default {DAILY_BUDGET}).",
        )

    def handle(self, *args, **options):
        budget = options["budget"]
        cache = AvailabilityCache()
        # Pick movies whose newest Availability row is oldest (or missing).
        candidates = (
            Movie.objects.all()
            .order_by("availabilities__last_checked_at")
            .distinct()
        )
        refreshed = 0
        skipped = 0
        for movie in candidates.iterator():
            if refreshed >= budget:
                self.stdout.write(self.style.WARNING(
                    f"Budget exhausted after {refreshed} refreshes; remaining queued for tomorrow."
                ))
                break
            if not cache.is_stale(movie):
                skipped += 1
                continue
            try:
                status = cache.get_or_refresh(movie)
            except RateLimitExceeded as exc:
                self.stdout.write(self.style.ERROR(f"Rate limit hit: {exc}"))
                break
            if status.refreshed:
                refreshed += 1
                self.stdout.write(
                    f"Refreshed {movie} (+{status.offers_written} offers, reason={status.reason})"
                )
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(
            f"Done. refreshed={refreshed} skipped={skipped} budget={budget}"
        ))