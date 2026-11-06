"""Country and content-aware freshness regression coverage."""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, OfferType, SourceType
from availability.freshness import source_is_stale
from availability.models import Country, Offer, Source
from availability.sources.streaming_availability import StreamingAvailabilityClient, StreamingAvailabilitySource
from tracker.models import Provider
from tracker.services.streaming_api import StreamingResult, StreamingOffer, UpstreamTitleNotFound


class CountryAwareFreshnessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._socket_patcher = patch("socket.socket", side_effect=RuntimeError("Network access forbidden in tests"))
        cls._socket_patcher.start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        try:
            cls._socket_patcher.stop()
        except Exception:
            pass

    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(
            slug="country-freshness-provider", defaults={"name": "Country Freshness Provider"}
        )
        self.source, _ = Source.objects.get_or_create(
            slug="streaming_availability",
            defaults={"name": "Streaming Availability", "type": SourceType.API},
        )
        self.us, _ = Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.nl, _ = Country.objects.get_or_create(code="NL", defaults={"name": "Netherlands"})
        self.jp, _ = Country.objects.get_or_create(code="JP", defaults={"name": "Japan"})

    def _observe(self, *, country, content_type=ContentType.MOVIE, age=dt.timedelta(hours=1), tag="offer"):
        offer, _ = Offer.objects.get_or_create(
            provider=self.provider,
            country=country,
            source=self.source,
            content_type=content_type,
            tmdb_id=9001,
            offer_type=OfferType.SUBSCRIPTION,
            defaults={"provider_content_id": f"{content_type}-{country.code}-{tag}"},
        )
        Offer.objects.filter(pk=offer.pk).update(last_seen=timezone.now() - age)

    def test_fresh_us_evidence_cannot_suppress_netherlands_refresh(self):
        self._observe(country=self.us)

        self.assertFalse(source_is_stale(9001, self.source.slug, country="US"))
        self.assertTrue(source_is_stale(9001, self.source.slug, country="NL"))

    def test_fresh_netherlands_evidence_satisfies_netherlands(self):
        self._observe(country=self.nl)

        self.assertFalse(source_is_stale(9001, self.source.slug, country="NL"))

    def test_recent_japan_check_cannot_suppress_netherlands_refresh(self):
        self._observe(country=self.jp)

        self.assertTrue(source_is_stale(9001, self.source.slug, country="NL"))

    def test_explicit_country_never_uses_unscoped_legacy_evidence(self):
        self._observe(country=self.us)

        # The legacy global query remains available only when requested as such.
        self.assertFalse(source_is_stale(9001, self.source.slug))
        self.assertTrue(source_is_stale(9001, self.source.slug, country="NL"))

    def test_movie_and_series_with_same_tmdb_id_are_not_interchangeable(self):
        self._observe(country=self.nl, content_type=ContentType.MOVIE)

        self.assertFalse(
            source_is_stale(9001, self.source.slug, country="NL", content_type=ContentType.MOVIE)
        )
        self.assertTrue(
            source_is_stale(9001, self.source.slug, country="NL", content_type=ContentType.SERIES)
        )

    def test_upstream_client_deterministic_outcomes(self):
        """Mock upstream refresh calls at actual client/service boundary with deterministic results."""
        client = MagicMock(spec=StreamingAvailabilityClient)
        source = StreamingAvailabilitySource(client=client)
        movie_mock = MagicMock(tmdb_id=9001, content_type=ContentType.MOVIE)

        # 1. NL positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9001,
            offers=[StreamingOffer(country="NL", service_id="netflix", service_name="Netflix", offer_type="subscription")]
        )
        res_nl = source.fetch(movie_mock)
        self.assertEqual(len(res_nl.offers), 1)

        # 2. US-only
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9001,
            offers=[StreamingOffer(country="US", service_id="hulu", service_name="Hulu", offer_type="subscription")]
        )
        res_us = source.fetch(movie_mock)
        self.assertEqual(res_us.offers[0].country, "US")

        # 3. JP positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9001,
            offers=[StreamingOffer(country="JP", service_id="prime", service_name="Prime", offer_type="subscription")]
        )
        res_jp = source.fetch(movie_mock)
        self.assertEqual(res_jp.offers[0].country, "JP")

        # 4. No offer
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9001, offers=[]
        )
        res_empty = source.fetch(movie_mock)
        self.assertEqual(res_empty.offers, [])

        # 5. TITLE_NOT_FOUND
        client.get_availability.side_effect = UpstreamTitleNotFound("not found")
        with self.assertRaises(UpstreamTitleNotFound):
            source.fetch(movie_mock)

        # 6. Transport failure
        client.get_availability.side_effect = ConnectionError("transport failure")
        with self.assertRaises(ConnectionError):
            source.fetch(movie_mock)

        # 7. Quota exhaustion
        client.get_availability.side_effect = RuntimeError("quota exhausted")
        with self.assertRaises(RuntimeError):
            source.fetch(movie_mock)

        # 8. Legacy unknown
        client.get_availability.side_effect = None
        client.get_availability.return_value = StreamingResult(tmdb_id=9001, offers=[])
        res_legacy = source.fetch(movie_mock)
        self.assertIsNotNone(res_legacy)
