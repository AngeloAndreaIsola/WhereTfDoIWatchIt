"""Reconcile abandoned RefreshRun rows left by killed workers.

Background
----------

A worker that is killed mid-catalogue-walk (a container restart, a deploy, a
manual ``docker compose restart``) leaves its ``RefreshRun`` with
``finished_at = NULL`` forever. Nothing reaps it: the row is opened at the start
of a source execution and closed at the end, and a process that dies in between
never gets to close it. By 2026-10-06 fifteen such rows had accumulated, the
oldest three days stale.

They are not harmful to correctness — nothing reads an unfinished run except
this command and the operator looking at them — but they do make
``RefreshRun.objects.filter(finished_at__isnull=True)`` useless as an
operational signal, which is exactly what you need when diagnosing whether a
worker is currently busy.

What this does *not* do
-----------------------

* It never deletes a row. Deleting would erase the only evidence that a source
  was mid-walk when the worker died.
* It never marks a run ``COMPLETED``. There is no such field: ``RefreshRun`` has
  no status at all, only ``finished_at`` plus an ``error`` string whose meaning
  the model documents explicitly:

      "Non-empty only when the run failed. A finished run with an empty error
       is a genuine success (including a genuine empty result), so a failed
       catalogue fetch can never masquerade as a successful refresh."

  So the terminal state written here - ``finished_at`` set **and** ``error``
  non-empty - is precisely the encoding the application already uses for a run
  that did not succeed. An abandoned run genuinely did not succeed, so this is
  not a new semantic state; it is the existing one, applied honestly. A reader
  can therefore keep trusting the invariant that a finished run with an empty
  ``error`` really did complete its work.

* It never touches historical evidence: completed runs (successful or failed),
  AU JustWatch SyncRuns, YouTube parser failures, failed catalogue markers and
  standalone SyncRuns are all out of scope by construction - this command only
  ever selects rows with ``finished_at IS NULL``.

Staleness rule
--------------

A run is only a candidate when **all** of these hold:

1. ``finished_at IS NULL``;
2. ``started_at`` is older than ``--min-age-minutes`` (default 120, deliberately
   more than double the measured maximum legitimate runtime of 30.9 minutes);
3. no Postgres advisory lock is currently held, so no live worker owns a
   catalogue walk.

The threshold is a CLI option rather than a constant because the legitimate
envelope is an empirical property of the slowest collector, not a constant of
the model. Defaulting to 120 keeps a genuinely slow 40-minute walk safe while
still catching rows abandoned a day ago.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from tracker.models import RefreshRun

#: Two hours: comfortably above the measured 30.9-minute maximum legitimate
#: catalogue runtime, and far below the ages of genuinely abandoned rows.
DEFAULT_MIN_AGE_MINUTES = 120

#: Written into ``error``. Records that the row was reconciled by an operator
#: command rather than completed by the worker, so the reason is never confused
#: with a source-level failure the worker actually observed.
ABANDONED_ERROR = (
    "abandoned: worker exited before finishing this run "
    "(reconciled by cleanup_availability_history)"
)


def held_advisory_locks() -> int:
    """Count advisory locks currently held, i.e. work in progress right now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        )
        return cursor.fetchone()[0]


class Command(BaseCommand):
    help = (
        "Reconcile RefreshRun rows abandoned by killed workers. "
        "Defaults to a dry run; pass --apply to write."
    )

    def add_arguments(self, parser):
        # Dry run is the default, but the flag is also accepted explicitly so
        # the safe invocation can be spelled out in a script or a runbook
        # instead of relying on the absence of --apply.
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview only. This is the default; the flag is accepted for "
                 "explicit scripts and runbooks.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the reconciliation. Without it nothing is written.",
        )
        parser.add_argument(
            "--min-age-minutes",
            type=int,
            default=DEFAULT_MIN_AGE_MINUTES,
            help=(
                "Minimum age of an unfinished run before it is considered "
                f"abandoned (default {DEFAULT_MIN_AGE_MINUTES})."
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        min_age = options["min_age_minutes"]
        cutoff = timezone.now() - timedelta(minutes=min_age)

        locks = held_advisory_locks()
        unfinished = RefreshRun.objects.filter(finished_at__isnull=True)
        candidates = list(
            unfinished.filter(started_at__lt=cutoff).order_by("started_at")
        )
        # Deliberately not reconciled: too young to be provably abandoned, so a
        # slow-but-live run cannot be closed out from under its worker.
        retained_young = list(
            unfinished.filter(started_at__gte=cutoff).order_by("started_at")
        )

        self.stdout.write(
            f"mode                 : {'APPLY' if apply else 'DRY RUN'}"
        )
        self.stdout.write(f"min age (minutes)    : {min_age}")
        self.stdout.write(f"advisory locks held  : {locks}")
        self.stdout.write(f"unfinished RefreshRuns: {unfinished.count()}")
        self.stdout.write(f"candidates           : {len(candidates)}")
        self.stdout.write(f"retained (too young) : {len(retained_young)}")

        if locks:
            self.stdout.write(
                self.style.WARNING(
                    "\nAn advisory lock is held, so a catalogue walk is in "
                    "progress. Refusing to reconcile: the unfinished rows may "
                    "belong to the live worker."
                )
            )
            candidates = []

        self.stdout.write("")
        self.stdout.write(
            f"{'id':<6}{'service':<24}{'age(min)':>10}  "
            f"{'current':<10}{'proposed':<12}reason"
        )
        for run in candidates:
            age = (timezone.now() - run.started_at).total_seconds() / 60
            self.stdout.write(
                f"{run.id:<6}{run.service[:23]:<24}{age:>10.0f}  "
                f"{'unfinished':<10}{'abandoned':<12}"
                f"worker gone, no lock, age>{min_age}m"
            )
        for run in retained_young:
            age = (timezone.now() - run.started_at).total_seconds() / 60
            self.stdout.write(
                f"{run.id:<6}{run.service[:23]:<24}{age:>10.0f}  "
                f"{'unfinished':<10}{'RETAIN':<12}"
                f"younger than {min_age}m; may still be running"
            )

        if not apply:
            self.stdout.write(
                "\nDry run: no rows were modified. Re-run with --apply to write."
            )
            return

        if not candidates:
            self.stdout.write("\nNothing to reconcile.")
            return

        with transaction.atomic():
            # Re-check under the transaction so a run that finished between the
            # listing and the write is left alone.
            stale_ids = [
                run.id
                for run in RefreshRun.objects.filter(
                    id__in=[r.id for r in candidates], finished_at__isnull=True
                )
            ]
            changed = RefreshRun.objects.filter(
                id__in=stale_ids, finished_at__isnull=True
            ).update(
                finished_at=timezone.now(),
                error=ABANDONED_ERROR,
                abandoned_at=timezone.now(),
            )

        self.stdout.write(
            self.style.SUCCESS(f"\nReconciled {changed} abandoned RefreshRun(s).")
        )
