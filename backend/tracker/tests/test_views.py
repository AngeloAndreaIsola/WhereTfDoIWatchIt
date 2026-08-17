"""Smoke tests for the DRF endpoints."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from tracker.models import Movie, Preference


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