"""The `watched` filter (all | watched | unwatched) across the filter bar's consumers.

One filter, three surfaces — ``/api/catalog/unified/`` (catalog),
``/api/watchlist/report/`` and ``/api/watched/report/`` — so the tests are
grouped by surface rather than by filter value.

The invariant that matters most is the one a reader cannot see in the code:
watched state comes from ``UserMovieData.is_watched`` (the row the mark/unmark
endpoints write) and NEVER from ``WatchlistItem``. Being on the watchlist
means "I want to see this", which is a different question, so a watchlisted-
but-unwatched title must read as unwatched everywhere.

No live HTTP: offers come from the ``local_offer`` factory.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from tracker.models import Movie, Provider, UserMovieData, WatchlistItem
from tracker.tests.availability_factories import local_offer


def _mark_watched(movie, watched_date=date(2024, 1, 1), rating=None, user_id=1):
    return UserMovieData.objects.create(
        user_id=user_id,
        movie=movie,
        is_watched=True,
        watched_date=watched_date,
        rating=rating,
    )


class WatchedFilterFixture(TestCase):
    """Three titles: a watched movie, a watched series, an unwatched movie.

    All three are on the watchlist so the report endpoints (which are scoped to
    WatchlistItem) exercise the filter; the dedicated
    ``WatchedFilterIsNotWatchlistTests`` covers the two memberships separately.
    """

    def setUp(self):
        self.watched_movie = Movie.objects.create(
            tmdb_id=701, title="Seen Film", year=2020, content_type="movie"
        )
        self.watched_series = Movie.objects.create(
            tmdb_id=702, title="Seen Show", year=2019, content_type="series"
        )
        self.unwatched_movie = Movie.objects.create(
            tmdb_id=703, title="Unseen Film", year=2021, content_type="movie"
        )
        _mark_watched(self.watched_movie)
        _mark_watched(self.watched_series)
        for movie in (self.watched_movie, self.watched_series, self.unwatched_movie):
            WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")

    def _tmdb_ids(self, endpoint, query=""):
        resp = self.client.get(f"/api/{endpoint}{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        payload = resp.json()
        rows = payload.get("report", payload.get("results", []))
        return [r["movie"]["tmdb_id"] for r in rows]


class WatchedFilterOnReportsTests(WatchedFilterFixture):
    """The watchlist report and dedicated watched list honour the filter."""

    def test_watched_selects_only_watched_titles(self):
        for endpoint in ("watchlist/report/", "watched/"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    sorted(self._tmdb_ids(endpoint + "?watched=watched")),
                    [701, 702],
                )

    def test_unwatched_selects_only_unwatched_titles(self):
        # The Watchlist report is scoped to WatchlistItem, so it can show an
        # unwatched title. The watched list is already scoped to watched
        # rows, so "unwatched" can only ever return nothing there — the two
        # scopes intersect, they do not contradict.
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=unwatched"), [703]
        )
        self.assertEqual(self._tmdb_ids("watched/?watched=unwatched"), [])

    def test_all_and_absent_are_equivalent_and_unfiltered(self):
        for endpoint in ("watchlist/report/", "watched/"):
            with self.subTest(endpoint=endpoint):
                baseline = self._tmdb_ids(endpoint)
                self.assertEqual(self._tmdb_ids(endpoint + "?watched=all"), baseline)
                self.assertEqual(self._tmdb_ids(endpoint + "?watched="), baseline)
                self.assertEqual(self._tmdb_ids(endpoint + "?watched=ALL"), baseline)

    def test_movies_and_series_are_both_filtered(self):
        # The watched series survives the watched filter; the unwatched movie
        # does not. Nothing here keys on content_type.
        self.assertIn(702, self._tmdb_ids("watchlist/report/?watched=watched"))
        self.assertNotIn(
            703, self._tmdb_ids("watchlist/report/?watched=watched")
        )
        self.assertNotIn(701, self._tmdb_ids("watchlist/report/?watched=unwatched"))

    def test_unknown_value_falls_back_to_all_rather_than_emptying(self):
        baseline = self._tmdb_ids("watchlist/report/?")
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=bogus"), baseline
        )


class WatchedFilterIsNotWatchlistTests(WatchedFilterFixture):
    """Watchlist membership must never stand in for watched state."""

    def setUp(self):
        super().setUp()
        # 701/702 drop off the watchlist; 703 stays on it and stays unwatched.
        # This separates the two memberships completely.
        WatchlistItem.objects.filter(
            movie__in=[self.watched_movie, self.watched_series]
        ).delete()

    def test_watchlisted_but_unwatched_reads_as_unwatched(self):
        # 703 is on the watchlist and NOT watched.
        self.assertIn(
            703, self._tmdb_ids("watchlist/report/?watched=unwatched")
        )
        self.assertNotIn(
            703, self._tmdb_ids("watchlist/report/?watched=watched")
        )

    def test_watched_but_not_watchlisted_reads_as_watched(self):
        # 702 is watched and deliberately absent from the watchlist, so the
        # watchlist-scoped report hides it while the watched list keeps it.
        self.assertNotIn(
            702, self._tmdb_ids("watchlist/report/?watched=watched")
        )
        self.assertEqual(
            sorted(self._tmdb_ids("watched/?watched=watched")), [701, 702]
        )

    def test_is_watched_flag_agrees_with_the_filter(self):
        resp = self.client.get("/api/watchlist/report/?watched=watched")
        self.assertEqual(resp.status_code, 200, resp.content)
        for row in resp.json()["report"]:
            self.assertTrue(row["is_watched"])
            self.assertTrue(row["user_data"]["is_watched"])


class WatchedFilterComposesTests(WatchedFilterFixture):
    """The new filter must not break the filters already in place."""

    def setUp(self):
        super().setUp()
        self.fourth = Movie.objects.create(
            tmdb_id=704,
            title="Seen Drama",
            year=2020,
            content_type="movie",
            # score_min filters on Movie.tmdb_rating, which is distinct from
            # the user's own UserMovieData.rating — set both so the test
            # exercises the TMDb field the filter actually reads.
            tmdb_rating=9.0,
        )
        _mark_watched(self.fourth, rating=8.0)
        WatchlistItem.objects.create(
            user_id=1, movie=self.fourth, source="manual"
        )
        local_offer(
            self.watched_movie,
            country="US",
            service="netflix",
            quality="4k",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )

    def test_composes_with_content_type(self):
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&type=series"), [702]
        )
        self.assertEqual(
            sorted(self._tmdb_ids("watchlist/report/?watched=watched&type=movie")),
            [701, 704],
        )

    def test_composes_with_search(self):
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&q=Drama"), [704]
        )

    def test_composes_with_year_range(self):
        self.assertEqual(
            sorted(
                self._tmdb_ids("watchlist/report/?watched=watched&year_min=2020")
            ),
            [701, 704],
        )

    def test_composes_with_score_min(self):
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&score_min=8.0"), [704]
        )

    def test_composes_with_country_and_quality(self):
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&country=US"), [701]
        )
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&quality=4k,uhd"),
            [701],
        )

    def test_composes_with_sorting(self):
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&sort_by=title_asc"),
            [704, 701, 702],
        )
        # year_desc sorts on (year, id) with reverse=True, so the 2020 pair
        # comes back id-descending: 704 then 701, then the 2019 series. The
        # watched filter narrows the set without disturbing that ordering.
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=watched&sort_by=year_desc"),
            [704, 701, 702],
        )
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=unwatched&sort_by=title_asc"),
            [703],
        )

    def test_composes_with_pagination(self):
        page = self.client.get(
            "/api/watchlist/report/?watched=watched&page=1&page_size=2"
        )
        self.assertEqual(page.status_code, 200, page.content)
        data = page.json()
        self.assertEqual(data["pagination"]["count"], 3)
        self.assertEqual(data["pagination"]["total_pages"], 2)
        self.assertEqual(len(data["report"]), 2)
        # The summary counts the filtered set, not the page.
        self.assertEqual(data["summary"]["total_movies"], 3)

        second = self.client.get(
            "/api/watchlist/report/?watched=watched&page=2&page_size=2"
        )
        self.assertEqual(len(second.json()["report"]), 1)
        self.assertFalse(second.json()["pagination"]["has_next"])

    def test_user_isolation(self):
        _mark_watched(self.unwatched_movie, watched_date=date(2024, 2, 2), user_id=2)

        # From user 1's perspective the title is still unwatched.
        self.assertEqual(
            self._tmdb_ids("watchlist/report/?watched=unwatched"), [703]
        )
        self.assertNotIn(
            703, self._tmdb_ids("watchlist/report/?watched=watched")
        )

    def test_unmark_flips_the_filter_result(self):
        self.assertIn(701, self._tmdb_ids("watchlist/report/?watched=watched"))

        resp = self.client.post(
            f"/api/unwatched/{self.watched_movie.tmdb_id}/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertNotIn(701, self._tmdb_ids("watchlist/report/?watched=watched"))
        self.assertIn(701, self._tmdb_ids("watchlist/report/?watched=unwatched"))


class UnifiedCatalogWatchedFilterTests(TestCase):
    """The catalog's own endpoint, whose unofficial half is a separate path."""

    def setUp(self):
        self.watched = Movie.objects.create(
            tmdb_id=711, title="Catalog Seen", year=2020, content_type="movie"
        )
        self.unwatched = Movie.objects.create(
            tmdb_id=712, title="Catalog Unseen", year=2021, content_type="movie"
        )
        self.series = Movie.objects.create(
            tmdb_id=713, title="Catalog Seen Show", year=2018, content_type="series"
        )
        _mark_watched(self.watched)
        _mark_watched(self.series)

    def _items(self, query=""):
        resp = self.client.get(f"/api/catalog/unified/{query}")
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["results"]

    def _official_ids(self, query=""):
        return sorted(
            item["tmdb_id"] for item in self._items(query) if item["source"] == "official"
        )

    def test_watched_filters_the_official_half(self):
        self.assertEqual(self._official_ids("?watched=watched"), [711, 713])

    def test_unwatched_filters_the_official_half(self):
        self.assertEqual(self._official_ids("?watched=unwatched"), [712])

    def test_absent_filter_returns_everything(self):
        self.assertEqual(self._official_ids(), [711, 712, 713])

    def test_is_watched_labels_rows_when_the_filter_is_active(self):
        rows = {item["tmdb_id"]: item for item in self._items("?watched=watched")}
        self.assertTrue(rows[711]["is_watched"])
        self.assertTrue(rows[713]["is_watched"])
        self.assertNotIn(712, rows)

    def test_is_watched_is_null_without_the_filter(self):
        rows = {item["tmdb_id"]: item for item in self._items()}
        self.assertIsNone(rows[711]["is_watched"])
        self.assertIsNone(rows[712]["is_watched"])

    def test_explicit_all_labels_official_rows_with_their_watched_state(self):
        rows = {item["tmdb_id"]: item for item in self._items("?watched=all")}
        self.assertIs(rows[711]["is_watched"], True)
        self.assertIs(rows[712]["is_watched"], False)
        self.assertIs(rows[713]["is_watched"], True)

    def test_explicit_all_labels_resolved_and_unresolved_unofficial_rows(self):
        resolved = Movie.objects.create(
            tmdb_id=714, title="Resolved Scraper", year=2020
        )
        _mark_watched(resolved)
        scraper_items = [
            {
                "tmdb_id": 714, "content_type": "movie",
                "source": "scraper", "title": "Resolved Scraper",
            },
            {
                "tmdb_id": None, "content_type": "movie",
                "source": "scraper", "title": "Unresolved Scraper",
            },
        ]

        with patch(
            "tracker.views.unofficial_catalogue_items", return_value=scraper_items
        ):
            rows = self._items("?providers=hydra&watched=all")

        by_title = {item["title"]: item for item in rows}
        self.assertIs(by_title["Resolved Scraper"]["is_watched"], True)
        self.assertIs(by_title["Unresolved Scraper"]["is_watched"], False)

    def test_no_watched_parameter_keeps_unofficial_label_absent(self):
        scraper_item = {
            "tmdb_id": None, "source": "scraper", "title": "Unresolved Scraper"
        }
        with patch(
            "tracker.views.unofficial_catalogue_items", return_value=[scraper_item]
        ):
            rows = self._items("?providers=hydra")

        self.assertNotIn("is_watched", rows[0])

    def test_composes_with_content_type_and_sort(self):
        self.assertEqual(
            self._official_ids("?watched=watched&content_type=series"), [713]
        )
        resp = self.client.get(
            "/api/catalog/unified/?watched=watched&sort_by=title_asc"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        titles = [
            item["title"]
            for item in resp.json()["results"]
            if item["source"] == "official"
        ]
        self.assertEqual(titles, sorted(titles))

    def test_composes_with_pagination(self):
        resp = self.client.get(
            "/api/catalog/unified/?watched=watched&page=1&page_size=1"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["total_pages"], 2)
        self.assertEqual(len(data["results"]), 1)

    def test_watchlist_membership_does_not_make_a_title_watched(self):
        WatchlistItem.objects.create(
            user_id=1, movie=self.unwatched, source="manual"
        )

        self.assertNotIn(712, self._official_ids("?watched=watched"))
        self.assertIn(712, self._official_ids("?watched=unwatched"))


class UnofficialIdentitySafetyTests(TestCase):
    """Unofficial rows inherit watched state only on a full identity match.

    TMDb keeps movie and TV id namespaces apart while ``Movie.tmdb_id`` is
    globally unique, so id-only matching could assign a watched movie's state
    to an unrelated series carrying the same number.
    """

    def setUp(self):
        Provider.objects.create(slug="hydra", name="Hydra", category="scraper")
        self.movie = Movie.objects.create(
            tmdb_id=800, title="Watched Film", year=2020, content_type="movie"
        )
        self.series = Movie.objects.create(
            tmdb_id=801, title="Watched Show", year=2019, content_type="series"
        )
        _mark_watched(self.movie)
        _mark_watched(self.series)

    @staticmethod
    def _item(tmdb_id, content_type, title):
        row = {"tmdb_id": tmdb_id, "source": "scraper", "title": title}
        if content_type is not None:
            row["content_type"] = content_type
        return row

    def _rows(self, items, query="watched=all"):
        url = "/api/catalog/unified/?providers=hydra"
        if query:
            url += f"&{query}"
        with patch("tracker.views.unofficial_catalogue_items", return_value=items):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()["results"]

    def _ambiguous_items(self):
        return [
            self._item(800, "movie", "Matching Film"),
            self._item(800, "tv", "Film ID as Series"),
            self._item(801, "series", "Matching Show"),
            self._item(801, "film", "Series ID as Movie"),
            self._item(900, "movie", "Unresolved Film"),
            self._item(None, "movie", "No TMDb ID"),
            self._item(800, None, "No Media Type"),
            self._item(800, "season", "Season Row"),
            self._item(800, "bogus", "Invalid Media Type"),
        ]

    def test_watched_movie_and_series_match_their_own_media_type(self):
        rows = {row["title"]: row for row in self._rows(self._ambiguous_items())}
        self.assertIs(rows["Matching Film"]["is_watched"], True)
        self.assertIs(rows["Matching Show"]["is_watched"], True)
        for title in (
            "Film ID as Series",
            "Series ID as Movie",
            "Unresolved Film",
            "No TMDb ID",
            "No Media Type",
            "Season Row",
            "Invalid Media Type",
        ):
            with self.subTest(title=title):
                self.assertIs(rows[title]["is_watched"], False)

    def test_same_id_mismatched_content_type_is_filtered_as_unwatched(self):
        watched_titles = {
            row["title"] for row in self._rows(self._ambiguous_items(), "watched=watched")
        }
        self.assertEqual(watched_titles, {"Matching Film", "Matching Show"})

        unwatched = self._rows(self._ambiguous_items(), "watched=unwatched")
        self.assertEqual(len(unwatched), 7)
        self.assertTrue(all(row["is_watched"] is False for row in unwatched))

    def test_missing_or_invalid_media_type_cannot_claim_watched(self):
        items = [
            self._item(800, None, "No Media Type"),
            self._item(800, "", "Blank Media Type"),
            self._item(800, "episode", "Episode Row"),
            self._item(800, "nonsense", "Invalid Media Type"),
        ]
        rows = {row["title"]: row for row in self._rows(items)}
        self.assertTrue(all(row["is_watched"] is False for row in rows.values()))

    def test_watched_lookup_is_one_batched_query(self):
        with CaptureQueriesContext(connection) as ctx:
            self._rows(self._ambiguous_items())
        watch_queries = [
            query["sql"]
            for query in ctx.captured_queries
            if "tracker_usermoviedata" in query["sql"]
        ]
        self.assertEqual(len(watch_queries), 1)

    def test_no_watched_parameter_leaves_the_label_absent(self):
        rows = self._rows([self._item(800, "movie", "Matching Film")], "")
        self.assertNotIn("is_watched", rows[0])

    def test_watchlist_membership_does_not_claim_watched(self):
        watchlisted = Movie.objects.create(
            tmdb_id=802, title="Watchlisted Film", content_type="movie"
        )
        WatchlistItem.objects.create(user_id=1, movie=watchlisted, source="manual")
        rows = self._rows([self._item(802, "movie", "Watchlisted Film")])
        self.assertIs(rows[0]["is_watched"], False)

    def test_another_users_watched_state_is_not_reused(self):
        other = Movie.objects.create(
            tmdb_id=803, title="Other User Film", content_type="movie"
        )
        _mark_watched(other, user_id=2)
        rows = self._rows([self._item(803, "movie", "Other User Film")])
        self.assertIs(rows[0]["is_watched"], False)
