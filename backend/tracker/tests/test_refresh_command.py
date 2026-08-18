"""Tests for refresh_availability management command and RefreshRun audit model."""
import io
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from tracker.models import Movie, RefreshRun, WatchlistItem
from tracker.services.cache import CacheStatus


class RefreshCommandTests(TestCase):
    def setUp(self):
        self.movie1 = Movie.objects.create(tmdb_id=101, title="Test Movie 1", year=2021)
        self.movie2 = Movie.objects.create(tmdb_id=102, title="Test Movie 2", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=self.movie1)
        WatchlistItem.objects.create(user_id=1, movie=self.movie2)

    @patch("tracker.services.bulk_refresh.AvailabilityCache")
    def test_refresh_command_creates_refresh_run(self, mock_cache_cls):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        mock_cache.limiter.remaining.return_value = 100

        # movie1 is stale and gets refreshed, movie2 is fresh and gets skipped
        mock_cache.is_stale.side_effect = lambda movie: movie.id == self.movie1.id
        mock_cache.get_or_refresh.return_value = CacheStatus(
            refreshed=True, reason="stale", offers_written=2
        )

        out = io.StringIO()
        call_command("refresh_availability", "--budget", "10", stdout=out)

        self.assertEqual(RefreshRun.objects.count(), 1)
        run = RefreshRun.objects.first()
        self.assertEqual(run.service, "streaming_availability")
        self.assertEqual(run.rows_refreshed, 1)
        self.assertEqual(run.rows_skipped, 1)
        self.assertFalse(run.rate_limited)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        self.assertIn("RefreshRun streaming_availability", str(run))

    @patch("tracker.services.bulk_refresh.AvailabilityCache")
    def test_refresh_command_handles_rate_limit_status(self, mock_cache_cls):
        mock_cache = MagicMock()
        mock_cache_cls.return_value = mock_cache
        mock_cache.limiter.remaining.return_value = 100

        mock_cache.is_stale.return_value = True
        mock_cache.get_or_refresh.return_value = CacheStatus(
            refreshed=False, reason="rate_limited", offers_written=0
        )

        out = io.StringIO()
        call_command("refresh_availability", "--budget", "10", stdout=out)

        self.assertEqual(RefreshRun.objects.count(), 1)
        run = RefreshRun.objects.first()
        self.assertEqual(run.service, "streaming_availability")
        self.assertEqual(run.rows_refreshed, 0)
        self.assertTrue(run.rate_limited)
        self.assertIsNotNone(run.finished_at)
