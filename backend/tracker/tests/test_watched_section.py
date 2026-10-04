"""GET /api/watched/report/ — the Watchlist report scoped to watched titles.

The Watched section reuses the watchlist report pipeline rather than a second
watched-list model: the titles come from ``UserMovieData.is_watched`` (the
flag the existing mark/unmark endpoints write) and every other aspect —
filters, sort, summary, pagination, watchability evaluation — is the code
already covered by ``WatchlistReportEndpointTests``.

No live HTTP: offers come from the ``local_offer`` factory.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase

from tracker.models import Movie, UserMovieData, WatchlistItem
from tracker.tests.availability_factories import local_offer


def _mark_watched(movie, watched_date=None, rating=None, user_id=1):
    return UserMovieData.objects.create(
        user_id=user_id,
        movie=movie,
        is_watched=True,
        watched_date=watched_date,
        rating=rating,
    )


class WatchedReportEmptyTests(TestCase):
    """Nothing watched yet → an empty report, not an error."""

    def test_empty_when_no_user_data_exists(self):
        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["report"], [])
        self.assertEqual(data["summary"]["total_movies"], 0)
        self.assertEqual(data["summary"]["countries"], [])
        self.assertEqual(data["summary"]["services"], [])
        self.assertEqual(data["pagination"]["count"], 0)
        self.assertEqual(data["pagination"]["total_pages"], 1)
        self.assertFalse(data["pagination"]["has_next"])

    def test_empty_when_only_watchlisted_but_never_watched(self):
        movie = Movie.objects.create(tmdb_id=901, title="Still Wanting", year=2024)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        # A watchlist row that was never watched must not leak into Watched.
        UserMovieData.objects.create(user_id=1, movie=movie, is_watched=False)

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["report"], [])

    def test_excludes_other_users_watched_rows(self):
        movie = Movie.objects.create(tmdb_id=902, title="Someone Else", year=2024)
        _mark_watched(movie, user_id=2)

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["report"], [])


class WatchedReportContentTests(TestCase):
    """Loading the section: movies, series, and the watch-state payload."""

    def test_returns_watched_movies_with_is_watched_and_user_data(self):
        movie = Movie.objects.create(
            tmdb_id=910, title="Watched Movie", year=2021, content_type="movie"
        )
        _mark_watched(movie, watched_date=date(2021, 6, 1), rating=8.0)

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(len(data["report"]), 1)
        row = data["report"][0]
        self.assertEqual(row["movie"]["tmdb_id"], 910)
        self.assertEqual(row["movie"]["title"], "Watched Movie")
        self.assertTrue(row["is_watched"])
        self.assertTrue(row["user_data"]["is_watched"])
        self.assertEqual(row["user_data"]["rating"], 8.0)
        self.assertEqual(data["summary"]["total_movies"], 1)

    def test_includes_watched_series_with_their_content_type(self):
        show = Movie.objects.create(
            tmdb_id=911, title="Watched Show", year=2019, content_type="series"
        )
        _mark_watched(show, watched_date=date(2019, 5, 5))

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        row = resp.json()["report"][0]
        self.assertEqual(row["movie"]["content_type"], "series")

    def test_mixed_movie_and_tv_titles_are_both_returned(self):
        movie = Movie.objects.create(
            tmdb_id=920, title="Film", year=2020, content_type="movie"
        )
        show = Movie.objects.create(
            tmdb_id=921, title="Series", year=2021, content_type="series"
        )
        _mark_watched(movie, watched_date=date(2021, 1, 1))
        _mark_watched(show, watched_date=date(2020, 1, 1))

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        report = resp.json()["report"]
        self.assertEqual(len(report), 2)
        by_tmdb = {row["movie"]["tmdb_id"]: row for row in report}
        self.assertEqual(by_tmdb[920]["movie"]["content_type"], "movie")
        self.assertEqual(by_tmdb[921]["movie"]["content_type"], "series")
        # Most recently watched first.
        self.assertEqual([row["movie"]["tmdb_id"] for row in report], [920, 921])

    def test_watchability_is_evaluated_like_the_watchlist_report(self):
        movie = Movie.objects.create(tmdb_id=930, title="Watchable", year=2022)
        _mark_watched(movie, watched_date=date(2022, 3, 3))
        local_offer(
            movie,
            country="US",
            service="netflix",
            quality="hd",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

        resp = self.client.get("/api/watched/report/?current_country=US")

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        row = data["report"][0]
        self.assertEqual(row["best_service"], "netflix")
        self.assertEqual(row["best_country"], "US")
        self.assertEqual(row["available_countries"], ["US"])
        self.assertEqual(data["summary"]["available_movies"], 1)

    def test_undated_watched_rows_are_listed_last_not_dropped(self):
        undated = Movie.objects.create(tmdb_id=940, title="No Date", year=2020)
        dated = Movie.objects.create(tmdb_id=941, title="Dated", year=2021)
        _mark_watched(undated, watched_date=None)
        _mark_watched(dated, watched_date=date(2021, 1, 1))

        resp = self.client.get("/api/watched/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        report = resp.json()["report"]
        self.assertEqual(len(report), 2)
        self.assertEqual(report[-1]["movie"]["tmdb_id"], 940)

    def test_unwatching_removes_the_title_from_the_section(self):
        movie = Movie.objects.create(tmdb_id=950, title="Oops", year=2020)
        _mark_watched(movie, watched_date=date(2020, 1, 1))
        self.assertEqual(len(self.client.get("/api/watched/report/").json()["report"]), 1)

        # The existing unmark endpoint, unchanged, drives the removal.
        unmark = self.client.post(
            f"/api/unwatched/{movie.tmdb_id}/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(unmark.status_code, 200, unmark.content)

        after = self.client.get("/api/watched/report/")
        self.assertEqual(after.status_code, 200, after.content)
        self.assertEqual(after.json()["report"], [])
        # Unwatching must not delete the watch state row itself.
        self.assertTrue(UserMovieData.objects.filter(movie=movie).exists())
        self.assertFalse(
            UserMovieData.objects.get(movie=movie).is_watched
        )

    def test_titles_on_the_watchlist_and_watched_appear_in_both_sections(self):
        movie = Movie.objects.create(tmdb_id=960, title="Both", year=2020)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
        _mark_watched(movie, watched_date=date(2020, 7, 7))

        watched = self.client.get("/api/watched/report/")
        watchlist = self.client.get("/api/watchlist/report/")

        self.assertEqual(watched.status_code, 200, watched.content)
        self.assertEqual(watchlist.status_code, 200, watchlist.content)
        self.assertEqual(len(watched.json()["report"]), 1)
        self.assertEqual(len(watchlist.json()["report"]), 1)


class WatchedReportFilterTests(TestCase):
    """The section inherits the composable filters and sorting."""

    def setUp(self):
        self.alpha = Movie.objects.create(tmdb_id=970, title="Alpha Movie", year=2001)
        self.beta = Movie.objects.create(
            tmdb_id=971, title="Beta Series", year=2015, content_type="series"
        )
        _mark_watched(self.alpha, watched_date=date(2020, 1, 1))
        _mark_watched(self.beta, watched_date=date(2021, 1, 1))

    def _tmdb_ids(self, query=""):
        resp = self.client.get(f"/api/watched/report/{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return [row["movie"]["tmdb_id"] for row in resp.json()["report"]]

    def test_text_search_filters_by_title(self):
        self.assertEqual(self._tmdb_ids("?q=Beta"), [971])

    def test_content_type_filter_selects_series(self):
        self.assertEqual(self._tmdb_ids("?type=series"), [971])

    def test_content_type_filter_selects_movies(self):
        self.assertEqual(self._tmdb_ids("?type=movie"), [970])

    def test_year_range_filter(self):
        self.assertEqual(self._tmdb_ids("?year_min=2010"), [971])

    def test_sort_by_title_asc(self):
        self.assertEqual(self._tmdb_ids("?sort_by=title_asc"), [970, 971])

    def test_sort_by_year_desc_overrides_default_recent_first(self):
        self.assertEqual(self._tmdb_ids("?sort_by=year_desc"), [971, 970])

    def test_no_match_returns_empty_report(self):
        self.assertEqual(self._tmdb_ids("?q=Nothing Matches This"), [])


class WatchedReportPaginationTests(TestCase):
    def setUp(self):
        for i in range(5):
            movie = Movie.objects.create(
                tmdb_id=980 + i, title=f"Title {i}", year=2000 + i
            )
            _mark_watched(movie, watched_date=date(2020, 1, 1) + timedelta(days=i))

    def test_pagination_splits_pages_and_reports_totals(self):
        first = self.client.get("/api/watched/report/?page=1&page_size=2")
        self.assertEqual(first.status_code, 200, first.content)
        data = first.json()
        self.assertEqual(data["pagination"]["count"], 5)
        self.assertEqual(data["pagination"]["total_pages"], 3)
        self.assertTrue(data["pagination"]["has_next"])
        self.assertFalse(data["pagination"]["has_previous"])
        self.assertEqual(len(data["report"]), 2)

        last = self.client.get("/api/watched/report/?page=3&page_size=2")
        last_data = last.json()
        self.assertEqual(len(last_data["report"]), 1)
        self.assertFalse(last_data["pagination"]["has_next"])
        self.assertTrue(last_data["pagination"]["has_previous"])

    def test_summary_counts_the_whole_filtered_set_not_the_page(self):
        resp = self.client.get("/api/watched/report/?page=1&page_size=2")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["summary"]["total_movies"], 5)


class WatchedVsWatchlistReportParityTests(TestCase):
    """The shared report pipeline must not change Watchlist semantics.

    ``watchlist_report`` and ``watched_report`` now both call
    ``_build_report``. These tests pin the seam: the Watchlist keeps listing
    every watchlisted title (watched or not, its own ordering), Watched lists
    only watched ones, and both answer with the same filters, sorting,
    pagination, user isolation and empty behavior.
    """

    def setUp(self):
        self.film = Movie.objects.create(
            tmdb_id=990, title="Alpha", year=2001, content_type="movie"
        )
        self.show = Movie.objects.create(
            tmdb_id=991, title="Beta", year=2015, content_type="series"
        )
        # Both titles are on the watchlist; only one has been watched.
        for movie in (self.film, self.show):
            WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        _mark_watched(self.film, watched_date=date(2020, 1, 1))

    def _get(self, endpoint, query=""):
        resp = self.client.get(f"/api/{endpoint}/{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_watchlist_keeps_unwatched_titles_that_watched_hides(self):
        watchlist = self._get("watchlist/report")
        watched = self._get("watched/report")

        self.assertEqual(
            sorted(r["movie"]["tmdb_id"] for r in watchlist["report"]),
            [990, 991],
        )
        self.assertEqual([r["movie"]["tmdb_id"] for r in watched["report"]], [990])

    def test_watchlist_rows_still_report_is_watched_per_title(self):
        report = {
            r["movie"]["tmdb_id"]: r for r in self._get("watchlist/report")["report"]
        }

        # The unwatched title is still listed with is_watched False and no
        # user_data — the refactor neither filters nor coerces that flag.
        self.assertFalse(report[991]["is_watched"])
        self.assertIsNone(report[991]["user_data"])
        self.assertTrue(report[990]["is_watched"])

    def test_both_endpoints_answer_the_same_shape(self):
        watchlist = self._get("watchlist/report")
        watched = self._get("watched/report")

        self.assertEqual(set(watchlist), set(watched), "top-level keys")
        self.assertEqual(
            set(watchlist["pagination"]), set(watched["pagination"]), "pagination keys"
        )
        self.assertEqual(
            set(watchlist["summary"]), set(watched["summary"]), "summary keys"
        )
        self.assertEqual(
            set(watchlist["report"][0]),
            set(watched["report"][0]),
            "row keys",
        )

    def test_shared_filters_apply_identically_to_both(self):
        # Watched is scoped to is_watched, so it can only ever be a subset of
        # the Watchlist result for the same query.
        for query, watchlist_expected, watched_expected in (
            ("?q=Beta", [991], []),
            ("?type=series", [991], []),
            ("?type=movie", [990], [990]),
            ("?year_min=2010", [991], []),
            ("?sort_by=title_asc", [990, 991], [990]),
            ("?sort_by=year_desc", [991, 990], [990]),
            ("?q=No Such Title", [], []),
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    [r["movie"]["tmdb_id"] for r in self._get("watchlist/report", query)["report"]],
                    watchlist_expected,
                )
                self.assertEqual(
                    [r["movie"]["tmdb_id"] for r in self._get("watched/report", query)["report"]],
                    watched_expected,
                )

    def test_quality_and_status_filters_apply_to_both(self):
        local_offer(
            self.film,
            country="US",
            service="netflix",
            quality="4k",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

        # 4K offer on the watched title: the quality filter keeps it.
        watched_4k = self._get("watched/report", "?quality=4k,uhd")
        self.assertEqual([r["movie"]["tmdb_id"] for r in watched_4k["report"]], [990])

        # The unwatched title has no offer at all, so it fails the quality filter.
        watchlist_4k = self._get("watchlist/report", "?quality=4k,uhd")
        self.assertEqual([r["movie"]["tmdb_id"] for r in watchlist_4k["report"]], [990])

        # Min-rating (score_min) filter runs on both sides.
        for endpoint in ("watchlist/report", "watched/report"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self._get(endpoint, "?score_min=9.9")["report"], []
                )

    def test_provider_filter_narrows_both_endpoints(self):
        local_offer(
            self.show,
            country="US",
            service="netflix",
            quality="hd",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

        watchlist = self._get("watchlist/report", "?provider=netflix")
        watched = self._get("watched/report", "?provider=netflix")

        self.assertEqual([r["movie"]["tmdb_id"] for r in watchlist["report"]], [991])
        # The watched title has no netflix offer, so it drops out.
        self.assertEqual(watched["report"], [])

    def test_country_filter_narrows_both_endpoints(self):
        local_offer(
            self.show,
            country="GB",
            service="prime",
            quality="hd",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

        watchlist = self._get("watchlist/report", "?country=GB")
        watched = self._get("watched/report", "?country=GB")

        self.assertEqual([r["movie"]["tmdb_id"] for r in watchlist["report"]], [991])
        self.assertEqual(watched["report"], [])

    def test_pagination_metadata_is_consistent_across_both(self):
        for i in range(3):
            movie = Movie.objects.create(
                tmdb_id=995 + i, title=f"Extra {i}", year=2010 + i
            )
            WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
            _mark_watched(movie, watched_date=date(2019, 1, 1) + timedelta(days=i))

        for endpoint in ("watchlist/report", "watched/report"):
            with self.subTest(endpoint=endpoint):
                first = self._get(endpoint, "?page=1&page_size=2")
                self.assertEqual(len(first["report"]), 2)
                self.assertTrue(first["pagination"]["has_next"])
                self.assertFalse(first["pagination"]["has_previous"])
                # Summary counts the filtered set, not the page.
                self.assertEqual(
                    first["summary"]["total_movies"],
                    first["pagination"]["count"],
                )
                self.assertEqual(
                    first["pagination"]["count"],
                    len(self._get(endpoint, "?page_size=100")["report"]),
                )

                total_pages = first["pagination"]["total_pages"]
                last = self._get(endpoint, f"?page={total_pages}&page_size=2")
                self.assertFalse(last["pagination"]["has_next"])
                self.assertTrue(last["pagination"]["has_previous"])
                self.assertGreaterEqual(len(last["report"]), 1)

        # The watchlist keeps the unwatched title too, so it lists one more.
        self.assertEqual(
            self._get("watchlist/report")["pagination"]["count"], 5
        )
        self.assertEqual(self._get("watched/report")["pagination"]["count"], 4)

    def test_user_isolation_holds_for_both_endpoints(self):
        other_movie = Movie.objects.create(tmdb_id=998, title="Other User", year=2000)
        WatchlistItem.objects.create(user_id=2, movie=other_movie, source="manual")
        _mark_watched(other_movie, watched_date=date(2020, 1, 1), user_id=2)

        watchlist = self._get("watchlist/report")
        watched = self._get("watched/report")

        self.assertNotIn(998, [r["movie"]["tmdb_id"] for r in watchlist["report"]])
        self.assertNotIn(998, [r["movie"]["tmdb_id"] for r in watched["report"]])

    def test_empty_watchlist_stays_empty_after_refactor(self):
        WatchlistItem.objects.all().delete()
        UserMovieData.objects.all().delete()

        for endpoint in ("watchlist/report", "watched/report"):
            with self.subTest(endpoint=endpoint):
                data = self._get(endpoint)
                self.assertEqual(data["report"], [])
                self.assertEqual(data["summary"]["total_movies"], 0)
                self.assertEqual(data["pagination"]["count"], 0)
                self.assertEqual(data["pagination"]["total_pages"], 1)