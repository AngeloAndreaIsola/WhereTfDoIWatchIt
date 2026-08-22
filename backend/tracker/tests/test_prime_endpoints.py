"""Smoke tests for the Prime Video MVP endpoints.

These tests use mock objects to verify the view logic without hitting
live TMDb or Streaming Availability APIs (which are gated behind API keys
and have strict rate limits).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Movie, Availability, Region, LanguageInfo, LanguageEntry


class PrimeUrlRoutingTests(TestCase):
    """Verify all expected API routes are wired up (no 404s)."""

    def test_prime_popular_route_resolves(self):
        url = reverse("prime-popular")
        self.assertEqual(url, "/api/prime/popular/")

    def test_prime_movie_route_resolves(self):
        url = reverse("prime-movie", kwargs={"tmdb_id": 550})
        self.assertEqual(url, "/api/prime/movie/550/")

    def test_health_route_resolves(self):
        url = reverse("health")
        self.assertEqual(url, "/api/health/")

    def test_search_route_resolves(self):
        url = reverse("search")
        self.assertEqual(url, "/api/search/")

    def test_preferences_route_resolves(self):
        url = reverse("preferences")
        self.assertEqual(url, "/api/preferences/")

    def test_watchlist_route_resolves(self):
        url = reverse("watchlist")
        self.assertEqual(url, "/api/watchlist/")


class PrimePopularEndpointTests(TestCase):
    """Tests for GET /api/prime/popular/."""

    def setUp(self):
        from tracker.views import _POPULAR_CACHE
        _POPULAR_CACHE.clear()

    def tearDown(self):
        from tracker.views import _POPULAR_CACHE
        _POPULAR_CACHE.clear()

    @patch("tracker.views.TMDbClient")
    def test_returns_200_with_results(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbMovie

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.return_value = [
            TMDbMovie(tmdb_id=1, title="Test Movie", year=2024,
                      poster_path="/test.jpg", overview="A test movie.", genre_ids=[28, 18]),
            TMDbMovie(tmdb_id=2, title="Another Movie", year=2023,
                      poster_path=None, overview=None, genre_ids=[35]),
        ]

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["source"], "tmdb_trending")
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["results"][0]["tmdb_id"], 1)
        self.assertEqual(data["results"][0]["title"], "Test Movie")
        self.assertEqual(data["results"][0]["year"], 2024)
        self.assertEqual(data["results"][0]["poster_path"], "/test.jpg")
        self.assertEqual(data["results"][0]["categories"], ["Action", "Drama"])
        self.assertEqual(data["results"][1]["categories"], ["Comedy"])

    @patch("tracker.views.TMDbClient")
    def test_respects_limit_param(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbMovie

        # Create 30 movies — mock should slice to limit
        movies = [
            TMDbMovie(tmdb_id=i, title=f"Movie {i}", year=2024,
                      poster_path=None, overview=None, genre_ids=[])
            for i in range(30)
        ]
        mock_client = mock_tmdb_cls.return_value
        # The real TMDbClient.popular() slices to limit; simulate that
        mock_client.popular.side_effect = lambda limit=24: movies[:limit]

        from django.test import Client
        response = Client().get("/api/prime/popular/?limit=5")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 5)

    @patch("tracker.views.TMDbClient")
    def test_returns_502_on_tmdb_failure(self, mock_tmdb_cls):
        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.side_effect = Exception("401 Unauthorized")

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertIn("TMDb fetch failed", data["error"])
        self.assertEqual(data["count"], 0)

    @patch("tracker.views.TMDbClient")
    def test_cache_serves_stale_data_on_failure(self, mock_tmdb_cls):
        """If TMDb fails but cache has data, serve cached results."""
        from tracker.services.tmdb import TMDbMovie
        from tracker.views import _POPULAR_CACHE

        # Pre-populate cache
        _POPULAR_CACHE["data"] = {
            "results": [{"tmdb_id": 1, "title": "Cached Movie", "year": 2020,
                         "poster_path": None, "overview": None, "categories": []}],
            "count": 1,
            "cached": False,
            "source": "tmdb_trending",
        }
        _POPULAR_CACHE["ts"] = timezone.now().timestamp()

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.side_effect = Exception("401 Unauthorized")

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["cached"])
        self.assertEqual(data["results"][0]["title"], "Cached Movie")

        # Clean up
        _POPULAR_CACHE.clear()


class PrimeMovieEndpointTests(TestCase):
    """Tests for GET /api/prime/movie/<tmdb_id>/."""

    @patch("tracker.views.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_returns_ranked_prime_offers(self, mock_cache_cls, mock_tmdb_cls):
        """Prime movie endpoint returns ranked, filtered offers."""
        from tracker.services.tmdb import TMDbMovie

        # Create a movie in the DB
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

        # Mock TMDb details (not called since movie exists)
        mock_tmdb = mock_tmdb_cls.return_value

        # Mock availability cache
        mock_cache = mock_cache_cls.return_value
        mock_cache._read_offers.return_value = [
            {"country": "JP", "service": "prime", "offer_type": "subscription",
             "quality": "4k", "audio": ["JA", "EN"], "subs": ["EN", "JA"]},
            {"country": "US", "service": "prime", "offer_type": "subscription",
             "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
            {"country": "GB", "service": "netflix", "offer_type": "subscription",
             "quality": "hd", "audio": ["EN"], "subs": ["EN"]},  # Should be filtered out
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/550/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["movie"]["tmdb_id"], 550)
        self.assertEqual(data["movie"]["title"], "Fight Club")
        # Only Prime offers should be present (Netflix filtered out)
        self.assertEqual(len(data["offers"]), 2)
        # JA audio should be ranked first (rank 0)
        self.assertEqual(data["offers"][0]["country"], "JP")
        self.assertEqual(data["offers"][0]["audio_languages"], ["JA", "EN"])
        self.assertEqual(data["offers"][0]["ranking"]["audio_rank"], 0)
        # The stable service id "prime" is presented as its display name.
        self.assertEqual(data["offers"][0]["service"], "Prime Video")

    @patch("tracker.views.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_auto_creates_movie_from_tmdb(self, mock_cache_cls, mock_tmdb_cls):
        """If movie not in DB, prime_movie auto-creates from TMDb."""
        from tracker.services.tmdb import TMDbMovie

        mock_tmdb = mock_tmdb_cls.return_value
        mock_tmdb.details.return_value = TMDbMovie(
            tmdb_id=999, title="New Movie", year=2025
        )

        mock_cache = mock_cache_cls.return_value
        mock_cache._read_offers.return_value = []
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/999/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["movie"]["tmdb_id"], 999)
        self.assertEqual(data["offers"], [])
        self.assertTrue(Movie.objects.filter(tmdb_id=999).exists())

    @patch("tracker.views.TMDbClient")
    def test_movie_not_tracked_tmdb_resolve_fails(self, mock_tmdb_cls):
        """If movie is not tracked and TMDb can't resolve it, return 404 with clean JSON.

        Mocked so this stays offline and deterministic regardless of whether a
        real TMDb credential happens to be present in the environment.
        """
        mock_tmdb_cls.return_value.details.side_effect = Exception("404 Not Found")

        from django.test import Client
        response = Client().get("/api/prime/movie/99999/")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["offers"], [])

    @patch("tracker.views.AvailabilityCache")
    def test_filters_to_subscription_free_only(self, mock_cache_cls):
        """Only subscription/free offers are returned, rent/buy excluded."""
        movie = Movie.objects.create(tmdb_id=777, title="Test Movie", year=2024)

        mock_cache = mock_cache_cls.return_value
        mock_cache._read_offers.return_value = [
            {"country": "US", "service": "prime", "offer_type": "subscription",
             "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
            {"country": "US", "service": "prime", "offer_type": "rent",
             "quality": "4k", "audio": ["EN"], "subs": ["EN"]},  # Should be filtered
            {"country": "US", "service": "prime", "offer_type": "buy",
             "quality": "4k", "audio": ["EN"], "subs": ["EN"]},  # Should be filtered
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/777/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertEqual(data["offers"][0]["offer_type"], "subscription")

    @patch("tracker.views.AvailabilityCache")
    def test_offers_sorted_by_priority(self, mock_cache_cls):
        """Offers are sorted: JA audio > EN audio > quality."""
        movie = Movie.objects.create(tmdb_id=888, title="Sort Test", year=2024)

        mock_cache = mock_cache_cls.return_value
        mock_cache._read_offers.return_value = [
            # US: EN audio, EN subs — lower priority
            {"country": "US", "service": "prime", "offer_type": "subscription",
             "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
            # JP: JA audio, EN subs — highest priority (JA audio rank 0)
            {"country": "JP", "service": "prime", "offer_type": "subscription",
             "quality": "hd", "audio": ["JA", "EN"], "subs": ["EN", "JA"]},
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/888/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # JP offer (JA audio) should be ranked first
        self.assertEqual(data["offers"][0]["country"], "JP")
        self.assertEqual(data["offers"][0]["ranking"]["audio_rank"], 0)
        self.assertEqual(data["offers"][1]["country"], "US")
        self.assertGreater(data["offers"][1]["ranking"]["audio_rank"],
                          data["offers"][0]["ranking"]["audio_rank"])
