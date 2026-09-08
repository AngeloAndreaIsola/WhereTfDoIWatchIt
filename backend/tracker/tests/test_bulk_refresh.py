"""Tests for bulk availability refresh service, management command, and API endpoint."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ..models import ApiCallCounter, Availability, Movie, Region, WatchlistItem
from ..services.bulk_refresh import process_watchlist_refresh
from ..services.cache import AvailabilityCache, CacheStatus
from ..services.rate_limit import RateLimitExceeded, RateLimiter


class BulkRefreshServiceTests(TestCase):
    def setUp(self):
        self.movie1 = Movie.objects.create(tmdb_id=101, title="Movie 1", year=2020)
        self.movie2 = Movie.objects.create(tmdb_id=102, title="Movie 2", year=2021)
        self.movie3 = Movie.objects.create(tmdb_id=103, title="Movie 3", year=2022)

        WatchlistItem.objects.create(user_id=1, movie=self.movie1)
        WatchlistItem.objects.create(user_id=1, movie=self.movie2)
        WatchlistItem.objects.create(user_id=1, movie=self.movie3)

        self.region = Region.objects.create(provider="netflix", country="US")

    def test_fresh_cached_movies_are_skipped(self):
        now = timezone.now()
        # All movies are fresh
        Availability.objects.create(movie=self.movie1, region=self.region, last_checked_at=now, is_available=True)
        Availability.objects.create(movie=self.movie2, region=self.region, last_checked_at=now, is_available=True)
        Availability.objects.create(movie=self.movie3, region=self.region, last_checked_at=now, is_available=True)

        summary = process_watchlist_refresh(budget=10, user_id=1)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["skipped_cached"], 3)
        self.assertEqual(summary["remaining_budget"], 10)
        self.assertFalse(summary["rate_limited"])

    def test_stale_movies_refreshed_up_to_budget(self):
        stale_time = timezone.now() - dt.timedelta(days=2)
        Availability.objects.create(movie=self.movie1, region=self.region, last_checked_at=stale_time)
        Availability.objects.create(movie=self.movie2, region=self.region, last_checked_at=stale_time)
        # movie3 has no availability (stalest)

        mock_cache = MagicMock(spec=AvailabilityCache)
        mock_cache.limiter = MagicMock()
        mock_cache.limiter.remaining.return_value = 100
        mock_cache.is_stale.return_value = True
        mock_cache.get_or_refresh.return_value = CacheStatus(refreshed=True, reason="stale", offers_written=2)

        summary = process_watchlist_refresh(budget=2, user_id=1, cache=mock_cache)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        self.assertEqual(summary["remaining_budget"], 0)
        self.assertFalse(summary["rate_limited"])

    def test_rate_limit_stopping(self):
        mock_cache = MagicMock(spec=AvailabilityCache)
        mock_cache.limiter = MagicMock()
        mock_cache.limiter.remaining.return_value = 100
        mock_cache.is_stale.return_value = True
        mock_cache.get_or_refresh.side_effect = RateLimitExceeded("rate limit")

        summary = process_watchlist_refresh(budget=10, user_id=1, cache=mock_cache)

        self.assertEqual(summary["attempted"], 3)
        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["skipped_rate_limited"], 3)

    def test_per_movie_error_resilience(self):
        mock_cache = MagicMock(spec=AvailabilityCache)
        mock_cache.limiter = MagicMock()
        mock_cache.limiter.remaining.return_value = 100
        mock_cache.is_stale.return_value = True

        # movie 1 raises unexpected Exception, movie 2 & 3 succeed
        mock_cache.get_or_refresh.side_effect = [
            ValueError("unexpected DB error"),
            CacheStatus(refreshed=True, reason="stale", offers_written=1),
            CacheStatus(refreshed=True, reason="stale", offers_written=1),
        ]

        summary = process_watchlist_refresh(budget=10, user_id=1, cache=mock_cache)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["remaining_budget"], 8)
        self.assertFalse(summary["rate_limited"])


class ManagementCommandTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=201, title="Command Movie", year=2021)
        WatchlistItem.objects.create(user_id=1, movie=self.movie)

    @patch("tracker.management.commands.refresh_availability.process_watchlist_refresh")
    def test_command_passes_budget_and_runs(self, mock_process):
        mock_process.return_value = {
            "attempted": 1,
            "refreshed": 1,
            "skipped_cached": 0,
            "skipped_budget_exhausted": 0,
            "skipped_rate_limited": 0,
            "errors": 0,
            "budget": 50,
            "remaining_budget": 49,
            "rate_limited": False,
        }

        call_command("refresh_availability", "--budget", "50")

        mock_process.assert_called_once_with(budget=50, user_id=1, content_types=["movie", "series"])


class WatchlistRefreshApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.movie = Movie.objects.create(tmdb_id=301, title="API Movie", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=self.movie)

    @patch("tracker.views.process_watchlist_refresh")
    def test_valid_request(self, mock_process):
        mock_process.return_value = {
            "attempted": 1,
            "refreshed": 1,
            "skipped_cached": 0,
            "skipped_budget_exhausted": 0,
            "skipped_rate_limited": 0,
            "errors": 0,
            "budget": 10,
            "remaining_budget": 9,
            "rate_limited": False,
        }

        resp = self.client.post("/api/watchlist/refresh/", {"budget": 10}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["refreshed"], 1)
        mock_process.assert_called_once_with(budget=10, user_id=1)

    def test_invalid_budget_types_and_ranges(self):
        # Budget <= 0
        resp = self.client.post("/api/watchlist/refresh/", {"budget": 0}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Budget > 90
        resp = self.client.post("/api/watchlist/refresh/", {"budget": 100}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # Budget non-int (string/bool)
        resp = self.client.post("/api/watchlist/refresh/", {"budget": "invalid"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.post("/api/watchlist/refresh/", {"budget": True}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("tracker.views.process_watchlist_refresh")
    def test_rate_limited_response(self, mock_process):
        mock_process.return_value = {
            "attempted": 1,
            "refreshed": 0,
            "skipped_cached": 0,
            "skipped_budget_exhausted": 0,
            "skipped_rate_limited": 1,
            "errors": 0,
            "budget": 10,
            "remaining_budget": 10,
            "rate_limited": True,
        }

        resp = self.client.post("/api/watchlist/refresh/", {"budget": 10}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertTrue(resp.data["rate_limited"])
