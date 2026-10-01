from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType, SourceType
from availability.models import Country, Offer as AvailabilityOffer, Source
from tracker.models import Movie, Provider, ProviderCategory


class AvailabilityReadBoundaryTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="local-read-netflix",
            defaults={"name": "Netflix", "category": ProviderCategory.BVOD},
        )
        self.source, _ = Source.objects.get_or_create(
            slug="availability-api", defaults={"name": "Availability API", "type": SourceType.API}
        )
        self.country, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})

    def local_offer(self, movie):
        return AvailabilityOffer.objects.create(
            provider=self.provider,
            country=self.country,
            source=self.source,
            content_type=movie.content_type,
            tmdb_id=movie.tmdb_id,
            offer_type=OfferType.SUBSCRIPTION,
            provider_url="https://example.test/watch",
            audio_languages=["EN"],
            subtitle_languages=[],
        )

    def request_without_refresh(self, url, **patches):
        with patch("tracker.services.streaming_api.StreamingAvailabilityClient.get_availability") as fetch, patch(
            "availability.services.refresh.AvailabilityRefreshService.refresh_content"
        ) as refresh_content, patch(
            "availability.services.refresh.AvailabilityRefreshService.refresh_catalogue"
        ) as refresh_catalogue:
            fetch.side_effect = AssertionError("HTTP request triggered availability fetch")
            refresh_content.side_effect = AssertionError(
                "HTTP request triggered availability refresh service"
            )
            refresh_catalogue.side_effect = AssertionError(
                "HTTP request triggered catalogue refresh service"
            )
            response = self.client.get(url)
        fetch.assert_not_called()
        refresh_content.assert_not_called()
        refresh_catalogue.assert_not_called()
        return response

    @patch("tracker.views.enrich_movie_from_tmdb")
    def test_movie_detail_does_not_refresh_availability(self, enrich):
        movie = Movie.objects.create(tmdb_id=101, title="Movie", year=2024)
        self.local_offer(movie)

        response = self.request_without_refresh(f"/api/movie/{movie.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_movie_availability_reads_local_availability_without_refresh(self):
        movie = Movie.objects.create(tmdb_id=105, title="Movie", year=2024)
        self.local_offer(movie)

        response = self.request_without_refresh("/api/movie/105/availability/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["offers"]), 1)

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_prime_movie_reads_local_availability_without_refresh(self, enrich):
        movie = Movie.objects.create(tmdb_id=102, title="Movie", year=2024)
        self.local_offer(movie)

        response = self.request_without_refresh("/api/prime/movie/102/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["offers"][0]["service"], "Netflix")

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_prime_series_reads_local_availability_without_refresh(self, enrich):
        series = Movie.objects.create(tmdb_id=103, title="Series", year=2024, content_type="series")
        AvailabilityOffer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.SERIES, tmdb_id=series.tmdb_id,
            season_number=1, episode_number=1, offer_type=OfferType.SUBSCRIPTION,
            provider_url="https://example.test/watch", audio_languages=None, subtitle_languages=[],
        )

        response = self.request_without_refresh("/api/prime/series/103/?season=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["offers"]), 1)

    @patch("tracker.services.offer_pipeline.enrich_movie_from_tmdb")
    def test_prime_reads_never_invoke_tmdb_watch_providers(self, enrich):
        """Retired Prime gap-fill: prime detail reads never call TMDb watch/providers.

        TMDb Watch Providers is recorded in SPEC "Future Directions" as a
        potential future availability source; until it exists as a write-side
        source, no read may invoke it.
        """
        movie = Movie.objects.create(tmdb_id=106, title="Movie", year=2024)
        self.local_offer(movie)
        series = Movie.objects.create(tmdb_id=107, title="Series", year=2024, content_type="series")
        AvailabilityOffer.objects.create(
            provider=self.provider, country=self.country, source=self.source,
            content_type=ContentType.SERIES, tmdb_id=series.tmdb_id,
            season_number=1, episode_number=1, offer_type=OfferType.SUBSCRIPTION,
            provider_url="https://example.test/watch", audio_languages=None, subtitle_languages=[],
        )

        with patch("tracker.services.tmdb.TMDbClient.watch_providers") as watch_providers:
            watch_providers.side_effect = AssertionError(
                "Prime read invoked TMDb watch/providers"
            )
            movie_response = self.request_without_refresh("/api/prime/movie/106/")
            series_response = self.request_without_refresh("/api/prime/series/107/?season=1")

        watch_providers.assert_not_called()
        self.assertEqual(movie_response.status_code, 200)
        self.assertEqual(series_response.status_code, 200)

    def test_watch_redirect_uses_existing_local_offer_without_refresh(self):
        movie = Movie.objects.create(tmdb_id=104, title="Movie", year=2024)
        self.local_offer(movie)

        response = self.request_without_refresh("/api/watch/104/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["found"])
        self.assertEqual(response.json()["watch_url"], "https://example.test/watch")
