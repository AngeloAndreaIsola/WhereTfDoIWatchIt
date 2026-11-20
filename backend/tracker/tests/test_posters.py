import json
from unittest.mock import patch

from django.test import TestCase, override_settings
import requests

from tracker.models import Movie, RefreshRequest, UserMovieData
from tracker.services import posters


def response(status=200, **details):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(details).encode()
    return result


@override_settings(TMDB_API_KEY="test-only-fixture")
class PosterHydrationTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=3176, title="Battle Royale", year=2000)
        self.image = "/fixture-poster.jpg"
        transport = patch("requests.sessions.Session.get")
        self.send = transport.start()
        self.addCleanup(transport.stop)
        self.send.return_value = response(id=3176, title="Battle Royale", release_date="2000-01-01",
                                          poster_path=self.image)

    def hydrate(self, path="", *, kind="movie", tmdb_id=3176):
        return self.client.post(f"/api/movie/{tmdb_id}/poster/",
                                {"content_type": kind, "poster_path": path}, content_type="application/json")

    def test_missing_poster_persists_without_changing_personal_or_refresh_state(self):
        UserMovieData.objects.create(movie=self.movie, is_watched=True, rating=4.0)
        for _ in range(2):
            result = self.hydrate()
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["poster_path"], self.image)
        self.send.assert_called_once()
        self.assertTrue(self.send.call_args.args[0].endswith("/movie/3176"))
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.poster_path, self.image)
        self.assertTrue(UserMovieData.objects.filter(movie=self.movie, is_watched=True, rating=4.0).exists())
        self.assertFalse(RefreshRequest.objects.exists())

    def test_existing_and_concurrently_edited_paths_take_precedence(self):
        def edit(*args, **kwargs):
            Movie.objects.filter(pk=self.movie.pk).update(poster_path="/manual-poster.jpg")
            return response(id=3176, release_date="2000-01-01", poster_path=self.image)

        self.send.side_effect = edit
        self.assertEqual(self.hydrate().json()["poster_path"], "/manual-poster.jpg")
        self.assertEqual(self.hydrate().json()["poster_path"], "/manual-poster.jpg")
        self.send.assert_called_once()

    def test_broken_saved_path_can_display_a_cached_alternate(self):
        path = "https://images.example.invalid/custom.jpg"
        self.movie.poster_path = path
        self.movie.save(update_fields=["poster_path"])
        for _ in range(2):
            self.assertEqual(self.hydrate(path).json()["poster_path"], self.image)
        self.movie.refresh_from_db()
        self.assertEqual(self.movie.poster_path, path)
        self.send.assert_called_once()

    def test_missing_image_is_cached(self):
        self.send.return_value = response(id=3176, release_date="2000-01-01", poster_path=None)
        for _ in range(2):
            self.assertIsNone(self.hydrate().json()["poster_path"])
        self.send.assert_called_once()

    def test_wrong_year_or_identity_is_rejected(self):
        self.send.return_value = response(id=3177, release_date="2000-01-01", poster_path=self.image)
        self.assertIsNone(self.hydrate().json()["poster_path"])
        self.movie.year = 2001
        self.movie.save(update_fields=["year"])
        self.send.return_value = response(id=3176, release_date="2000-01-01", poster_path=self.image)
        self.assertIsNone(self.hydrate().json()["poster_path"])

    def test_tv_collision_uses_the_series_namespace(self):
        series = Movie.objects.create(tmdb_id=3176, content_type="series", title="Series", year=2001)
        self.send.return_value = response(id=3176, first_air_date="2001-01-01", poster_path=self.image)
        self.assertEqual(self.hydrate(kind="series").json()["poster_path"], self.image)
        self.assertTrue(self.send.call_args.args[0].endswith("/tv/3176"))
        series.refresh_from_db()
        self.movie.refresh_from_db()
        self.assertEqual(series.poster_path, self.image)
        self.assertEqual(self.movie.poster_path, "")

    def test_unknown_or_quarantined_records_never_dispatch(self):
        self.movie.is_quarantined = True
        self.movie.save(update_fields=["is_quarantined"])
        self.assertEqual(self.hydrate().status_code, 404)
        self.assertEqual(self.hydrate(tmdb_id=111).status_code, 404)
        self.assertEqual(self.hydrate(kind="all").status_code, 400)
        self.send.assert_not_called()

    def test_rate_limit_uses_retry_after_without_repeating_http(self):
        self.send.return_value = response(429)
        self.send.return_value.headers["Retry-After"] = "90"
        for _ in range(2):
            result = self.hydrate()
            self.assertEqual(result.status_code, 429)
            self.assertGreaterEqual(int(result.headers["Retry-After"]), 89)
        self.send.assert_called_once()

    def test_shared_daily_cap_and_in_flight_guard(self):
        with patch.object(posters, "try_source_lock", return_value=False):
            self.assertEqual(self.hydrate().status_code, 503)
        Movie.objects.create(tmdb_id=11013, title="Secretary")
        with patch.object(posters, "POSTER_DAILY_LIMIT", 1):
            self.assertEqual(self.hydrate().status_code, 200)
            self.assertEqual(self.hydrate(tmdb_id=11013).status_code, 429)
        self.send.assert_called_once()
