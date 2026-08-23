"""Tests for Letterboxd CSV import."""
from unittest.mock import MagicMock

from django.test import TestCase

from tracker.models import Movie, WatchlistItem
from tracker.services.letterboxd import (
    LetterboxdRow,
    import_rows,
    parse_csv,
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
