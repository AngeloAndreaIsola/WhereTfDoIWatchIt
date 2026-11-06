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

from tracker.models import Movie, Availability, Region, LanguageInfo, LanguageEntry, Preference, Provider, ScrapedContentMapping, Subscription
from tracker.services.offers import Offer
from tracker.tests.availability_factories import local_offer

from availability.domain.enums import ContentType as CanonicalContentType
from availability.domain.enums import MatchMethod, SourceType
from availability.models import Source
from availability.persistence.repositories import MappingRepository, ProviderContentRepository
from availability.services.legacy_ingest import ingest_scraped_mapping


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

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_returns_ranked_official_and_free_offers(self, mock_tmdb_cls, mock_enrich_tmdb_cls):
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

        # Canonical offers for the movie.
        local_offer(movie, country="JP", service="prime", quality="4k",
                    audio_languages=["JA", "EN"], subtitle_languages=["EN", "JA"])
        local_offer(movie, country="US", service="prime", quality="hd",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        # A Free Streaming Provider (e.g. BBC iPlayer) — must be included.
        local_offer(movie, country="GB", service="bbc_iplayer", offer_type="free",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        # A Major (Netflix) — included now that the section is Official + Free.
        local_offer(movie, country="GB", service="netflix", quality="hd",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        # A scraper (Imported Small Source) — must be filtered out of the
        # official section (it lives in the separate Unofficial Sources block).
        Provider.objects.get_or_create(
            slug="hydrahd",
            defaults={"name": "HydraHD", "category": "scraper", "is_enabled": True},
        )
        local_offer(movie, country="US", service="hydrahd", offer_type="free",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

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
    def test_auto_creates_movie_from_tmdb(self, mock_tmdb_cls, mock_enrich_tmdb_cls):
        """If movie not in DB, prime_movie auto-creates from TMDb."""
        from tracker.services.tmdb import TMDbMovie

        mock_tmdb = mock_tmdb_cls.return_value
        mock_tmdb.details.return_value = TMDbMovie(
            tmdb_id=999, title="New Movie", year=2025
        )
        mock_enrich_tmdb_cls.return_value.details.return_value = TMDbMovie(
            tmdb_id=999, title="New Movie", year=2025
        )

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

    def test_filters_to_subscription_free_only(self):
        """Only subscription/free offers are returned, rent/buy excluded."""
        movie = Movie.objects.create(tmdb_id=777, title="Test Movie", year=2024)

        prime_provider = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")

        local_offer(movie, country="US", service="prime", offer_type="subscription",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(movie, country="US", service="prime", offer_type="rent",
                    quality="4k", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(movie, country="US", service="prime", offer_type="buy",
                    quality="4k", audio_languages=["EN"], subtitle_languages=["EN"])

        from django.test import Client
        response = Client().get("/api/prime/movie/777/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertEqual(data["offers"][0]["offer_type"], "subscription")

    def test_free_with_ads_offer_is_displayed(self):
        """A ``free_with_ads`` (AVOD) offer is a free stream and must show.

        Regression (Natural Born Killers, NL): the only offer was
        ``rakuten_tv | free_with_ads``. The pipeline kept only
        ``("subscription", "free")``, so the detail page rendered nothing even
        though the offer was persisted and canonical.
        """
        movie = Movie.objects.create(tmdb_id=241, title="Natural Born Killers", year=1994)
        local_offer(movie, country="NL", service="rakuten_tv", offer_type="free_with_ads",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        from django.test import Client
        response = Client().get("/api/prime/movie/241/?current_country=NL")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["offers"]), 1)
        self.assertEqual(data["offers"][0]["offer_type"], "free_with_ads")
        self.assertEqual(data["offers"][0]["country"], "NL")

    def test_unowned_service_offers_are_shown_but_not_claimed_accessible(self):
        """Movie 194 regression: offers on unowned services must not vanish.

        Amélie's only offers were Netflix/Max/Hulu — none selected by the user.
        The pipeline used to drop every offer whose provider was not
        subscribed, leaving ``offers == []`` while the source-first verdict
        still said ``available``; the page then showed "a source reported this
        title as available, but no current offer could be displayed" about
        offers it had silently discarded. The offers must stay in the list,
        annotated as not-entitled and not accessible, and the verdict must
        never claim watchability with the user's subscriptions.
        """
        movie = Movie.objects.create(tmdb_id=194, title="Amélie", year=2001)
        # No Subscription rows at all: every offer is from an unowned service.
        local_offer(movie, country="NL", service="netflix", offer_type="subscription",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(movie, country="NL", service="hulu", offer_type="subscription",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])
        local_offer(movie, country="NL", service="max", offer_type="subscription",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        from django.test import Client
        response = Client().get("/api/prime/movie/194/?current_country=NL")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual({o["service"] for o in data["offers"]},
                         {"Netflix", "Hulu", "Max"})
        for offer in data["offers"]:
            self.assertFalse(offer["is_entitled"])
            self.assertFalse(offer["is_accessible"])
        # Available, but not with the user's subscriptions — never "available
        # here" and never hidden. The request is for NL; the offers are
        # present (foreign/unowned) but do not constitute verified NL
        # availability, so the state stays "unknown" while the verdict
        # remains "available_elsewhere".
        self.assertEqual(data["watchability"]["verdict"], "available_elsewhere")
        self.assertEqual(data["availability"]["state"], "unknown")

    def test_accessible_offer_outranks_unowned_offer(self):
        """A playable offer leads the list even when an unowned offer ranks higher.

        Ranking still sorts by language/quality within each group, but an offer
        the user can actually watch is a better "Best Match" than a
        higher-scored one they cannot play.
        """
        movie = Movie.objects.create(tmdb_id=889, title="Order Test", year=2024)
        Preference.objects.create(
            user_id=1, audio_languages=["EN", "IT", "JA"],
            subtitle_languages=["EN", "IT", "JA"], current_country="US",
        )
        prime_provider = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")

        # Netflix (unowned) has the better JA audio rank; Prime (owned) is EN.
        local_offer(movie, country="US", service="netflix",
                    quality="hd", audio_languages=["JA"], subtitle_languages=["JA"])
        local_offer(movie, country="US", service="prime",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        from django.test import Client
        response = Client().get("/api/prime/movie/889/?current_country=US")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["offers"][0]["is_accessible"])
        self.assertEqual(data["offers"][0]["service"], "Prime Video")
        self.assertFalse(data["offers"][1]["is_accessible"])

    def test_detail_includes_canonical_availability_verdict(self):
        """The detail payload carries the canonical verdict, never just offers.

        The frontend reads ``availability`` to tell "checked and unavailable"
        apart from "the evidence never resolved"; without it an empty ``offers``
        list always rendered "Availability not known yet."
        """
        movie = Movie.objects.create(tmdb_id=241, title="Natural Born Killers", year=1994)
        local_offer(movie, country="NL", service="rakuten_tv", offer_type="free_with_ads",
                    quality="hd", audio_languages=["EN"], subtitle_languages=["EN"])

        from django.test import Client
        response = Client().get("/api/prime/movie/241/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("availability", data)
        verdict = data["availability"]
        self.assertEqual(verdict["state"], "available")
        self.assertIn("rakuten_tv", verdict["available_on"])
        self.assertIn("resolved_sources", verdict)
        self.assertIn("sources", verdict)

    def test_detail_verdict_is_unknown_when_no_source_has_answered(self):
        """No offers and no request evidence ⇒ ``unknown``, never ``unavailable``."""
        Movie.objects.create(tmdb_id=242, title="Never Checked", year=2020)

        from django.test import Client
        response = Client().get("/api/prime/movie/242/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["offers"], [])
        self.assertEqual(data["availability"]["state"], "unknown")
        self.assertNotEqual(data["availability"]["state"], "unavailable")

    def test_offers_sorted_by_priority(self):
        """Offers are sorted: JA audio > EN audio > quality."""
        movie = Movie.objects.create(tmdb_id=888, title="Sort Test", year=2024)

        prime_provider = Provider.objects.get(slug="prime")
        Subscription.objects.create(user_id=1, provider=prime_provider, name="Prime Video")

        # US: EN audio, EN subs — lower priority
        local_offer(movie, country="US", service="prime", quality="hd",
                    audio_languages=["EN"], subtitle_languages=["EN"])
        # JP: JA audio, EN subs — highest priority (JA audio rank 0)
        local_offer(movie, country="JP", service="prime", quality="hd",
                    audio_languages=["JA", "EN"], subtitle_languages=["EN", "JA"])

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
    def test_returns_200_keyed_on_series_for_tracked_row(
        self, mock_enrich_tmdb, mock_pipeline_cls
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
    def test_resolves_unknown_id_through_tmdb_tv_endpoint(
        self, mock_enrich_tmdb, mock_pipeline_cls
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
    def test_movie_row_is_not_resolved_as_series(
        self, mock_enrich_tmdb, mock_pipeline_cls
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
    def test_unresolvable_id_returns_404_with_clean_json(
        self, mock_enrich_tmdb, mock_pipeline_cls
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
    def test_current_country_query_param_reaches_pipeline(
        self, mock_pipeline_cls
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
    def test_current_country_falls_back_to_saved_preference(
        self, mock_pipeline_cls, mock_prefs
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


class PrimeScrapedBvodBlockTests(TestCase):
    """The prime detail page's scraped BVOD block reads the canonical catalogue.

    Legacy behaviour kept through the Phase C read migration: an ingested
    scraper row for a bvod/avod/tvod/fast provider appears in the main offers
    list (show-level only), rows that are unavailable or owned by a disabled
    provider do not, and rows a canonical source observed stay out — they
    belong to the official read path.
    """

    def setUp(self):
        self.movie = Movie.objects.create(
            tmdb_id=770001, title="Scraped BVOD Film", year=2021,
            content_type="movie",
        )
        self.series = Movie.objects.create(
            tmdb_id=770002, title="Scraped BVOD Show", year=2019,
            content_type="series",
            seasons_data=[{"season_number": 1, "name": "Season 1"}],
        )
        self.provider, _ = Provider.objects.get_or_create(
            slug="bvod-ione",
            defaults={
                "name": "BVOD One",
                "category": "bvod",
                "is_enabled": True,
                "country_code": "IT",
            },
        )
        self.provider.category = "bvod"
        self.provider.is_enabled = True
        self.provider.save()

    def _ingest(self, target, *, is_available=True):
        row = ScrapedContentMapping.objects.create(
            provider=self.provider,
            movie=target,
            scraped_title=target.title,
            scraped_year=target.year,
            scraped_url="https://bvod-ione.example/watch",
            country_code="IT",
            is_available=is_available,
            raw_payload={"audio_languages": ["EN"], "subtitle_languages": ["IT"]},
        )
        ingest_scraped_mapping(row)
        return row

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_ingested_bvod_row_appears_once_in_prime_offers(self, *_):
        self._ingest(self.movie)

        from django.test import Client
        response = Client().get(f"/api/prime/movie/{self.movie.tmdb_id}/")

        self.assertEqual(response.status_code, 200)
        offers = response.json()["offers"]
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer["service"], "BVOD One")
        self.assertEqual(offer["source_indicator"], "BVOD")
        self.assertEqual(offer["offer_type"], "free")
        self.assertEqual(offer["country"], "IT")
        self.assertEqual(offer["audio_languages"], ["EN"])
        self.assertEqual(offer["subtitle_languages"], ["IT"])
        # The scraped block ranks its offers, so the payload is the
        # RankedOffer shape, exactly like the official offers above it.
        self.assertIn("ranking", offer)
        self.assertIn("is_language_match", offer)

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_season_request_skips_the_scraped_block(self, *_):
        self._ingest(self.series)

        from django.test import Client
        client = Client()
        # Without a season param the show-level scraped row is served.
        no_season = client.get(
            f"/api/prime/series/{self.series.tmdb_id}/"
        ).json()
        self.assertEqual(len(no_season["offers"]), 1)

        # Scraper rows carry no season signal: a season-filtered request
        # never includes them.
        with_season = client.get(
            f"/api/prime/series/{self.series.tmdb_id}/?season=1"
        ).json()
        self.assertEqual(with_season["offers"], [])
        self.assertEqual(with_season["selected_season"], 1)

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_unavailable_scraped_row_is_excluded(self, *_):
        self._ingest(self.movie, is_available=False)

        from django.test import Client
        response = Client().get(f"/api/prime/movie/{self.movie.tmdb_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["offers"], [])

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_disabled_provider_row_is_excluded(self, *_):
        self._ingest(self.movie)
        self.provider.is_enabled = False
        self.provider.save()

        from django.test import Client
        response = Client().get(f"/api/prime/movie/{self.movie.tmdb_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["offers"], [])

    @patch("tracker.services.enrichment.TMDbClient")
    @patch("tracker.views.TMDbClient")
    def test_canonical_source_row_is_not_in_the_scraped_block(self, *_):
        # A row a canonical source observed for the same bvod provider: it
        # surfaces through the official read path, never the scraped block.
        source, _ = Source.objects.get_or_create(
            slug="availability-api",
            defaults={"name": "Availability API", "type": SourceType.API},
        )
        content, _ = ProviderContentRepository().upsert(
            provider=self.provider,
            source=source,
            provider_content_id="canon-770001",
            content_type=CanonicalContentType.MOVIE,
            title=self.movie.title,
            provider_url="https://bvod-ione.example/canon",
        )
        MappingRepository().record(
            provider=self.provider,
            source=source,
            provider_content_id="canon-770001",
            content_type=CanonicalContentType.MOVIE,
            tmdb_id=self.movie.tmdb_id,
            confidence=1.0,
            match_method=MatchMethod.TITLE_YEAR,
            content=content,
        )

        from django.test import Client
        response = Client().get(f"/api/prime/movie/{self.movie.tmdb_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["offers"], [])
