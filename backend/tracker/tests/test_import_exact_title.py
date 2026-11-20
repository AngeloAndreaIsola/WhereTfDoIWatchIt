"""Regression tests for #108 — unique exact-title candidates before ambiguity.

Offline fixtures only (mocked TMDb client); reproduces the measured
Matrix/Fight Club counterexamples from the 2026-11-17 disposable-Postgres probe
and pins the conservative negative cases.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from tracker.models import Movie, UserMovieData, WatchlistItem
from tracker.services.letterboxd import (
    LetterboxdRow,
    _exact_title_decision,
    _normalize_title,
    import_rows,
)
from tracker.services.tmdb import TMDbClient, TMDbItem


def _item(tmdb_id, title, year, content_type="movie"):
    return TMDbItem(tmdb_id=tmdb_id, title=title, year=year, content_type=content_type)


def _client(matches):
    client = MagicMock()
    client.search.return_value = matches
    return client


class NormalizeTitleTests(TestCase):
    def test_casefold_whitespace_diacritics_apostrophes(self):
        self.assertEqual(_normalize_title("  The   MATRIX "), "the matrix")
        self.assertEqual(_normalize_title("Amélie"), "amelie")
        self.assertEqual(_normalize_title("Fight Club\u2019s Cut"), "fight club's cut")
        self.assertEqual(_normalize_title(""), "")


class ExactTitleDecisionTests(TestCase):
    def test_unique_exact_wins_over_sequel_without_year(self):
        matches = [_item(603, "The Matrix", 1999), _item(604, "The Matrix Reloaded", 2003)]
        pick, reason = _exact_title_decision(matches, None, "The Matrix")
        self.assertEqual(pick.tmdb_id, 603)
        self.assertEqual(reason, "exact_title_unique")

    def test_unique_exact_wins_over_same_year_edition(self):
        matches = [_item(550, "Fight Club", 1999), _item(9999, "Fight Club Director's Cut", 1999)]
        pick, reason = _exact_title_decision(matches, 1999, "Fight Club")
        self.assertEqual(pick.tmdb_id, 550)
        self.assertEqual(reason, "exact_title_unique")

    def test_year_conflict_defers(self):
        matches = [_item(603, "The Matrix", 1999)]
        pick, reason = _exact_title_decision(matches, 2021, "The Matrix")
        self.assertIsNone(pick)
        self.assertEqual(reason, "exact_title_year_conflict")

    def test_identical_titles_same_year_stay_ambiguous(self):
        matches = [_item(1, "Dune", 2021), _item(2, "Dune", 2021)]
        pick, reason = _exact_title_decision(matches, 2021, "Dune")
        self.assertIsNone(pick)
        self.assertEqual(reason, "multiple_exact_titles")

    def test_identical_titles_isolated_by_row_year(self):
        matches = [_item(1, "Dune", 1984), _item(2, "Dune", 2021)]
        pick, reason = _exact_title_decision(matches, 2021, "Dune")
        self.assertEqual(pick.tmdb_id, 2)
        self.assertEqual(reason, "exact_title_unique_year")

    def test_identical_titles_without_year_stay_ambiguous(self):
        matches = [_item(1, "Dune", 1984), _item(2, "Dune", 2021)]
        pick, reason = _exact_title_decision(matches, None, "Dune")
        self.assertIsNone(pick)
        self.assertEqual(reason, "multiple_exact_titles")

    def test_movie_tv_collision_stays_ambiguous(self):
        matches = [
            _item(1, "Same Name", 2020, "movie"),
            _item(2, "Same Name", 2020, "series"),
        ]
        pick, reason = _exact_title_decision(matches, 2020, "Same Name")
        self.assertIsNone(pick)
        self.assertEqual(reason, "exact_title_type_collision")

    def test_missing_candidate_year_is_not_a_conflict(self):
        matches = [_item(603, "The Matrix", None)]
        pick, reason = _exact_title_decision(matches, 1999, "The Matrix")
        self.assertEqual(pick.tmdb_id, 603)
        self.assertEqual(reason, "exact_title_unique")

    def test_no_exact_title_reports_reason(self):
        matches = [_item(604, "The Matrix Reloaded", 2003)]
        pick, reason = _exact_title_decision(matches, None, "The Matrix")
        self.assertIsNone(pick)
        self.assertEqual(reason, "no_exact_title")


class ImportExactTitleTests(TestCase):
    """End-to-end import_rows behavior on the measured counterexamples."""

    def test_matrix_without_year_resolves(self):
        result = import_rows(
            [LetterboxdRow(title="The Matrix", year=None)],
            client=_client([_item(603, "The Matrix", 1999), _item(604, "The Matrix Reloaded", 2003)]),
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.ambiguous, [])
        self.assertEqual(result.resolved[0].movie.tmdb_id, 603)

    def test_fight_club_with_year_resolves_over_edition(self):
        result = import_rows(
            [LetterboxdRow(title="Fight Club", year=1999)],
            client=_client([_item(550, "Fight Club", 1999), _item(9999, "Fight Club Director's Cut", 1999)]),
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.ambiguous, [])
        self.assertEqual(result.resolved[0].movie.tmdb_id, 550)

    def test_year_conflict_not_auto_accepted_via_fuzzy_fallback(self):
        # Exact title exists but its year conflicts; a same-year sequel must
        # NOT be auto-accepted in its place.
        result = import_rows(
            [LetterboxdRow(title="The Matrix", year=2003)],
            client=_client([_item(603, "The Matrix", 1999), _item(604, "The Matrix Reloaded", 2003)]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(len(result.ambiguous), 1)
        self.assertEqual(result.ambiguous[0]["reason"], "exact_title_year_conflict")
        self.assertEqual(result.ambiguous[0]["decision"], "exact_title_year_conflict")
        self.assertFalse(Movie.objects.exists())

    def test_identical_title_year_candidates_remain_reviewable(self):
        result = import_rows(
            [LetterboxdRow(title="Dune", year=2021)],
            client=_client([_item(1, "Dune", 2021), _item(2, "Dune", 2021)]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(result.ambiguous[0]["reason"], "multiple_exact_titles")

    def test_legacy_year_only_behavior_preserved_without_exact_title(self):
        result = import_rows(
            [LetterboxdRow(title="Some Film", year=1999)],
            client=_client([_item(10, "Some Film Extended", 1999), _item(11, "Other", 2001)]),
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.resolved[0].movie.tmdb_id, 10)

    def test_legacy_multiple_matches_still_ambiguous(self):
        result = import_rows(
            [LetterboxdRow(title="Some Film", year=None)],
            client=_client([_item(10, "Some Film Extended", 1999), _item(11, "Some Film Remake", 2001)]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(result.ambiguous[0]["reason"], "multiple_matches")
        self.assertIn("decision", result.ambiguous[0])

    def test_same_type_local_title_collision_is_reviewable(self):
        Movie.objects.create(tmdb_id=1081, title="Amélie", year=2001)
        Movie.objects.create(tmdb_id=1082, title="Amelie", year=2001)
        Movie.objects.create(tmdb_id=1081, title="Amelie", year=2001, content_type="series")
        client = _client([])

        result = import_rows([LetterboxdRow("Amelie", 2001)], client=client)

        self.assertEqual(result.resolved, [])
        [review] = result.ambiguous
        self.assertEqual(review["decision"], "multiple_local_exact_titles")
        self.assertEqual(
            [(candidate["tmdb_id"], candidate["content_type"]) for candidate in review["candidates"]],
            [(1081, "movie"), (1082, "movie")],
        )
        self.assertFalse(WatchlistItem.objects.exists())
        client.search.assert_not_called()

    def test_local_year_disambiguates_and_missing_year_defers(self):
        older = Movie.objects.create(tmdb_id=1081, title="Equal Title", year=2001)
        Movie.objects.create(tmdb_id=1082, title="Equal Title", year=2020)
        client = _client([])

        result = import_rows(
            [LetterboxdRow("Equal Title", None), LetterboxdRow("Equal Title", 2001)],
            client=client,
        )

        self.assertEqual(result.ambiguous[0]["decision"], "multiple_local_exact_titles")
        self.assertEqual([item.movie_id for item in result.resolved], [older.pk])
        client.search.assert_not_called()

    def test_existing_typed_uri_association_disambiguates_local_titles(self):
        Movie.objects.create(tmdb_id=1081, title="Equal Title", year=2001)
        preferred = Movie.objects.create(tmdb_id=1082, title="Equal Title", year=2001)
        uri = "https://letterboxd.com/film/equal-title/"
        UserMovieData.objects.create(movie=preferred, letterboxd_uri=uri)
        client = _client([])

        result = import_rows([LetterboxdRow("Equal Title", 2001, uri=uri)], client=client)

        self.assertFalse(result.ambiguous)
        self.assertEqual([item.movie_id for item in result.resolved], [preferred.pk])
        client.search.assert_not_called()

    def test_conflicting_typed_uri_associations_are_reviewable(self):
        uri = "https://letterboxd.com/film/equal-title/"
        movies = [Movie.objects.create(tmdb_id=pk, title="Equal Title", year=2001) for pk in (1081, 1082)]
        for movie in movies:
            UserMovieData.objects.create(movie=movie, letterboxd_uri=uri, is_watched=True, rating=4.0)
        client = _client([])

        result = import_rows([LetterboxdRow("Equal Title", 2001, uri=uri)], client=client)

        self.assertFalse(result.resolved)
        [review] = result.ambiguous
        self.assertEqual(review["decision"], "multiple_local_uri_identities")
        self.assertEqual(review["uri"], uri)
        self.assertEqual([item["tmdb_id"] for item in review["candidates"]], [1081, 1082])
        self.assertFalse(WatchlistItem.objects.exists())
        self.assertEqual(UserMovieData.objects.filter(is_watched=True, rating=4.0).count(), 2)
        client.search.assert_not_called()

    def test_uri_associations_remain_separate_across_media_types(self):
        uri = "https://letterboxd.com/film/equal-title/"
        movie = Movie.objects.create(tmdb_id=1081, title="Equal Title", year=2001)
        series = Movie.objects.create(tmdb_id=1081, title="Equal Title", year=2001, content_type="series")
        for item in (movie, series):
            UserMovieData.objects.create(movie=item, letterboxd_uri=uri)
        client = _client([])

        result = import_rows([LetterboxdRow("Equal Title", 2001, uri=uri)], client=client)

        self.assertFalse(result.ambiguous)
        self.assertEqual([item.movie_id for item in result.resolved], [movie.pk])
        client.search.assert_not_called()

    def test_single_exact_wrong_type_candidate_is_reviewable(self):
        client = _client([_item(1081, "A Distinct Title", 2020, "movie")])
        result = import_rows(
            [LetterboxdRow("A Distinct Title", 2020, source="myanimelist")],
            client=client,
        )
        client.search.assert_called_once_with("A Distinct Title", year=2020, content_type="series")
        self.assertEqual(result.resolved, [])
        [review] = result.ambiguous
        self.assertEqual(review["reason"], "candidate_type_conflict")
        self.assertEqual(review["expected_content_type"], "series")
        self.assertEqual(review["candidates"][0]["content_type"], "movie")
        self.assertFalse(Movie.objects.exists())

    def test_legacy_fallback_also_requires_matching_type(self):
        result = import_rows(
            [LetterboxdRow("Some Film", 2020)],
            client=_client([_item(1082, "Some Film Extended", 2020, "series")]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(result.ambiguous[0]["reason"], "candidate_type_conflict")

    def test_original_name_counts_as_exact_evidence_without_replacing_display_title(self):
        candidate = TMDbClient._parse_search_result({
            "id": 1083, "name": "Spirited Detective", "original_name": "Tantei",
            "first_air_date": "2020-04-01",
        }, content_type="series")
        result = import_rows(
            [LetterboxdRow("Tantei", 2020, source="myanimelist")],
            client=_client([candidate, _item(1084, "Tantei Returns", 2020, "series")]),
        )
        self.assertEqual(result.ambiguous, [])
        self.assertEqual(result.resolved[0].movie.title, "Spirited Detective")

    def test_original_title_and_localized_title_collision_is_reviewable(self):
        candidate = TMDbClient._parse_search_result({
            "id": 1085, "title": "Farewell", "original_title": "Adieu",
            "release_date": "2020-04-01",
        })
        result = import_rows(
            [LetterboxdRow("Adieu", 2020)],
            client=_client([candidate, _item(1086, "Adieu", 2020)]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(result.ambiguous[0]["reason"], "multiple_exact_titles")

    def test_diacritic_exact_match_resolves(self):
        result = import_rows(
            [LetterboxdRow(title="Amelie", year=2001)],
            client=_client([_item(194, "Amélie", 2001)]),
        )
        self.assertEqual(len(result.resolved), 1)
        self.assertEqual(result.resolved[0].movie.tmdb_id, 194)

    def test_reimport_idempotency_preserved(self):
        matches = [_item(603, "The Matrix", 1999), _item(604, "The Matrix Reloaded", 2003)]
        rows = [LetterboxdRow(title="The Matrix", year=None, uri="https://letterboxd.com/film/the-matrix/")]
        first = import_rows(rows, client=_client(matches))
        second = import_rows(rows, client=_client(matches))
        self.assertEqual(len(first.resolved), 1)
        self.assertEqual(len(second.resolved), 1)
        self.assertEqual(first.resolved[0].pk, second.resolved[0].pk)
        self.assertEqual(WatchlistItem.objects.count(), 1)

    def test_watched_history_does_not_create_watchlist_row(self):
        result = import_rows(
            [LetterboxdRow(title="The Matrix", year=1999, file_type="watched")],
            client=_client([_item(603, "The Matrix", 1999)]),
        )
        self.assertEqual(result.resolved, [])
        self.assertEqual(result.watched_count, 1)
        self.assertEqual(WatchlistItem.objects.count(), 0)

    def test_decision_evidence_logged(self):
        with self.assertLogs("tracker.services.letterboxd", level="INFO") as logs:
            import_rows(
                [LetterboxdRow(title="The Matrix", year=None)],
                client=_client([_item(603, "The Matrix", 1999), _item(604, "The Matrix Reloaded", 2003)]),
            )
        self.assertTrue(
            any("import_match_decision" in line and "exact_title_unique" in line for line in logs.output)
        )
