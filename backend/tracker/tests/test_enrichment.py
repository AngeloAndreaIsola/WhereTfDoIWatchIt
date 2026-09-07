"""Tests for the TMDb enrichment module."""
from __future__ import annotations

from django.test import TestCase, override_settings
from unittest.mock import patch, MagicMock

from tracker.models import Movie
from tracker.services.enrichment import enrich_movie_from_tmdb, resolve_or_create_movie, sync_movie_fields
from tracker.services.tmdb import TMDbItem


TMDB_KEY = "0123456789abcdef0123456789abcdef"


def _details(**kwargs):
    """Build a TMDbItem with the given fields, defaulting unused ones."""
    return TMDbItem(
        tmdb_id=kwargs.get("tmdb_id", 123),
        title=kwargs.get("title", "Test Movie"),
        year=kwargs.get("year"),
        content_type=kwargs.get("content_type", "movie"),
        overview=kwargs.get("overview"),
        poster_path=kwargs.get("poster_path"),
        genres=kwargs.get("genres") or [],
        genre_ids=kwargs.get("genre_ids") or [],
        cast=kwargs.get("cast") or [],
        director=kwargs.get("director"),
        seasons_data=kwargs.get("seasons_data") or [],
        rating=kwargs.get("rating"),
    )


class SyncMovieFieldsTests(TestCase):
    def test_no_change_returns_false(self):
        movie = Movie.objects.create(
            tmdb_id=1, title="A", year=2020, content_type="movie",
            overview="old", poster_path="p.jpg", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=7.0, seasons_data=[],
        )
        details = _details(
            overview="old", poster_path="p.jpg", year=2020, rating=7.0,
        )
        self.assertFalse(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "old")

    def test_overview_change_saves(self):
        movie = Movie.objects.create(
            tmdb_id=2, title="B", year=2020, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        details = _details(overview="new overview")
        self.assertTrue(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "new overview")

    def test_empty_strings_not_wiped(self):
        movie = Movie.objects.create(
            tmdb_id=3, title="C", year=2020, content_type="movie",
            overview="existing", poster_path="existing.jpg", genres=[],
            genre_ids=[], cast=[], director=[], tmdb_rating=5.0,
            seasons_data=[],
        )
        details = _details(overview=None, poster_path=None, rating=None)
        self.assertFalse(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "existing")
        self.assertEqual(movie.poster_path, "existing.jpg")
        self.assertEqual(movie.tmdb_rating, 5.0)

    def test_cast_update(self):
        movie = Movie.objects.create(
            tmdb_id=4, title="D", year=2020, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        new_cast = [{"id": 1, "name": "Alice"}]
        details = _details(cast=new_cast)
        self.assertTrue(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.cast, new_cast)

    def test_seasons_data_update(self):
        movie = Movie.objects.create(
            tmdb_id=5, title="E", year=2020, content_type="series",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        seasons = [{"season_number": 1, "episode_count": 10}]
        details = _details(seasons_data=seasons, content_type="series")
        self.assertTrue(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.seasons_data, seasons)

    def test_year_update(self):
        movie = Movie.objects.create(
            tmdb_id=6, title="F", year=2019, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        details = _details(year=2020)
        self.assertTrue(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.year, 2020)

    def test_director_update(self):
        movie = Movie.objects.create(
            tmdb_id=7, title="G", year=2020, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        details = _details(director=["Dir One"])
        self.assertTrue(sync_movie_fields(movie, details))
        movie.refresh_from_db()
        self.assertEqual(movie.director, ["Dir One"])


class EnrichMovieFromTmdbTests(TestCase):
    @override_settings(TMDB_API_KEY=TMDB_KEY)
    @patch("tracker.services.enrichment.TMDbClient")
    def test_calls_details_and_sync(self, MockClient):
        details = _details(overview="from api", year=2021)
        instance = MockClient.return_value
        instance.is_configured.return_value = True
        instance.details.return_value = details

        movie = Movie.objects.create(
            tmdb_id=10, title="H", year=2019, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        result = enrich_movie_from_tmdb(movie)
        self.assertTrue(result)
        instance.details.assert_called_once_with(10, content_type="movie")
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "from api")
        self.assertEqual(movie.year, 2021)

    @patch("tracker.services.enrichment.TMDbClient")
    def test_resolve_or_create_movie_creates_new(self, MockClient):
        details = _details(tmdb_id=20, title="New Movie", year=2022, content_type="movie")
        instance = MockClient.return_value
        instance.details.return_value = details

        movie = resolve_or_create_movie(20)
        self.assertEqual(movie.tmdb_id, 20)
        self.assertEqual(movie.title, "New Movie")
        self.assertEqual(movie.year, 2022)
        instance.details.assert_called_once_with(20)

    @patch("tracker.services.enrichment.TMDbClient")
    def test_resolve_or_create_movie_updates_existing(self, MockClient):
        details = _details(tmdb_id=30, title="Updated Title", year=2023, poster_path="/new-poster.jpg")
        instance = MockClient.return_value
        instance.details.return_value = details

        movie = Movie.objects.create(
            tmdb_id=30, title="Old Title", year=2020, poster_path="", content_type="movie",
            overview="", genres=[], genre_ids=[], cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        result = resolve_or_create_movie(30)
        self.assertEqual(result.title, "Updated Title")
        self.assertEqual(result.year, 2023)
        self.assertEqual(result.poster_path, "/new-poster.jpg")
        instance.details.assert_called_once_with(30)

    @override_settings(TMDB_API_KEY="")
    def test_noop_when_unconfigured(self):
        movie = Movie.objects.create(
            tmdb_id=11, title="I", year=2020, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        self.assertFalse(enrich_movie_from_tmdb(movie))

    @override_settings(TMDB_API_KEY=TMDB_KEY)
    @patch("tracker.services.enrichment.TMDbClient")
    def test_returns_false_on_fetch_exception(self, MockClient):
        instance = MockClient.return_value
        instance.is_configured.return_value = True
        instance.details.side_effect = Exception("network error")

        movie = Movie.objects.create(
            tmdb_id=12, title="J", year=2020, content_type="movie",
            overview="keep", poster_path="keep.jpg", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        self.assertFalse(enrich_movie_from_tmdb(movie))
        movie.refresh_from_db()
        self.assertEqual(movie.overview, "keep")

    @override_settings(TMDB_API_KEY=TMDB_KEY)
    @patch("tracker.services.enrichment.TMDbClient")
    def test_returns_false_on_non_item(self, MockClient):
        instance = MockClient.return_value
        instance.is_configured.return_value = True
        instance.details.return_value = MagicMock()  # not a TMDbItem

        movie = Movie.objects.create(
            tmdb_id=13, title="K", year=2020, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], director=[], tmdb_rating=None, seasons_data=[],
        )
        self.assertFalse(enrich_movie_from_tmdb(movie))
