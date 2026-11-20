"""Regression tests for #107 — MyAnimeList imports must search and store series.

Offline fixtures only (mocked TMDb client). Pins the exact search kwargs,
content-type persistence, cross-type identity separation, and reviewable
ambiguity required by the issue's acceptance criteria.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from tracker.models import Movie, WatchlistItem
from tracker.services.letterboxd import (
    LetterboxdRow,
    _search_content_type,
    import_rows,
    parse_mal_xml,
)
from tracker.services.tmdb import TMDbItem


def _series(tmdb_id, title, year=None):
    return TMDbItem(tmdb_id=tmdb_id, title=title, year=year, content_type="series")


def _movie(tmdb_id, title, year=None):
    return TMDbItem(tmdb_id=tmdb_id, title=title, year=year, content_type="movie")


MAL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<myanimelist>
  <anime>
    <series_animedb_id>31646</series_animedb_id>
    <series_title>3-gatsu no Lion</series_title>
    <series_type>TV</series_type>
    <my_status>Plan to Watch</my_status>
  </anime>
  <anime>
    <series_animedb_id>199</series_animedb_id>
    <series_title>Spirited Away</series_title>
    <series_type>Movie</series_type>
    <my_status>Watching</my_status>
  </anime>
  <anime>
    <series_animedb_id>5114</series_animedb_id>
    <series_title>Fullmetal Alchemist: Brotherhood</series_title>
    <my_status>Plan to Watch</my_status>
  </anime>
</myanimelist>
"""


class MalParseMediaTypeTests(TestCase):
    def test_series_type_drives_media_type(self):
        rows = parse_mal_xml(MAL_XML)
        self.assertEqual(len(rows), 3)
        by_title = {r.title: r for r in rows}
        self.assertEqual(by_title["3-gatsu no Lion"].media_type, "series")
        self.assertEqual(by_title["Spirited Away"].media_type, "movie")
        self.assertIsNone(by_title["Fullmetal Alchemist: Brotherhood"].media_type)
        self.assertTrue(all(r.source == "myanimelist" for r in rows))


class SearchContentTypeContractTests(TestCase):
    def test_mal_default_is_series(self):
        self.assertEqual(
            _search_content_type(LetterboxdRow(title="X", year=None, source="myanimelist")),
            "series",
        )

    def test_explicit_declaration_wins(self):
        self.assertEqual(
            _search_content_type(
                LetterboxdRow(title="X", year=None, source="myanimelist", media_type="movie")
            ),
            "movie",
        )

    def test_letterboxd_keeps_movie_semantics(self):
        self.assertEqual(
            _search_content_type(LetterboxdRow(title="X", year=2000)), "movie"
        )


class MalImportTests(TestCase):
    def test_mal_anime_searches_series_and_persists_series(self):
        client = MagicMock()
        client.search.return_value = [_series(31646, "3-gatsu no Lion", 2016)]
        result = import_rows(
            [LetterboxdRow(title="3-gatsu no Lion", year=None, source="myanimelist", mal_id=31646)],
            client=client,
        )
        client.search.assert_called_once_with(
            "3-gatsu no Lion", year=None, content_type="series"
        )
        self.assertEqual(len(result.resolved), 1)
        movie = result.resolved[0].movie
        self.assertEqual(movie.content_type, "series")
        self.assertEqual(movie.tmdb_id, 31646)
        self.assertEqual(result.resolved[0].source, "myanimelist")

    def test_mal_declared_anime_film_searches_movie(self):
        client = MagicMock()
        client.search.return_value = [_movie(199, "Spirited Away", 2001)]
        result = import_rows(
            [LetterboxdRow(title="Spirited Away", year=None, source="myanimelist", media_type="movie")],
            client=client,
        )
        client.search.assert_called_once_with(
            "Spirited Away", year=None, content_type="movie"
        )
        self.assertEqual(result.resolved[0].movie.content_type, "movie")

    def test_letterboxd_film_import_keeps_movie_kwargs(self):
        client = MagicMock()
        client.search.return_value = [_movie(550, "Fight Club", 1999)]
        import_rows([LetterboxdRow(title="Fight Club", year=1999)], client=client)
        client.search.assert_called_once_with("Fight Club", year=1999, content_type="movie")

    def test_same_name_film_and_series_keep_separate_identities(self):
        film = Movie.objects.create(tmdb_id=100, title="Same Name", year=2020, content_type="movie")
        client = MagicMock()
        client.search.return_value = [_series(200, "Same Name", 2020)]
        result = import_rows(
            [LetterboxdRow(title="Same Name", year=None, source="myanimelist")],
            client=client,
        )
        self.assertEqual(len(result.resolved), 1)
        series_movie = result.resolved[0].movie
        self.assertEqual(series_movie.content_type, "series")
        self.assertNotEqual(series_movie.pk, film.pk)
        self.assertEqual(Movie.objects.count(), 2)
        self.assertEqual(WatchlistItem.objects.get().movie_id, series_movie.pk)

    def test_numeric_tmdb_id_collision_across_types_is_not_conflated(self):
        Movie.objects.create(tmdb_id=100, title="Movie Side", content_type="movie")
        client = MagicMock()
        client.search.return_value = [_series(100, "Series Side", 2020)]
        result = import_rows(
            [LetterboxdRow(title="Series Side", year=None, source="myanimelist")],
            client=client,
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.resolved[0].movie.content_type, "series")
        self.assertEqual(Movie.objects.filter(tmdb_id=100).count(), 2)

    def test_local_movie_cache_not_reused_for_series_row(self):
        Movie.objects.create(tmdb_id=100, title="Cached Title", year=2020, content_type="movie")
        client = MagicMock()
        client.search.return_value = [_series(200, "Cached Title", 2020)]
        result = import_rows(
            [LetterboxdRow(title="Cached Title", year=None, source="myanimelist")],
            client=client,
        )
        client.search.assert_called_once()  # wrong-type cache hit must not short-circuit
        self.assertEqual(result.resolved[0].movie.content_type, "series")
        self.assertEqual(result.resolved[0].movie.tmdb_id, 200)

    def test_ambiguous_series_candidates_remain_reviewable(self):
        client = MagicMock()
        client.search.return_value = [_series(1, "Dune", 2021), _series(2, "Dune", 2021)]
        result = import_rows(
            [LetterboxdRow(title="Dune", year=None, source="myanimelist")],
            client=client,
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(len(result.ambiguous), 1)
        self.assertFalse(Movie.objects.exists())

    def test_xml_end_to_end_persists_series(self):
        rows = parse_mal_xml(MAL_XML)
        client = MagicMock()
        client.search.side_effect = lambda query, year=None, content_type=None: {
            "3-gatsu no Lion": [_series(31646, "3-gatsu no Lion", 2016)],
            "Spirited Away": [_movie(199, "Spirited Away", 2001)],
            "Fullmetal Alchemist: Brotherhood": [_series(5114, "Fullmetal Alchemist: Brotherhood", 2009)],
        }.get(query, [])
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.resolved), 3)
        types = {i.movie.title: i.movie.content_type for i in result.resolved}
        self.assertEqual(types["3-gatsu no Lion"], "series")
        self.assertEqual(types["Spirited Away"], "movie")
        self.assertEqual(types["Fullmetal Alchemist: Brotherhood"], "series")
