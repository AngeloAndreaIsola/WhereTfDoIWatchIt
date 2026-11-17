from django.test import TestCase
from tracker.models import Movie, Preference, WatchlistItem


class DurationFilterEndpointTests(TestCase):
    def setUp(self):
        self.pref = Preference.objects.create(user_id=1)
        self.m1 = Movie.objects.create(
            tmdb_id=1001, title="Fast Movie", content_type="movie", runtime_minutes=25, year=2021
        )
        self.m2 = Movie.objects.create(
            tmdb_id=1002, title="Medium Movie", content_type="movie", runtime_minutes=90, year=2022
        )
        self.m3 = Movie.objects.create(
            tmdb_id=1003, title="Long Movie", content_type="movie", runtime_minutes=150, year=2023
        )
        self.s1 = Movie.objects.create(
            tmdb_id=1004, title="Series Show", content_type="series", runtime_minutes=45, year=2023
        )

        WatchlistItem.objects.create(user_id=1, movie=self.m1)
        WatchlistItem.objects.create(user_id=1, movie=self.m2)
        WatchlistItem.objects.create(user_id=1, movie=self.m3)
        WatchlistItem.objects.create(user_id=1, movie=self.s1)

    def test_catalog_endpoint_duration_filter(self):
        # 1-2 hours (61-120 min)
        res = self.client.get("/api/catalog/unified/?duration_min=61&duration_max=120")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data.get("results", [])
        tmdb_ids = [r["tmdb_id"] for r in results]
        self.assertIn(1002, tmdb_ids)
        self.assertNotIn(1001, tmdb_ids)
        self.assertNotIn(1003, tmdb_ids)
        self.assertNotIn(1004, tmdb_ids)

    def test_watchlist_report_endpoint_duration_filter(self):
        # Under 30 min (<=30)
        res = self.client.get("/api/watchlist/report/?duration_max=30")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        report = data.get("report", [])
        tmdb_ids = [item["movie"]["tmdb_id"] for item in report]
        self.assertEqual(tmdb_ids, [1001])
        self.assertEqual(data["summary"]["total_movies"], 1)

    def test_homepage_endpoint_duration_filter(self):
        # Verify homepage_feed accepts duration params without error
        res = self.client.get("/api/homepage/?duration_min=61&duration_max=120")
        self.assertEqual(res.status_code, 200)
