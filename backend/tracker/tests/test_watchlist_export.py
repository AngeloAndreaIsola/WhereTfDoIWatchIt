"""Canonical WhereTfDoIWatchIt export/import (round-trip backup) tests.

No test in this module makes live HTTP requests: export endpoints are plain
CSV downloads and every import goes through local matching (TMDb mocked).
"""
import csv
import io
from unittest.mock import MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tracker.models import Movie, UserMovieData, WatchlistItem
from tracker.services.letterboxd import import_rows
from tracker.services.watchlist_export import (
    CanonicalImportError,
    is_canonical_csv,
    parse_canonical_csv,
)

CANONICAL_CSV = (
    b"title,year,letterboxd_uri,status\n"
    b"The Matrix,1999,https://letterboxd.com/film/the-matrix/,watchlist\n"
    b"Inception,2010,https://letterboxd.com/film/inception/,watched\n"
)

LETTERBOXD_CSV = (
    b"Title,Year,Letterboxd URI\n"
    b"The Matrix,1999,https://letterboxd.com/film/the-matrix/\n"
)


def _movie(tmdb_id, title, year):
    return Movie.objects.create(tmdb_id=tmdb_id, title=title, year=year)


def _rows(content):
    """Parse a CSV response body into (header, data_rows)."""
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    parsed = list(csv.reader(io.StringIO(text)))
    return parsed[0], parsed[1:]


class ExportEndpointTests(TestCase):
    """GET /api/export/watchlist/, /api/export/watched/, /api/export/."""

    def setUp(self):
        self.matrix = _movie(603, "The Matrix", 1999)
        self.arrival = _movie(329865, "Arrival", 2016)
        self.inception = _movie(27205, "Inception", 2010)
        self.parasite = _movie(496243, "Parasite", 2019)
        self.never_seen = _movie(155, "Never Seen", 2000)

        WatchlistItem.objects.create(user_id=1, movie=self.matrix)
        WatchlistItem.objects.create(user_id=1, movie=self.arrival)
        UserMovieData.objects.create(
            user_id=1,
            movie=self.matrix,
            is_watched=True,
            letterboxd_uri="https://letterboxd.com/film/the-matrix/",
        )
        UserMovieData.objects.create(
            user_id=1,
            movie=self.arrival,
            letterboxd_uri="https://letterboxd.com/film/arrival/",
        )
        UserMovieData.objects.create(
            user_id=1,
            movie=self.inception,
            is_watched=True,
            letterboxd_uri="https://letterboxd.com/film/inception/",
        )
        UserMovieData.objects.create(user_id=1, movie=self.parasite, is_watched=True)
        UserMovieData.objects.create(user_id=1, movie=self.never_seen, is_watched=False)

        # Another user's data must never leak into exports.
        other = _movie(111, "Other User Film", 2001)
        WatchlistItem.objects.create(user_id=2, movie=other)
        UserMovieData.objects.create(user_id=2, movie=other, is_watched=True)

    def _get(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", resp["Content-Disposition"])
        return resp

    def test_watchlist_export(self):
        resp = self._get("/api/export/watchlist/")
        self.assertIn('filename="wheretfdoiwatchit-watchlist.csv"', resp["Content-Disposition"])
        header, rows = _rows(resp.content)
        self.assertEqual(header, ["title", "year", "letterboxd_uri", "status"])
        self.assertEqual(
            {r[0] for r in rows}, {"The Matrix", "Arrival"}
        )
        for row in rows:
            self.assertEqual(row[3], "watchlist")
        by_title = {r[0]: r for r in rows}
        self.assertEqual(by_title["Arrival"][2], "https://letterboxd.com/film/arrival/")
        self.assertEqual(by_title["The Matrix"][1], "1999")

    def test_watched_export_only_includes_watched(self):
        resp = self._get("/api/export/watched/")
        self.assertIn('filename="wheretfdoiwatchit-watched.csv"', resp["Content-Disposition"])
        header, rows = _rows(resp.content)
        self.assertEqual(header, ["title", "year", "letterboxd_uri", "status"])
        self.assertEqual(
            {r[0] for r in rows}, {"The Matrix", "Inception", "Parasite"}
        )
        self.assertNotIn("Never Seen", {r[0] for r in rows})
        self.assertNotIn("Other User Film", {r[0] for r in rows})
        for row in rows:
            self.assertEqual(row[3], "watched")

    def test_combined_export_contains_both_statuses(self):
        resp = self._get("/api/export/")
        self.assertIn('filename="wheretfdoiwatchit-export.csv"', resp["Content-Disposition"])
        header, rows = _rows(resp.content)
        self.assertEqual(header, ["title", "year", "letterboxd_uri", "status"])
        statuses = {row[0]: row[3] for row in rows}
        self.assertEqual(statuses["Arrival"], "watchlist")
        self.assertEqual(statuses["Inception"], "watched")
        self.assertEqual(statuses["Parasite"], "watched")
        # The Matrix is in both states — both rows legitimately appear.
        matrix_statuses = {row[3] for row in rows if row[0] == "The Matrix"}
        self.assertEqual(matrix_statuses, {"watchlist", "watched"})
        self.assertNotIn("Never Seen", statuses)
        self.assertNotIn("Other User Film", statuses)

    def test_missing_year_and_uri_exported_empty(self):
        no_year = _movie(777, "No Year Film", None)
        WatchlistItem.objects.create(user_id=1, movie=no_year)
        resp = self._get("/api/export/watchlist/")
        _, rows = _rows(resp.content)
        no_year_row = next(r for r in rows if r[0] == "No Year Film")
        self.assertEqual(no_year_row[1], "")
        self.assertEqual(no_year_row[2], "")

    def test_csv_escaping_round_trips_through_csv_reader(self):
        tricky_titles = [
            "Film, With Comma",
            'The "Quoted" Film',
            "L'Inconnu — Amélie",
            "Two\nLines",
        ]
        for index, title in enumerate(tricky_titles, start=900):
            movie = _movie(index, title, 2000)
            WatchlistItem.objects.create(user_id=1, movie=movie)
        resp = self._get("/api/export/watchlist/")
        _, rows = _rows(resp.content)
        exported = {r[0] for r in rows}
        for title in tricky_titles:
            self.assertIn(title, exported)


class EmptyExportTests(TestCase):
    """Empty datasets still return a valid header-only CSV (usable round-trip)."""

    def test_empty_exports_return_header_only(self):
        for url in ("/api/export/watchlist/", "/api/export/watched/", "/api/export/"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp["Content-Type"].startswith("text/csv"))
            header, rows = _rows(resp.content)
            self.assertEqual(header, ["title", "year", "letterboxd_uri", "status"])
            self.assertEqual(rows, [])


class CanonicalParserTests(TestCase):
    def test_detection_uses_header_not_filename(self):
        self.assertTrue(is_canonical_csv(CANONICAL_CSV))
        self.assertTrue(is_canonical_csv(CANONICAL_CSV))  # filename-independent
        self.assertFalse(is_canonical_csv(LETTERBOXD_CSV))
        self.assertFalse(is_canonical_csv(b"PK\x03\x04not-a-csv"))
        self.assertFalse(is_canonical_csv(b""))

    def test_parse_mixed_statuses(self):
        rows = parse_canonical_csv(CANONICAL_CSV)
        self.assertEqual(
            [(r.title, r.year, r.uri, r.file_type) for r in rows],
            [
                ("The Matrix", 1999, "https://letterboxd.com/film/the-matrix/", "watchlist"),
                ("Inception", 2010, "https://letterboxd.com/film/inception/", "watched"),
            ],
        )

    def test_invalid_status_rejected(self):
        bad = (
            b"title,year,letterboxd_uri,status\n"
            b"Inception,2010,,banana\n"
        )
        with self.assertRaises(CanonicalImportError) as ctx:
            parse_canonical_csv(bad)
        self.assertIn("banana", str(ctx.exception))
        self.assertIn("watchlist", str(ctx.exception))

    def test_missing_status_column_rejected(self):
        partial = b"title,year,letterboxd_uri\nThe Matrix,1999,\n"
        with self.assertRaises(CanonicalImportError) as ctx:
            parse_canonical_csv(partial)
        self.assertIn("status", str(ctx.exception))

    def test_blank_title_rows_skipped(self):
        csv_bytes = (
            b"title,year,letterboxd_uri,status\n"
            b",1999,https://letterboxd.com/film/none/,watchlist\n"
            b"Arrival,2016,,watchlist\n"
        )
        rows = parse_canonical_csv(csv_bytes)
        self.assertEqual([r.title for r in rows], ["Arrival"])


class CanonicalUploadTests(TestCase):
    """POST /api/watchlist/ routes canonical CSVs to the new parser."""

    def setUp(self):
        _movie(603, "The Matrix", 1999)
        _movie(27205, "Inception", 2010)

    def _upload(self, content, filename="my-export.csv"):
        return self.client.post(
            "/api/watchlist/",
            {"file": SimpleUploadedFile(filename, content)},
        )

    def test_canonical_upload_restores_both_states(self):
        resp = self._upload(CANONICAL_CSV, filename="my-export.csv")
        self.assertEqual(resp.status_code, 200)
        matrix = Movie.objects.get(title="The Matrix")
        inception = Movie.objects.get(title="Inception")
        self.assertTrue(
            WatchlistItem.objects.filter(user_id=1, movie=matrix).exists()
        )
        self.assertFalse(
            WatchlistItem.objects.filter(user_id=1, movie=inception).exists()
        )
        self.assertTrue(
            UserMovieData.objects.get(user_id=1, movie=inception).is_watched
        )
        self.assertFalse(
            UserMovieData.objects.get(user_id=1, movie=matrix).is_watched
        )

    def test_invalid_status_returns_400(self):
        bad = b"title,year,letterboxd_uri,status\nInception,2010,,banana\n"
        resp = self._upload(bad)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("banana", resp.data["error"])

    def test_letterboxd_csv_still_imports(self):
        resp = self._upload(LETTERBOXD_CSV, filename="watchlist.csv")
        self.assertEqual(resp.status_code, 200)
        matrix = Movie.objects.get(title="The Matrix")
        self.assertTrue(
            WatchlistItem.objects.filter(user_id=1, movie=matrix).exists()
        )


class RoundTripTests(TestCase):
    """Export → parse → import_rows → same DB state, twice."""

    def setUp(self):
        self.matrix = _movie(603, "The Matrix", 1999)
        self.arrival = _movie(329865, "Arrival", 2016)
        self.inception = _movie(27205, "Inception", 2010)
        self.parasite = _movie(496243, "Parasite", 2019)

        WatchlistItem.objects.create(user_id=1, movie=self.matrix, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=self.arrival)
        UserMovieData.objects.create(
            user_id=1, movie=self.matrix, letterboxd_uri="https://letterboxd.com/film/the-matrix/"
        )
        UserMovieData.objects.create(
            user_id=1, movie=self.arrival, letterboxd_uri="https://letterboxd.com/film/arrival/"
        )
        UserMovieData.objects.create(
            user_id=1,
            movie=self.inception,
            is_watched=True,
            letterboxd_uri="https://letterboxd.com/film/inception/",
        )
        UserMovieData.objects.create(
            user_id=1,
            movie=self.parasite,
            is_watched=True,
            letterboxd_uri="https://letterboxd.com/film/parasite/",
        )

        self.tmdb_mock = MagicMock()
        self.tmdb_mock.search.return_value = []

    def _watchlist_titles(self):
        return set(
            WatchlistItem.objects.filter(user_id=1).values_list(
                "movie__title", flat=True
            )
        )

    def _watched_titles(self):
        return set(
            UserMovieData.objects.filter(user_id=1, is_watched=True).values_list(
                "movie__title", flat=True
            )
        )

    def test_round_trip_watchlist(self):
        resp = self.client.get("/api/export/watchlist/")
        rows = parse_canonical_csv(resp.content)
        self.assertEqual({r.file_type for r in rows}, {"watchlist"})

        result = import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(result.ambiguous, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(self._watchlist_titles(), {"The Matrix", "Arrival"})
        # Watched state is untouched by a watchlist-only import.
        self.assertEqual(self._watched_titles(), {"Inception", "Parasite"})
        self.tmdb_mock.search.assert_not_called()

        # Second import of the same file must not duplicate anything.
        import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 2)
        self.assertEqual(self._watchlist_titles(), {"The Matrix", "Arrival"})

    def test_round_trip_watchlist_preserves_original_source(self):
        # "The Matrix" was originally a manual item; round-trip must reuse it.
        resp = self.client.get("/api/export/watchlist/")
        rows = parse_canonical_csv(resp.content)
        import_rows(rows, client=self.tmdb_mock)
        item = WatchlistItem.objects.get(user_id=1, movie=self.matrix)
        self.assertEqual(item.source, "manual")
        self.assertEqual(WatchlistItem.objects.filter(user_id=1, movie=self.matrix).count(), 1)

    def test_round_trip_watched(self):
        resp = self.client.get("/api/export/watched/")
        rows = parse_canonical_csv(resp.content)
        self.assertEqual({r.file_type for r in rows}, {"watched"})

        result = import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(result.ambiguous, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(
            self._watched_titles(), {"Inception", "Parasite"}
        )
        # Watched rows never create WatchlistItems.
        self.assertEqual(self._watchlist_titles(), {"The Matrix", "Arrival"})

        import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(
            UserMovieData.objects.filter(user_id=1, is_watched=True).count(), 2
        )
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 2)

    def test_round_trip_combined(self):
        resp = self.client.get("/api/export/")
        rows = parse_canonical_csv(resp.content)
        self.assertEqual(
            {r.file_type for r in rows}, {"watchlist", "watched"}
        )

        import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(self._watchlist_titles(), {"The Matrix", "Arrival"})
        self.assertEqual(self._watched_titles(), {"Inception", "Parasite"})
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 2)
        self.assertEqual(UserMovieData.objects.filter(user_id=1).count(), 4)

        # Re-import the identical export: no duplicates, state unchanged.
        import_rows(rows, client=self.tmdb_mock)
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 2)
        self.assertEqual(UserMovieData.objects.filter(user_id=1).count(), 4)
        self.assertEqual(self._watchlist_titles(), {"The Matrix", "Arrival"})
        self.assertEqual(self._watched_titles(), {"Inception", "Parasite"})

    def test_upload_of_exported_file_through_view(self):
        """Full endpoint round trip: export download bytes → upload → same state."""
        exported = self.client.get("/api/export/").content
        resp = self.client.post(
            "/api/watchlist/",
            {"file": SimpleUploadedFile("wheretfdoiwatchit-export.csv", exported)},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WatchlistItem.objects.filter(user_id=1).count(), 2)
        self.assertEqual(UserMovieData.objects.filter(user_id=1).count(), 4)
        self.assertEqual(self._watched_titles(), {"Inception", "Parasite"})
