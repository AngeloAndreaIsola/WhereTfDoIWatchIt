from django.test import TestCase
from tracker.models import Movie, Preference
from tracker.services.filtering import apply_composable_filters


class DurationFilterTests(TestCase):
    def setUp(self):
        self.pref = Preference.objects.create(user_id=1)
        # Create movies across various runtimes
        self.m_under_30 = Movie.objects.create(
            tmdb_id=1, title="Short Short", content_type="movie", runtime_minutes=25, year=2020
        )
        self.m_boundary_30 = Movie.objects.create(
            tmdb_id=2, title="Exact Thirty", content_type="movie", runtime_minutes=30, year=2021
        )
        self.m_boundary_31 = Movie.objects.create(
            tmdb_id=3, title="Thirty One", content_type="movie", runtime_minutes=31, year=2021
        )
        self.m_boundary_60 = Movie.objects.create(
            tmdb_id=4, title="Exact Sixty", content_type="movie", runtime_minutes=60, year=2022
        )
        self.m_boundary_61 = Movie.objects.create(
            tmdb_id=5, title="Sixty One", content_type="movie", runtime_minutes=61, year=2022
        )
        self.m_boundary_120 = Movie.objects.create(
            tmdb_id=6, title="Two Hours", content_type="movie", runtime_minutes=120, year=2023
        )
        self.m_boundary_121 = Movie.objects.create(
            tmdb_id=7, title="Two Hours One", content_type="movie", runtime_minutes=121, year=2023
        )
        self.m_boundary_180 = Movie.objects.create(
            tmdb_id=8, title="Three Hours", content_type="movie", runtime_minutes=180, year=2024
        )
        self.m_boundary_181 = Movie.objects.create(
            tmdb_id=9, title="Over Three Hours", content_type="movie", runtime_minutes=181, year=2024
        )
        self.m_very_long = Movie.objects.create(
            tmdb_id=10, title="Epic Movie", content_type="movie", runtime_minutes=220, year=2024
        )

        # Edge cases: None, 0, negative
        self.m_no_runtime = Movie.objects.create(
            tmdb_id=11, title="No Runtime", content_type="movie", runtime_minutes=None, year=2020
        )
        self.m_zero_runtime = Movie.objects.create(
            tmdb_id=12, title="Zero Runtime", content_type="movie", runtime_minutes=0, year=2020
        )
        self.m_neg_runtime = Movie.objects.create(
            tmdb_id=13, title="Negative Runtime", content_type="movie", runtime_minutes=-10, year=2020
        )

        # TV series with runtime and without runtime
        self.s_with_runtime = Movie.objects.create(
            tmdb_id=14, title="TV Series A", content_type="series", runtime_minutes=45, year=2021
        )
        self.s_no_runtime = Movie.objects.create(
            tmdb_id=15, title="TV Series B", content_type="series", runtime_minutes=None, year=2021
        )

    def test_no_filter_includes_all_titles(self):
        """When no duration filter is active, all items including missing/zero/TV are preserved."""
        evaluated, _ = apply_composable_filters(Movie.objects.all(), {}, self.pref)
        all_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(len(all_ids), 15)
        self.assertIn(self.m_no_runtime.id, all_ids)
        self.assertIn(self.m_zero_runtime.id, all_ids)
        self.assertIn(self.s_with_runtime.id, all_ids)
        self.assertIn(self.s_no_runtime.id, all_ids)

    def test_preset_under_30_minutes(self):
        """Under 30 min: <= 30 minutes (duration_max=30)."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_max": "30"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_under_30.id, self.m_boundary_30.id})

    def test_preset_30_to_60_minutes(self):
        """30–60 min: 31–60 minutes (duration_min=31, duration_max=60)."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "31", "duration_max": "60"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_31.id, self.m_boundary_60.id})

    def test_preset_1_to_2_hours(self):
        """1–2 hours: 61–120 minutes (duration_min=61, duration_max=120)."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "61", "duration_max": "120"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_61.id, self.m_boundary_120.id})

    def test_preset_2_to_3_hours(self):
        """2–3 hours: 121–180 minutes (duration_min=121, duration_max=180)."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "121", "duration_max": "180"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_121.id, self.m_boundary_180.id})

    def test_preset_over_3_hours(self):
        """Over 3 hours: >= 181 minutes (duration_min=181)."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "181"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_181.id, self.m_very_long.id})

    def test_custom_min_and_max(self):
        """Custom range: duration_min=50, duration_max=125."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "50", "duration_max": "125"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(
            movie_ids,
            {
                self.m_boundary_60.id,
                self.m_boundary_61.id,
                self.m_boundary_120.id,
                self.m_boundary_121.id,
            },
        )

    def test_missing_and_invalid_runtimes_excluded_when_filter_active(self):
        """Titles with None, 0, or negative runtimes are excluded when duration filter is set."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_max": "300"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertNotIn(self.m_no_runtime.id, movie_ids)
        self.assertNotIn(self.m_zero_runtime.id, movie_ids)
        self.assertNotIn(self.m_neg_runtime.id, movie_ids)

    def test_tv_shows_excluded_when_filter_active(self):
        """TV series are treated as unknown episode duration and excluded when duration filter is active."""
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_max": "100"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertNotIn(self.s_with_runtime.id, movie_ids)
        self.assertNotIn(self.s_no_runtime.id, movie_ids)

    def test_invalid_parameters_handled_gracefully(self):
        """Non-numeric values, negative values, and min > max are handled safely."""
        # non-numeric -> treated as no restriction
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "abc", "duration_max": "xyz"}, self.pref
        )
        self.assertEqual(len(evaluated), 15)

        # min > max -> ignored as invalid range
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(), {"duration_min": "100", "duration_max": "50"}, self.pref
        )
        self.assertEqual(len(evaluated), 15)

    def test_in_memory_list_filtering(self):
        """Filtering works identically on a Python list of Movie instances."""
        all_movies = list(Movie.objects.all())
        evaluated, _ = apply_composable_filters(
            all_movies, {"duration_min": "31", "duration_max": "60"}, self.pref
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_31.id, self.m_boundary_60.id})

    def test_combining_duration_with_other_filters(self):
        """Duration combines with content_type, year, and search q."""
        # Only year 2022 and 1-2 hours
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(),
            {"duration_min": "61", "duration_max": "120", "year": "2022"},
            self.pref,
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_61.id})

        # Query search + duration
        evaluated, _ = apply_composable_filters(
            Movie.objects.all(),
            {"duration_max": "30", "q": "Exact"},
            self.pref,
        )
        movie_ids = {item["movie"].id for item in evaluated}
        self.assertEqual(movie_ids, {self.m_boundary_30.id})
