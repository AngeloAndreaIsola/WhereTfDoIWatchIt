"""Actor multi-AND and duration min/max filtering on /api/watched/report/.

Recovered from the feature/watched-section worktree.
Covers actor multi-AND (every selected actor must match), duration min/max,
and their combinations. No live HTTP: titles are created directly.
"""
from __future__ import annotations

from datetime import date

from django.test import TestCase

from tracker.models import Movie, UserMovieData


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
