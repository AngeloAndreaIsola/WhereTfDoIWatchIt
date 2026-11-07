"""Writer-barrier coverage for ``DELETE /api/providers/<slug>/``.

These are **control-flow tests**: SQLite cannot exercise PostgreSQL advisory
locks, so the barrier primitives are mocked.  They prove that the endpoint
acquires the real ``writer_barrier`` before opening its transaction, holds it
through every canonical deletion and the provider row itself, releases it on
success and on failure, refuses to delete when acquisition fails, and does not
nest a second session lock when the legacy ``post_delete`` mirror runs.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from availability.domain.enums import (
    ContentType,
    OfferType,
    SourceType,
    SyncMode,
)
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)
from availability.services import write_barrier
from availability.services.legacy_ingest import ingest_scraped_mapping
from tracker.models import Movie, Provider, ScrapedContentMapping


@contextmanager
def _tracked_atomic(events, real_atomic):
    events.append("atomic_enter")
    with real_atomic():
        yield
    events.append("atomic_exit")


class ProviderDeleteBarrierTests(TestCase):
    def tearDown(self):
        write_barrier._barrier_state.depth = 0

    def _seed_provider(self, slug, tmdb_id):
        provider = Provider.objects.create(
            slug=slug, name=slug.title(), category="scraper"
        )
        movie = Movie.objects.create(
            tmdb_id=tmdb_id, title=f"{slug} Film", year=2020
        )
        legacy = ScrapedContentMapping.objects.create(
            provider=provider,
            scraped_title=f"{slug} Film",
            scraped_year=2020,
            movie=movie,
            is_confirmed=True,
            confidence_score=1.0,
        )
        ingest_scraped_mapping(legacy)

        source = Source.objects.create(
            slug=f"{slug}-source", name=f"{slug} source", type=SourceType.API
        )
        country, _ = Country.objects.get_or_create(
            code="GB", defaults={"name": "United Kingdom", "enabled": True}
        )
        run = SyncRun.objects.create(
            provider=provider,
            source=source,
            country=country,
            mode=SyncMode.FULL,
        )
        Offer.objects.create(
            provider=provider,
            country=country,
            source=source,
            sync_run=run,
            content_type=ContentType.MOVIE,
            tmdb_id=tmdb_id,
            offer_type=OfferType.FREE,
            provider_content_id=f"legacy-{legacy.pk}",
        )
        SyncCursor.objects.create(
            provider=provider, source=source, country=country, cursor="page-1"
        )
        return provider, legacy

    def _assert_provider_rows_present(self, provider, legacy):
        self.assertTrue(Provider.objects.filter(pk=provider.pk).exists())
        self.assertTrue(
            ScrapedContentMapping.objects.filter(pk=legacy.pk).exists()
        )
        self.assertTrue(
            ProviderContent.objects.filter(provider=provider).exists()
        )
        self.assertTrue(ContentMapping.objects.filter(provider=provider).exists())
        self.assertTrue(Offer.objects.filter(provider=provider).exists())
        self.assertTrue(SyncRun.objects.filter(provider=provider).exists())
        self.assertTrue(SyncCursor.objects.filter(provider=provider).exists())

    def test_failed_acquisition_prevents_every_deletion(self):
        provider, legacy = self._seed_provider("hydra", 900)
        other, other_legacy = self._seed_provider("other", 901)

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=False
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock:
            response = self.client.delete(f"/api/providers/{provider.slug}/")

        self.assertEqual(response.status_code, 503)
        self.assertIn("write barrier", response.json()["error"])
        self._assert_provider_rows_present(provider, legacy)
        self._assert_provider_rows_present(other, other_legacy)
        unlock.assert_not_called()

    def test_barrier_is_acquired_before_the_transaction_and_released_after_it(self):
        provider, legacy = self._seed_provider("hydra", 900)
        events = []
        real_atomic = transaction.atomic

        def tracking_atomic(*args, **kwargs):
            return _tracked_atomic(events, lambda: real_atomic(*args, **kwargs))

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier,
            "_try_lock",
            side_effect=lambda *, exclusive: events.append("lock") or True,
        ) as try_lock, patch.object(
            write_barrier,
            "_unlock",
            side_effect=lambda *, exclusive: events.append("unlock"),
        ), patch.object(
            transaction,
            "atomic",
            tracking_atomic,
        ):
            response = self.client.delete(f"/api/providers/{provider.slug}/")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(events[0], "lock")
        self.assertEqual(events[1], "atomic_enter")
        self.assertEqual(events[-1], "unlock")
        # The last atomic block closed before the one unlock.
        self.assertEqual(events[-2], "atomic_exit")
        # The legacy post_delete mirror must reuse the outer acquisition.
        try_lock.assert_called_once_with(exclusive=False)
        self.assertFalse(Provider.objects.filter(pk=provider.pk).exists())
        self.assertFalse(
            ScrapedContentMapping.objects.filter(pk=legacy.pk).exists()
        )
        self.assertFalse(ProviderContent.objects.filter(provider_id=provider.pk).exists())
        self.assertFalse(ContentMapping.objects.filter(provider_id=provider.pk).exists())
        self.assertFalse(Offer.objects.filter(provider_id=provider.pk).exists())
        self.assertFalse(SyncRun.objects.filter(provider_id=provider.pk).exists())
        self.assertFalse(SyncCursor.objects.filter(provider_id=provider.pk).exists())

    def test_exception_rolls_back_deletions_and_releases_the_barrier(self):
        provider, legacy = self._seed_provider("hydra", 900)

        with patch.object(
            write_barrier, "advisory_locks_supported", return_value=True
        ), patch.object(
            write_barrier, "_try_lock", return_value=True
        ), patch.object(
            write_barrier, "_unlock"
        ) as unlock, patch.object(
            Provider, "delete", side_effect=RuntimeError("provider delete failed")
        ):
            with self.assertRaises(RuntimeError):
                self.client.delete(f"/api/providers/{provider.slug}/")

        # The canonical deletes ran before the failure and were rolled back.
        self._assert_provider_rows_present(provider, legacy)
        unlock.assert_called_once_with(exclusive=False)

    def test_major_provider_is_refused_before_the_barrier(self):
        provider, legacy = self._seed_provider("major", 902)
        provider.is_major = True
        provider.save(update_fields=["is_major"])

        with patch.object(
            write_barrier, "_try_lock"
        ) as try_lock:
            response = self.client.delete(f"/api/providers/{provider.slug}/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Major canonical providers", response.json()["error"])
        self._assert_provider_rows_present(provider, legacy)
        try_lock.assert_not_called()

    def test_successful_delete_leaves_an_unrelated_provider_intact(self):
        provider, _legacy = self._seed_provider("hydra", 900)
        other, other_legacy = self._seed_provider("other", 901)

        response = self.client.delete(f"/api/providers/{provider.slug}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Provider.objects.filter(pk=provider.pk).exists())
        self._assert_provider_rows_present(other, other_legacy)
