"""Smoke tests for the DRF endpoints."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from tracker.models import Movie, Preference, Provider, Subscription


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

    def test_post_creates_subscription_or_links_provider(self):
        p, _ = Provider.objects.get_or_create(slug="disney", defaults={"name": "Disney+", "is_enabled": True})
        resp = self.client.post(
            "/api/subscriptions/",
            data={"provider_slug": "disney"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["provider"]["slug"], "disney")

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
        Preference.objects.get_or_create(user_id=1, defaults={"audio_languages": ["EN"], "subtitle_languages": ["EN"]})
        Preference.objects.filter(user_id=1).update(audio_languages=["EN"], subtitle_languages=["EN"])
        resp = self.client.get("/api/preferences/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["audio_languages"], ["EN"])

    def test_put_updates(self):
        Preference.objects.get_or_create(user_id=1)
        resp = self.client.put(
            "/api/preferences/",
            data={"audio_languages": ["EN", "IT"], "subtitle_languages": ["EN"], "candidate_countries": ["US"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Preference.objects.get(user_id=1).audio_languages, ["EN", "IT"])


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
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        Movie.objects.create(tmdb_id=42, title="Cached", year=2020)
        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=2)
        instance._read_offers.return_value = [
            {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio": ["EN"], "subs": ["EN"]}
        ]
        resp = self.client.get("/api/movie/42/availability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["movie"]["tmdb_id"], 42)
        self.assertEqual(len(resp.json()["offers"]), 1)

    @patch("tracker.views.AvailabilityCache")
    def test_respects_rate_limiter(self, mock_cache_cls):
        from tracker.services.rate_limit import RateLimitExceeded
        Movie.objects.create(tmdb_id=42, title="Cached", year=2020)
        instance = mock_cache_cls.return_value
        instance.get_or_refresh.side_effect = RateLimitExceeded("Rate limit reached")
        resp = self.client.get("/api/movie/42/availability/")
        self.assertEqual(resp.status_code, 429)
        self.assertTrue(resp.json().get("rate_limited"))


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
        self.assertEqual(resp.json(), {
            "report": [],
            "summary": {
                "total_movies": 0,
                "available_movies": 0,
                "unavailable_movies": 0,
                "countries": [],
                "services": [],
            },
        })

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
        Preference.objects.create(user_id=1, candidate_countries=["US", "GB"])
        m1 = Movie.objects.create(tmdb_id=201, title="Movie One", year=2020)
        m2 = Movie.objects.create(tmdb_id=202, title="Movie Two", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=1)

        def mock_read_offers(movie):
            if movie.tmdb_id == 201:
                return [
                    {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio": ["EN"], "subs": ["EN"]},
                    {"country": "gb", "service": "Prime", "offer_type": "subscription", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
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
    def test_summary_analytics_and_deduplication(self, mock_cache_cls):
        from tracker.models import WatchlistItem
        Preference.objects.create(user_id=1, candidate_countries=["US", "GB", "CA"])
        m1 = Movie.objects.create(tmdb_id=501, title="Movie A", year=2020)
        m2 = Movie.objects.create(tmdb_id=502, title="Movie B", year=2021)
        m3 = Movie.objects.create(tmdb_id=503, title="Movie C", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m3, source="manual")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=1)

        def mock_read_offers(movie):
            if movie.tmdb_id == 501:
                return [
                    {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "4k", "audio": ["EN"], "subs": ["EN"]},
                    {"country": "us", "service": "Netflix", "offer_type": "subscription", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
                    {"country": "gb", "service": "Prime", "offer_type": "subscription", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
                    {"country": "us", "service": "Apple", "offer_type": "buy", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
                ]
            if movie.tmdb_id == 502:
                return [
                    {"country": "us", "service": "Netflix", "offer_type": "free", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
                    {"country": "ca", "service": "Apple", "offer_type": "rent", "quality": "hd", "audio": ["EN"], "subs": ["EN"]},
                ]
            return []

        instance._read_offers.side_effect = mock_read_offers

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("summary", data)
        summary = data["summary"]
        self.assertEqual(summary["total_movies"], 3)
        self.assertEqual(summary["available_movies"], 2)
        self.assertEqual(summary["unavailable_movies"], 1)

        self.assertEqual(summary["countries"], [
            {"country": "US", "count": 2},
            {"country": "GB", "count": 1},
        ])

        self.assertEqual(summary["services"], [
            {"service": "Netflix", "count": 2},
            {"service": "Prime", "count": 1},
        ])


class WatchlistResolveEndpointTests(TestCase):
    def test_missing_selections_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class WatchEndpointTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1001, title="Test Watch Movie", year=2021)

    @patch("tracker.views.AvailabilityCache")
    def test_watch_endpoint_returns_found_and_url(self, mock_cache_cls):
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=1)
        instance._read_offers.return_value = [
            {
                "country": "US",
                "service": "netflix",
                "offer_type": "subscription",
                "quality": "hd",
                "url": "https://www.netflix.com/title/123",
                "audio": ["EN"],
                "subs": ["EN"],
            }
        ]
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["watch_url"], "https://www.netflix.com/title/123")
        self.assertEqual(data["movie"]["tmdb_id"], 1001)

    @patch("tracker.views.AvailabilityCache")
    def test_watch_endpoint_prioritizes_subscribed_providers(self, mock_cache_cls):
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        p_disney, _ = Provider.objects.get_or_create(slug="disney", defaults={"name": "Disney+", "is_enabled": True})
        Subscription.objects.create(user_id=1, provider=p_disney, name="Disney+")

        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=2)
        instance._read_offers.return_value = [
            {
                "country": "US",
                "service": "netflix",
                "offer_type": "subscription",
                "quality": "4k",
                "url": "https://www.netflix.com/title/123",
                "audio": ["EN"],
                "subs": ["EN"],
            },
            {
                "country": "US",
                "service": "disney",
                "offer_type": "subscription",
                "quality": "hd",
                "url": "https://www.disneyplus.com/movies/456",
                "audio": ["EN"],
                "subs": ["EN"],
            },
        ]
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["watch_url"], "https://www.disneyplus.com/movies/456")

    @patch("tracker.views.AvailabilityCache")
    def test_watch_endpoint_returns_found_false_when_no_valid_url(self, mock_cache_cls):
        instance = mock_cache_cls.return_value
        instance.get_or_refresh.return_value = MagicMock(refreshed=False, reason="fresh", offers_written=0)
        instance._read_offers.return_value = []
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["found"])
        self.assertNotIn("watch_url", data)
        self.assertIn("message", data)

    @patch("tracker.views.TMDbClient")
    def test_watch_endpoint_404_when_movie_not_found(self, mock_tmdb):
        mock_instance = mock_tmdb.return_value
        mock_instance.details.side_effect = Exception("Not found on TMDb")
        resp = self.client.get("/api/watch/999999/")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["found"])
        self.assertEqual(resp.json()["message"], "Movie not found")

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
        self.assertEqual(Movie.objects.get(tmdb_id=27205).title, "Inception")


class MovieLanguageInfoEndpointTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=500, title="Test Movie", year=2023)

    def test_404_when_movie_not_tracked(self):
        resp = self.client.get("/api/movie/9999/language-info/?provider=Netflix&country=US")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.put(
            "/api/movie/9999/language-info/",
            data={"provider": "Netflix", "country": "US", "audio_languages": ["EN"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_requires_provider_and_country(self):
        resp = self.client.get(f"/api/movie/{self.movie.tmdb_id}/language-info/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("provider and country parameters required", resp.json()["error"])

    def test_get_returns_empty_when_no_language_info(self):
        resp = self.client.get(
            f"/api/movie/{self.movie.tmdb_id}/language-info/?provider=Netflix&country=US"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["movie_tmdb_id"], self.movie.tmdb_id)
        self.assertEqual(data["provider"], "Netflix")
        self.assertEqual(data["country"], "US")
        self.assertEqual(data["audio_languages"], [])
        self.assertEqual(data["subtitle_languages"], [])

    def test_put_creates_language_info_and_normalizes_codes(self):
        payload = {
            "provider": "Netflix",
            "country": "us",
            "audio_languages": ["EN", "  ES ", "en"],
            "subtitle_languages": [" IT ", "fr"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["country"], "US")
        self.assertEqual(data["audio_languages"], ["EN", "ES"])
        self.assertEqual(data["subtitle_languages"], ["IT", "FR"])

        # Subsequent GET should return updated info
        get_resp = self.client.get(
            f"/api/movie/{self.movie.tmdb_id}/language-info/?provider=Netflix&country=US"
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["audio_languages"], ["EN", "ES"])
        self.assertEqual(sorted(get_resp.json()["subtitle_languages"]), ["FR", "IT"])

    def test_put_updates_and_overwrites_existing_entries(self):
        # Initial put
        self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data={"provider": "Prime", "country": "US", "audio_languages": ["EN"], "subtitle_languages": ["ES"]},
            content_type="application/json",
        )
        # Update put
        update_resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data={"provider": "Prime", "country": "US", "audio_languages": ["DE"], "subtitle_languages": []},
            content_type="application/json",
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["audio_languages"], ["DE"])
        self.assertEqual(update_resp.json()["subtitle_languages"], [])

    def test_put_validates_invalid_language_codes(self):
        payload = {
            "provider": "Netflix",
            "country": "US",
            "audio_languages": ["Klingon"],
            "subtitle_languages": ["ES"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_validates_invalid_country_code(self):
        payload = {
            "provider": "Netflix",
            "country": "USA",
            "audio_languages": ["EN"],
            "subtitle_languages": ["ES"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
