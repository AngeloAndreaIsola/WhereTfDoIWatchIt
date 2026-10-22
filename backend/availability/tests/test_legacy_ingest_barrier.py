"""Writer-barrier coverage for the legacy scraper ingestion bridge.

These are **control-flow tests**: SQLite has no PostgreSQL advisory locks, so
they patch the barrier's acquisition primitives and prove that the real
``writer_barrier`` context manager is entered before the write transaction,
held across it, released on every exit path, and that a refused acquisition
writes nothing.  They do not prove cross-session PostgreSQL lock semantics —
those are covered by the cross-connection tests in
``availability.tests.test_remediate_mapping_audit`` when run against an
isolated PostgreSQL test database.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from availability.models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    ContentMapping,
    ProviderContent,
    Source,
)
from availability.services import legacy_ingest, write_barrier
from availability.services.legacy_ingest import (
    delete_scraped_mapping,
    ingest_all,
    ingest_scraped_mapping,
)
from tracker.models import Movie, Provider, ScrapedContentMapping


@contextmanager
def _tracked_atomic(events, real_atomic):
    events.append("atomic_enter")
    with real_atomic():
        yield
    events.append("atomic_exit")


class LegacyIngestBarrierTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper"
        )

    def tearDown(self):
        # A failing assertion must never leak re-entrancy depth into the next
        # test in the same thread.
        write_barrier._barrier_state.depth = 0

    def _row(self, **overrides):
        defaults = {
            "provider": self.provider,
            "scraped_title": "Scraper Movie",
            "scraped_year": 2022,
            "scraped_url": "https://hydra.example/movie",
            "is_available": True,
            "country_code": "GB",
        }
        defaults.update(overrides)
        return ScrapedContentMapping.objects.create(**defaults)

    def _tracking_atomic(self, events):
        real_atomic = transaction.atomic

        def tracking_atomic(*args, **kwargs):
            return _tracked_atomic(events, lambda: real_atomic(*args, **kwargs))

        return tracking_atomic

    def test_barrier_is_acquired_before_the_transaction_and_held_until_it_exits(self):
        mapping = self._row()
        events = []

        def try_lock(*, exclusive):
            events.append(f"lock_exclusive={exclusive}")
            return True

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", side_effect=try_lock
        ), patch.object(
            write_barrier, "_unlock", side_effect=lambda *, exclusive: events.append(
                f"unlock_exclusive={exclusive}"
            )
        ), patch.object(
            legacy_ingest.transaction,
            "atomic",
            self._tracking_atomic(events),
        ):
            content = ingest_scraped_mapping(mapping)

        # Repository helpers open their own savepoints, so the important
        # ordering is the outer boundary: the lock precedes the first atomic
        # block and is released only after the last one closes.
        self.assertEqual(events[0], "lock_exclusive=False")
        self.assertEqual(events[1], "atomic_enter")
        self.assertEqual(events[-2], "atomic_exit")
        self.assertEqual(events[-1], "unlock_exclusive=False")
        self.assertEqual(content.provider_content_id, f"legacy-{mapping.pk}")

    def test_failed_acquisition_performs_no_canonical_writes(self):
        mapping = self._row()

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=False
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                ingest_scraped_mapping(mapping)

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        # The provenance Source is a canonical write too; it must not appear.
        self.assertFalse(
            Source.objects.filter(slug=LEGACY_SCRAPER_SOURCE_SLUG).exists()
        )
        unlock.assert_not_called()

    def test_exception_rolls_back_and_still_releases_the_barrier(self):
        movie = Movie.objects.create(tmdb_id=900, title="Linked Film", year=2020)
        mapping = self._row(movie=movie, is_confirmed=True, confidence_score=0.99)

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock, patch.object(
            legacy_ingest.MappingRepository,
            "record",
            side_effect=RuntimeError("mapping write failed"),
        ):
            with self.assertRaises(RuntimeError):
                ingest_scraped_mapping(mapping)

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertFalse(
            Source.objects.filter(slug=LEGACY_SCRAPER_SOURCE_SLUG).exists()
        )
        unlock.assert_called_once_with(exclusive=False)

    def test_delete_refuses_when_the_barrier_cannot_be_acquired(self):
        mapping = self._row()
        ingest_scraped_mapping(mapping)
        self.assertEqual(ProviderContent.objects.count(), 1)

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=False
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            with self.assertRaises(write_barrier.MaintenanceInProgress):
                delete_scraped_mapping(mapping)

        self.assertEqual(ProviderContent.objects.count(), 1)
        self.assertEqual(ContentMapping.objects.count(), 0)
        unlock.assert_not_called()

    def test_successful_delete_holds_the_barrier_through_the_transaction(self):
        mapping = self._row()
        ingest_scraped_mapping(mapping)
        events = []

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier,
            "_try_lock",
            side_effect=lambda *, exclusive: events.append("lock") or True,
        ), patch.object(
            write_barrier, "_unlock", side_effect=lambda *, exclusive: events.append("unlock")
        ), patch.object(
            legacy_ingest.transaction,
            "atomic",
            self._tracking_atomic(events),
        ):
            delete_scraped_mapping(mapping)

        self.assertEqual(events[0], "lock")
        self.assertEqual(events[1], "atomic_enter")
        self.assertEqual(events[-2], "atomic_exit")
        self.assertEqual(events[-1], "unlock")
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)

    def test_delete_leaves_another_providers_projection_untouched(self):
        other = Provider.objects.create(
            slug="other", name="Other", category="scraper"
        )
        first = self._row()
        second = self._row(provider=other, scraped_title="Other Film")
        ingest_scraped_mapping(first)
        ingest_scraped_mapping(second)
        self.assertEqual(ProviderContent.objects.count(), 2)

        delete_scraped_mapping(first)

        remaining = ProviderContent.objects.get()
        self.assertEqual(remaining.provider, other)
        self.assertEqual(remaining.provider_content_id, f"legacy-{second.pk}")

    def test_ingest_all_acquires_the_barrier_once_per_item_without_nesting(self):
        self._row()
        self._row(scraped_title="Second Film", scraped_year=2020)
        lock_depths = []

        def try_lock(*, exclusive):
            lock_depths.append(getattr(write_barrier._barrier_state, "depth", 0))
            return True

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", side_effect=try_lock
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            count = ingest_all()

        self.assertEqual(count, 2)
        self.assertEqual(ProviderContent.objects.count(), 2)
        # Two sequential outer acquisitions, never a nested one (which would
        # report a depth of 1 on the second call and only one database lock).
        self.assertEqual(lock_depths, [0, 0])
        self.assertEqual(unlock.call_count, 2)
