"""Watched-list contract, ordering, and database pagination regressions."""
from datetime import date
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tracker.models import Movie, MovieWatchEvent, UserMovieData, WatchlistItem


class WatchedListTests(TestCase):
    def add_title(self, tmdb_id, title, *, content_type="movie", **user_data):
        movie = Movie.objects.create(
            tmdb_id=tmdb_id, title=title, year=2024,
            content_type=content_type, poster_path="/poster.jpg",
        )
        UserMovieData.objects.create(movie=movie, **{"is_watched": True, **user_data})
        return movie

    def get_list(self, **params):
        response = self.client.get("/api/watched/", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def ids(self, **params):
        return [row["movie"]["tmdb_id"] for row in self.get_list(**params)["results"]]

    def test_only_current_users_watched_titles_are_returned(self):
        watched = self.add_title(1, "Watched")
        unwatched = self.add_title(2, "Unwatched", is_watched=False)
        self.add_title(3, "Another user", user_id=2)
        Movie.objects.create(tmdb_id=4, title="No user data")
        MovieWatchEvent.objects.create(movie=unwatched, watched_date=date(2024, 1, 1))
        WatchlistItem.objects.create(movie=unwatched)
        self.assertEqual(self.ids(), [watched.tmdb_id])

    def test_movies_and_series_filters(self):
        self.add_title(1, "Movie")
        self.add_title(2, "Series", content_type="series")
        self.assertEqual(self.ids(content_type="movie"), [1])
        self.assertEqual(self.ids(content_type="series"), [2])
        self.assertEqual(self.ids(content_type="all"), [1, 2])

    def test_title_search_is_case_insensitive_trimmed_and_composable(self):
        self.add_title(1, "The Matrix")
        self.add_title(2, "Matrix series", content_type="series")
        self.add_title(3, "Other")
        result = self.get_list(search="  MATRIX ", content_type="series")
        self.assertEqual([row["movie"]["tmdb_id"] for row in result["results"]], [2])
        self.assertEqual(result["pagination"]["count"], 1)
        self.assertEqual(self.ids(search="missing"), [])

    def test_date_order_uses_latest_event_then_fallback_with_unknown_dates_last(self):
        unknown = self.add_title(1, "A unknown")
        older = self.add_title(2, "B older", watched_date=date(2020, 1, 1))
        latest = self.add_title(3, "Z latest", watched_date=date(2019, 1, 1))
        MovieWatchEvent.objects.create(movie=latest, watched_date=date(2024, 2, 1))
        MovieWatchEvent.objects.create(movie=latest, watched_date=date(2022, 1, 1))
        MovieWatchEvent.objects.create(movie=unknown, watched_date=None)
        self.assertEqual(self.ids(), [latest.tmdb_id, older.tmdb_id, unknown.tmdb_id])
        self.assertEqual(self.get_list()["results"][0]["last_watched_at"], "2024-02-01")
        self.assertIsNone(self.get_list()["results"][-1]["last_watched_at"])
        self.assertEqual(self.ids(sort="unsupported"), [3, 2, 1])

    def test_watch_counts_include_undated_events_and_exclude_other_users(self):
        movie = self.add_title(1, "Rewatched")
        for watched_date in (date(2024, 1, 1), date(2024, 2, 1), None):
            MovieWatchEvent.objects.create(movie=movie, watched_date=watched_date)
        MovieWatchEvent.objects.create(movie=movie, user_id=2, watched_date=date(2025, 1, 1))
        row = self.get_list()["results"][0]
        self.assertEqual(row["watch_count"], 3)
        self.assertEqual(row["last_watched_at"], "2024-02-01")

    def test_watched_title_without_events_has_one_watch(self):
        self.add_title(1, "Legacy")
        row = self.get_list()["results"][0]
        self.assertEqual(row["watch_count"], 1)
        self.assertIsNone(row["last_watched_at"])

    def test_fallback_date_when_events_are_absent_or_all_undated(self):
        self.add_title(1, "No events", watched_date=date(2024, 2, 1))
        movie = self.add_title(2, "Undated events", watched_date=date(2024, 1, 1))
        MovieWatchEvent.objects.create(movie=movie, watched_date=None)
        MovieWatchEvent.objects.create(movie=movie, watched_date=None)
        rows = self.get_list()["results"]
        self.assertEqual([r["last_watched_at"] for r in rows], ["2024-02-01", "2024-01-01"])
        self.assertEqual([r["watch_count"] for r in rows], [1, 2])

    def test_dated_event_takes_precedence_even_over_newer_fallback(self):
        movie = self.add_title(1, "Event date", watched_date=date(2025, 1, 1))
        MovieWatchEvent.objects.create(movie=movie, watched_date=date(2020, 1, 1))
        self.assertEqual(self.get_list()["results"][0]["last_watched_at"], "2020-01-01")

    def test_rating_sort_descending_with_unrated_last_and_zero_preserved(self):
        self.add_title(1, "Unrated")
        self.add_title(2, "Zero", rating=0)
        self.add_title(3, "Low", rating=2)
        self.add_title(4, "High", rating=9)
        self.assertEqual(self.ids(sort="rating"), [4, 3, 2, 1])

    def test_title_sort_is_alphabetical_independent_of_dates_and_ratings(self):
        self.add_title(1, "Zulu", rating=10, watched_date=date(2025, 1, 1))
        self.add_title(2, "Alpha")
        self.add_title(3, "Middle", rating=5)
        self.assertEqual(self.ids(sort="title"), [2, 3, 1])

    def test_ties_are_stable_by_title_and_tmdb_id_across_pages(self):
        # Insert in reverse order so neither primary key nor insertion order suffices.
        for tmdb_id, title in ((4, "Zulu"), (3, "Same"), (2, "Same"), (1, "Alpha")):
            self.add_title(tmdb_id, title, rating=8, watched_date=date(2024, 1, 1))
        for sort in ("last_watched", "rating", "title"):
            with self.subTest(sort=sort):
                self.assertEqual(self.ids(sort=sort), [1, 2, 3, 4])
                self.assertEqual(self.ids(sort=sort, page_size=2, page=2), [3, 4])

    def test_pagination_boundaries_and_response_contract(self):
        for i in range(1, 6):
            self.add_title(i, f"Title {i}")
        first = self.get_list(page_size=2)
        self.assertEqual(first["pagination"], {
            "count": 5, "page": 1, "page_size": 2, "total_pages": 3,
            "has_next": True, "has_previous": False,
        })
        self.assertEqual(self.ids(page_size=2, page=2), [3, 4])
        last = self.get_list(page_size=2, page=3)
        self.assertEqual([r["movie"]["tmdb_id"] for r in last["results"]], [5])
        self.assertFalse(last["pagination"]["has_next"])
        self.assertTrue(last["pagination"]["has_previous"])
        beyond = self.get_list(page_size=2, page=4)
        self.assertEqual(beyond["results"], [])
        self.assertEqual(beyond["pagination"]["count"], 5)
        self.assertEqual(beyond["pagination"]["page"], 4)
        self.assertEqual(first["results"][0], {
            "movie": {"tmdb_id": 1, "title": "Title 1", "year": 2024,
                      "content_type": "movie", "poster_path": "/poster.jpg"},
            "user_data": {"is_watched": True, "rating": None, "review": "", "watched_date": None},
            "watch_count": 1, "last_watched_at": None, "is_in_watchlist": False,
        })

    def test_empty_list_has_one_page(self):
        self.assertEqual(self.get_list(), {"results": [], "pagination": {
            "count": 0, "page": 1, "page_size": 24, "total_pages": 1,
            "has_next": False, "has_previous": False,
        }})

    def test_invalid_page_parameters_default_safely(self):
        self.add_title(1, "Title")
        for value in ("", "abc", "-2", "1.5", "NaN", "Infinity", "²", "9" * 5000):
            with self.subTest(value=value[:20]):
                pagination = self.get_list(page=value, page_size=value)["pagination"]
                self.assertEqual(pagination["page"], 1)
                self.assertEqual(pagination["page_size"], 24)
        pagination = self.get_list(page="0", page_size="0")["pagination"]
        self.assertEqual((pagination["page"], pagination["page_size"]), (1, 1))
        self.assertEqual(self.get_list(page_size="999999")["pagination"]["page_size"], 100)

    def test_huge_page_returns_empty_without_overflowing_database_offset(self):
        self.add_title(1, "Title")
        with self.assertNumQueries(1):
            result = self.get_list(page="9" * 100)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["pagination"]["page"], int("9" * 100))

    def test_watchlist_sources_do_not_duplicate_rows_or_inflate_event_counts(self):
        movie = self.add_title(1, "Multiple sources")
        self.add_title(2, "Not in watchlist")
        other_user = self.add_title(3, "Other user's watchlist")
        for source in ("manual", "letterboxd", "google"):
            WatchlistItem.objects.create(movie=movie, source=source)
        WatchlistItem.objects.create(movie=other_user, user_id=2)
        for day in (1, 2, 3):
            MovieWatchEvent.objects.create(movie=movie, watched_date=date(2024, 1, day))
        result = self.get_list(sort="title")
        rows = {r["movie"]["tmdb_id"]: r for r in result["results"]}
        self.assertEqual(result["pagination"]["count"], 3)
        self.assertEqual(len(result["results"]), 3)
        self.assertTrue(rows[1]["is_in_watchlist"])
        self.assertFalse(rows[2]["is_in_watchlist"])
        self.assertFalse(rows[3]["is_in_watchlist"])
        self.assertEqual(rows[1]["watch_count"], 3)

    def test_database_pagination_and_queries_stay_bounded_as_watched_list_grows(self):
        for target_count in (20, 200, 1000):
            current = Movie.objects.count()
            movies = Movie.objects.bulk_create([
                Movie(tmdb_id=i, title=f"Title {i:04d}")
                for i in range(current + 1, target_count + 1)
            ])
            UserMovieData.objects.bulk_create([
                UserMovieData(movie=m, is_watched=True) for m in movies
            ])
            MovieWatchEvent.objects.bulk_create([MovieWatchEvent(movie=m) for m in movies])
            WatchlistItem.objects.bulk_create([WatchlistItem(movie=m) for m in movies])
            with self.subTest(count=target_count), \
                    patch.object(UserMovieData, "from_db", wraps=UserMovieData.from_db) as hydrate, \
                    patch.object(MovieWatchEvent, "from_db", wraps=MovieWatchEvent.from_db) as events, \
                    CaptureQueriesContext(connection) as queries:
                result = self.get_list(page=3, page_size=7, sort="title")
            self.assertEqual(result["pagination"]["count"], target_count)
            expected_ids = list(range(15, min(22, target_count + 1)))
            self.assertEqual([r["movie"]["tmdb_id"] for r in result["results"]], expected_ids)
            self.assertEqual(hydrate.call_count, len(expected_ids))
            events.assert_not_called()
            self.assertEqual(len(queries), 2, [q["sql"] for q in queries])
            self.assertIn('COUNT(*)', queries[0]["sql"])
            self.assertRegex(queries[1]["sql"], r"LIMIT 7 OFFSET 14$")
            self.assertTrue(all(r["is_in_watchlist"] for r in result["results"]))
            self.assertTrue(all(r["watch_count"] == 1 for r in result["results"]))
