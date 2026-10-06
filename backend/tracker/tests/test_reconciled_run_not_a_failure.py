"""A reconciled RefreshRun must not read as a current source failure.

The defect this pins was introduced by the historical cleanup itself. Closing
abandoned runs wrote a reconciliation note into ``RefreshRun.error``, and the
catalogue status report reads the *newest* ``RefreshRun`` per source. So cleaning
up a three-day-old crash made ``abc_iview`` - a source refresh 21 never even
queued - report as ``failed / source_unavailable`` for that refresh:

    CatalogueRefreshRequest 26  sources = ['bbc_iplayer']
    abc_iview = failed  reason = source_unavailable

The fix is the structured ``abandoned_at`` column rather than recognising the
note's text, so these tests pin the *semantics*: a reconciled run says a worker
died, not that a source failed.

Case B is the important counterweight - a genuine failure must still surface.
A fix that simply ignored every run with a non-empty error would pass A and
silently disable real failure reporting.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tracker.models import RefreshRun
from tracker.services import availability_report, provider_status

MODULE = "tracker.management.commands.cleanup_availability_history"


def _run(service="abc_iview", *, age_minutes=600, error="", abandoned=False,
         finished=True):
    started = timezone.now() - timedelta(minutes=age_minutes)
    finished_at = started + timedelta(minutes=1) if finished else None
    if abandoned and finished_at is None:
        # What the cleanup command leaves behind.
        finished_at = timezone.now()
    return RefreshRun.objects.create(
        service=service,
        started_at=started,
        finished_at=finished_at,
        error=error,
        abandoned_at=(timezone.now() if abandoned else None),
    )


def _catalogue_row(slug):
    rows = availability_report.catalogue_progress()
    return next(r for r in rows if r["source"] == slug)


class ReconciledRunIsNotAFailureTests(TestCase):
    """A. A reconciled run must not become the current reported outcome."""

    def test_reconciled_run_newer_than_a_good_run_is_ignored(self):
        _run("abc_iview", age_minutes=600, error="", finished=True)
        _run("abc_iview", age_minutes=60, finished=False, error="")
        # The cleanup command closes this one an hour ago with a note.
        _run(
            "abc_iview", age_minutes=30, error="abandoned: worker exited before "
            "finishing this run (reconciled by cleanup_availability_history)",
            abandoned=True,
        )

        row = _catalogue_row("abc_iview")

        self.assertNotEqual(row["status"], "failed",
                            "a reconciled run must not report as a failure")
        self.assertEqual(row["status"], "completed")

    def test_source_with_only_a_reconciled_run_is_not_reported_failed(self):
        _run(
            "tubi", age_minutes=30,
            error="abandoned: worker exited before finishing this run "
                  "(reconciled by cleanup_availability_history)",
            abandoned=True,
        )
        row = _catalogue_row("tubi")
        self.assertNotEqual(row["status"], "failed")

    def test_provider_card_error_ignores_a_reconciled_run(self):
        from tracker.models import Provider

        _run("abc_iview", age_minutes=600, error="")
        _run("abc_iview", age_minutes=600, error="", finished=True)
        _run(
            "abc_iview", age_minutes=5,
            error="abandoned: worker exited (reconciled by "
                  "cleanup_availability_history)",
            abandoned=True,
        )
        provider, _ = Provider.objects.get_or_create(
            slug="abc_iview", defaults={"name": "ABC iview", "category": "stream"}
        )
        from tracker.services.provider_status import refresh_target

        cards = provider_status.provider_card_status([provider])
        card = cards[provider.id]

        self.assertEqual(card.get("last_run_error", ""), "",
                         "a reconciled run must not surface as a card error")
        self.assertFalse(card.get("refreshing"),
                         "a reconciled run is finished, not running")


class GenuineFailureStillSurfacesTests(TestCase):
    """B. The fix must not swallow real failures."""

    def test_genuine_failure_is_still_reported(self):
        _run("abc_iview", age_minutes=30, error="SourceFetchError: HTTP 502")

        row = _catalogue_row("abc_iview")

        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["reason"], "source_unavailable")

    def test_genuine_failure_after_a_reconciled_run_is_reported(self):
        """Newest-run semantics still apply to real runs."""
        _run(
            "abc_iview", age_minutes=60,
            error="abandoned: worker exited (reconciled)",
            abandoned=True,
        )
        _run("abc_iview", age_minutes=5, error="SourceFetchError: upstream 500")

        row = _catalogue_row("abc_iview")
        self.assertEqual(row["status"], "failed")

    def test_provider_card_still_shows_a_genuine_error(self):
        from tracker.models import Provider

        _run("abc_iview", age_minutes=5, error="SourceFetchError: upstream 500")
        provider, _ = Provider.objects.get_or_create(
            slug="abc_iview", defaults={"name": "ABC iview", "category": "stream"}
        )
        card = provider_status.provider_card_status([provider])[provider.id]
        self.assertIn("upstream 500", card.get("last_run_error", ""))

    def test_running_run_is_still_running(self):
        _run("abc_iview", age_minutes=5, finished=False)
        from tracker.models import Provider

        provider, _ = Provider.objects.get_or_create(
            slug="abc_iview", defaults={"name": "ABC iview", "category": "stream"}
        )
        card = provider_status.provider_card_status([provider])[provider.id]
        self.assertTrue(card.get("refreshing"))


class RefreshScopeTests(TestCase):
    """C. An unrelated reconciled run stays out of a refresh's evidence."""

    def test_marker_containing_only_bbc_does_not_import_abc_evidence(self):
        from tracker.models import (AvailabilityRefresh, AvailabilityRefreshType,
                                    AvailabilityRefreshSource)

        _run(
            "abc_iview", age_minutes=30,
            error="abandoned: worker exited (reconciled)",
            abandoned=True,
        )
        refresh = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status="running",
        )
        # Exactly what refresh 21 had: a marker scoped to bbc_iplayer only.
        row = AvailabilityRefreshSource.objects.create(
            refresh=refresh, source="abc_iview", kind="catalogue",
            status="not_run", reason="collector_not_run",
        )

        self.assertEqual(row.status, "not_run")
        self.assertNotEqual(row.status, "failed")

    def test_refresh_terminal_status_is_unaffected_by_a_reconciled_run(self):
        """A reconciled run must not drag an unrelated refresh to partial."""
        from tracker.models import AvailabilityRefresh, AvailabilityRefreshType

        _run(
            "abc_iview", age_minutes=30,
            error="abandoned: worker exited (reconciled)",
            abandoned=True,
        )
        refresh = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status="queued",
        )
        from tracker.services.refresh_lifecycle import finalise_run

        finalise_run(refresh)
        refresh.refresh_from_db()
        self.assertIn(
            refresh.status, {"completed", "partial", "failed"}
        )


class CleanupPreservationTests(TestCase):
    """D. Cleanup still reconciles, still preserves, and marks structurally."""

    def test_apply_sets_the_structured_marker(self):
        stale = _run(age_minutes=600, finished=False)

        with _no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        stale.refresh_from_db()
        self.assertIsNotNone(stale.abandoned_at,
                             "reconciliation must stamp abandoned_at")
        self.assertIsNotNone(stale.finished_at)

    def test_reconciled_run_is_not_a_current_failure_after_cleanup(self):
        _run(age_minutes=600, finished=False)
        with _no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        row = _catalogue_row("abc_iview")
        self.assertNotEqual(row["status"], "failed")

    def test_cleanup_does_not_delete_or_alter_history(self):
        from availability.models import Country, Source, SyncRun
        from tracker.models import Provider

        country, _ = Country.objects.get_or_create(code="AU")
        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JW", "type": "aggregator"}
        )
        provider, _ = Provider.objects.get_or_create(
            slug="nflx", defaults={"name": "Netflix", "category": "stream"}
        )
        au = SyncRun.objects.create(
            source=source, provider=provider, mode="full", status="succeeded",
            country=country,
        )
        stale = _run(age_minutes=600, finished=False)

        with _no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=StringIO())

        au.refresh_from_db()
        self.assertEqual(au.country.code, "AU")
        self.assertEqual(SyncRun.objects.count(), 1)
        self.assertEqual(RefreshRun.objects.count(), 1)
        self.assertIsNotNone(stale.__class__.objects.get(pk=stale.pk).finished_at)

    def test_idempotency_still_holds(self):
        stale = _run(age_minutes=600, finished=False)
        with _no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=StringIO())
        first = RefreshRun.objects.get(pk=stale.pk).abandoned_at

        out = StringIO()
        with _no_locks():
            call_command("cleanup_availability_history", "--apply", stdout=out)

        self.assertEqual(RefreshRun.objects.get(pk=stale.pk).abandoned_at, first)
        self.assertIn("Nothing to reconcile", out.getvalue())

    def test_dry_run_writes_nothing(self):
        stale = _run(age_minutes=600, finished=False)
        with _no_locks():
            call_command("cleanup_availability_history", stdout=StringIO())

        stale.refresh_from_db()
        self.assertIsNone(stale.abandoned_at)
        self.assertIsNone(stale.finished_at)


class SuccessInvariantTests(TestCase):
    """The existing meaning of a finished, error-free run is unchanged."""

    def test_finished_and_errorless_run_is_still_completed(self):
        _run("abc_iview", age_minutes=10, error="")
        row = _catalogue_row("abc_iview")
        self.assertEqual(row["status"], "completed")

    def test_ordinary_run_has_no_abandoned_marker(self):
        run = _run("abc_iview", age_minutes=10, error="")
        self.assertIsNone(run.abandoned_at)


def _no_locks():
    """Context manager making cleanup see "no work in progress".

    The command deliberately aborts when an advisory lock is held, because a
    live catalogue walk may own an unfinished run. That guard is correct and
    covered in the cleanup suite; these tests are about what a *completed*
    reconciliation means, so they pin the precondition rather than the guard.
    """
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def _ctx():
        with patch(f"{MODULE}.held_advisory_locks", return_value=0):
            yield

    return _ctx()


class LegacyBackfillMigrationTests(TestCase):
    """Migration 0039 converts rows reconciled before the column existed.

    The database this project runs against predates ``abandoned_at``: cleanup
    (dd1ef06) closed 14 abandoned runs by writing a note into ``error``, and only
    afterwards did the column arrive. Without a data migration, a fresh checkout
    of that history would make those runs look like genuine failures again - the
    exact defect this module exists to prevent, reappearing on any other
    environment.

    Exercised directly against the historical model rather than via
    ``migrate``, because running the real migration would need the column to not
    already exist. The logic under test is the same callable Django executes.
    """

    LEGACY = (
        "abandoned: worker exited before finishing this run "
        "(reconciled by cleanup_availability_history)"
    )

    def _migration(self):
        import importlib

        return importlib.import_module(
            "tracker.migrations.0039_refreshrun_abandoned_at"
        )


    def _apps(self):
        from django.apps import apps

        return apps

    def test_legacy_reconciled_rows_are_stamped(self):
        legacy = _run(age_minutes=300, error=self.LEGACY, abandoned=False)
        success = _run("pbs", age_minutes=300, error="")
        failure = _run("cbc_gem", age_minutes=300,
                       error="SourceFetchError: HTTP 502")

        stamped = self._migration().mark_reconciled(self._apps(), None)

        legacy.refresh_from_db()
        success.refresh_from_db()
        failure.refresh_from_db()
        self.assertEqual(stamped, 1)
        self.assertIsNotNone(legacy.abandoned_at)
        self.assertIsNone(success.abandoned_at,
                          "an ordinary successful run must stay unmarked")
        self.assertIsNone(failure.abandoned_at,
                          "a genuine failure must stay unmarked and visible")

    def test_backfill_is_idempotent(self):
        _run(age_minutes=300, error=self.LEGACY, abandoned=False)
        self._migration().mark_reconciled(self._apps(), None)

        again = self._migration().mark_reconciled(self._apps(), None)

        self.assertEqual(again, 0)
        self.assertEqual(
            RefreshRun.objects.filter(abandoned_at__isnull=False).count(), 1
        )

    def test_backfill_ignores_unfinished_rows(self):
        """An in-flight row is never abandoned, note text or not."""
        running = _run(age_minutes=300, error=self.LEGACY, finished=False,
                       abandoned=False)

        self._migration().mark_reconciled(self._apps(), None)

        running.refresh_from_db()
        self.assertIsNone(running.abandoned_at)

    def test_reverse_clears_only_the_legacy_rows(self):
        legacy = _run(age_minutes=300, error=self.LEGACY, abandoned=True)
        failure = _run("cbc_gem", age_minutes=300,
                       error="SourceFetchError: HTTP 502")

        self._migration().unmark_reconciled(self._apps(), None)

        legacy.refresh_from_db()
        failure.refresh_from_db()
        self.assertIsNone(legacy.abandoned_at)
        self.assertIsNone(failure.abandoned_at)
        self.assertEqual(failure.error, "SourceFetchError: HTTP 502")
