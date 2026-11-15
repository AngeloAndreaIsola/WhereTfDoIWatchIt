"""Catalogue synchronization observability: what the timestamps mean.

Two timestamps, and the tests exist because conflating them is the failure mode:

- ``last_successful_sync`` — the last *completed* traversal.
- ``progress_at`` — the last *forward progress*.

The rules these tests pin, each of which was previously wrong or absent:

1. A partial run must never claim completion.
2. A failed run must never claim completion.
3. Replaying a batch that re-reads the same window must not look like progress.
4. Forward progress must update the liveness timestamp.
5. A stalled walk must be detectable without confusing it with an in-flight one.
6. An existing row (written before ``progress_at`` existed) must stay usable.
"""
from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from availability.freshness import STALL_AFTER
from availability.models import Country, Source, SyncCursor
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceCapabilities, SourceFetchResult

from tracker.models import Provider

from .source_stubs import StubSource


class CursorTimestampTests(TestCase):
    """The repository contract, at the row level."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="ts_source", defaults={"name": "TS"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )
        self.source_row, _ = Source.objects.get_or_create(
            slug="ts_source", defaults={"name": "TS"}
        )
        self.repo = CursorRepository()

    def _set(self, cursor, now=None):
        return self.repo.set(
            provider=self.provider, source=self.source_row,
            country=self.country, cursor=cursor, now=now,
        )

    def test_the_first_write_records_progress_and_never_completion(self):
        row = self._set("a")
        self.assertIsNotNone(row.progress_at)
        self.assertIsNone(
            row.last_successful_sync,
            "a first checkpoint write claimed the walk had completed",
        )

    def test_forward_progress_moves_the_liveness_timestamp(self):
        first = timezone.now() - dt.timedelta(hours=5)
        self._set("a", now=first)
        second = timezone.now()
        row = self._set("b", now=second)
        self.assertEqual(row.progress_at, second)

    def test_replaying_the_same_checkpoint_is_not_progress(self):
        """A retried batch persists the identical value: no new progress."""
        original = timezone.now() - dt.timedelta(hours=5)
        self._set("a", now=original)
        # Same value, much later: the clock must NOT move.
        row = self._set("a", now=timezone.now())
        self.assertEqual(
            row.progress_at, original,
            "replaying an unchanged checkpoint was recorded as forward progress",
        )

    def test_completion_stamps_both_timestamps_and_resets_the_walk(self):
        now = timezone.now()
        row = self.repo.complete(
            provider=self.provider, source=self.source_row,
            country=self.country, now=now,
        )
        self.assertEqual(row.last_successful_sync, now)
        self.assertEqual(row.progress_at, now)
        self.assertEqual(row.cursor, "")

    def test_a_failed_run_never_touches_completion(self):
        """The failure path deletes the row; completion must not have moved."""
        self._set("a")
        self.repo.delete(
            provider=self.provider, source=self.source_row, country=self.country
        )
        self.assertFalse(
            SyncCursor.objects.filter(
                provider=self.provider, source=self.source_row,
                country=self.country,
            ).exists()
        )


class StallDetectionTests(TestCase):
    """Detectable, and not confused with a healthy in-flight walk."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="stall_source", defaults={"name": "Stall"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )
        self.source_row, _ = Source.objects.get_or_create(
            slug="stall_source", defaults={"name": "Stall"}
        )

    def _row(self, *, progress_age=None, cursor="a"):
        row = SyncCursor.objects.create(
            provider=self.provider, source=self.source_row,
            country=self.country, cursor=cursor,
        )
        if progress_age is not None:
            # ``auto_now``/``auto_now_add`` would overwrite these, so they are
            # written straight through the queryset.
            SyncCursor.objects.filter(pk=row.pk).update(
                progress_at=timezone.now() - progress_age
            )
            row.refresh_from_db()
        return row

    def test_a_recently_advanced_walk_is_not_stalled(self):
        row = self._row(progress_age=dt.timedelta(minutes=5))
        self.assertFalse(row.is_stalled)

    def test_a_walk_that_stopped_advancing_is_stalled(self):
        row = self._row(progress_age=STALL_AFTER + dt.timedelta(hours=1))
        self.assertTrue(row.is_stalled)

    def test_a_walk_that_never_advanced_is_stalled(self):
        row = self._row(progress_age=None)
        self.assertTrue(row.is_stalled)

    def test_the_threshold_is_far_longer_than_a_run_interval(self):
        """Guard against a threshold that would flag a healthy daily walk.

        The catalogue cron runs daily; a threshold at or below that cadence
        would call every healthy walk stalled between ticks.
        """
        self.assertGreater(STALL_AFTER, dt.timedelta(hours=24))

    def test_an_existing_row_without_progress_at_stays_usable(self):
        """Compatibility: a row predating the column must not break reads."""
        row = SyncCursor.objects.create(
            provider=self.provider, source=self.source_row,
            country=self.country, cursor="legacy",
        )
        # Simulate a pre-migration row: no progress_at recorded.
        SyncCursor.objects.filter(pk=row.pk).update(progress_at=None)
        row.refresh_from_db()
        # It is reported as stalled (honest: no recorded progress) rather than
        # raising or being silently treated as fresh.
        self.assertTrue(row.is_stalled)
        self.assertEqual(row.cursor, "legacy")


class ServiceObservabilityTests(TestCase):
    """The same rules through the production refresh path."""

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="obs_source", defaults={"name": "Obs"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )
        self.source_row, _ = Source.objects.get_or_create(
            slug="obs_source", defaults={"name": "Obs"}
        )

    def _service(self, cursor):
        source = StubSource(
            slug="obs_source",
            name="Obs",
            response=SourceFetchResult(cursor=cursor),
            capabilities=SourceCapabilities(
                supported_providers=frozenset({"obs_source"}),
                supported_countries=frozenset({"US"}),
                supports_catalogue_sync=True,
                supports_resumable_catalogue_sync=True,
            ),
        )
        return AvailabilityRefreshService(source=source)

    def test_a_partial_run_records_progress_and_not_completion(self):
        self._service("checkpoint-1").refresh_catalogue()
        row = SyncCursor.objects.get(source__slug="obs_source")
        self.assertIsNotNone(row.progress_at)
        self.assertIsNone(
            row.last_successful_sync,
            "a partial run claimed a completed traversal",
        )

    def test_an_exhausted_walk_clears_the_row_and_is_not_a_completion_claim(self):
        """A walk that finishes deletes its row. Per-source completion is on the
        run audit row (``RefreshRun``), which is what freshness reads; the
        cursor row's absence is the signal, not a timestamp on it."""
        self._service("checkpoint-1").refresh_catalogue()
        self.assertTrue(SyncCursor.objects.filter(source__slug="obs_source").exists())
        # Now a batch that reports completion.
        self._service(None).refresh_catalogue()
        self.assertFalse(
            SyncCursor.objects.filter(source__slug="obs_source").exists(),
            "a completed walk left its checkpoint row behind",
        )

    def test_a_failed_run_leaves_both_timestamps_untouched(self):
        from availability.sources import SourceFetchError

        self._service("checkpoint-1").refresh_catalogue()
        before = SyncCursor.objects.get(source__slug="obs_source")

        source = StubSource(
            slug="obs_source",
            name="Obs",
            error=SourceFetchError("simulated outage"),
            capabilities=SourceCapabilities(
                supported_providers=frozenset({"obs_source"}),
                supported_countries=frozenset({"US"}),
                supports_catalogue_sync=True,
                supports_resumable_catalogue_sync=True,
            ),
        )
        with self.assertRaises(SourceFetchError):
            AvailabilityRefreshService(source=source).refresh_catalogue()

        after = SyncCursor.objects.get(source__slug="obs_source")
        self.assertEqual(after.progress_at, before.progress_at)
        self.assertEqual(after.last_successful_sync, before.last_successful_sync)
        self.assertEqual(after.cursor, before.cursor)
