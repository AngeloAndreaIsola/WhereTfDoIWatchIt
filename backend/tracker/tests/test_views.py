"""Smoke tests for the DRF endpoints."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from tracker.models import Movie, Preference, Subscription


class SubscriptionsEndpointTests(TestCase):
    def test_get_returns_empty_list(self):
        resp = self.client.get("/api/subscriptions/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_post_creates_subscription_and_trims_whitespace(self):
        resp = self.client.post(
            "/api/subscriptions/",
            data={"name": "  Netflix  "},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "Netflix")
        self.assertTrue(Subscription.objects.filter(user_id=1, name="Netflix").exists())

    def test_post_duplicate_case_insensitive_returns_400(self):
        Subscription.objects.create(user_id=1, name="Netflix")
        resp = self.client.post(
            "/api/subscriptions/",
            data={"name": "NETFLIX"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"name": ["Subscription with this name already exists."]})

    def test_delete_removes_subscription(self):
        sub = Subscription.objects.create(user_id=1, name="Hulu")
        resp = self.client.delete(f"/api/subscriptions/{sub.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Subscription.objects.filter(id=sub.id).exists())

    def test_delete_non_existent_returns_404(self):
        resp = self.client.delete("/api/subscriptions/9999/")
        self.assertEqual(resp.status_code, 404)


class PreferencesEndpointTests(TestCase):
    def test_get_returns_existing(self):
        Preference.objects.get_or_create(user_id=1, defaults={"audio_languages": ["en"], "subtitle_languages": ["en"]})
        Preference.objects.filter(user_id=1).update(audio_languages=["en"], subtitle_languages=["en"])
        resp = self.client.get("/api/preferences/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["audio_languages"], ["en"])

    def test_put_updates(self):
        Preference.objects.get_or_create(user_id=1)
        resp = self.client.put(
            "/api/preferences/",
            data={"audio_languages": ["en", "it"], "subtitle_languages": ["en"], "candidate_countries": ["us"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Preference.objects.get(user_id=1).audio_languages, ["en", "it"])


class SearchEndpointTests(TestCase):
    @patch("tracker.views.TMDbClient")
    def test_search_calls_tmdb(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbMovie
        instance.search.return_value = [TMDbMovie(tmdb_id=1, title="Foo", year=2010)]
        resp = self.client.get("/api/search/?q=Foo")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"][0]["title"], "Foo")

    def test_search_requires_query(self):
        resp = self.client.get("/api/search/")
        self.assertEqual(resp.status_code, 400)


class MovieAvailabilityTests(TestCase):
    def test_404_when_not_tracked(self):
        resp = self.client.get("/api/movie/9999/availability/")
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.views.AvailabilityCache")
    def test_returns_ranked_offers(self, mock_cache_cls):
        Preference.objects.create(user_id=1, audio_languages=["en"], subtitle_languages=["en"])
        Movie.objects.create(tmdb_id=42, title="Cached", year=2020)
        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=2)
        instance._read_offers.return_value = [
            {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio": ["en"], "subs": ["en"]}
        ]
        resp = self.client.get("/api/movie/42/availability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["movie"]["tmdb_id"], 42)
        self.assertEqual(len(resp.json()["offers"]), 1)


class WatchlistEndpointTests(TestCase):
    def test_get_returns_empty(self):
        resp = self.client.get("/api/watchlist/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_post_requires_file(self):
        resp = self.client.post("/api/watchlist/")
        self.assertEqual(resp.status_code, 400)


class WatchlistReportEndpointTests(TestCase):
    def test_empty_list(self):
        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"report": []})

    @patch("tracker.views.AvailabilityCache")
    def test_all_unavailable_list(self, mock_cache_cls):
        from tracker.models import WatchlistItem
        movie = Movie.objects.create(tmdb_id=101, title="Unavailable Movie", year=2021)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=0)
        instance._read_offers.return_value = []

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["report"]), 1)
        row = data["report"][0]
        self.assertEqual(row["movie"]["tmdb_id"], 101)
        self.assertEqual(row["movie"]["title"], "Unavailable Movie")
        self.assertEqual(row["available_countries"], [])
        self.assertIsNone(row["best_country"])
        self.assertIsNone(row["best_service"])

    @patch("tracker.views.AvailabilityCache")
    def test_mixed_list(self, mock_cache_cls):
        from tracker.models import WatchlistItem
        Preference.objects.create(user_id=1, candidate_countries=["us", "gb"])
        m1 = Movie.objects.create(tmdb_id=201, title="Movie One", year=2020)
        m2 = Movie.objects.create(tmdb_id=202, title="Movie Two", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=1)

        def mock_read_offers(movie):
            if movie.tmdb_id == 201:
                return [
                    {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio": ["en"], "subs": ["en"]},
                    {"country": "gb", "service": "Prime", "offer_type": "subscription", "quality": "hd", "audio": ["en"], "subs": ["en"]},
                ]
            return []

        instance._read_offers.side_effect = mock_read_offers

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["report"]), 2)

        row1 = next(r for r in data["report"] if r["movie"]["tmdb_id"] == 201)
        self.assertEqual(row1["available_countries"], ["GB", "US"])
        self.assertEqual(row1["best_country"], "US")
        self.assertEqual(row1["best_service"], "Netflix")

        row2 = next(r for r in data["report"] if r["movie"]["tmdb_id"] == 202)
        self.assertEqual(row2["available_countries"], [])
        self.assertIsNone(row2["best_country"])
        self.assertIsNone(row2["best_service"])

    @patch("tracker.views.AvailabilityCache")
    def test_respects_rate_limiter(self, mock_cache_cls):
        from tracker.models import WatchlistItem
        from tracker.services.rate_limit import RateLimitExceeded
        movie = Movie.objects.create(tmdb_id=301, title="Rate Limited Movie", year=2023)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.side_effect = RateLimitExceeded("Rate limit reached")

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(resp.json().get("rate_limited"))
class WatchlistResolveEndpointTests(TestCase):
    def test_missing_selections_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"],
            "selections parameter is required and must not be empty",
        )

    def test_empty_selections_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": []},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"],
            "selections parameter is required and must not be empty",
        )

    def test_invalid_tmdb_id_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": [{"original_title": "Test", "year": 2020, "tmdb_id": "not-an-int"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"],
            "selections parameter is required and must not be empty",
        )

    def test_valid_selections_creates_movie_and_watchlist_item(self):
        payload = {
            "selections": [
                {"original_title": "Inception", "year": 2010, "tmdb_id": 27205},
                {"original_title": "The Matrix", "year": 1999, "tmdb_id": 603},
            ]
        }
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["resolved_count"], 2)
        self.assertEqual(len(data["items"]), 2)

        movie = Movie.objects.get(tmdb_id=27205)
        self.assertEqual(movie.title, "Inception")
        self.assertEqual(movie.year, 2010)

        watchlist_item = data["items"][0]
        self.assertEqual(watchlist_item["movie"]["tmdb_id"], 27205)
        self.assertEqual(watchlist_item["source"], "letterboxd_resolved")

    def test_duplicate_selections_deduplicated_and_existing_item_returned(self):
        # Pre-create movie and WatchlistItem
        movie = Movie.objects.create(tmdb_id=27205, title="Inception", year=2010)
        from tracker.models import WatchlistItem
        existing_item = WatchlistItem.objects.create(
            user_id=1, movie=movie, source="letterboxd_resolved"
        )

        payload = {
            "selections": [
                {"original_title": "Inception", "year": 2010, "tmdb_id": 27205},
                {"original_title": "Inception Duplicate", "year": 2010, "tmdb_id": 27205},
            ]
        }
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["resolved_count"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], existing_item.id)
        # Verify title was not updated for existing movie
        self.assertEqual(Movie.objects.get(tmdb_id=27205).title, "Inception")
