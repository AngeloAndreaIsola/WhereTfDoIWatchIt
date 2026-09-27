"""Smoke tests for the Prime Video MVP endpoints.

These tests use mock objects to verify the view logic without hitting
live TMDb or Streaming Availability APIs (which are gated behind API keys
and have strict rate limits).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracker.models import Movie, Availability, Region, LanguageInfo, LanguageEntry, Provider, Subscription
from tracker.services.offers import Offer


class PrimeUrlRoutingTests(TestCase):
    """Verify all expected API routes are wired up (no 404s)."""

    def test_prime_popular_route_resolves(self):
        url = reverse("prime-popular")
        self.assertEqual(url, "/api/prime/popular/")

    def test_prime_popular_series_route_resolves(self):
        url = reverse("prime-popular-series")
        self.assertEqual(url, "/api/prime/popular-series/")

    def test_prime_movie_route_resolves(self):
        url = reverse("prime-movie", kwargs={"tmdb_id": 550})
        self.assertEqual(url, "/api/prime/movie/550/")

    def test_prime_series_route_resolves(self):
        url = reverse("prime-series", kwargs={"tmdb_id": 1396})
        self.assertEqual(url, "/api/prime/series/1396/")

    def test_health_route_resolves(self):
        url = reverse("health")
        self.assertEqual(url, "/api/health/")

    def test_search_route_resolves(self):
        url = reverse("search")
        self.assertEqual(url, "/api/search/")

    def test_preferences_route_resolves(self):
        url = reverse("preferences")
        self.assertEqual(url, "/api/preferences/")

    def test_watchlist_route_resolves(self):
        url = reverse("watchlist")
        self.assertEqual(url, "/api/watchlist/")


class PrimePopularEndpointTests(TestCase):
    """Tests for GET /api/prime/popular/."""

    def setUp(self):
        from tracker.views import _POPULAR_CACHE
        _POPULAR_CACHE.clear()

    def tearDown(self):
        from tracker.views import _POPULAR_CACHE
        _POPULAR_CACHE.clear()

    @patch("tracker.views.TMDbClient")
    def test_returns_200_with_results(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbMovie

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.return_value = [
            TMDbMovie(tmdb_id=1, title="Test Movie", year=2024,
                      poster_path="/test.jpg", overview="A test movie.", genre_ids=[28, 18]),
            TMDbMovie(tmdb_id=2, title="Another Movie", year=2023,
                      poster_path=None, overview=None, genre_ids=[35]),
        ]

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["source"], "tmdb_trending")
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["results"][0]["tmdb_id"], 1)
        self.assertEqual(data["results"][0]["title"], "Test Movie")
        self.assertEqual(data["results"][0]["year"], 2024)
        self.assertEqual(data["results"][0]["poster_path"], "/test.jpg")
        self.assertEqual(data["results"][0]["categories"], ["Action", "Drama"])
        self.assertEqual(data["results"][1]["categories"], ["Comedy"])
        # Homepage cards need the content type to route to /movie/ vs /tv/.
        self.assertEqual(data["results"][0]["content_type"], "movie")
        self.assertEqual(data["results"][1]["content_type"], "movie")

    @patch("tracker.views.TMDbClient")
    def test_respects_limit_param(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbMovie

        # Create 30 movies — mock should slice to limit
        movies = [
            TMDbMovie(tmdb_id=i, title=f"Movie {i}", year=2024,
                      poster_path=None, overview=None, genre_ids=[])
            for i in range(30)
        ]
        mock_client = mock_tmdb_cls.return_value
        # The real TMDbClient.popular() slices to limit; simulate that
        mock_client.popular.side_effect = lambda limit=24: movies[:limit]

        from django.test import Client
        response = Client().get("/api/prime/popular/?limit=5")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 5)

    @patch("tracker.views.TMDbClient")
    def test_returns_502_on_tmdb_failure(self, mock_tmdb_cls):
        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.side_effect = Exception("401 Unauthorized")

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertIn("TMDb fetch failed", data["error"])
        self.assertEqual(data["count"], 0)

    @patch("tracker.views.TMDbClient")
    def test_cache_serves_stale_data_on_failure(self, mock_tmdb_cls):
        """If TMDb fails but cache has data, serve cached results."""
        from tracker.services.tmdb import TMDbMovie
        from tracker.views import _POPULAR_CACHE

        # Pre-populate cache
        _POPULAR_CACHE["data"] = {
            "results": [{"tmdb_id": 1, "title": "Cached Movie", "year": 2020,
                         "poster_path": None, "overview": None, "categories": []}],
            "count": 1,
            "cached": False,
            "source": "tmdb_trending",
        }
        _POPULAR_CACHE["ts"] = timezone.now().timestamp()

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular.side_effect = Exception("401 Unauthorized")

        from django.test import Client
        response = Client().get("/api/prime/popular/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["cached"])
        self.assertEqual(data["results"][0]["title"], "Cached Movie")

        # Clean up
        _POPULAR_CACHE.clear()


class PrimePopularSeriesEndpointTests(TestCase):
    """Tests for GET /api/prime/popular-series/."""

    def setUp(self):
        from tracker.views import _POPULAR_SERIES_CACHE
        _POPULAR_SERIES_CACHE.clear()

    def tearDown(self):
        from tracker.views import _POPULAR_SERIES_CACHE
        _POPULAR_SERIES_CACHE.clear()

    @patch("tracker.views.TMDbClient")
    def test_returns_200_with_series_results(self, mock_tmdb_cls):
        from tracker.services.tmdb import TMDbItem

        # Create a series in DB
        Movie.objects.create(tmdb_id=200, title="DB Series", year=2023, content_type="series")

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular_series.return_value = [
            TMDbItem(tmdb_id=101, title="Test Series", year=2024, content_type="series",
                     poster_path="/tv.jpg", overview="A test series.", genre_ids=[18]),
        ]

        from django.test import Client
        response = Client().get("/api/prime/popular-series/?limit=48")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["source"], "active_providers_and_tmdb")
        self.assertEqual(data["results"][0]["tmdb_id"], 200)
        self.assertEqual(data["results"][0]["title"], "DB Series")
        self.assertEqual(data["results"][1]["tmdb_id"], 101)
        self.assertEqual(data["results"][1]["title"], "Test Series")
        # Homepage cards need the content type to route to /movie/ vs /tv/.
        self.assertEqual(data["results"][0]["content_type"], "series")
        self.assertEqual(data["results"][1]["content_type"], "series")

    @patch("tracker.views.TMDbClient")
    def test_returns_db_series_when_tmdb_fails(self, mock_tmdb_cls):
        Movie.objects.create(tmdb_id=300, title="Fallback Series", year=2022, content_type="series")

        mock_client = mock_tmdb_cls.return_value
        mock_client.popular_series.side_effect = Exception("401 Unauthorized")

        from django.test import Client
        response = Client().get("/api/prime/popular-series/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["title"], "Fallback Series")
        self.assertEqual(data["results"][0]["content_type"], "series")


class StreamingApiSeriesTests(TestCase):
    """Verify series support in StreamingAvailabilityClient."""

    @patch("tracker.services.streaming_api.requests.get")
    def test_get_availability_series_endpoint(self, mock_get):
        from tracker.services.streaming_api import StreamingAvailabilityClient

        mock_resp = mock_get.return_value
        mock_resp.json.return_value = {"streamingOptions": {}}
        mock_resp.raise_for_status.return_value = None

        client = StreamingAvailabilityClient(api_key="test_key")
        client.get_availability(12345, content_type="series")

        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        self.assertIn("/shows/tv/12345", url)

    @patch("tracker.services.streaming_api.requests.get")
    def test_get_availability_movie_endpoint(self, mock_get):
        from tracker.services.streaming_api import StreamingAvailabilityClient

        mock_resp = mock_get.return_value
        mock_resp.json.return_value = {"streamingOptions": {}}
        mock_resp.raise_for_status.return_value = None

        client = StreamingAvailabilityClient(api_key="test_key")
        client.get_availability(12345, content_type="movie")

        mock_get.assert_called_once()
        url = mock_get.call_args[0][0]
        self.assertIn("/shows/movie/12345", url)


class SeedFixtureSeriesTests(TestCase):
    """Verify seed_prime_fixture --content-type argument."""

    def test_seed_prime_fixture_with_content_type_series(self):
        from django.core.management import call_command

        call_command("seed_prime_fixture", "--content-type", "series")
        movie = Movie.objects.filter(content_type="series").first()
        self.assertIsNotNone(movie)
        self.assertEqual(movie.content_type, "series")


class PrimeMovieEndpointTests(TestCase):
    """Tests for GET /api/prime/movie/<tmdb_id>/."""

    def setUp(self):
        # TMDb watch/providers gap-fill is a separate source with its own tests;
        # these assert on the mocked cache payload only.
        prime_patcher = patch(
            "tracker.services.offer_pipeline.prime_offers", return_value=[]
        )
        self.addCleanup(prime_patcher.stop)
        prime_patcher.start()

    @patch("tracker.services.offer_pipeline.AvailabilityCache")
    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_returns_ranked_official_and_free_offers(self, mock_cache_cls, mock_tmdb_cls, mock_enrich_tmdb_cls, mock_pipeline_cache_cls):
        mock_pipeline_cache_cls.return_value = mock_cache_cls.return_value
        """Detail endpoint returns ranked SVOD + BVOD offers, not scrapers."""
        from tracker.services.tmdb import TMDbMovie

        # Create a movie in the DB
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

        # Set up subscriptions so the pipeline includes these providers
        prime_provider = Provider.objects.get(slug="prime")
        netflix_provider = Provider.objects.get(slug="netflix")
        bbc_provider = Provider.objects.get(slug="bbc_iplayer")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")
        Subscription.objects.create(user_id=1, provider=netflix_provider, name="Netflix")
        Subscription.objects.create(user_id=1, provider=bbc_provider, name="BBC iPlayer")

        # Mock TMDb details (not called since movie exists)
        mock_tmdb = mock_tmdb_cls.return_value

        # Mock availability cache
        mock_cache = mock_cache_cls.return_value
        mock_cache.read_offers.return_value = [
            Offer(country="JP", service="prime", offer_type="subscription",
             quality="4k", audio_languages=["JA", "EN"], subtitle_languages=["EN", "JA"]),
            Offer(country="US", service="prime", offer_type="subscription",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            # A Free Streaming Provider (e.g. BBC iPlayer) — must be included.
            Offer(country="GB", service="bbc_iplayer", offer_type="free",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            # A Major (Netflix) — included now that the section is Official + Free.
            Offer(country="GB", service="netflix", offer_type="subscription",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            # A scraper (Imported Small Source) — must be filtered out of the
            # official section (it lives in the separate Unofficial Sources block).
            Offer(country="", service="hydrahd", offer_type="free",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"], is_scraped_source=True),
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/550/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["movie"]["tmdb_id"], 550)
        self.assertEqual(data["movie"]["title"], "Fight Club")
        # Prime + Free (bbc_iplayer) + Major (netflix); hydrahd scraper excluded.
        services = [o["service"] for o in data["offers"]]
        self.assertEqual(len(data["offers"]), 4)
        self.assertIn("BBC iPlayer", services)
        self.assertIn("Netflix", services)
        self.assertNotIn("HydraHD", services)
        # JA audio should be ranked first (rank 0)
        self.assertEqual(data["offers"][0]["country"], "JP")
        self.assertEqual(data["offers"][0]["audio_languages"], ["JA", "EN"])
        self.assertEqual(data["offers"][0]["ranking"]["audio_rank"], 0)
        # The stable service id "prime" is presented as its display name.
        self.assertEqual(data["offers"][0]["service"], "Prime Video")
        # Free providers carry a "BVOD" source indicator.
        bbc = next(o for o in data["offers"] if o["service"] == "BBC iPlayer")
        self.assertEqual(bbc["source_indicator"], "BVOD")
        # Free provider offers get a country fallback from the Provider row.
        self.assertEqual(bbc["country"], "GB")

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_auto_creates_movie_from_tmdb(self, mock_cache_cls, mock_tmdb_cls, mock_enrich_tmdb_cls):
        """If movie not in DB, prime_movie auto-creates from TMDb."""
        from tracker.services.tmdb import TMDbMovie

        mock_tmdb = mock_tmdb_cls.return_value
        mock_tmdb.details.return_value = TMDbMovie(
            tmdb_id=999, title="New Movie", year=2025
        )
        mock_enrich_tmdb_cls.return_value.details.return_value = TMDbMovie(
            tmdb_id=999, title="New Movie", year=2025
        )

        mock_cache = mock_cache_cls.return_value
        mock_cache.read_offers.return_value = []
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/999/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["movie"]["tmdb_id"], 999)
        self.assertEqual(data["offers"], [])
        self.assertTrue(Movie.objects.filter(tmdb_id=999).exists())

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_movie_not_tracked_tmdb_resolve_fails(self, mock_tmdb_cls, mock_enrich_tmdb_cls):
        """If movie is not tracked and TMDb can't resolve it, return 404 with clean JSON.

        Mocked so this stays offline and deterministic regardless of whether a
        real TMDb credential happens to be present in the environment.
        """
        mock_tmdb_cls.return_value.details.side_effect = Exception("404 Not Found")
        mock_enrich_tmdb_cls.return_value.details.side_effect = Exception("404 Not Found")

        from django.test import Client
        response = Client().get("/api/prime/movie/99999/")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["offers"], [])

    @patch("tracker.services.offer_pipeline.AvailabilityCache")
    @patch("tracker.views.AvailabilityCache")
    def test_filters_to_subscription_free_only(self, mock_cache_cls, mock_pipeline_cache_cls):
        mock_pipeline_cache_cls.return_value = mock_cache_cls.return_value
        """Only subscription/free offers are returned, rent/buy excluded."""
        movie = Movie.objects.create(tmdb_id=777, title="Test Movie", year=2024)

        prime_provider = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")

        mock_cache = mock_cache_cls.return_value
        mock_cache.read_offers.return_value = [
            Offer(country="US", service="prime", offer_type="subscription",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            Offer(country="US", service="prime", offer_type="rent",
             quality="4k", audio_languages=["EN"], subtitle_languages=["EN"]),
            Offer(country="US", service="prime", offer_type="buy",
             quality="4k", audio_languages=["EN"], subtitle_languages=["EN"]),
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/777/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertEqual(data["offers"][0]["offer_type"], "subscription")

    @patch("tracker.services.offer_pipeline.AvailabilityCache")
    @patch("tracker.views.AvailabilityCache")
    def test_offers_sorted_by_priority(self, mock_cache_cls, mock_pipeline_cache_cls):
        mock_pipeline_cache_cls.return_value = mock_cache_cls.return_value
        """Offers are sorted: JA audio > EN audio > quality."""
        movie = Movie.objects.create(tmdb_id=888, title="Sort Test", year=2024)

        prime_provider = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")

        mock_cache = mock_cache_cls.return_value
        mock_cache.read_offers.return_value = [
            # US: EN audio, EN subs — lower priority
            Offer(country="US", service="prime", offer_type="subscription",
             quality="hd", audio_languages=["EN"], subtitle_languages=["EN"]),
            # JP: JA audio, EN subs — highest priority (JA audio rank 0)
            Offer(country="JP", service="prime", offer_type="subscription",
             quality="hd", audio_languages=["JA", "EN"], subtitle_languages=["EN", "JA"]),
        ]
        mock_cache.get_or_refresh.return_value = None

        from django.test import Client
        response = Client().get("/api/prime/movie/888/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # JP offer (JA audio) should be ranked first
        self.assertEqual(data["offers"][0]["country"], "JP")
        self.assertEqual(data["offers"][0]["ranking"]["audio_rank"], 0)
        self.assertEqual(data["offers"][1]["country"], "US")
        self.assertGreater(data["offers"][1]["ranking"]["audio_rank"],
                          data["offers"][0]["ranking"]["audio_rank"])


class PrimeSeriesEndpointTests(TestCase):
    """Tests for GET /api/prime/series/<tmdb_id>/."""

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_returns_200_keyed_on_series_for_tracked_row(
        self, mock_cache_cls, mock_enrich_tmdb, mock_pipeline_cls
    ):
        """A tracked series is served under the ``series`` key without a TMDb call."""
        Movie.objects.create(tmdb_id=1396, title="Breaking Bad", year=2008,
                             content_type="series")
        mock_pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])

        from django.test import Client
        response = Client().get("/api/prime/series/1396/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("series", data)
        self.assertNotIn("movie", data)
        self.assertEqual(data["series"]["tmdb_id"], 1396)
        self.assertEqual(data["series"]["content_type"], "series")
        self.assertEqual(data["offers"], [])
        self.assertIn("is_in_watchlist", data)
        mock_enrich_tmdb.return_value.details.assert_not_called()

        # The offer pipeline runs against the series row itself.
        passed = mock_pipeline_cls.return_value.run.call_args[0][0]
        self.assertEqual(passed.content_type, "series")

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_resolves_unknown_id_through_tmdb_tv_endpoint(
        self, mock_cache_cls, mock_enrich_tmdb, mock_pipeline_cls
    ):
        """An untracked id is created from the TMDb TV details endpoint."""
        from tracker.services.tmdb import TMDbItem

        mock_enrich_tmdb.return_value.details.return_value = TMDbItem(
            tmdb_id=1399, title="New Series", year=2024, content_type="series",
            overview="A series.", poster_path="/s.jpg", genre_ids=[18],
        )
        mock_pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])

        from django.test import Client
        response = Client().get("/api/prime/series/1399/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["series"]["tmdb_id"], 1399)
        self.assertEqual(data["series"]["content_type"], "series")
        self.assertTrue(Movie.objects.filter(tmdb_id=1399, content_type="series").exists())
        mock_enrich_tmdb.return_value.details.assert_called_once_with(
            1399, content_type="series"
        )

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_movie_row_is_not_resolved_as_series(
        self, mock_cache_cls, mock_enrich_tmdb, mock_pipeline_cls
    ):
        """A row typed as a movie is never served (or flipped) as a series."""
        movie = Movie.objects.create(tmdb_id=4242, title="Some Movie",
                                     year=2021, content_type="movie")
        mock_enrich_tmdb.return_value.details.side_effect = Exception("404 Not Found")

        from django.test import Client
        response = Client().get("/api/prime/series/4242/")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["offers"], [])
        self.assertIn("Series not tracked", data["message"])
        movie.refresh_from_db()
        self.assertEqual(movie.content_type, "movie")
        mock_pipeline_cls.return_value.run.assert_not_called()

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.AvailabilityCache")
    def test_unresolvable_id_returns_404_with_clean_json(
        self, mock_cache_cls, mock_enrich_tmdb, mock_pipeline_cls
    ):
        mock_enrich_tmdb.return_value.details.side_effect = Exception("404 Not Found")

        from django.test import Client
        response = Client().get("/api/prime/series/99999/")

        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["series"]["tmdb_id"], 99999)
        self.assertIsNone(data["series"]["title"])
        self.assertEqual(data["offers"], [])

    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.views.AvailabilityCache")
    def test_current_country_query_param_reaches_pipeline(
        self, mock_cache_cls, mock_pipeline_cls
    ):
        Movie.objects.create(tmdb_id=1396, title="Breaking Bad", year=2008,
                             content_type="series")
        mock_pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])

        from django.test import Client
        response = Client().get("/api/prime/series/1396/?current_country=JP")

        self.assertEqual(response.status_code, 200)
        kwargs = mock_pipeline_cls.return_value.run.call_args.kwargs
        self.assertEqual(kwargs.get("current_country"), "JP")

    @patch("tracker.views.resolve_preferences")
    @patch("tracker.services.offer_pipeline.OfficialOffersPipeline")
    @patch("tracker.views.AvailabilityCache")
    def test_current_country_falls_back_to_saved_preference(
        self, mock_cache_cls, mock_pipeline_cls, mock_prefs
    ):
        Movie.objects.create(tmdb_id=1396, title="Breaking Bad", year=2008,
                             content_type="series")
        mock_prefs.return_value = {"current_country": "BR"}
        mock_pipeline_cls.return_value.run.return_value = MagicMock(offers_out=[])

        from django.test import Client
        response = Client().get("/api/prime/series/1396/")

        self.assertEqual(response.status_code, 200)
        kwargs = mock_pipeline_cls.return_value.run.call_args.kwargs
        self.assertEqual(kwargs.get("current_country"), "BR")


class SeriesAvailabilityCacheTests(TestCase):
    """Series rows must refresh through the TMDb-TV twin /shows/tv/ endpoint."""

    def test_stale_series_row_refreshes_with_series_content_type(self):
        from tracker.services.cache import AvailabilityCache
        from tracker.services.rate_limit import RateLimiter

        series = Movie.objects.create(tmdb_id=1396, title="Breaking Bad", year=2008,
                                      content_type="series")
        client = MagicMock()
        client.get_availability.return_value = MagicMock(offers=[])

        cache = AvailabilityCache(client=client, limiter=RateLimiter("test_series_cache"))
        cache.get_or_refresh(series)

        client.get_availability.assert_called_once_with(1396, content_type="series")
