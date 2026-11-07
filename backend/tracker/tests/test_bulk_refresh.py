"""Tests for bulk availability refresh service, management command, and API endpoint."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from ..models import ApiCallCounter, Movie, WatchlistItem
from ..services.bulk_refresh import (
    is_stale,
    process_catalogue_refresh,
    process_watchlist_refresh,
)
from ..services.rate_limit import RateLimitExceeded, RateLimiter
from .availability_factories import local_offer


def _stale_offers(movie, when):
    """Backdate the canonical offers of *movie* so it reads as stale."""
    from availability.models import Offer

    Offer.objects.filter(tmdb_id=movie.tmdb_id).update(last_seen=when)


class BulkRefreshServiceTests(TestCase):
    def setUp(self):
        self.movie1 = Movie.objects.create(tmdb_id=101, title="Movie 1", year=2020)
        self.movie2 = Movie.objects.create(tmdb_id=102, title="Movie 2", year=2021)
        self.movie3 = Movie.objects.create(tmdb_id=103, title="Movie 3", year=2022)

        WatchlistItem.objects.create(user_id=1, movie=self.movie1)
        WatchlistItem.objects.create(user_id=1, movie=self.movie2)
        WatchlistItem.objects.create(user_id=1, movie=self.movie3)

        self.refresh_service = MagicMock()
        self.limiter = MagicMock(spec=RateLimiter)
        self.limiter.remaining.return_value = 100

    def _run(self, budget=10, **kwargs):
        kwargs.setdefault("refresh_service", self.refresh_service)
        kwargs.setdefault("limiter", self.limiter)
        return process_watchlist_refresh(budget=budget, user_id=1, **kwargs)

    def test_watchmode_cannot_enter_the_catalogue_path(self):
        """The catalogue lane is structurally unable to make a Watchmode call.

        ``process_pending_catalogue_refresh`` runs in the same worker tick as
        the title drain. That is safe only while Watchmode remains title-only:
        the explicit ``--source watchmode`` override must be rejected by
        capability before any client is invoked, not merely fail later inside
        a fetch.
        """
        from availability.sources.watchmode_client import WatchmodeClient

        with patch.object(WatchmodeClient, "_get") as http_get:
            with self.assertRaises(ValueError):
                process_catalogue_refresh(source_slug="watchmode")

        http_get.assert_not_called()

    def test_fresh_canonical_offers_are_skipped(self):
        # Fresh canonical availability for every movie; nothing to refresh.
        local_offer(self.movie1)
        local_offer(self.movie2)
        local_offer(self.movie3)

        summary = self._run()

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["refreshed"], 0)
        self.assertEqual(summary["skipped_cached"], 3)
        self.assertEqual(summary["remaining_budget"], 10)
        self.assertFalse(summary["rate_limited"])
        self.refresh_service.refresh_content.assert_not_called()

    def test_stale_movies_refreshed_up_to_budget(self):
        stale_time = timezone.now() - dt.timedelta(days=2)
        local_offer(self.movie1)
        local_offer(self.movie2)
        _stale_offers(self.movie1, stale_time)
        _stale_offers(self.movie2, stale_time)
        # movie3 has no canonical offers (stalest — never checked)

        summary = self._run(budget=2)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["skipped_budget_exhausted"], 1)
        self.assertEqual(summary["remaining_budget"], 0)
        self.assertFalse(summary["rate_limited"])
        # Never-checked movie sorts first, so the two refreshed are 103 + one stale.
        refreshed_ids = {
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        }
        self.assertIn(103, refreshed_ids)

    def test_never_checked_candidates_are_refreshed_before_stale_ones(self):
        stale_time = timezone.now() - dt.timedelta(days=2)
        local_offer(self.movie1)
        _stale_offers(self.movie1, stale_time)

        summary = self._run(budget=1)

        refreshed = [
            call.args[0].tmdb_id
            for call in self.refresh_service.refresh_content.call_args_list
        ]
        self.assertEqual(refreshed, [102])
        self.assertEqual(summary["refreshed"], 1)

    def test_rate_limit_stopping(self):
        self.limiter.acquire.side_effect = RateLimitExceeded("rate limit")

        summary = self._run(budget=10)

        self.assertEqual(summary["attempted"], 3)
        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["skipped_rate_limited"], 3)
        self.refresh_service.refresh_content.assert_not_called()

    def test_upfront_exhausted_quota_skips_everything(self):
        self.limiter.remaining.return_value = 0

        summary = self._run(budget=10)

        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["skipped_rate_limited"], 3)
        self.assertEqual(summary["refreshed"], 0)

    def test_per_movie_error_resilience(self):
        # movie 1 raises unexpected Exception, movie 2 & 3 succeed
        self.refresh_service.refresh_content.side_effect = [
            ValueError("unexpected DB error"),
            MagicMock(),
            MagicMock(),
        ]

        summary = self._run(budget=10)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["remaining_budget"], 7)
        self.assertFalse(summary["rate_limited"])

    def test_freshness_comes_from_canonical_last_seen(self):
        self.assertTrue(is_stale(self.movie1))  # never refreshed canonically
        local_offer(self.movie1)
        self.assertFalse(is_stale(self.movie1))
        _stale_offers(self.movie1, timezone.now() - dt.timedelta(hours=48))
        self.assertTrue(is_stale(self.movie1))


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


class RateLimiterIntegrationTests(TestCase):
    """The migrated path still reserves a call per live refresh."""

    def test_acquire_increments_the_shared_counter(self):
        limiter = RateLimiter("test_bulk_refresh_migration")
        ApiCallCounter.objects.filter(service="test_bulk_refresh_migration").delete()
        limiter.acquire()
        self.assertEqual(ApiCallCounter.objects.get(service="test_bulk_refresh_migration").count, 1)
        self.assertEqual(limiter.remaining(), 99)
