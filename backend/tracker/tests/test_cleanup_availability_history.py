"""Safety rules for ``cleanup_availability_history``.

The command's whole value is that it does not destroy history or close a run
that is still in progress. Both are easy to get subtly wrong, so they are pinned
here:

  * a dry run must write nothing;
  * a run younger than the threshold is retained - a slow catalogue walk is
    legitimate, so "unfinished" alone must never be enough;
  * while an advisory lock is held the command refuses entirely;
  * completed runs are never candidates, successful or failed;
  * unrelated historical evidence (AU JustWatch SyncRuns, YouTube parser
    failures, failed markers) is never touched;
  * applying twice changes nothing the second time.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from availability.models import Source, SyncRun
from tracker.models import (
    CatalogueRefreshRequest,
    Provider,
    CatalogueRefreshStatus,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
)

import importlib

MODULE = "tracker.management.commands.cleanup_availability_history"
ABANDONED_ERROR = importlib.import_module(MODULE).ABANDONED_ERROR


@contextmanager
def no_locks():
    """Isolate the "no live work in progress" precondition.

    The command refuses to reconcile while any advisory lock is held, because a
    live catalogue walk may own an unfinished run. On the dev stack a stalled
    collector can hold one for many minutes, which would make these tests assert
    on ambient environment rather than on the command's logic. The guard itself is
    covered by ``test_active_lock_blocks_reconciliation``.
    """
    with patch(f"{MODULE}.held_advisory_locks", return_value=0):
        yield


def _run(service="abc_iview", *, age_minutes=600, finished=True, error=""):
    started = timezone.now() - timedelta(minutes=age_minutes)
    return RefreshRun.objects.create(
        service=service,
        started_at=started,
        finished_at=started + timedelta(minutes=1) if finished else None,
        error=error,
    )


def _sync_run(code="AU"):
    from availability.models import Country

    country, _ = Country.objects.get_or_create(code=code)
    source, _ = Source.objects.get_or_create(
        slug="justwatch", defaults={"name": "JW", "type": "aggregator"}
    )
    provider, _ = Provider.objects.get_or_create(
        slug="nflx", defaults={"name": "Netflix", "category": "stream"}
    )
    return SyncRun.objects.create(
        source=source, provider=provider, mode="full", status="succeeded",
        country=country,
    )


class DryRunTests(TestCase):
    def test_dry_run_writes_nothing(self):
        stale = _run(age_minutes=600, finished=False)

        out = StringIO()
        call_command("cleanup_availability_history", stdout=out)

        stale.refresh_from_db()
        self.assertIsNone(stale.finished_at)
        self.assertEqual(stale.error, "")
        self.assertIn("Dry run", out.getvalue())

    def test_dry_run_is_the_default(self):
        stale = _run(age_minutes=600, finished=False)

        call_command("cleanup_availability_history", stdout=StringIO())

        stale.refresh_from_db()
        self.assertIsNone(stale.finished_at)

    def test_explicit_dry_run_flag_is_accepted(self):
        stale = _run(age_minutes=600, finished=False)

        call_command("cleanup_availability_history", "--dry-run", stdout=StringIO())

        stale.refresh_from_db()
        self.assertIsNone(stale.finished_at)


class SelectionTests(TestCase):
    def test_proven_stale_run_is_reconciled(self):
        stale = _run(age_minutes=600, finished=False)

        with no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        stale.refresh_from_db()
        self.assertIsNotNone(stale.finished_at)
        self.assertEqual(stale.error, ABANDONED_ERROR)

    def test_recent_unfinished_run_is_retained(self):
        recent = _run(age_minutes=10, finished=False)

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        recent.refresh_from_db()
        self.assertIsNone(recent.finished_at)
        self.assertEqual(recent.error, "")

    def test_run_just_under_threshold_is_retained(self):
        # 119 minutes: below the 120-minute default, and well above the 30.9
        # minutes a legitimate walk has ever taken, so this is the case where a
        # too-short threshold would close a genuinely slow run.
        borderline = _run(age_minutes=119, finished=False)

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        borderline.refresh_from_db()
        self.assertIsNone(borderline.finished_at)

    def test_completed_success_is_retained(self):
        good = _run(age_minutes=600, finished=True, error="")

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        good.refresh_from_db()
        self.assertEqual(good.error, "")
        self.assertIsNotNone(good.finished_at)

    def test_completed_failure_is_retained(self):
        """A historical failure is evidence of how the system evolved."""
        bad = _run(age_minutes=600, finished=True, error="SourceFetchError: HTTP 502")

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        bad.refresh_from_db()
        self.assertEqual(bad.error, "SourceFetchError: HTTP 502")

    def test_active_lock_blocks_reconciliation(self):
        stale = _run(age_minutes=600, finished=False)

        with patch(f"{MODULE}.held_advisory_locks", return_value=1):
            out = StringIO()
            call_command("cleanup_availability_history", "--apply", stdout=out)

        stale.refresh_from_db()
        self.assertIsNone(stale.finished_at)
        self.assertIn("advisory lock is held", out.getvalue())

    def test_custom_threshold_is_honoured(self):
        recent = _run(age_minutes=45, finished=False)

        with no_locks():
            call_command(
                "cleanup_availability_history", "--apply", "--min-age-minutes", "30",
                stdout=StringIO(),
            )

        recent.refresh_from_db()
        self.assertIsNotNone(recent.finished_at)


class HistoricalEvidenceTests(TestCase):
    def test_au_justwatch_sync_runs_are_untouched(self):
        run = _sync_run("AU")

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        run.refresh_from_db()
        self.assertEqual(run.country.code, "AU")
        self.assertEqual(SyncRun.objects.filter(country__code="AU").count(), 1)

    def test_youtube_parser_failures_are_untouched(self):
        from tracker.models import Movie

        movie = Movie.objects.create(tmdb_id=777001, title="Y")
        failed = RefreshRequest.objects.create(
            movie=movie, source="youtube",
            status=RefreshRequestStatus.FAILED,
            last_error="SourceFetchError: Unexpected YouTube search payload shape",
        )

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        failed.refresh_from_db()
        self.assertEqual(
            failed.last_error,
            "SourceFetchError: Unexpected YouTube search payload shape",
        )

    def test_historical_failed_markers_are_untouched(self):
        marker = CatalogueRefreshRequest.objects.create(
            sources=["pbs"], status=CatalogueRefreshStatus.FAILED
        )

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.FAILED)
        self.assertEqual(marker.sources, ["pbs"])

    def test_standalone_sync_runs_are_untouched(self):
        run = _sync_run("NL")
        call_command("cleanup_availability_history", "--apply", stdout=StringIO())
        self.assertEqual(SyncRun.objects.filter(pk=run.pk).count(), 1)


class IdempotencyTests(TestCase):
    def test_second_apply_changes_nothing(self):
        stale = _run(age_minutes=600, finished=False)

        call_command("cleanup_availability_history", "--apply", stdout=StringIO())
        first = stale.__class__.objects.get(pk=stale.pk).finished_at

        out = StringIO()
        call_command("cleanup_availability_history", "--apply", stdout=out)

        self.assertEqual(
            stale.__class__.objects.get(pk=stale.pk).finished_at, first,
            "a second apply must not re-touch an already-reconciled run",
        )
        self.assertIn("Nothing to reconcile", out.getvalue())

    def test_repeated_dry_runs_are_stable(self):
        _run(age_minutes=600, finished=False)
        for _ in range(3):
            call_command("cleanup_availability_history", stdout=StringIO())

        self.assertEqual(
            RefreshRun.objects.filter(finished_at__isnull=True).count(), 1
        )

    def test_run_finishing_before_apply_is_not_closed(self):
        """A run that completes between listing and write is left alone."""
        stale = _run(age_minutes=600, finished=False)
        real_update = RefreshRun.objects.filter

        def finish_then_filter(*args, **kwargs):
            queryset = real_update(*args, **kwargs)
            if "finished_at__isnull" in str(args) or "finished_at__isnull" in str(kwargs):
                RefreshRun.objects.filter(pk=stale.pk).update(
                    finished_at=timezone.now(), error="genuine completion"
                )
            return queryset

        with patch.object(RefreshRun.objects, "filter", finish_then_filter):
            call_command("cleanup_availability_history", "--apply",
                         stdout=StringIO())

        stale.refresh_from_db()
        self.assertEqual(stale.error, "genuine completion")
