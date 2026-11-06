"""Country and content-aware freshness regression coverage."""
from __future__ import annotations

import datetime as dt

from django.test import TestCase
from django.utils import timezone

from availability.domain.enums import ContentType, OfferType, SourceType
from availability.freshness import source_is_stale
from availability.models import Country, Offer, Source
from tracker.models import Provider


class CountryAwareFreshnessTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="country-freshness-provider", name="Country Freshness Provider"
        )
        self.source = Source.objects.create(
            slug="streaming_availability",
            name="Streaming Availability",
            type=SourceType.API,
        )
        self.us = Country.objects.create(code="US", name="United States")
        self.nl = Country.objects.create(code="NL", name="Netherlands")
        self.jp = Country.objects.create(code="JP", name="Japan")

    def _observe(self, *, country, content_type=ContentType.MOVIE, age=dt.timedelta(hours=1), tag="offer"):
        offer = Offer.objects.create(
            provider=self.provider,
            country=country,
            source=self.source,
            content_type=content_type,
            tmdb_id=9001,
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=f"{content_type}-{country.code}-{tag}",
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
