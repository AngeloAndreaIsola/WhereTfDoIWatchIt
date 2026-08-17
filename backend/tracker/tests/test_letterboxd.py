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

    def test_no_match_skipped(self):
        client = self._mock_client({})
        rows = [LetterboxdRow(title="Unknown", year=2000)]
        result = import_rows(rows, client=client)
        self.assertEqual(result.skipped[0]["reason"], "no_match")
        self.assertEqual(len(result.resolved), 0)

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

    def test_idempotent_on_repeat(self):
        client = self._mock_client({
            "Inception": [TMDbMovie(tmdb_id=27205, title="Inception", year=2010)],
        })
        rows = [LetterboxdRow(title="Inception", year=2010)]
        first = import_rows(rows, client=client)
        second = import_rows(rows, client=client)
        self.assertEqual(WatchlistItem.objects.count(), 1)