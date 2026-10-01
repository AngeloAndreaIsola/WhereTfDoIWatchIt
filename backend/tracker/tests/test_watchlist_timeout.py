"""Regression tests for watchlist report timeout fix.

The watchlist_report endpoint must not block on external API calls.
These tests verify that it uses only cached data and handles all edge cases
without timing out.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import Offer
from tracker.models import Movie, Preference, WatchlistItem
from tracker.tests.availability_factories import local_offer


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

    def _create_fresh_availability(self, movie: Movie):
        """Create fresh (non-stale) availability for a movie."""
        local_offer(
            movie,
            country="US",
            service="prime",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

    def _create_stale_availability(self, movie: Movie):
        """Create stale availability for a movie."""
        offer = local_offer(
            movie,
            country="US",
            service="prime",
            audio_languages=["EN"],
        )
        Offer.objects.filter(pk=offer.pk).update(
            last_seen=timezone.now() - timedelta(hours=48)  # Stale after 24h
        )

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    def test_report_with_fresh_cached_data_does_not_call_external_api(self, mock_get_availability):
        """Report with fresh cached data should not trigger external API calls."""
        self._create_fresh_availability(self.movie1)
        self._create_fresh_availability(self.movie2)
        self._create_fresh_availability(self.movie3)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should never be called for fresh data
        mock_get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        self.assertEqual(data["summary"]["available_movies"], 3)

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    def test_report_with_stale_data_does_not_block(self, mock_get_availability):
        """Report with stale cached data should not block on refresh."""
        self._create_stale_availability(self.movie1)
        self._create_stale_availability(self.movie2)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called even for stale data
        mock_get_availability.assert_not_called()

        # Stale data should still be returned
        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # Movies with stale availability should still appear
        self.assertEqual(data["summary"]["available_movies"], 2)

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    def test_report_with_missing_data_does_not_block(self, mock_get_availability):
        """Report with missing cached data should not block waiting for API."""
        # No availability created for any movie (all missing)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called for missing data
        mock_get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # No movies should have availability
        self.assertEqual(data["summary"]["available_movies"], 0)

    @patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability")
    def test_report_with_mixed_cache_states(self, mock_get_availability):
        """Report handles mix of fresh, stale, and missing data without blocking."""
        self._create_fresh_availability(self.movie1)
        self._create_stale_availability(self.movie2)
        # movie3 has no availability (missing)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        # External API should NOT be called regardless of cache state mix
        mock_get_availability.assert_not_called()

        data = resp.json()
        self.assertEqual(len(data["report"]), 3)
        # Two movies have some availability
        self.assertEqual(data["summary"]["available_movies"], 2)

    def test_external_api_failure_does_not_crash_report(self):
        """Offer read failures should not crash the entire report."""
        # Simulate the canonical offer read raising an exception
        with patch(
            "tracker.views.get_local_offers",
            side_effect=Exception("External API timeout"),
        ):
            # Report should still return successfully
            resp = self.client.get("/api/watchlist/report/")

        # Should not return 500, should handle gracefully
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Report should still be generated (movies appear without availability)
        self.assertIn("report", data)
        self.assertEqual(len(data["report"]), 3)

    def test_partial_cache_failure_does_not_crash_report(self):
        """A failure on one movie's read should not crash the entire report."""
        from tracker.services.availability_reads import get_local_offers as real_get_local_offers

        self._create_fresh_availability(self.movie1)

        # First call succeeds, second fails, third succeeds
        call_count = [0]

        def side_effect(movie, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Cache read failure")
            return real_get_local_offers(movie, **kwargs)

        with patch("tracker.views.get_local_offers", side_effect=side_effect):
            resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Report should still be generated for every movie
        self.assertIn("report", data)
        self.assertEqual(len(data["report"]), 3)
        self.assertEqual(call_count[0], 3)
