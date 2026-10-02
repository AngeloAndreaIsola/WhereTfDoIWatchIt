"""Queue consumer for event-driven refresh requests (Phase B worker).

Long-running loop that drains pending ``RefreshRequest`` rows created by
detail views, watchlist adds, imports and scraper confirmations. The enqueue
side never performs network I/O (see ``request_availability_refresh``); this
command is the missing consumer that turns queued intent into canonical
``Offer`` rows.

Each tick calls ``process_refresh_queue`` — queue-only: strict priority order
(``RefreshPriority`` ladder), the shared ``RateLimiter``/daily quota and a
``RefreshRun`` audit row, but never the background stale sweep (that stays
with the scheduled ``refresh_availability`` run), so looping cannot burn the
daily API budget on maintenance.

Typical deployment: the ``worker`` service in docker-compose.
"""
from __future__ import annotations

import json
import logging
import time

from django.core.management.base import BaseCommand
from django.db import connections

from availability.sources import DEFAULT_SOURCE_SLUG

from ...services.rate_limit import RateLimiter
from ...services.refresh_requests import process_refresh_queue

logger = logging.getLogger(__name__)

# When the daily quota is exhausted there is nothing to do until it resets —
# back off instead of re-checking the queue every normal tick.
RATE_LIMITED_SLEEP = 60.0


class Command(BaseCommand):
    help = "Continuously drain pending RefreshRequest rows (refresh worker)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=float,
            default=5.0,
            help="Seconds between ticks (default 5).",
        )
        parser.add_argument(
            "--budget",
            type=int,
            default=10,
            help="Maximum refreshes per tick (default 10).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single tick and exit (tests / one-shot runs).",
        )

    def handle(self, *args, **options):
        interval = max(options["interval"], 0.1)
        budget = options["budget"]
        limiter = RateLimiter(DEFAULT_SOURCE_SLUG)

        while True:
            sleep_for = interval
            try:
                summary = process_refresh_queue(budget=budget, limiter=limiter)
                if summary:
                    if summary["requests_drained"] or summary["errors"]:
                        self.stdout.write(json.dumps(summary))
                    if summary["rate_limited"]:
                        sleep_for = RATE_LIMITED_SLEEP
            except Exception:  # noqa: BLE001 - a transient DB error must not kill the worker
                logger.exception("refresh_worker_tick_failed")
                sleep_for = interval
            finally:
                # Long-running loop: drop the connection so a stale one
                # (db restart, idle timeout) is re-established next tick.
                connections.close_all()

            if options["once"]:
                return
            time.sleep(sleep_for)
