"""Queue consumer for event-driven refresh requests (Phase B worker).

Long-running loop that drains pending ``RefreshRequest`` rows created by
detail views, watchlist adds, imports and scraper confirmations. The enqueue
side never performs network I/O (see ``request_availability_refresh``); this
command is the missing consumer that turns queued intent into canonical
``Offer`` rows.

Each tick calls ``process_refresh_queue`` — queue-only: strict priority order
(``RefreshPriority`` ladder) and a ``RefreshRun`` audit row, but never the
background stale sweep (that stays with the scheduled ``refresh_availability``
run), so looping cannot burn the daily API budget on maintenance. Quota is
resolved **per source** inside the drain (each request carries its own source
slug), not from one limiter held here — a source that has spent its budget
defers only its own requests. After the title queue drains, the tick also runs
a pending ``CatalogueRefreshRequest`` marker, if any: whole-catalogue syncs
pressed by the watchlist's "Refresh all" (no API budget of their own).

Typical deployment: the ``worker`` service in docker-compose.
"""
from __future__ import annotations

import json
import logging
import time

from django.core.management.base import BaseCommand
from django.db import connections

from ...services.bulk_refresh import process_pending_catalogue_refresh
from ...services.refresh_lifecycle import (
    AvailabilityRefreshLifecycle,
    UnsettledWorkError,
    claim_active_run,
    reconcile_stale_runs,
)
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
        # No limiter is injected: each request carries its own source and the
        # drain resolves that source's budget from the registry, so a single
        # limiter here would re-impose one shared quota on all sources.

        while True:
            sleep_for = interval
            run = None
            try:
                # The worker is the authoritative actor for a run's
                # lifecycle: it claims the active run (QUEUED -> RUNNING)
                # before doing any work, and finalises it from persisted
                # state afterwards. A crash in between leaves a RUNNING run
                # whose request rows are still truthful, which is exactly what
                # makes the next process able to settle it correctly.
                run = claim_active_run()
                if run is not None:
                    # A run left RUNNING by a dead worker is settled here,
                    # before this tick adopts anything. Deferred when its
                    # queue is genuinely still in flight.
                    for recovered in reconcile_stale_runs():
                        self.stdout.write(
                            json.dumps(
                                {
                                    "reconciled": recovered.pk,
                                    "status": recovered.status,
                                }
                            )
                        )
                    run = claim_active_run()

                summary = process_refresh_queue(budget=budget)
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
                    # Back off only when there is genuinely nothing to do.
                    #
                    # Quota is per source now, so "a source is exhausted" is
                    # not "the queue is stuck": other sources may still have
                    # pending requests this tick should pick up. Sleeping the
                    # whole worker on one source's budget is exactly what
                    # made an exhausted Streaming Availability look like a
                    # dead watchlist. Sleep only when nothing was drained, no
                    # error occurred, and every remaining request belongs to a
                    # blocked source — the true idle-until-reset case.
                    if (
                        summary["rate_limited"]
                        and not summary["requests_drained"]
                        and not summary["errors"]
                        # ``.get`` because a caller (or a test double) may hand
                        # back a summary predating this field; a minimal
                        # summary must not crash the tick.
                        and not summary.get("skipped_cached", 0)
                    ):
                        sleep_for = RATE_LIMITED_SLEEP
                # Catalogue marker after the title queue: whole-catalogue
                # syncs spend no API budget, but the title queue always
                # gets its budgeted drain first.
                catalogue = process_pending_catalogue_refresh()
                if catalogue:
                    self.stdout.write(json.dumps({"catalogue": catalogue}))

                # Finalise from persisted state, never from `summary`: a
                # finalise that trusted the worker's own tally would report
                # progress the database never recorded if the tick died
                # between draining and writing.
                if run is not None:
                    AvailabilityRefreshLifecycle.finalise(run)
            except Exception:  # noqa: BLE001 - a transient DB error must not kill the worker
                logger.exception("refresh_worker_tick_failed")
                if run is not None:
                    # The tick failed as a whole. Mark the run failed rather
                    # than leaving it RUNNING to be mistaken for progress.
                    #
                    # `fail()` settles unexecutable work and then refuses to
                    # terminate a run that still owns *runnable* work, because
                    # the terminal invariant applies to FAILED too. So it can
                    # legitimately raise: that would mean the run genuinely has
                    # executable backlog, which is not "this refresh failed" —
                    # it is "this refresh is unfinished and should be resumed".
                    #
                    # In that case the run is deliberately left RUNNING for
                    # `reconcile_stale_runs` / the next tick, and the refusal is
                    # logged. Swallowing it here is correct: this handler exists
                    # so one bad tick cannot kill the worker, and letting the
                    # invariant's exception escape would do exactly that.
                    try:
                        AvailabilityRefreshLifecycle.fail(
                            run, error="worker tick raised"
                        )
                    except UnsettledWorkError:
                        logger.warning(
                            "refresh_tick_failure_deferred refresh_id=%s: run still "
                            "owns runnable work, leaving RUNNING for reconciliation",
                            run.pk,
                        )
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
