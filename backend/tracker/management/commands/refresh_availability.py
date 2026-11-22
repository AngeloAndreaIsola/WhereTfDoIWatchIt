"""Daily refresh of stale watchlist Availability rows within API budget.

Default budget: 90 calls/day (10 reserve for ad-hoc user-initiated fetches).
"""
from __future__ import annotations

import json
import logging

from django.core.management.base import BaseCommand

from ...ownership import resolve_personal_owner_id
from ...services.bulk_refresh import process_watchlist_refresh

logger = logging.getLogger(__name__)

DAILY_BUDGET = 90


class Command(BaseCommand):
    help = "Refresh stalest watchlist Availability rows for movies and series within the daily API budget."

    def add_arguments(self, parser):
        parser.add_argument(
            "--budget",
            type=int,
            default=DAILY_BUDGET,
            help=f"Maximum API calls to make (default {DAILY_BUDGET}).",
        )

    def handle(self, *args, **options):
        budget = options["budget"]
        summary = process_watchlist_refresh(
            budget=budget,
            user_id=resolve_personal_owner_id(),
            content_types=["movie", "series"],
        )

        summary_str = json.dumps(summary, indent=2)
        if summary.get("rate_limited"):
            self.stdout.write(self.style.WARNING(
                f"Watchlist refresh stopped early due to rate limit.\nSummary:\n{summary_str}"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Watchlist refresh completed.\nSummary:\n{summary_str}"
            ))
