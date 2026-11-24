"""Smoke tests for the DRF endpoints."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from tracker.models import (
    Movie, Preference, Provider, RefreshRequest, RefreshRequestStatus,
    ScrapedContentMapping, Subscription, WatchlistItem,
)
from tracker.services.offers import Offer
from tracker.tests.availability_factories import local_offer


class ProvidersEndpointTests(TestCase):
    def test_get_providers_returns_bvod_category(self):
        Provider.objects.get_or_create(
            slug="raiplay",
            defaults={
                "name": "RaiPlay",
                "category": "bvod",
                "is_major": False,
                "is_enabled": True,
                "icon_name": "raiplay",
                "domain": "raiplay.it",
            },
        )
        resp = self.client.get("/api/providers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        bvod_providers = [p for p in data if p.get("category") == "bvod"]
        self.assertTrue(len(bvod_providers) > 0)
        self.assertEqual(bvod_providers[0]["slug"], "raiplay")

    def test_get_providers_includes_new_bvod_providers(self):
        for slug, name, domain, country in [
            ("pbs", "PBS", "pbs.org", "US"),
            ("abc_iview", "ABC iview", "iview.abc.net.au", "AU"),
            ("mediaset_infinity", "Mediaset Infinity", "mediasetinfinity.mediaset.it", "IT"),
            ("cbc_gem", "CBC Gem", "gem.cbc.ca", "CA"),
        ]:
            Provider.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "category": "bvod",
                    "is_major": False,
                    "is_enabled": True,
                    "icon_name": slug,
                    "domain": domain,
                    "country_code": country,
                },
            )

        resp = self.client.get("/api/providers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        bvod_slugs = {p["slug"] for p in data if p.get("category") == "bvod"}
        for expected_slug in ["pbs", "abc_iview", "mediaset_infinity", "cbc_gem"]:
            self.assertIn(expected_slug, bvod_slugs)

    def test_toggle_bvod_provider_subscription(self):
        p, _ = Provider.objects.get_or_create(
            slug="bbc_iplayer",
            defaults={
                "name": "BBC iPlayer",
                "category": "bvod",
                "is_major": False,
                "is_enabled": True,
                "icon_name": "bbc_iplayer",
                "domain": "bbc.co.uk/iplayer",
            },
        )
        resp = self.client.post(
            f"/api/providers/{p.slug}/toggle/",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["is_subscribed"])
        self.assertTrue(Subscription.objects.filter(user_id=1, provider=p).exists())

        # Toggle again
        resp2 = self.client.post(
            f"/api/providers/{p.slug}/toggle/",
            content_type="application/json",
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertFalse(resp2.json()["is_subscribed"])
        self.assertFalse(Subscription.objects.filter(user_id=1, provider=p).exists())


class SubscriptionsEndpointTests(TestCase):
    def test_get_returns_empty_list(self):
        resp = self.client.get("/api/subscriptions/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_post_creates_subscription_and_trims_whitespace(self):
        resp = self.client.post(
            "/api/subscriptions/",
            data={"name": "  Netflix  "},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "Netflix")
        self.assertTrue(Subscription.objects.filter(user_id=1, name="Netflix").exists())

    def test_post_creates_subscription_or_links_provider(self):
        p = Provider.objects.filter(slug="disney").first()
        resp = self.client.post(
            "/api/subscriptions/",
            data={"provider_slug": "disney"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["provider"]["slug"], "disney")

    def test_delete_removes_subscription(self):
        sub = Subscription.objects.create(user_id=1, name="Hulu")
        resp = self.client.delete(f"/api/subscriptions/{sub.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Subscription.objects.filter(id=sub.id).exists())

    def test_delete_non_existent_returns_404(self):
        resp = self.client.delete("/api/subscriptions/9999/")
        self.assertEqual(resp.status_code, 404)


class PreferencesEndpointTests(TestCase):
    def test_get_returns_existing(self):
        Preference.objects.get_or_create(user_id=1, defaults={"audio_languages": ["EN"], "subtitle_languages": ["EN"]})
        Preference.objects.filter(user_id=1).update(audio_languages=["EN"], subtitle_languages=["EN"])
        resp = self.client.get("/api/preferences/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["audio_languages"], ["EN"])

    def test_put_updates(self):
        Preference.objects.get_or_create(user_id=1)
        resp = self.client.put(
            "/api/preferences/",
            data={"audio_languages": ["EN", "IT"], "subtitle_languages": ["EN"], "candidate_countries": ["US"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Preference.objects.get(user_id=1).audio_languages, ["EN", "IT"])


class SearchEndpointTests(TestCase):
    @patch("tracker.views.TMDbClient")
    def test_search_calls_tmdb(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbMovie
        instance.search.return_value = [TMDbMovie(tmdb_id=1, title="Foo", year=2010)]
        resp = self.client.get("/api/search/?q=Foo")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"][0]["title"], "Foo")

    def test_search_requires_query(self):
        resp = self.client.get("/api/search/")
        self.assertEqual(resp.status_code, 400)

    @patch("tracker.views.TMDbClient")
    def test_search_defaults_to_movie_and_labels_results(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbItem
        instance.search.return_value = [TMDbItem(tmdb_id=1, title="Foo", year=2010)]
        resp = self.client.get("/api/search/?q=Foo")
        self.assertEqual(resp.status_code, 200)
        instance.search.assert_called_once_with("Foo", year=None, content_type="movie")
        result = resp.json()["results"][0]
        self.assertEqual(result["content_type"], "movie")

    @patch("tracker.views.TMDbClient")
    def test_search_type_series(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbItem
        instance.search.return_value = [
            TMDbItem(tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series")
        ]
        resp = self.client.get("/api/search/?q=Breaking&type=series")
        self.assertEqual(resp.status_code, 200)
        instance.search.assert_called_once_with("Breaking", year=None, content_type="series")
        result = resp.json()["results"][0]
        self.assertEqual(result["content_type"], "series")

    @patch("tracker.views.TMDbClient")
    def test_search_type_all_returns_both_types(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbItem
        instance.search.return_value = [
            TMDbItem(tmdb_id=10, title="The Matrix", year=1999, content_type="movie"),
            TMDbItem(tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"),
            TMDbItem(tmdb_id=603, title="The Matrix Reloaded", year=2003, content_type="movie"),
        ]
        resp = self.client.get("/api/search/?q=matrix&type=all")
        self.assertEqual(resp.status_code, 200)
        instance.search.assert_called_once_with("matrix", year=None, content_type="all")
        results = resp.json()["results"]
        self.assertEqual(
            [r["content_type"] for r in results],
            ["movie", "series", "movie"],
        )

    @patch("tracker.views.TMDbClient")
    def test_search_type_all_interleaves_so_series_are_not_starved(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbItem
        # 10 movies first, 1 series last: without interleaving the series is cut.
        instance.search.return_value = (
            [TMDbItem(tmdb_id=i, title=f"Movie {i}", year=2000, content_type="movie") for i in range(10)]
            + [TMDbItem(tmdb_id=999, title="Only Series", year=2020, content_type="series")]
        )
        resp = self.client.get("/api/search/?q=whatever&type=all")
        results = resp.json()["results"]
        self.assertEqual(len(results), 10)
        self.assertIn("series", [r["content_type"] for r in results])

    @patch("tracker.views.TMDbClient")
    def test_search_accepts_type_alias_and_tv_alias(self, mock_cls):
        instance = mock_cls.return_value
        from tracker.services.tmdb import TMDbItem
        instance.search.return_value = [
            TMDbItem(tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series")
        ]
        resp = self.client.get("/api/search/?q=bb&content_type=tv")
        self.assertEqual(resp.status_code, 200)
        instance.search.assert_called_once_with("bb", year=None, content_type="series")

    def test_search_rejects_invalid_type(self):
        resp = self.client.get("/api/search/?q=Foo&type=banana")
        self.assertEqual(resp.status_code, 400)


class AddToWatchlistEndpointTests(TestCase):
    @patch("tracker.services.enrichment.TMDbClient")
    def test_add_series_uses_tv_details_and_stores_series(self, mock_cls):
        from tracker.services.tmdb import TMDbItem
        instance = mock_cls.return_value
        instance.details.return_value = TMDbItem(
            tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"
        )

        resp = self.client.post(
            "/api/watchlist/add/",
            data={"tmdb_id": 1396, "content_type": "series"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        instance.details.assert_called_once_with(1396, content_type="series")
        movie = Movie.objects.get(tmdb_id=1396)
        self.assertEqual(movie.content_type, "series")
        self.assertTrue(
            WatchlistItem.objects.filter(user_id=1, movie=movie, source="manual").exists()
        )

    @patch("tracker.services.enrichment.TMDbClient")
    def test_add_movie_without_content_type_keeps_movie_behavior(self, mock_cls):
        from tracker.services.tmdb import TMDbItem
        instance = mock_cls.return_value
        instance.details.return_value = TMDbItem(
            tmdb_id=680, title="Pulp Fiction", year=1994, content_type="movie"
        )

        resp = self.client.post(
            "/api/watchlist/add/",
            data={"tmdb_id": 680},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        instance.details.assert_called_once_with(680, content_type="movie")
        movie = Movie.objects.get(tmdb_id=680)
        self.assertEqual(movie.content_type, "movie")

    def test_add_rejects_invalid_content_type(self):
        resp = self.client.post(
            "/api/watchlist/add/",
            data={"tmdb_id": 1396, "content_type": "banana"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Movie.objects.filter(tmdb_id=1396).exists())


class MovieAvailabilityTests(TestCase):
    def test_404_when_not_tracked(self):
        resp = self.client.get("/api/movie/9999/availability/")
        self.assertEqual(resp.status_code, 404)

    def test_returns_ranked_offers(self):
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        movie = Movie.objects.create(tmdb_id=42, title="Cached", year=2020)
        local_offer(movie, service="netflix", quality="4k", audio_languages=["EN"], subtitle_languages=["EN"])
        resp = self.client.get("/api/movie/42/availability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["movie"]["tmdb_id"], 42)
        self.assertEqual(len(resp.json()["offers"]), 1)

    def test_missing_local_availability_is_an_empty_non_blocking_response(self):
        Movie.objects.create(tmdb_id=42, title="Cached", year=2020)
        resp = self.client.get("/api/movie/42/availability/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["offers"], [])


class WatchlistEndpointTests(TestCase):
    def test_get_returns_empty(self):
        resp = self.client.get("/api/watchlist/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_post_requires_file(self):
        resp = self.client.post("/api/watchlist/")
        self.assertEqual(resp.status_code, 400)


class WatchlistReportEndpointTests(TestCase):
    def test_empty_list(self):
        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        res_data = resp.json()
        self.assertEqual(res_data["report"], [])
        self.assertEqual(res_data["summary"], {
            "total_movies": 0,
            "available_movies": 0,
            "unavailable_movies": 0,
            "needs_recheck_movies": 0,
            "countries": [],
            "services": [],
        })
        self.assertEqual(res_data["pagination"], {
            "count": 0,
            "page": 1,
            "page_size": 24,
            "total_pages": 1,
            "has_next": False,
            "has_previous": False,
        })

    def test_all_unavailable_list(self):
        from tracker.models import WatchlistItem
        movie = Movie.objects.create(tmdb_id=101, title="Unavailable Movie", year=2021)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["report"]), 1)
        row = data["report"][0]
        self.assertEqual(row["movie"]["tmdb_id"], 101)
        self.assertEqual(row["movie"]["title"], "Unavailable Movie")
        self.assertEqual(row["available_countries"], [])
        self.assertIsNone(row["best_country"])
        self.assertIsNone(row["best_service"])

    def test_available_elsewhere_never_reads_unavailable(self):
        """Regression (Platoon, NL): offers present ⇒ 🟠, never ⚫.

        The response simultaneously reported ``available_countries`` including
        NL (from ``rank_offers``) and ``watchability.status: "not_watchable"``
        ⚫ — the watchlist badge contradicting its own summary and the
        canonical verdict. ⚫ is reserved for canonical unavailability.
        """
        Preference.objects.create(user_id=1, candidate_countries=[])
        movie = Movie.objects.create(tmdb_id=792, title="Platoon", year=1986)
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        # The only local offer is a rent one, and no subscription is held.
        local_offer(movie, country="NL", service="rakuten_tv", offer_type="rent",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        resp = self.client.get("/api/watchlist/report/?current_country=NL")
        self.assertEqual(resp.status_code, 200)
        row = next(r for r in resp.json()["report"] if r["movie"]["tmdb_id"] == 792)
        self.assertEqual(row["watchability"]["status"], "available_elsewhere")
        self.assertEqual(row["watchability"]["status_symbol"], "🟠")
        self.assertNotEqual(row["watchability"]["status"], "not_watchable")

    def test_mixed_list(self):
        from tracker.models import WatchlistItem
        Preference.objects.create(user_id=1, candidate_countries=["US", "GB"])
        m1 = Movie.objects.create(tmdb_id=201, title="Movie One", year=2020)
        m2 = Movie.objects.create(tmdb_id=202, title="Movie Two", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")

        local_offer(m1, country="US", service="netflix", quality="4k",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m1, country="GB", service="prime", quality="hd",
                    audio_languages=["EN"], subtitle_languages=["EN"])

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["report"]), 2)

        row1 = next(r for r in data["report"] if r["movie"]["tmdb_id"] == 201)
        self.assertEqual(row1["available_countries"], ["GB", "US"])
        self.assertEqual(row1["best_country"], "US")
        self.assertEqual(row1["best_service"], "netflix")

        row2 = next(r for r in data["report"] if r["movie"]["tmdb_id"] == 202)
        self.assertEqual(row2["available_countries"], [])
        self.assertIsNone(row2["best_country"])
        self.assertIsNone(row2["best_service"])

    def test_summary_analytics_and_deduplication(self):
        from tracker.models import WatchlistItem
        Preference.objects.create(user_id=1, candidate_countries=["US", "GB", "CA"])
        m1 = Movie.objects.create(tmdb_id=501, title="Movie A", year=2020)
        m2 = Movie.objects.create(tmdb_id=502, title="Movie B", year=2021)
        m3 = Movie.objects.create(tmdb_id=503, title="Movie C", year=2022)
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m3, source="manual")

        local_offer(m1, country="US", service="netflix", quality="4k",
                    provider_content_id="n1",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m1, country="US", service="netflix", quality="hd",
                    provider_content_id="n2",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m1, country="GB", service="prime", quality="hd",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m1, country="US", service="apple", offer_type="buy",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m2, country="US", service="netflix", offer_type="free",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m2, country="CA", service="apple", offer_type="rent",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        resp = self.client.get("/api/watchlist/report/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("summary", data)
        summary = data["summary"]
        self.assertEqual(summary["total_movies"], 3)
        self.assertEqual(summary["available_movies"], 2)
        self.assertEqual(summary["unavailable_movies"], 1)

        self.assertEqual(summary["countries"], [
            {"country": "US", "count": 2},
            {"country": "CA", "count": 1},
            {"country": "GB", "count": 1},
        ])

        # Services are canonical provider slugs in the read path.
        self.assertEqual(summary["services"], [
            {"service": "apple", "count": 2},
            {"service": "netflix", "count": 2},
            {"service": "prime", "count": 1},
        ])

    def test_watchlist_report_actor_and_genre_filters(self):
        """Actor (actor_id) and genre filters must match against Movie.cast / genre_ids.

        Regression guard: search-by-actor and search-by-category returned nothing
        in production because bulk-created Movie rows had empty cast/genre_ids. The
        filter uses the populated JSON fields, so this asserts both filters work
        when the metadata actually exists.
        """
        from tracker.models import WatchlistItem

        m1 = Movie.objects.create(
            tmdb_id=701, title="Has Gosling", year=2017,
            genre_ids=[18], genres=[{"id": 18, "name": "Drama"}],
            cast=[{"id": 819, "name": "Ryan Gosling", "character": "X"}],
        )
        m2 = Movie.objects.create(
            tmdb_id=702, title="No Gosling", year=2017,
            genre_ids=[35], genres=[{"id": 35, "name": "Comedy"}],
            cast=[{"id": 287, "name": "Brad Pitt", "character": "Y"}],
        )
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")

        # Genre filter (by id) should keep only m1.
        resp = self.client.get("/api/watchlist/report/?genre=18")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([r["movie"]["tmdb_id"] for r in data["report"]], [701])

        # Actor filter (by id) should keep only m1.
        resp = self.client.get("/api/watchlist/report/?actor_id=819")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual([r["movie"]["tmdb_id"] for r in data["report"]], [701])

        # Actor filter excludes m2.
        resp = self.client.get("/api/watchlist/report/?actor_id=287")
        self.assertEqual([r["movie"]["tmdb_id"] for r in resp.json()["report"]], [702])

    def test_watchlist_report_with_content_type_filter(self):
        from tracker.models import WatchlistItem
        m1 = Movie.objects.create(tmdb_id=601, title="Movie Title", year=2020, content_type="movie")
        m2 = Movie.objects.create(tmdb_id=602, title="TV Series Title", year=2021, content_type="series")
        WatchlistItem.objects.create(user_id=1, movie=m1, source="manual")
        WatchlistItem.objects.create(user_id=1, movie=m2, source="manual")

        resp = self.client.get("/api/watchlist/report/?type=series")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["report"]), 1)
        self.assertEqual(data["report"][0]["movie"]["tmdb_id"], 602)


class WatchlistResolveEndpointTests(TestCase):
    def test_missing_selections_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class WatchEndpointTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=1001, title="Test Watch Movie", year=2021)

    def test_watch_endpoint_returns_found_and_url(self):
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(
            self.movie,
            country="US",
            service="netflix",
            offer_type="subscription",
            quality="hd",
            url="https://www.netflix.com/title/123",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["watch_url"], "https://www.netflix.com/title/123")
        self.assertEqual(data["movie"]["tmdb_id"], 1001)
        self.assertEqual(data["movie"]["content_type"], "movie")

    def test_watch_endpoint_prioritizes_subscribed_providers(self):
        Preference.objects.create(user_id=1, audio_languages=["EN"], subtitle_languages=["EN"])
        p_disney = Provider.objects.filter(slug="disney").first()
        if p_disney:
            Subscription.objects.create(user_id=1, provider=p_disney, name="Disney+")

        local_offer(
            self.movie,
            country="US",
            service="netflix",
            offer_type="subscription",
            quality="4k",
            url="https://www.netflix.com/title/123",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        local_offer(
            self.movie,
            country="US",
            service="disney",
            offer_type="subscription",
            quality="hd",
            url="https://www.disneyplus.com/movies/456",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["watch_url"], "https://www.disneyplus.com/movies/456")

    def test_watch_endpoint_returns_found_false_when_no_valid_url(self):
        resp = self.client.get(f"/api/watch/{self.movie.tmdb_id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["found"])
        self.assertNotIn("watch_url", data)
        self.assertIn("message", data)

    @patch("tracker.services.enrichment.TMDbClient")
    def test_watch_endpoint_404_when_movie_not_found(self, mock_tmdb):
        mock_instance = mock_tmdb.return_value
        mock_instance.details.side_effect = Exception("Not found on TMDb")
        resp = self.client.get("/api/watch/999999/")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["found"])
        self.assertEqual(resp.json()["message"], "Movie not found")

    def test_watch_endpoint_serves_existing_series_row(self):
        series = Movie.objects.create(tmdb_id=1396, title="Breaking Bad", year=2008,
                                      content_type="series")
        local_offer(
            series,
            country="US",
            service="prime",
            offer_type="subscription",
            quality="hd",
            url="https://www.primevideo.com/detail/1396",
            audio_languages=["EN"],
            subtitle_languages=["EN"],
        )
        resp = self.client.get("/api/watch/1396/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["found"])
        self.assertEqual(data["watch_url"], "https://www.primevideo.com/detail/1396")
        self.assertEqual(data["movie"]["content_type"], "series")

    @patch("tracker.services.enrichment.TMDbClient")
    def test_watch_endpoint_resolves_series_when_id_is_not_a_movie(self, mock_tmdb):
        """An unknown id that TMDb reports as "no such movie" is retried as a series."""
        import requests

        from tracker.services.tmdb import TMDbItem

        not_found = requests.Response()
        not_found.status_code = 404

        mock_instance = mock_tmdb.return_value
        mock_instance.details.side_effect = [
            requests.HTTPError("404 Client Error", response=not_found),
            TMDbItem(tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"),
        ]

        resp = self.client.get("/api/watch/1396/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["found"])
        self.assertEqual(data["movie"]["content_type"], "series")
        self.assertEqual(mock_instance.details.call_count, 2)
        mock_instance.details.assert_any_call(1396, content_type="series")
        self.assertTrue(Movie.objects.filter(tmdb_id=1396, content_type="series").exists())

    @patch("tracker.services.enrichment.TMDbClient")
    def test_watch_endpoint_does_not_retry_series_on_unrelated_failure(self, mock_tmdb):
        """Non-"not found" TMDb failures are reported as-is, without a second call."""
        mock_tmdb.return_value.details.side_effect = Exception("connection reset by peer")

        resp = self.client.get("/api/watch/999998/")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.json()["found"])
        self.assertEqual(resp.json()["message"], "Movie not found")
        self.assertEqual(mock_tmdb.return_value.details.call_count, 1)
        self.assertFalse(Movie.objects.filter(tmdb_id=999998).exists())

    def test_empty_selections_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": []},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"],
            "selections parameter is required and must not be empty",
        )

    def test_invalid_tmdb_id_returns_400(self):
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data={"selections": [{"original_title": "Test", "year": 2020, "tmdb_id": "not-an-int"}]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json()["error"],
            "selections parameter is required and must not be empty",
        )

    def test_valid_selections_creates_movie_and_watchlist_item(self):
        payload = {
            "selections": [
                {"original_title": "Inception", "year": 2010, "tmdb_id": 27205},
                {"original_title": "The Matrix", "year": 1999, "tmdb_id": 603},
            ]
        }
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["resolved_count"], 2)
        self.assertEqual(len(data["items"]), 2)

        movie = Movie.objects.get(tmdb_id=27205)
        self.assertEqual(movie.title, "Inception")
        self.assertEqual(movie.year, 2010)

        watchlist_item = data["items"][0]
        self.assertEqual(watchlist_item["movie"]["tmdb_id"], 27205)
        self.assertEqual(watchlist_item["source"], "letterboxd_resolved")

    def test_duplicate_selections_deduplicated_and_existing_item_returned(self):
        movie = Movie.objects.create(tmdb_id=27205, title="Inception", year=2010)
        from tracker.models import WatchlistItem
        existing_item = WatchlistItem.objects.create(
            user_id=1, movie=movie, source="letterboxd_resolved"
        )

        payload = {
            "selections": [
                {"original_title": "Inception", "year": 2010, "tmdb_id": 27205},
                {"original_title": "Inception Duplicate", "year": 2010, "tmdb_id": 27205},
            ]
        }
        resp = self.client.post(
            "/api/watchlist/resolve/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["resolved_count"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["id"], existing_item.id)
        self.assertEqual(Movie.objects.get(tmdb_id=27205).title, "Inception")


class MovieLanguageInfoEndpointTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=500, title="Test Movie", year=2023)

    def test_404_when_movie_not_tracked(self):
        resp = self.client.get("/api/movie/9999/language-info/?provider=Netflix&country=US")
        self.assertEqual(resp.status_code, 404)

        resp = self.client.put(
            "/api/movie/9999/language-info/",
            data={"provider": "Netflix", "country": "US", "audio_languages": ["EN"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_requires_provider_and_country(self):
        resp = self.client.get(f"/api/movie/{self.movie.tmdb_id}/language-info/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("provider and country parameters required", resp.json()["error"])

    def test_get_returns_empty_when_no_language_info(self):
        resp = self.client.get(
            f"/api/movie/{self.movie.tmdb_id}/language-info/?provider=Netflix&country=US"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["movie_tmdb_id"], self.movie.tmdb_id)
        self.assertEqual(data["provider"], "Netflix")
        self.assertEqual(data["country"], "US")
        self.assertEqual(data["audio_languages"], [])
        self.assertEqual(data["subtitle_languages"], [])

    def test_put_creates_language_info_and_normalizes_codes(self):
        payload = {
            "provider": "Netflix",
            "country": "us",
            "audio_languages": ["EN", "  ES ", "en"],
            "subtitle_languages": [" IT ", "fr"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["country"], "US")
        self.assertEqual(data["audio_languages"], ["EN", "ES"])
        self.assertEqual(data["subtitle_languages"], ["IT", "FR"])

        # Subsequent GET should return updated info
        get_resp = self.client.get(
            f"/api/movie/{self.movie.tmdb_id}/language-info/?provider=Netflix&country=US"
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["audio_languages"], ["EN", "ES"])
        self.assertEqual(sorted(get_resp.json()["subtitle_languages"]), ["FR", "IT"])

    def test_put_updates_and_overwrites_existing_entries(self):
        # Initial put
        self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data={"provider": "Prime", "country": "US", "audio_languages": ["EN"], "subtitle_languages": ["ES"]},
            content_type="application/json",
        )
        # Update put
        update_resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data={"provider": "Prime", "country": "US", "audio_languages": ["DE"], "subtitle_languages": []},
            content_type="application/json",
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["audio_languages"], ["DE"])
        self.assertEqual(update_resp.json()["subtitle_languages"], [])

    def test_put_validates_invalid_language_codes(self):
        payload = {
            "provider": "Netflix",
            "country": "US",
            "audio_languages": ["Klingon"],
            "subtitle_languages": ["ES"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_validates_invalid_country_code(self):
        payload = {
            "provider": "Netflix",
            "country": "USA",
            "audio_languages": ["EN"],
            "subtitle_languages": ["ES"],
        }
        resp = self.client.put(
            f"/api/movie/{self.movie.tmdb_id}/language-info/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


class MovieDetailEndpointTests(TestCase):
    def test_returns_404_when_not_found(self):
        resp = self.client.get("/api/movie/99999/")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "movie not found")

    @patch("tracker.services.enrichment.TMDbClient")
    def test_pending_refresh_state_matches_watchlist_report(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbItem

        movie = Movie.objects.create(
            tmdb_id=776, title="Pending Film", year=2023,
            poster_path="/poster.jpg", overview="", genres=[], genre_ids=[],
            cast=[], tmdb_rating=7.0,
        )
        mock_tmdb_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=776, title="Pending Film", year=2023, content_type="movie",
            overview="", poster_path="/poster.jpg", genres=[], genre_ids=[],
            cast=[], seasons_data=[], rating=7.0,
        )
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        RefreshRequest.objects.create(
            movie=movie, source="watchmode", country="NL",
            status=RefreshRequestStatus.PENDING,
        )

        detail = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/").json()
        report = self.client.get("/api/watchlist/report/").json()
        report_watchability = next(
            row["watchability"] for row in report["report"]
            if row["movie"]["tmdb_id"] == movie.tmdb_id
        )

        self.assertEqual(detail["watchability"]["status"], "checking")
        self.assertEqual(
            detail["watchability"]["status"], report_watchability["status"]
        )
        self.assertEqual(
            detail["watchability"]["refresh_state"],
            report_watchability["refresh_state"],
        )

    @patch("tracker.views.request_title_refresh")
    @patch("tracker.services.enrichment.TMDbClient")
    def test_country_scoped_scraped_offer_agrees_across_detail_report_and_v2(
        self, mock_tmdb_cls, _mock_refresh
    ):
        from tracker.services.tmdb import TMDbItem
        from availability.services.legacy_ingest import ingest_scraped_mapping

        movie = Movie.objects.create(
            tmdb_id=6077, title="Territorial Test", year=2021,
            poster_path="", overview="", genres=[], genre_ids=[], cast=[],
        )
        mock_tmdb_cls.return_value.details.return_value = TMDbItem(
            tmdb_id=6077, title="Territorial Test", year=2021,
            content_type="movie", overview="", poster_path="", genres=[],
            genre_ids=[], cast=[], seasons_data=[], rating=None,
        )
        provider, _ = Provider.objects.update_or_create(
            slug="tivu_play",
            defaults={
                "name": "Tivù Play",
                "category": "bvod",
                "is_enabled": True,
                "country_code": "IT",
            },
        )
        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "current_country": "NL",
                "candidate_countries": [],
                "vpn_enabled": False,
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
            },
        )
        WatchlistItem.objects.create(user_id=1, movie=movie, source="manual")
        local_offer(
            movie, country="US", service="tivu_play", offer_type="free",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        mapping = ScrapedContentMapping.objects.create(
            provider=provider,
            movie=movie,
            scraped_title=movie.title,
            scraped_year=movie.year,
            scraped_url="https://tivu.example/watch",
            country_code="US",
            is_available=True,
            is_confirmed=True,
            confidence_score=1.0,
            raw_payload={"audio_languages": ["EN"], "subtitle_languages": ["EN"]},
        )
        ingest_scraped_mapping(mapping)

        detail = self.client.get(
            f"/api/prime/movie/{movie.tmdb_id}/?current_country=NL"
        ).json()
        report = self.client.get(
            "/api/watchlist/report/?current_country=NL"
        ).json()
        report_row = next(
            row for row in report["report"]
            if row["movie"]["tmdb_id"] == movie.tmdb_id
        )
        v2 = self.client.get(
            f"/api/v2/unofficial/movie/{movie.tmdb_id}/offers/"
            "?include_bvod=true&current_country=NL"
        ).json()

        detail_offers = [o for o in detail["offers"] if o["country"] == "US"]
        self.assertTrue(detail_offers)
        [v2_offer] = v2["offers"]
        for detail_offer in detail_offers:
            self.assertEqual(detail_offer["territorial_scope"], "country")
            self.assertEqual(detail_offer["locality"], "out_of_reach")
            self.assertFalse(detail_offer["is_accessible"])
        self.assertEqual(detail["watchability"]["status"], "available_elsewhere")
        self.assertEqual(report_row["watchability"]["status"], "available_elsewhere")
        self.assertEqual(v2_offer["locality"], detail_offers[0]["locality"])
        self.assertFalse(v2_offer["is_accessible"])

        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "current_country": "NL",
                "candidate_countries": ["US"],
                "vpn_enabled": True,
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
            },
        )
        detail_vpn = self.client.get(
            f"/api/prime/movie/{movie.tmdb_id}/?current_country=NL"
        ).json()
        report_vpn = self.client.get(
            "/api/watchlist/report/?current_country=NL"
        ).json()
        [v2_vpn_offer] = self.client.get(
            f"/api/v2/unofficial/movie/{movie.tmdb_id}/offers/"
            "?include_bvod=true&current_country=NL"
        ).json()["offers"]
        detail_vpn_offers = [
            offer for offer in detail_vpn["offers"]
            if offer["country"] == "US"
        ]
        self.assertTrue(detail_vpn_offers)
        report_vpn_row = next(
            row for row in report_vpn["report"]
            if row["movie"]["tmdb_id"] == movie.tmdb_id
        )
        self.assertTrue(all(offer["locality"] == "vpn" for offer in detail_vpn_offers))
        self.assertTrue(all(offer["is_accessible"] for offer in detail_vpn_offers))
        self.assertEqual(detail_vpn["watchability"]["status"], "available_vpn")
        self.assertEqual(report_vpn_row["watchability"]["status"], "watchable_vpn")
        self.assertEqual(v2_vpn_offer["locality"], "vpn")
        self.assertTrue(v2_vpn_offer["is_accessible"])

    @patch("tracker.services.enrichment.TMDbClient")
    def test_returns_full_local_detail(self, remote):
        movie = Movie.objects.create(
            tmdb_id=777, title="Test Film", year=2023,
            poster_path="/poster.jpg", overview="A test movie",
            genres=[{"id": 28, "name": "Action"}], genre_ids=[28],
            cast=[{"id": 1, "name": "Actor One", "character": "Role"}],
            director=["Jane Director"], tmdb_rating=7.5,
        )
        response = self.client.get(f"/api/movie/{movie.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["movie"]["tmdb_id"], 777)
        self.assertEqual(data["movie"]["title"], "Test Film")
        self.assertEqual(data["movie"]["tmdb_rating"], 7.5)
        self.assertEqual(data["movie"]["cast"], [{"id": 1, "name": "Actor One", "character": "Role"}])
        self.assertEqual(data["movie"]["director"], ["Jane Director"])
        self.assertIsNone(data["user_data"])
        self.assertEqual(data["watch_events"], [])
        remote.assert_not_called()

    @patch("tracker.services.enrichment.TMDbClient")
    def test_detail_get_does_not_refresh_stale_metadata(self, remote):
        movie = Movie.objects.create(tmdb_id=888, title="Old Title", year=2020)
        response = self.client.get(f"/api/movie/{movie.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["movie"]["title"], "Old Title")
        movie.refresh_from_db()
        self.assertEqual(movie.canonical_title, "")
        self.assertIsNone(movie.tmdb_rating)
        remote.assert_not_called()


class UnifiedCatalogEndpointTests(TestCase):
    def test_unified_catalog_merges_official_and_unofficial(self):
        from availability.services.legacy_ingest import ingest_scraped_mapping
        from tracker.models import ScrapedContentMapping
        m = Movie.objects.create(tmdb_id=101, title="Official Movie", year=2021)
        p = Provider.objects.create(slug="hydra", name="Hydra", category="scraper", is_enabled=True)
        mapping = ScrapedContentMapping.objects.create(
            provider=p,
            scraped_title="Scraper Movie",
            scraped_year=2022,
            scraped_url="https://hydra.example/movie",
            is_available=True,
        )
        ingest_scraped_mapping(mapping)

        resp = self.client.get("/api/catalog/unified/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)

        sources = {item["source"] for item in data["results"]}
        self.assertEqual(sources, {"official", "scraper"})

        scraper_item = next(i for i in data["results"] if i["source"] == "scraper")
        self.assertEqual(scraper_item["title"], "Scraper Movie")
        self.assertEqual(scraper_item["year"], 2022)
        self.assertIsNone(scraper_item["watchability"])
        self.assertEqual(
            scraper_item["unofficial_details"]["scraped_url"],
            "https://hydra.example/movie",
        )

    def test_unified_catalog_filters_by_provider(self):
        from availability.services.legacy_ingest import ingest_scraped_mapping
        from tracker.models import ScrapedContentMapping
        Movie.objects.create(tmdb_id=101, title="Official Movie", year=2021)
        p1 = Provider.objects.create(slug="hydra", name="Hydra", category="scraper", is_enabled=True)
        p2 = Provider.objects.create(slug="kissanime", name="KissAnime", category="scraper", is_enabled=True)

        ingest_scraped_mapping(ScrapedContentMapping.objects.create(
            provider=p1, scraped_title="Hydra Film", scraped_year=2021, scraped_url="https://hydra.ex", is_available=True
        ))
        ingest_scraped_mapping(ScrapedContentMapping.objects.create(
            provider=p2, scraped_title="Kiss Film", scraped_year=2021, scraped_url="https://kiss.ex", is_available=True
        ))

        resp = self.client.get("/api/catalog/unified/?providers=hydra")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["provider"]["slug"], "hydra")

    def test_unified_catalog_filters_by_quality(self):
        m1 = Movie.objects.create(tmdb_id=101, title="4K Movie", year=2021)
        m2 = Movie.objects.create(tmdb_id=102, title="HD Movie", year=2021)

        local_offer(m1, service="netflix", quality="4k", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(m2, service="prime", quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        resp = self.client.get("/api/catalog/unified/?quality=4k")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["tmdb_id"], 101)

    def test_unified_catalog_filters_by_score_and_sorts(self):
        Movie.objects.create(tmdb_id=201, title="Movie Low", year=2020, tmdb_rating=5.5)
        Movie.objects.create(tmdb_id=202, title="Movie Mid", year=2022, tmdb_rating=7.5)
        Movie.objects.create(tmdb_id=203, title="Movie High", year=2021, tmdb_rating=8.9)

        # Min score filter
        resp = self.client.get("/api/catalog/unified/?score_min=7.0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)
        tmdb_ids = [item["tmdb_id"] for item in data["results"]]
        self.assertIn(202, tmdb_ids)
        self.assertIn(203, tmdb_ids)

        # Sort by rating desc
        resp_sort = self.client.get("/api/catalog/unified/?sort_by=rating_desc")
        self.assertEqual(resp_sort.status_code, 200)
        data_sort = resp_sort.json()
        sorted_ids = [item["tmdb_id"] for item in data_sort["results"]]
        self.assertEqual(sorted_ids, [203, 202, 201])


class LinkTMDBEndpointTests(TestCase):
    @patch("tracker.views.TMDbClient")
    def test_links_tmdb_and_updates_movie(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbItem
        movie = Movie.objects.create(
            tmdb_id=0, title="Unlinked Movie", year=2022,
            poster_path="", overview="", genres=[], genre_ids=[],
            cast=[], tmdb_rating=None,
        )
        linked_details = TMDbItem(
            tmdb_id=555, title="Correct Title", year=2022, content_type="movie",
            overview="Real synopsis", poster_path="/real-poster.jpg",
            genres=[{"id": 18, "name": "Drama"}], genre_ids=[18],
            cast=[{"id": 10, "name": "Star", "character": "Lead"}],
            seasons_data=[], rating=9.0,
        )
        mock_instance = mock_tmdb_cls.return_value
        mock_instance.details.return_value = linked_details

        resp = self.client.post(
            f"/api/movie/{movie.id}/link-tmdb/",
            data={"tmdb_id": 555},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["message"].startswith("Linked to TMDB"))
        self.assertEqual(data["movie"]["tmdb_id"], 555)
        self.assertEqual(data["movie"]["title"], "Correct Title")
        self.assertEqual(data["movie"]["poster_path"], "/real-poster.jpg")
        self.assertEqual(data["movie"]["tmdb_rating"], 9.0)
        self.assertEqual(data["movie"]["cast"], [{"id": 10, "name": "Star", "character": "Lead"}])

        movie.refresh_from_db()
        self.assertEqual(movie.tmdb_id, 555)
        self.assertEqual(movie.title, "Correct Title")
        self.assertEqual(movie.tmdb_rating, 9.0)

    @patch("tracker.views.TMDbClient")
    def test_uses_title_override_when_provided(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbItem
        movie = Movie.objects.create(tmdb_id=0, title="Original", year=2021)
        mock_instance = mock_tmdb_cls.return_value
        mock_instance.details.return_value = TMDbItem(
            tmdb_id=111, title="TMDb Title", year=2021, content_type="movie",
            overview="", poster_path="", genres=[], genre_ids=[],
            cast=[], seasons_data=[], rating=None,
        )
        resp = self.client.post(
            f"/api/movie/{movie.id}/link-tmdb/",
            data={"tmdb_id": 111, "title": "My Custom Title"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        movie.refresh_from_db()
        self.assertEqual(movie.title, "My Custom Title")

    def test_requires_tmdb_id(self):
        movie = Movie.objects.create(tmdb_id=1, title="X", year=2020)
        resp = self.client.post(
            f"/api/movie/{movie.id}/link-tmdb/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "tmdb_id is required and must be an integer")

    def test_returns_404_when_movie_not_found(self):
        resp = self.client.post(
            "/api/movie/99999/link-tmdb/",
            data={"tmdb_id": 123},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
