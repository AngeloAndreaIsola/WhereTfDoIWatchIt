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
daily API budget on maintenance. After the title queue drains, the tick also
runs a pending ``CatalogueRefreshRequest`` marker, if any: whole-catalogue
syncs pressed by the watchlist's "Refresh all" (no API budget of their own).

Typical deployment: the ``worker`` service in docker-compose.
"""
from __future__ import annotations

import json
import logging
import time

from django.core.management.base import BaseCommand
from django.db import connections

from availability.sources import DEFAULT_SOURCE_SLUG

from ...services.bulk_refresh import process_pending_catalogue_refresh
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
                    # Rate-limited ticks are printed too: they are the
                    # *interesting* ones (queue stuck until the quota
                    # resets) and were previously silent, which made a
                    # quota-exhausted worker indistinguishable from a dead
                    # one. The 60s backoff keeps the line rate at ~1/min.
                    if (
                        summary["requests_drained"]
                        or summary["errors"]
                        or summary["rate_limited"]
                    ):
                        self.stdout.write(json.dumps(summary))
                    if summary["rate_limited"]:
                        sleep_for = RATE_LIMITED_SLEEP
                # Catalogue marker after the title queue: whole-catalogue
                # syncs spend no API budget, but the title queue always
                # gets its budgeted drain first.
                catalogue = process_pending_catalogue_refresh()
                if catalogue:
                    self.stdout.write(json.dumps({"catalogue": catalogue}))
            except Exception:  # noqa: BLE001 - a transient DB error must not kill the worker
                logger.exception("refresh_worker_tick_failed")
                sleep_for = interval
            finally:
                # Drop the connection so a stale one (db restart, idle
                # timeout) is re-established next tick — but never
                # mid-transaction: a test (or any caller wrapping a tick
                # in an atomic block) would lose its open transaction,
                # and closing one is wrong regardless.
                if not any(
                    conn.in_atomic_block for conn in connections.all()
                ):
                    connections.close_all()

            if options["once"]:
                return
            time.sleep(sleep_for)
