"""Tests for Letterboxd CSV import and public profile/list URL import.

No test in this module makes live HTTP requests to Letterboxd or TMDb: HTML
fixtures are inline and every fetch/TMDb call is mocked.
"""
import json
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase
from rest_framework import status

from tracker.models import Movie, UserMovieData, WatchlistItem
import gzip
import xml.etree.ElementTree as ET

from tracker.services import letterboxd as letterboxd_service
from tracker.services.letterboxd import (
    LetterboxdFetchError,
    LetterboxdImportError,
    LetterboxdInvalidURLError,
    LetterboxdProfileNotFound,
    LetterboxdRow,
    MAX_PROFILE_PAGES,
    build_profile_page_url,
    fetch_profile_page,
    fetch_profile_rows,
    import_rows,
    normalize_profile_url,
    parse_csv,
    parse_file_or_zip,
    parse_mal_xml,
    parse_profile_page,
    parse_watchlist_page,
    parse_watched_page,
)
from tracker.services.tmdb import TMDbMovie


SAMPLE_CSV = (
    b"Title,Year,Letterboxd URI\n"
    b"Inception,2010,https://letterboxd.com/film/inception/\n"
    b",2020,https://letterboxd.com/film/empty/\n"
    b"Some Movie,notayear,https://letterboxd.com/film/some-movie/\n"
)


class ParseTests(TestCase):
    def test_parses_title_and_year(self):
        rows = parse_csv(SAMPLE_CSV)
        self.assertEqual(len(rows), 2)  # empty title row skipped
        self.assertEqual(rows[0].title, "Inception")
        self.assertEqual(rows[0].year, 2010)
        self.assertEqual(rows[0].uri, "https://letterboxd.com/film/inception/")

    def test_invalid_year_becomes_none(self):
        rows = parse_csv(SAMPLE_CSV)
        bad = next(r for r in rows if r.title == "Some Movie")
        self.assertIsNone(bad.year)

    def test_skips_empty_and_whitespace_only_title_rows(self):
        csv_data = (
            b"Title,Year,Letterboxd URI\n"
            b",2020,https://letterboxd.com/film/empty1/\n"
            b"   ,2021,https://letterboxd.com/film/empty2/\n"
            b"\t,2022,https://letterboxd.com/film/empty3/\n"
            b"Valid Movie,2023,https://letterboxd.com/film/valid-movie/\n"
        )
        rows = parse_csv(csv_data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "Valid Movie")
        self.assertEqual(rows[0].year, 2023)

    def test_preserves_letterboxd_uri(self):
        csv_data = (
            b"Title,Year,Letterboxd URI\n"
            b"With URI,2021,https://letterboxd.com/film/with-uri/\n"
            b"Without URI,2022,\n"
        )
        rows = parse_csv(csv_data)
        self.assertEqual(rows[0].uri, "https://letterboxd.com/film/with-uri/")
        self.assertIsNone(rows[1].uri)


class ImportTests(TestCase):
    def _mock_client(self, results_by_title):
        client = MagicMock()

        def _search(query, year=None):
            return results_by_title.get(query, [])

        client.search.side_effect = _search
        return client

    def test_resolves_exact_year_match(self):
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })
        rows = [LetterboxdRow(title="Inception", year=2010)]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(len(result.ambiguous), 0)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertTrue(Movie.objects.filter(tmdb_id=27205).exists())

    def test_missing_year_with_single_match_resolves(self):
        client = self._mock_client({
            "The Matrix": [TMDbMovie(tmdb_id=603, title="The Matrix", year=1999)],
        })
        rows = [LetterboxdRow(title="The Matrix", year=None, uri="https://letterboxd.com/film/the-matrix/")]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(len(result.ambiguous), 0)
        self.assertEqual(len(result.skipped), 0)
        self.assertEqual(WatchlistItem.objects.count(), 1)
        self.assertTrue(Movie.objects.filter(tmdb_id=603).exists())

    def test_missing_year_with_multiple_matches_is_ambiguous(self):
        client = self._mock_client({
            "Avatar": [
                TMDbMovie(tmdb_id=19995, title="Avatar", year=2009),
                TMDbMovie(tmdb_id=76600, title="Avatar: The Way of Water", year=2022),
            ],
        })
        rows = [LetterboxdRow(title="Avatar", year=None, uri="https://letterboxd.com/film/avatar/")]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.resolved), 0)
        self.assertEqual(len(result.ambiguous), 1)
        amb = result.ambiguous[0]
        self.assertEqual(amb["title"], "Avatar")
        self.assertIsNone(amb["year"])
        self.assertEqual(amb["uri"], "https://letterboxd.com/film/avatar/")
        self.assertEqual(amb["reason"], "multiple_matches")
        self.assertEqual(len(amb["candidates"]), 2)
        self.assertEqual(
            amb["candidates"][0],
            {
                "tmdb_id": 19995,
                "title": "Avatar",
                "year": 2009,
                "overview": None,
                "poster_path": None,
            },
        )

    def test_duplicate_movie_rows_do_not_create_duplicate_watchlist_items(self):
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })
        rows = [
            LetterboxdRow(title="Inception", year=2010, uri="https://letterboxd.com/film/inception/"),
            LetterboxdRow(title="Inception", year=2010, uri="https://letterboxd.com/film/inception/"),
        ]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.resolved), 2)  # Both resolved to same movie
        self.assertEqual(Movie.objects.count(), 1)
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_idempotent_on_repeat(self):
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })
        rows = [LetterboxdRow(title="Inception", year=2010)]
        first = import_rows(rows, client=client)
        second = import_rows(rows, client=client)
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_ambiguous_result_structure(self):
        client = self._mock_client({
            "Thing": [
                TMDbMovie(tmdb_id=1, title="Thing One", year=1999),
                TMDbMovie(tmdb_id=2, title="Thing Two", year=1999),
            ],
        })
        rows = [LetterboxdRow(title="Thing", year=1999, uri="https://letterboxd.com/film/thing/")]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.ambiguous), 1)
        amb = result.ambiguous[0]
        self.assertIn("title", amb)
        self.assertIn("year", amb)
        self.assertIn("uri", amb)
        self.assertIn("reason", amb)
        self.assertIn("candidates", amb)
        self.assertEqual(amb["title"], "Thing")
        self.assertEqual(amb["year"], 1999)
        self.assertEqual(amb["uri"], "https://letterboxd.com/film/thing/")
        self.assertEqual(amb["reason"], "year_mismatch")
        self.assertEqual(
            amb["candidates"],
            [
                {
                    "tmdb_id": 1,
                    "title": "Thing One",
                    "year": 1999,
                    "overview": None,
                    "poster_path": None,
                },
                {
                    "tmdb_id": 2,
                    "title": "Thing Two",
                    "year": 1999,
                    "overview": None,
                    "poster_path": None,
                },
            ],
        )

    def test_no_match_skipped_structure_and_does_not_crash(self):
        client = self._mock_client({})
        rows = [LetterboxdRow(title="Unknown Movie", year=2000, uri="https://letterboxd.com/film/unknown/")]
        result = import_rows(rows, client=client)
        self.assertEqual(len(result.skipped), 1)
        skipped = result.skipped[0]
        self.assertEqual(skipped["title"], "Unknown Movie")
        self.assertEqual(skipped["year"], 2000)
        self.assertEqual(skipped["uri"], "https://letterboxd.com/film/unknown/")
        self.assertEqual(skipped["reason"], "no_match")
        self.assertEqual(len(result.resolved), 0)
        self.assertEqual(len(result.ambiguous), 0)

    def test_year_mismatch_is_ambiguous(self):
        client = self._mock_client({
            "Thing": [
                TMDbMovie(tmdb_id=1, title="Thing One", year=1999),
                TMDbMovie(tmdb_id=2, title="Thing Two", year=1999),
            ],
        })
        rows = [LetterboxdRow(title="Thing", year=1999)]
        result = import_rows(rows, client=client)
        # Two same-year candidates → ambiguous flag.
        self.assertEqual(len(result.ambiguous), 1)
        self.assertEqual(len(result.resolved), 0)

    def test_uri_preserved_in_row_and_results_not_stored_on_models(self):
        # Letterboxd URI is preserved on LetterboxdRow, skipped, and ambiguous dicts,
        # but only tmdb_id/title/year are stored on Movie and WatchlistItem models.
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })
        rows = [LetterboxdRow(title="Inception", year=2010, uri="https://letterboxd.com/film/inception/")]
        result = import_rows(rows, client=client)
        item = result.resolved[0]
        self.assertFalse(hasattr(item, "uri"))
        self.assertFalse(hasattr(item.movie, "uri"))


class ExtendedLetterboxdImportTests(TestCase):
    def _mock_client(self, results_by_title):
        client = MagicMock()
        def _search(query, year=None):
            return results_by_title.get(query, [])
        client.search.side_effect = _search
        return client

    def test_parses_and_imports_ratings_and_reviews_and_watched(self):
        ratings_csv = (
            b"Date,Name,Year,Letterboxd URI,Rating\n"
            b"2023-01-01,Parasite,2019,https://letterboxd.com/film/parasite-2019/,4.5\n"
        )
        reviews_csv = (
            b"Date,Name,Year,Letterboxd URI,Rating,Rewatch,Review,Watched Date\n"
            b"2023-01-02,Parasite,2019,https://letterboxd.com/film/parasite-2019/,4.5,Yes,Masterpiece!,2023-01-02\n"
        )
        client = self._mock_client({
            "Parasite": [TMDbMovie(tmdb_id=496243, title="Parasite", year=2019)],
        })

        from tracker.services.letterboxd import parse_csv, parse_file_or_zip
        from tracker.models import UserMovieData, MovieWatchEvent

        rows1 = parse_csv(ratings_csv, filename="ratings.csv")
        self.assertEqual(rows1[0].file_type, "ratings")
        self.assertEqual(rows1[0].rating, 4.5)

        res1 = import_rows(rows1, client=client)
        self.assertEqual(res1.ratings_count, 1)
        self.assertEqual(res1.watched_count, 1)

        user_data = UserMovieData.objects.get(movie__tmdb_id=496243)
        self.assertTrue(user_data.is_watched)
        self.assertEqual(user_data.rating, 4.5)

        rows2 = parse_csv(reviews_csv, filename="reviews.csv")
        self.assertEqual(rows2[0].file_type, "reviews")
        self.assertEqual(rows2[0].review, "Masterpiece!")

        res2 = import_rows(rows2, client=client)
        self.assertEqual(res2.reviews_count, 1)

        user_data.refresh_from_db()
        self.assertEqual(user_data.review, "Masterpiece!")

    def test_diary_preserves_multiple_viewings_per_movie(self):
        diary_csv = (
            b"Date,Name,Year,Letterboxd URI,Rating,Rewatch,Review,Watched Date\n"
            b"2022-05-01,Inception,2010,https://letterboxd.com/film/inception/,5.0,No,First view,2022-05-01\n"
            b"2023-06-15,Inception,2010,https://letterboxd.com/film/inception/,5.0,Yes,Second view,2023-06-15\n"
        )
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })

        from tracker.services.letterboxd import parse_csv
        from tracker.models import MovieWatchEvent

        rows = parse_csv(diary_csv, filename="diary.csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].file_type, "diary")
        self.assertTrue(rows[1].rewatch)

        res = import_rows(rows, client=client)
        self.assertEqual(res.diary_events_count, 2)

        events = MovieWatchEvent.objects.filter(movie__tmdb_id=27205).order_by("watched_date")
        self.assertEqual(events.count(), 2)
        self.assertEqual(str(events[0].watched_date), "2022-05-01")
        self.assertFalse(events[0].rewatch)
        self.assertEqual(str(events[1].watched_date), "2023-06-15")
        self.assertTrue(events[1].rewatch)

        # Idempotent re-import does not duplicate events
        res_repeat = import_rows(rows, client=client)
        self.assertEqual(MovieWatchEvent.objects.filter(movie__tmdb_id=27205).count(), 2)

    def test_zip_archive_import(self):
        import io, zipfile
        from tracker.services.letterboxd import parse_file_or_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("watchlist.csv", "Title,Year\nSpirited Away,2001\n")
            z.writestr("watched.csv", "Date,Name,Year\nSpirited Away,2001\n")

        zip_bytes = buf.getvalue()
        rows = parse_file_or_zip(zip_bytes, filename="export.zip")
        self.assertEqual(len(rows), 2)
        types = {r.file_type for r in rows}
        self.assertIn("watchlist", types)
        self.assertIn("watched", types)


class MyAnimeListImportTests(TestCase):
    def setUp(self):
        self.sample_xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>\n'
            b'<myanimelist>\n'
            b'    <anime>\n'
            b'        <series_animedb_id>31646</series_animedb_id>\n'
            b'        <series_title><![CDATA[3-gatsu no Lion]]></series_title>\n'
            b'        <my_status>Plan to Watch</my_status>\n'
            b'    </anime>\n'
            b'    <anime>\n'
            b'        <series_animedb_id>35180</series_animedb_id>\n'
            b'        <series_title><![CDATA[3-gatsu no Lion 2nd Season]]></series_title>\n'
            b'        <my_status>Watching</my_status>\n'
            b'    </anime>\n'
            b'    <anime>\n'
            b'        <series_animedb_id>1</series_animedb_id>\n'
            b'        <series_title><![CDATA[Cowboy Bebop]]></series_title>\n'
            b'        <my_status>Completed</my_status>\n'
            b'    </anime>\n'
            b'    <anime>\n'
            b'        <series_animedb_id>2</series_animedb_id>\n'
            b'        <series_title><![CDATA[Trigun]]></series_title>\n'
            b'        <my_status>Dropped</my_status>\n'
            b'    </anime>\n'
            b'</myanimelist>'
        )

    def test_parse_mal_xml_filters_status(self):
        rows = parse_mal_xml(self.sample_xml)
        self.assertEqual(len(rows), 2)
        titles = [r.title for r in rows]
        self.assertIn("3-gatsu no Lion", titles)
        self.assertIn("3-gatsu no Lion 2nd Season", titles)
        self.assertNotIn("Cowboy Bebop", titles)
        self.assertNotIn("Trigun", titles)

        self.assertEqual(rows[0].source, "myanimelist")
        self.assertEqual(rows[0].mal_id, 31646)

    def test_parse_file_or_zip_handles_gzip_xml(self):
        gz_bytes = gzip.compress(self.sample_xml)
        rows = parse_file_or_zip(gz_bytes, filename="animelist.xml.gz")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].source, "myanimelist")

    def test_import_rows_preserves_myanimelist_source(self):
        client = MagicMock()
        client.search.return_value = [
            TMDbMovie(tmdb_id=123, title="3-gatsu no Lion", year=2016)
        ]
        rows = [
            LetterboxdRow(
                title="3-gatsu no Lion",
                year=None,
                source="myanimelist",
                mal_id=31646,
            )
        ]
        res = import_rows(rows, client=client)
        self.assertEqual(len(res.resolved), 1)
        item = res.resolved[0]
        self.assertEqual(item.source, "myanimelist")
        self.assertEqual(item.movie.title, "3-gatsu no Lion")


# ---------------------------------------------------------------------------
# Letterboxd public-profile URL import
# ---------------------------------------------------------------------------

# Inline fixture mirroring Letterboxd's current poster markup
# (react-component tile: data-item-name carries "Title (Year)",
# data-target-link carries the relative film route, img alt carries the title).
WATCHLIST_PAGE_HTML = """
<html><body>
<ul class="grid -p125 -scaled128">
  <li class="griditem">
    <div class="react-component" data-item-name="Inception (2010)"
         data-item-slug="inception" data-target-link="/film/inception/">
      <div class="poster film-poster">
        <img class="image" width="125" height="187" alt="Inception" src="https://s.ltrbxd.com/p1.jpg">
      </div>
    </div>
  </li>
  <li class="griditem">
    <div class="react-component" data-item-name="The Matrix (1999)"
         data-target-link="/film/the-matrix/">
      <div class="poster film-poster">
        <img class="image" alt="The Matrix" src="https://s.ltrbxd.com/p2.jpg">
      </div>
    </div>
  </li>
</ul>
</body></html>
"""

WATCHED_PAGE_HTML = """
<html><body>
<ul class="grid">
  <li class="griditem">
    <div class="react-component" data-item-name="Parasite (2019)"
         data-target-link="/film/parasite-2019/">
      <div class="poster film-poster"><img class="image" alt="Parasite"></div>
    </div>
  </li>
  <li class="griditem">
    <div class="react-component" data-item-name="Whiplash (2014)"
         data-target-link="/film/whiplash/">
      <div class="poster film-poster"><img class="image" alt="Whiplash"></div>
    </div>
  </li>
</ul>
</body></html>
"""

EMPTY_PAGE_HTML = "<html><body><div class='poster-grid'></div></body></html>"

SINGLE_POSTER_PAGE_HTML = """
<html><body>
<div class="react-component" data-item-name="Heat (1995)" data-target-link="/film/heat/">
  <div class="poster film-poster"><img class="image" alt="Heat"></div>
</div>
</body></html>
"""


class NormalizeProfileUrlTests(TestCase):
    """URL validation is pure and never touches the network."""

    def test_watchlist_url(self):
        self.assertEqual(
            normalize_profile_url("https://letterboxd.com/foo/watchlist/"),
            ("foo", "watchlist"),
        )

    def test_watched_url_films_alias(self):
        self.assertEqual(
            normalize_profile_url("https://letterboxd.com/foo/films/watched/"),
            ("foo", "watched"),
        )

    def test_watched_url_films_page(self):
        self.assertEqual(
            normalize_profile_url("https://letterboxd.com/foo/films/"),
            ("foo", "watched"),
        )

    def test_bare_profile_url_has_no_type(self):
        username, file_type = normalize_profile_url("https://letterboxd.com/foo/")
        self.assertEqual(username, "foo")
        self.assertEqual(file_type, "")

    def test_www_and_query_string_accepted(self):
        self.assertEqual(
            normalize_profile_url("https://www.letterboxd.com/foo/watchlist/?sort=name"),
            ("foo", "watchlist"),
        )

    def test_paginated_url_normalizes_to_first_page_type(self):
        self.assertEqual(
            normalize_profile_url("https://letterboxd.com/foo/watchlist/page/3/"),
            ("foo", "watchlist"),
        )

    def test_rejects_foreign_host(self):
        with self.assertRaises(LetterboxdInvalidURLError):
            normalize_profile_url("https://example.com/foo")

    def test_rejects_film_page(self):
        with self.assertRaises(LetterboxdInvalidURLError):
            normalize_profile_url("https://letterboxd.com/film/inception/")

    def test_rejects_unsupported_path(self):
        with self.assertRaises(LetterboxdInvalidURLError):
            normalize_profile_url("https://letterboxd.com/foo/something-else/")

    def test_rejects_empty_url(self):
        with self.assertRaises(LetterboxdInvalidURLError):
            normalize_profile_url("")

    def test_invalid_url_is_an_application_level_error(self):
        self.assertTrue(issubclass(LetterboxdInvalidURLError, LetterboxdImportError))


class ParseProfilePageTests(TestCase):
    def test_watchlist_page_produces_two_rows(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "https://letterboxd.com/foo/watchlist/")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].title, "Inception")
        self.assertEqual(rows[0].year, 2010)
        self.assertEqual(rows[0].file_type, "watchlist")
        self.assertEqual(rows[0].source, "letterboxd_url")
        self.assertEqual(rows[1].title, "The Matrix")
        self.assertEqual(rows[1].year, 1999)

    def test_watched_page_produces_watched_rows(self):
        rows = parse_watched_page(WATCHED_PAGE_HTML, "https://letterboxd.com/foo/films/")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].file_type, "watched")
        self.assertEqual(rows[0].source, "letterboxd_url")
        self.assertEqual(rows[0].title, "Parasite")
        self.assertEqual(rows[0].year, 2019)

    def test_relative_film_url_normalized_to_absolute(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "https://letterboxd.com/foo/watchlist/")
        self.assertEqual(rows[0].uri, "https://letterboxd.com/film/inception/")
        self.assertEqual(rows[1].uri, "https://letterboxd.com/film/the-matrix/")

    def test_absolute_film_url_kept(self):
        html = """
        <div class="react-component" data-item-name="Sicario (2015)"
             data-target-link="https://letterboxd.com/film/sicario/">
          <img alt="Sicario">
        </div>
        """
        rows = parse_profile_page(html, "page-url", "watchlist")
        self.assertEqual(rows[0].uri, "https://letterboxd.com/film/sicario/")

    def test_empty_page_produces_zero_rows(self):
        self.assertEqual(parse_watchlist_page(EMPTY_PAGE_HTML, "page-url"), [])
        self.assertEqual(parse_watched_page("", "page-url"), [])

    def test_poster_container_fallback(self):
        """Classic .poster-container markup (no react-component attrs)."""
        html = """
        <div class="poster-container">
          <a href="/film/la-la-land/"><img alt="La La Land"></a>
        </div>
        """
        rows = parse_profile_page(html, "page-url", "watchlist")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].title, "La La Land")
        self.assertEqual(rows[0].uri, "https://letterboxd.com/film/la-la-land/")
        self.assertIsNone(rows[0].year)

    def test_missing_year_stays_none(self):
        html = """
        <div class="react-component" data-item-name="Unreleased Film"
             data-target-link="/film/unreleased-film/">
          <img alt="Unreleased Film">
        </div>
        """
        rows = parse_profile_page(html, "page-url", "watchlist")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].year)

    def test_year_range_is_not_guessed(self):
        html = """
        <div class="react-component" data-item-name="The Wire (2002-2008)"
             data-target-link="/film/the-wire/">
          <img alt="The Wire">
        </div>
        """
        rows = parse_profile_page(html, "page-url", "watchlist")
        self.assertIsNone(rows[0].year)

    def test_entries_without_film_link_are_ignored(self):
        html = """
        <div class="react-component" data-target-link="/foo/watchlist/"><img alt="Nav"></div>
        <div class="react-component" data-item-name="Heat (1995)" data-target-link="/film/heat/">
          <img alt="Heat">
        </div>
        """
        rows = parse_profile_page(html, "page-url", "watchlist")
        self.assertEqual([r.title for r in rows], ["Heat"])


class ProfilePaginationTests(TestCase):
    def _fetcher(self, mapping, calls):
        def fetch(url: str) -> str:
            calls.append(url)
            return mapping[url]

        return fetch

    def test_stops_on_first_empty_page(self):
        mapping = {
            "https://letterboxd.com/foo/watchlist/": WATCHLIST_PAGE_HTML,  # 2 posters
            "https://letterboxd.com/foo/watchlist/page/2/": SINGLE_POSTER_PAGE_HTML,  # 1
            "https://letterboxd.com/foo/watchlist/page/3/": EMPTY_PAGE_HTML,  # 0
        }
        calls: list[str] = []
        rows, pages_fetched = fetch_profile_rows(
            "foo", "watchlist", fetcher=self._fetcher(mapping, calls)
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(pages_fetched, 3)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(url.startswith("https://letterboxd.com/foo/watchlist/") for url in calls))

    def test_watched_pagination_uses_films_urls(self):
        mapping = {
            "https://letterboxd.com/foo/films/": WATCHED_PAGE_HTML,
            "https://letterboxd.com/foo/films/page/2/": EMPTY_PAGE_HTML,
        }
        calls: list[str] = []
        rows, pages_fetched = fetch_profile_rows(
            "foo", "watched", fetcher=self._fetcher(mapping, calls)
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(pages_fetched, 2)
        self.assertEqual(calls[0], "https://letterboxd.com/foo/films/")

    def test_empty_first_page_stops_immediately(self):
        calls: list[str] = []
        rows, pages_fetched = fetch_profile_rows(
            "foo",
            "watchlist",
            fetcher=self._fetcher({"https://letterboxd.com/foo/watchlist/": EMPTY_PAGE_HTML}, calls),
        )
        self.assertEqual(rows, [])
        self.assertEqual(pages_fetched, 1)
        self.assertEqual(len(calls), 1)

    def test_hard_maximum_bounds_pagination(self):
        """Even a page that never empties cannot fetch more than max_pages."""
        calls: list[str] = []

        def always_full(url: str) -> str:
            calls.append(url)
            return SINGLE_POSTER_PAGE_HTML

        rows, pages_fetched = fetch_profile_rows(
            "foo", "watchlist", fetcher=always_full, max_pages=5
        )
        self.assertEqual(len(calls), 5)
        self.assertEqual(pages_fetched, 5)
        self.assertEqual(len(rows), 5)

    def test_documented_default_maximum(self):
        self.assertEqual(MAX_PROFILE_PAGES, 200)

    def test_build_profile_page_url(self):
        self.assertEqual(
            build_profile_page_url("foo", "watchlist"),
            "https://letterboxd.com/foo/watchlist/",
        )
        self.assertEqual(
            build_profile_page_url("foo", "watchlist", 2),
            "https://letterboxd.com/foo/watchlist/page/2/",
        )
        self.assertEqual(
            build_profile_page_url("foo", "watched"),
            "https://letterboxd.com/foo/films/",
        )


class FetchProfilePageHttpTests(TestCase):
    """HTTP layer: every failure becomes an application-level error."""

    def _patch_session(self, *, status_code=200, text="<html></html>", side_effect=None):
        response = MagicMock()
        response.status_code = status_code
        response.text = text
        session = MagicMock()
        if side_effect is not None:
            session.get.side_effect = side_effect
        else:
            session.get.return_value = response
        return patch("tracker.services.letterboxd._get_session", return_value=session)

    def test_successful_page(self):
        with self._patch_session(text="<html><body>ok</body></html>") as mocked:
            body = fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertIn("ok", body)
        self.assertTrue(mocked.called)

    def test_timeout_becomes_fetch_error(self):
        with self._patch_session(side_effect=requests.Timeout("boom")):
            with self.assertRaises(LetterboxdFetchError) as ctx:
                fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertIn("timed out", str(ctx.exception))

    def test_connection_error_becomes_fetch_error(self):
        with self._patch_session(side_effect=requests.ConnectionError("nope")):
            with self.assertRaises(LetterboxdFetchError) as ctx:
                fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertIn("connect", str(ctx.exception).lower())

    def test_http_404_is_profile_not_found(self):
        with self._patch_session(status_code=404, text="<html>nope</html>"):
            with self.assertRaises(LetterboxdProfileNotFound):
                fetch_profile_page("https://letterboxd.com/nope/watchlist/")

    def test_http_403_challenge_is_fetch_error(self):
        challenge = "<html><head><title>Just a moment...</title></head></html>"
        with self._patch_session(status_code=403, text=challenge):
            with self.assertRaises(LetterboxdFetchError) as ctx:
                fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_http_500_is_fetch_error(self):
        with self._patch_session(status_code=500, text="server error"):
            with self.assertRaises(LetterboxdFetchError) as ctx:
                fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertIn("500", str(ctx.exception))

    def test_empty_profile_returns_empty_html(self):
        with self._patch_session(status_code=200, text="<html><body></body></html>"):
            body = fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertEqual(parse_profile_page(body, "url", "watchlist"), [])

    def test_errors_do_not_leak_tracebacks(self):
        with self._patch_session(side_effect=requests.ConnectionError("secret internal detail")):
            with self.assertRaises(LetterboxdFetchError) as ctx:
                fetch_profile_page("https://letterboxd.com/foo/watchlist/")
        self.assertNotIn("secret internal detail", str(ctx.exception))


class SessionHeaderTests(TestCase):
    """Letterboxd/Cloudflare challenges requests that are not browser-like.

    A bare User-Agent gets HTTP 403 on paginated ``/films/`` pages; Client
    Hints + Sec-Fetch navigation headers are what a real browser sends.
    """

    def setUp(self):
        self._previous = letterboxd_service._SESSION
        letterboxd_service._SESSION = None

    def tearDown(self):
        letterboxd_service._SESSION = self._previous

    def test_session_sends_browser_navigation_headers(self):
        headers = letterboxd_service._get_session().headers
        self.assertIn("Mozilla", headers["User-Agent"])
        self.assertIn("sec-ch-ua", headers)
        self.assertEqual(headers["sec-ch-ua-platform"], '"macOS"')
        self.assertEqual(headers["Upgrade-Insecure-Requests"], "1")
        self.assertEqual(headers["Sec-Fetch-Site"], "cross-site")
        self.assertEqual(headers["Sec-Fetch-Mode"], "navigate")
        self.assertEqual(headers["Sec-Fetch-User"], "?1")
        self.assertEqual(headers["Sec-Fetch-Dest"], "document")

    def test_session_is_reused(self):
        first = letterboxd_service._get_session()
        self.assertIs(first, letterboxd_service._get_session())


class LetterboxdUrlImportBehaviourTests(TestCase):
    """Scraped rows flow through the existing import_rows() unchanged."""

    def _mock_client(self, results_by_title):
        client = MagicMock()
        client.search.side_effect = lambda query, year=None: results_by_title.get(query, [])
        return client

    def test_watchlist_rows_create_watchlist_items(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "url")
        client = self._mock_client(
            {
                "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
                "The Matrix": [TMDbMovie(tmdb_id=603, title="The Matrix", year=1999)],
            }
        )
        result = import_rows(rows, client=client, user_id=1)
        self.assertEqual(len(result.resolved), 2)
        self.assertEqual(WatchlistItem.objects.count(), 2)
        self.assertEqual(
            set(WatchlistItem.objects.values_list("source", flat=True)),
            {"letterboxd_url"},
        )
        self.assertEqual(result.watched_count, 0)

    def test_watched_rows_mark_watched_without_watchlist_items(self):
        rows = parse_watched_page(WATCHED_PAGE_HTML, "url")
        self.assertEqual(rows[0].file_type, "watched")
        client = self._mock_client(
            {
                "Parasite": [TMDbMovie(tmdb_id=496243, title="Parasite", year=2019)],
                "Whiplash": [TMDbMovie(tmdb_id=244786, title="Whiplash", year=2014)],
            }
        )
        result = import_rows(rows, client=client, user_id=1)
        self.assertEqual(result.watched_count, 2)
        self.assertEqual(WatchlistItem.objects.count(), 0)
        watched = UserMovieData.objects.get(movie__tmdb_id=496243)
        self.assertTrue(watched.is_watched)

    def test_ambiguous_structure_unchanged(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "url")
        client = self._mock_client(
            {
                "Inception": [
                    TMDbMovie(tmdb_id=1, title="Inception", year=2010),
                    TMDbMovie(tmdb_id=2, title="Inception Returns", year=2010),
                ],
                "The Matrix": [],
            }
        )
        result = import_rows(rows, client=client, user_id=1)
        self.assertEqual(len(result.ambiguous), 1)
        amb = result.ambiguous[0]
        for key in ("title", "year", "uri", "reason", "candidates"):
            self.assertIn(key, amb)
        self.assertEqual(amb["title"], "Inception")
        self.assertEqual(amb["year"], 2010)
        self.assertEqual(amb["uri"], "https://letterboxd.com/film/inception/")

    def test_scraped_rows_import_is_idempotent(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "url")
        client = self._mock_client(
            {
                "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
                "The Matrix": [TMDbMovie(tmdb_id=603, title="The Matrix", year=1999)],
            }
        )
        import_rows(rows, client=client, user_id=1)
        import_rows(rows, client=client, user_id=1)
        self.assertEqual(WatchlistItem.objects.count(), 2)
        self.assertEqual(Movie.objects.count(), 2)

    def test_reimport_same_watched_rows_stays_idempotent(self):
        rows = parse_watched_page(WATCHED_PAGE_HTML, "url")
        client = self._mock_client(
            {
                "Parasite": [TMDbMovie(tmdb_id=496243, title="Parasite", year=2019)],
                "Whiplash": [TMDbMovie(tmdb_id=244786, title="Whiplash", year=2014)],
            }
        )
        import_rows(rows, client=client, user_id=1)
        import_rows(rows, client=client, user_id=1)
        self.assertEqual(WatchlistItem.objects.count(), 0)
        self.assertEqual(UserMovieData.objects.filter(is_watched=True).count(), 2)


class LetterboxdUrlImportViewTests(TestCase):
    """POST /api/importers/letterboxd-url/ end-to-end (HTTP + TMDb mocked)."""

    url = "/api/importers/letterboxd-url/"

    def _post(self, payload):
        return self.client.post(
            self.url, data=json.dumps(payload), content_type="application/json"
        )

    def _tmdb_client(self, results_by_title):
        client = MagicMock()
        client.search.side_effect = lambda query, year=None: results_by_title.get(query, [])
        return client

    def test_missing_url_returns_400(self):
        resp = self._post({"type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("url", resp.data["error"].lower())

    def test_invalid_url_returns_400(self):
        resp = self._post({"url": "https://example.com/foo", "type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("letterboxd", resp.data["error"].lower())

    def test_film_page_url_returns_400(self):
        resp = self._post({"url": "https://letterboxd.com/film/inception/", "type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bare_profile_without_type_returns_400(self):
        resp = self._post({"url": "https://letterboxd.com/foo/"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("type", resp.data["error"].lower())

    def test_unsupported_type_returns_400(self):
        resp = self._post({"url": "https://letterboxd.com/foo/", "type": "diary"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_watchlist_import_returns_metadata_and_creates_items(self):
        rows = parse_watchlist_page(WATCHLIST_PAGE_HTML, "url")
        client = self._tmdb_client(
            {
                "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
                "The Matrix": [TMDbMovie(tmdb_id=603, title="The Matrix", year=1999)],
            }
        )
        with patch("tracker.views.fetch_profile_rows", return_value=(rows, 3)) as fetch_mock:
            with patch("tracker.views.TMDbClient", return_value=client):
                resp = self._post({"url": "https://letterboxd.com/foo/watchlist/"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        fetch_mock.assert_called_once_with("foo", "watchlist")
        self.assertEqual(resp.data["source"], "letterboxd_url")
        self.assertEqual(resp.data["username"], "foo")
        self.assertEqual(resp.data["import_type"], "watchlist")
        self.assertEqual(resp.data["pages_fetched"], 3)
        self.assertEqual(resp.data["rows_found"], 2)
        self.assertEqual(len(resp.data["resolved"]), 2)
        self.assertEqual(resp.data["ambiguous"], [])
        self.assertEqual(resp.data["skipped"], [])
        for key in (
            "watched_count",
            "ratings_count",
            "reviews_count",
            "diary_events_count",
        ):
            self.assertIn(key, resp.data)
        self.assertEqual(WatchlistItem.objects.count(), 2)

    def test_watched_import_does_not_create_watchlist_items(self):
        rows = parse_watched_page(WATCHED_PAGE_HTML, "url")
        client = self._tmdb_client(
            {
                "Parasite": [TMDbMovie(tmdb_id=496243, title="Parasite", year=2019)],
                "Whiplash": [TMDbMovie(tmdb_id=244786, title="Whiplash", year=2014)],
            }
        )
        with patch("tracker.views.fetch_profile_rows", return_value=(rows, 1)):
            with patch("tracker.views.TMDbClient", return_value=client):
                resp = self._post(
                    {"url": "https://letterboxd.com/foo/films/watched/", "type": "watched"}
                )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["import_type"], "watched")
        self.assertEqual(resp.data["watched_count"], 2)
        self.assertEqual(WatchlistItem.objects.count(), 0)
        self.assertTrue(
            UserMovieData.objects.get(movie__tmdb_id=496243).is_watched
        )

    def test_profile_not_found_returns_404(self):
        with patch(
            "tracker.views.fetch_profile_rows",
            side_effect=LetterboxdProfileNotFound("Letterboxd profile or list not found."),
        ):
            resp = self._post({"url": "https://letterboxd.com/ghost/", "type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("not found", resp.data["error"].lower())

    def test_fetch_failure_returns_502(self):
        with patch(
            "tracker.views.fetch_profile_rows",
            side_effect=LetterboxdFetchError("Letterboxd request timed out. Try again in a moment."),
        ):
            resp = self._post({"url": "https://letterboxd.com/foo/", "type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("timed out", resp.data["error"])

    def test_unexpected_failure_is_sanitized(self):
        with patch("tracker.views.fetch_profile_rows", side_effect=RuntimeError("internal secret")):
            resp = self._post({"url": "https://letterboxd.com/foo/", "type": "watchlist"})
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertNotIn("internal secret", resp.data["error"])
