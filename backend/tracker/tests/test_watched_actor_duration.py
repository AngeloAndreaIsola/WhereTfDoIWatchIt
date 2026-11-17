"""Actor multi-AND and duration min/max filtering on /api/watched/report/.

Recovered from the feature/watched-section worktree.
Covers actor multi-AND (every selected actor must match), duration min/max,
and their combinations. No live HTTP: titles are created directly.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from django.test import TestCase

from tracker.models import Movie, MovieWatchEvent, UserMovieData


def _mark_watched(movie, watched_date=date(2024, 1, 1), rating=None, user_id=1):
    return UserMovieData.objects.create(
        user_id=user_id,
        movie=movie,
        is_watched=True,
        watched_date=watched_date,
        rating=rating,
    )


class WatchedActorDurationFilterTests(TestCase):
    """Actor multi-AND and duration min/max on the watched report endpoint."""

    def setUp(self):
        # Three watched movies with cast and runtime metadata.
        self.m1 = Movie.objects.create(
            tmdb_id=101, title="Actor A + B Movie", year=2020,
            content_type="movie", runtime_minutes=120,
            cast=[{"id": 1, "name": "Actor One"}, {"id": 2, "name": "Actor Two"}],
        )
        self.m2 = Movie.objects.create(
            tmdb_id=102, title="Actor A Only Movie", year=2021,
            content_type="movie", runtime_minutes=90,
            cast=[{"id": 1, "name": "Actor One"}],
        )
        self.m3 = Movie.objects.create(
            tmdb_id=103, title="No Cast Movie", year=2022,
            content_type="movie", runtime_minutes=150,
            cast=[],
        )
        self.m4 = Movie.objects.create(
            tmdb_id=104, title="No Runtime Movie", year=2023,
            content_type="movie",
            cast=[{"id": 1, "name": "Actor One"}, {"id": 2, "name": "Actor Two"}],
            runtime_minutes=None,
        )
        for m in (self.m1, self.m2, self.m3, self.m4):
            _mark_watched(m)

    def _tmdb_ids(self, query=""):
        resp = self.client.get(f"/api/watched/report/?{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return [r["movie"]["tmdb_id"] for r in resp.json()["report"]]

    # --- Actor multi-AND ---

    def test_single_actor_match(self):
        self.assertEqual(
            sorted(self._tmdb_ids("actor_id=1")),
            [101, 102, 104],
        )

    def test_actor_multi_and_requires_all(self):
        # Both Actor 1 AND Actor 2 must be in the cast.
        self.assertEqual(
            sorted(self._tmdb_ids("actor_id=1&actor_id=2")),
            [101, 104],
        )

    def test_duplicate_actor_selections_are_idempotent(self):
        self.assertEqual(
            sorted(self._tmdb_ids("actor_id=1&actor_id=1")),
            [101, 102, 104],
        )

    def test_unknown_actor_id_returns_none(self):
        self.assertEqual(self._tmdb_ids("actor_id=9999"), [])

    def test_title_missing_cast_excluded_when_actor_filtered(self):
        # m3 has empty cast; it should not match any actor filter.
        self.assertNotIn(103, self._tmdb_ids("actor_id=1"))

    # --- Duration min/max ---

    def test_duration_min(self):
        self.assertEqual(
            sorted(self._tmdb_ids("duration_min=100")),
            [101, 103],
        )

    def test_duration_max(self):
        self.assertEqual(
            sorted(self._tmdb_ids("duration_max=100")),
            [102],
        )

    def test_duration_range_inclusive(self):
        self.assertEqual(
            sorted(self._tmdb_ids("duration_min=90&duration_max=120")),
            [101, 102],
        )

    def test_missing_duration_excluded(self):
        # m4 has runtime_minutes=None; excluded when duration filter active.
        self.assertNotIn(104, self._tmdb_ids("duration_min=1"))

    def test_inverted_duration_range_is_ignored(self):
        # Preserve the existing shared duration-filter contract. Invalid
        # bounds do not silently change results on other catalogue surfaces.
        self.assertEqual(
            sorted(self._tmdb_ids("duration_min=200&duration_max=100")),
            [101, 102, 103, 104],
        )

    # --- Combinations ---

    def test_actor_and_duration_combine(self):
        self.assertEqual(
            sorted(self._tmdb_ids("actor_id=1&actor_id=2&duration_min=100")),
            [101],
        )

    def test_actor_and_duration_combine_max(self):
        self.assertEqual(
            sorted(self._tmdb_ids("actor_id=1&duration_max=100")),
            [102],
        )

    def test_composes_with_content_type(self):
        # All are movies; filtering by series returns nothing.
        self.assertEqual(self._tmdb_ids("actor_id=1&content_type=series"), [])

    def test_actor_filter_with_no_watched_titles_returns_empty(self):
        UserMovieData.objects.all().delete()
        self.assertEqual(self._tmdb_ids("actor_id=1"), [])

    def test_report_preserves_watched_history_fields(self):
        response = self.client.get("/api/watched/report/")
        self.assertEqual(response.status_code, 200)
        entry = next(row for row in response.json()["report"] if row["movie"]["tmdb_id"] == 101)
        self.assertTrue(entry["user_data"]["is_watched"])
        self.assertEqual(entry["user_data"]["watched_date"], "2024-01-01")
        self.assertEqual(entry["watch_count"], 1)
        self.assertEqual(entry["last_watched_at"], "2024-01-01")

    def test_watched_rating_sort_uses_user_rating(self):
        UserMovieData.objects.filter(movie=self.m1).update(rating=2)
        UserMovieData.objects.filter(movie=self.m2).update(rating=9)
        ids = self._tmdb_ids("sort_by=watched_rating_desc")
        self.assertEqual(ids[:2], [102, 101])

    def test_report_event_date_drives_default_order_and_watch_count(self):
        UserMovieData.objects.filter(movie=self.m1).update(watched_date=date(2024, 1, 1))
        MovieWatchEvent.objects.create(movie=self.m1, watched_date=date(2024, 6, 1))
        MovieWatchEvent.objects.create(movie=self.m1, watched_date=date(2024, 5, 1), rewatch=True)
        response = self.client.get("/api/watched/report/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["report"][0]["movie"]["tmdb_id"], 101)
        row = next(r for r in response.json()["report"] if r["movie"]["tmdb_id"] == 101)
        self.assertEqual(row["last_watched_at"], "2024-06-01")
        self.assertEqual(row["watch_count"], 2)

    def test_sql_pagination_pages_actor_filtered_results_without_gaps(self):
        created = []
        for n in range(30):
            movie = Movie.objects.create(
                tmdb_id=2000 + n, title=f"Paged {n:02d}", content_type="movie",
                runtime_minutes=90, cast=[{"id": 77, "name": "Page Actor"}] if n % 2 == 0 else [],
            )
            _mark_watched(movie, watched_date=date(2024, 1, 1))
            created.append(movie.tmdb_id)
        pages = []
        for page in (1, 2, 3):
            response = self.client.get(
                f"/api/watched/report/?actor_id=77&duration_min=90&page={page}&page_size=5"
            )
            self.assertEqual(response.status_code, 200, response.content)
            body = response.json()
            self.assertEqual(body["pagination"]["count"], 15)
            self.assertEqual(body["pagination"]["total_pages"], 3)
            pages.extend(row["movie"]["tmdb_id"] for row in body["report"])
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(pages, [2000 + n for n in range(0, 30, 2)])

    def test_unfiltered_report_hydrates_only_requested_page(self):
        for n in range(40):
            movie = Movie.objects.create(tmdb_id=3000 + n, title=f"Large {n:02d}")
            _mark_watched(movie)
        with patch.object(UserMovieData, "from_db", wraps=UserMovieData.from_db) as hydrated, \
                CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/watched/report/?page=2&page_size=7")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pagination"]["count"], 44)
        self.assertEqual(hydrated.call_count, 7)
        self.assertLessEqual(len(queries), 10)

    def test_expensive_availability_filter_refuses_unbounded_candidate_scan(self):
        movies = Movie.objects.bulk_create([
            Movie(tmdb_id=10000 + n, title=f"Candidate {n}") for n in range(5001)
        ])
        UserMovieData.objects.bulk_create([UserMovieData(movie=movie, is_watched=True) for movie in movies])
        response = self.client.get("/api/watched/report/?watchable=true")
        self.assertEqual(response.status_code, 400)
        self.assertIn("too many watched titles", response.json()["error"])

    def test_every_filterbar_sort_is_global_and_stable_across_pages(self):
        sort_rows = []
        for n in range(35):
            title = f"Sortcase {34 - n:02d}"
            year = None if n % 9 == 0 else 1990 + (n % 6)
            tmdb_rating = None if n % 8 == 0 else float(n % 7)
            personal_rating = None if n % 10 == 0 else float((n * 3) % 11)
            last_date = None if n == 0 else date(2024, 1 + (n % 12), 1 + (n % 27))
            movie = Movie.objects.create(
                tmdb_id=4000 + n,
                title=title,
                year=year,
                tmdb_rating=tmdb_rating,
                content_type="movie",
                runtime_minutes=90,
                genre_ids=[1] if n % 3 == 0 else [],
                cast=[{"id": 88, "name": "Sort Actor"}] if n % 2 == 0 else [],
            )
            _mark_watched(movie, watched_date=date(2023, 1, 1) if n == 0 else last_date,
                          rating=personal_rating)
            if last_date:
                MovieWatchEvent.objects.create(movie=movie, watched_date=last_date)
            sort_rows.append({"id": movie.tmdb_id, "title": title, "year": year,
                              "tmdb_rating": tmdb_rating, "rating": personal_rating,
                              "last": date(2023, 1, 1) if n == 0 else last_date})

        sort_cases = (
            ("", "last", True),
            ("watched_rating_desc", "rating", True),
            ("rating_desc", "tmdb_rating", True),
            ("year_desc", "year", True),
            ("year_asc", "year", False),
            ("title_asc", "title", False),
            ("title_desc", "title", True),
        )
        for sort_param, field, descending in sort_cases:
            with self.subTest(sort=sort_param or "default"):
                if field == "title":
                    expected = sorted(sort_rows, key=lambda row: (row["title"], row["id"]))
                    expected.sort(key=lambda row: row["title"].lower(), reverse=descending)
                elif descending:
                    # Keep NULLs last and title/id tie breakers ascending.
                    expected = sorted(sort_rows, key=lambda row: (row[field] is None,
                        -(row[field].toordinal() if field == "last" and row[field] else row[field] or 0),
                        row["title"].lower(), row["title"], row["id"]))
                else:
                    expected = sorted(sort_rows, key=lambda row: (row[field] is None,
                        row[field].toordinal() if field == "last" and row[field] else row[field] or 0,
                        row["title"].lower(), row["title"], row["id"]))

                all_ids = []
                for page in range(1, 8):
                    query = f"q=Sortcase&page={page}&page_size=5"
                    if sort_param:
                        query += f"&sort_by={sort_param}"
                    response = self.client.get(f"/api/watched/report/?{query}")
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(response.json()["pagination"]["count"], 35)
                    all_ids.extend(row["movie"]["tmdb_id"] for row in response.json()["report"])
                self.assertEqual(all_ids, [row["id"] for row in expected])
                self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_sorted_pages_remain_global_after_actor_duration_and_genre_filters(self):
        for n in range(35):
            movie = Movie.objects.create(
                tmdb_id=5000 + n,
                title=f"Filtered sort {34 - n:02d}",
                year=2000 + n % 5,
                tmdb_rating=float(n % 9),
                content_type="movie",
                runtime_minutes=90 if n % 4 else 160,
                genre_ids=[1] if n % 3 == 0 else [],
                cast=[{"id": 89, "name": "Filter Actor"}] if n % 2 == 0 else [],
            )
            _mark_watched(movie, watched_date=date(2024, 1, 1), rating=float(n % 10))
        expected = [5000 + n for n in range(30, -1, -1) if n % 6 == 0]
        actual = []
        for page in (1, 2):
            response = self.client.get(
                f"/api/watched/report/?q=Filtered&actor_id=89&duration_min=90&duration_max=160&genre=1&sort_by=title_asc&page={page}&page_size=5"
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["pagination"]["count"], len(expected))
            actual.extend(row["movie"]["tmdb_id"] for row in response.json()["report"])
        self.assertEqual(actual, expected)
