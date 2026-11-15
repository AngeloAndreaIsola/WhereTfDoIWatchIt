"""Tests for refresh_availability management command and RefreshRun audit model."""
import datetime as dt
import io
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tracker.models import Movie, RefreshRun, WatchlistItem
from tracker.services.rate_limit import RateLimitExceeded, RateLimiter
from tracker.tests.availability_factories import local_offer

REFRESH_CLS = "tracker.services.bulk_refresh.AvailabilityRefreshService"
# The sweep resolves the default source's budget through
# ``rate_limiter_for``, so that is the factory a caller must stand in for.
LIMITER_CLS = "tracker.services.bulk_refresh.rate_limiter_for"


class RefreshCommandTests(TestCase):
    def setUp(self):
        self.movie1 = Movie.objects.create(tmdb_id=101, title="Test Movie 1", year=2021)
        self.movie2 = Movie.objects.create(tmdb_id=102, title="Test Movie 2", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=self.movie1)
        WatchlistItem.objects.create(user_id=1, movie=self.movie2)

        self.refresh_service = MagicMock()
        self.limiter = MagicMock(spec=RateLimiter)
        self.limiter.remaining.return_value = 100

    @patch(LIMITER_CLS)
    @patch(REFRESH_CLS)
    def test_refresh_command_creates_refresh_run(self, mock_service_cls, mock_limiter_cls):
        mock_service_cls.return_value = self.refresh_service
        mock_limiter_cls.return_value = self.limiter

        # movie2 is fresh (canonical offers observed now) and gets skipped;
        # movie1 has no canonical offers yet and gets refreshed.
        local_offer(self.movie2)

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

    @patch(LIMITER_CLS)
    @patch(REFRESH_CLS)
    def test_refresh_command_handles_rate_limit_status(self, mock_service_cls, mock_limiter_cls):
        mock_service_cls.return_value = self.refresh_service
        mock_limiter_cls.return_value = self.limiter
        self.limiter.acquire.side_effect = RateLimitExceeded("Rate limit reached")

        out = io.StringIO()
        call_command("refresh_availability", "--budget", "10", stdout=out)

        self.assertEqual(RefreshRun.objects.count(), 1)
        run = RefreshRun.objects.first()
        self.assertEqual(run.service, "streaming_availability")
        self.assertEqual(run.rows_refreshed, 0)
        self.assertTrue(run.rate_limited)
        self.assertIsNotNone(run.finished_at)
        self.refresh_service.refresh_content.assert_not_called()

    @patch(LIMITER_CLS)
    @patch(REFRESH_CLS)
    def test_stale_canonical_offers_are_refreshed_again(self, mock_service_cls, mock_limiter_cls):
        mock_service_cls.return_value = self.refresh_service
        mock_limiter_cls.return_value = self.limiter
        local_offer(self.movie1)
        local_offer(self.movie2)  # movie2 stays fresh
        from availability.models import Offer

        Offer.objects.filter(tmdb_id=101).update(
            last_seen=timezone.now() - dt.timedelta(hours=48)
        )

        out = io.StringIO()
        call_command("refresh_availability", "--budget", "10", stdout=out)

        refreshed = [
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        ]
        self.assertEqual(refreshed, [101])
