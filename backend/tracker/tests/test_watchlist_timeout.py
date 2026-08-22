"""Regression tests for watchlist report timeout fix.

The watchlist_report endpoint must not block on external API calls.
These tests verify that it uses only cached data and handles all edge cases
without timing out.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    Availability,
    LanguageEntry,
    LanguageInfo,
    Movie,
    Preference,
    Region,
    WatchlistItem,
)
from tracker.services.cache import AvailabilityCache


class WatchlistReportNonBlockingTests(TestCase):
    """Verify watchlist_report never blocks on external API calls."""

    def setUp(self):
        """Create test movies and watchlist items."""
        self.movie1 = Movie.objects.create(tmdb_id=1, title="Movie 1", year=2020)
        self.movie2 = Movie.objects.create(tmdb_id=2, title="Movie 2", year=2021)
        self.movie3 = Movie.objects.create(tmdb_id=3, title="Movie 3", year=2022)

        WatchlistItem.objects.create(user_id=1, movie=self.movie1)
        WatchlistItem.objects.create(user_id=1, movie=self.movie2)
        WatchlistItem.objects.create(user_id=1, movie=self.movie3)

        Preference.objects.create(
            user_id=1,
            audio_languages=["EN"],
            subtitle_languages=["EN"],
            candidate_countries=["US"],
        )

    def _create_fresh_availability(self, movie: Movie, country: str = "US", provider: str = "prime"):
        """Create fresh (non-stale) availability for a movie."""
        region = Region.objects.create(provider=provider, country=country)
        Availability.objects.create(
            movie=movie,
            region=region,
            last_checked_at=timezone.now(),
            is_available=True,
            offer_type="subscription",
            quality="hd",
        )
        info = LanguageInfo.objects.create(movie=movie, region=region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="EN")
        LanguageEntry.objects.create(language_info=info, kind="subtitle", language_code="EN")

    def _create_stale_availability(self, movie: Movie, country: str = "US", provider: str = "prime"):
        """Create stale availability for a movie."""
        region = Region.objects.create(provider=provider, country=country)
        Availability.objects.create(
            movie=movie,
            region=region,
            last_checked_at=timezone.now() - timedelta(hours=48),  # Stale after 24h
            is_available=True,
            offer_type="subscription",
            quality="hd",
        )
        info = LanguageInfo.objects.create(movie=movie, region=region)
        LanguageEntry.objects.create(language_info=info, kind="audio", language_code="EN")

    @patch("tracker.views.AvailabilityCache")
    def test_report_with_fresh_cached_data_does_not_call_external_api(self, mock_cache_class):
        """Report with fresh cached data should not trigger external API calls."""
        self._create_fresh_availability(self.movie1)
        self._create_fresh_availability(self.movie2)
        self._create_fresh_availability(self.movie3)

        # Create a real cache instance but mock the streaming API client
        cache = AvailabilityCache()
        mock_client = MagicMock()
        cache.client = mock_client
        mock_cache_class.return_value = cache

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should never be called for fresh data
        mock_client.get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        self.assertEqual(data["summary"]["available_movies"], 3)

    @patch("tracker.views.AvailabilityCache")
    def test_report_with_stale_data_does_not_block(self, mock_cache_class):
        """Report with stale cached data should not block on refresh."""
        self._create_stale_availability(self.movie1)
        self._create_stale_availability(self.movie2)

        # Create a real cache instance but mock the streaming API client
        cache = AvailabilityCache()
        mock_client = MagicMock()
        cache.client = mock_client
        mock_cache_class.return_value = cache

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called even for stale data
        mock_client.get_availability.assert_not_called()

        # Stale data should still be returned
        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # Movies with stale availability should still appear
        self.assertEqual(data["summary"]["available_movies"], 2)

    @patch("tracker.views.AvailabilityCache")
    def test_report_with_missing_data_does_not_block(self, mock_cache_class):
        """Report with missing cached data should not block waiting for API."""
        # No availability created for any movie (all missing)

        cache = AvailabilityCache()
        mock_client = MagicMock()
        cache.client = mock_client
        mock_cache_class.return_value = cache

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called for missing data
        mock_client.get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # No movies should have availability
        self.assertEqual(data["summary"]["available_movies"], 0)

    @patch("tracker.views.AvailabilityCache")
    def test_report_with_mixed_cache_states(self, mock_cache_class):
        """Report handles mix of fresh, stale, and missing data without blocking."""
        self._create_fresh_availability(self.movie1)
        self._create_stale_availability(self.movie2)
        # movie3 has no availability (missing)

        cache = AvailabilityCache()
        mock_client = MagicMock()
        cache.client = mock_client
        mock_cache_class.return_value = cache

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called regardless of cache state mix
        mock_client.get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # Two movies have some availability
        self.assertEqual(data["summary"]["available_movies"], 2)

    @patch("tracker.views.AvailabilityCache.get_cached_only")
    def test_external_api_failure_does_not_crash_report(self, mock_get_cached):
        """External API failures should not crash the entire report."""
        # Simulate get_cached_only raising an exception
        mock_get_cached.side_effect = Exception("External API timeout")

        # Report should still return successfully
        resp = self.client.get("/api/watchlist/report/")

        # Should not return 500, should handle gracefully
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Report should still be generated (though movies may have no availability)
        self.assertIn("report", data)

    @patch("tracker.views.AvailabilityCache.get_cached_only")
    def test_partial_cache_failure_does_not_crash_report(self, mock_get_cached):
        """Partial cache failures should not crash the entire report."""
        self._create_fresh_availability(self.movie1)

        # First call succeeds, second fails, third succeeds
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Cache read failure")
            # Return a real cache status for other calls
            cache = AvailabilityCache()
            return cache.get_cached_only(args[0])

        mock_get_cached.side_effect = side_effect

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Report should still be generated
        self.assertIn("report", data)


class AvailabilityCacheNonBlockingMethodTests(TestCase):
    """Test AvailabilityCache.get_cached_only method."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=123, title="Test Movie", year=2020)
        self.cache = AvailabilityCache()

    def test_get_cached_only_with_fresh_data(self):
        """get_cached_only returns fresh status without calling API."""
        region = Region.objects.create(provider="prime", country="US")
        Availability.objects.create(
            movie=self.movie,
            region=region,
            last_checked_at=timezone.now(),
            is_available=True,
            offer_type="subscription",
            quality="hd",
        )

        with patch.object(self.cache.client, "get_availability") as mock_api:
            status = self.cache.get_cached_only(self.movie)

            # Should not call external API
            mock_api.assert_not_called()
            self.assertEqual(status.reason, "fresh")
            self.assertEqual(status.offers_written, 1)
            self.assertFalse(status.refreshed)

    def test_get_cached_only_with_stale_data(self):
        """get_cached_only returns stale status without calling API."""
        region = Region.objects.create(provider="prime", country="US")
        Availability.objects.create(
            movie=self.movie,
            region=region,
            last_checked_at=timezone.now() - timedelta(hours=48),
            is_available=True,
            offer_type="subscription",
            quality="hd",
        )

        with patch.object(self.cache.client, "get_availability") as mock_api:
            status = self.cache.get_cached_only(self.movie)

            # Should not call external API even for stale data
            mock_api.assert_not_called()
            self.assertEqual(status.reason, "stale")
            self.assertEqual(status.offers_written, 1)
            self.assertFalse(status.refreshed)

    def test_get_cached_only_with_missing_data(self):
        """get_cached_only returns missing status without calling API."""
        # No availability created

        with patch.object(self.cache.client, "get_availability") as mock_api:
            status = self.cache.get_cached_only(self.movie)

            # Should not call external API for missing data
            mock_api.assert_not_called()
            self.assertEqual(status.reason, "missing")
            self.assertEqual(status.offers_written, 0)
            self.assertFalse(status.refreshed)
