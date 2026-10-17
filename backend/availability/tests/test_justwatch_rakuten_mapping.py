"""Rakuten TV (JustWatch ``wuaki``) mapping: the free-with-ads coverage fix.

JustWatch carries Rakuten TV under the pre-rebrand ``technicalName``
``wuaki``. It was unmapped, so every Rakuten TV offer — including the
ad-supported free stream it carries for titles like *Natural Born Killers* in
NL — was dropped before it could become an ``Offer``. These tests pin the
whole chain: the static mapping, the ``ADS`` -> ``free_with_ads`` normalization,
persistence onto a ``rakuten_tv`` provider, the report verdict, and the fact
that an unmapped package is *observable* rather than silently swallowed.
"""
from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from availability.domain.enums import OfferType
from availability.models import Country, Offer
from availability.services.normalize import normalize_justwatch_offer_type
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.justwatch import JustWatchSource
from availability.sources.justwatch_client import JustWatchClient
from availability.sources.justwatch_providers import provider_slug_for_package
from tracker.models import Movie
from tracker.services import availability_report as ar

from . import aggregator_fixtures as fx


def _sleeper(_seconds):
    """Never actually sleep in tests."""


def _source(payload):
    """A JustWatch source whose single POST returns *payload*."""

    def http_post(url, json=None, headers=None, timeout=None):
        return fx.FakeResponse(payload=payload)

    return JustWatchSource(client=JustWatchClient(http_post=http_post, sleeper=_sleeper))


class RakutenMappingTests(SimpleTestCase):
    def test_wuaki_maps_to_the_canonical_rakuten_tv_slug(self):
        self.assertEqual(provider_slug_for_package("wuaki"), "rakuten_tv")

    def test_ads_stays_free_with_ads_not_subscription_or_rental(self):
        """The access type must survive so the UI says "free with ads"."""
        self.assertEqual(
            normalize_justwatch_offer_type("ADS"), OfferType.FREE_WITH_ADS
        )
        self.assertNotEqual(
            normalize_justwatch_offer_type("ADS"), OfferType.SUBSCRIPTION
        )
        self.assertNotEqual(normalize_justwatch_offer_type("ADS"), OfferType.RENT)

    def test_unmapped_package_is_never_guessed(self):
        self.assertEqual(provider_slug_for_package("nosuchservice"), "")
        self.assertEqual(provider_slug_for_package(""), "")


class RakutenTitleFetchTests(TestCase):
    """Source -> Offer -> report for a free-with-ads Rakuten TV title."""

    @classmethod
    def setUpTestData(cls):
        cls.movie = Movie.objects.create(
            tmdb_id=241, title="Natural Born Killers", year=1994,
            content_type="movie",
        )
        cls.nl, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"}
        )

    def _refresh(self):
        return AvailabilityRefreshService(source=_source(fx.JW_SEARCH_RAKUTEN_NL))

    def test_title_fetch_persists_a_free_with_ads_offer_on_rakuten_tv(self):
        self._refresh().refresh_content(self.movie, country="NL")

        offer = Offer.objects.get(
            tmdb_id=241, provider__slug="rakuten_tv", country=self.nl
        )
        self.assertEqual(offer.offer_type, OfferType.FREE_WITH_ADS)
        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.source.slug, "justwatch")

    def test_the_buy_offer_is_still_excluded_by_design(self):
        """Only the ADS offer counts; the Apple BUY offer must not appear."""
        self._refresh().refresh_content(self.movie, country="NL")
        self.assertFalse(
            Offer.objects.filter(tmdb_id=241, provider__slug="apple").exists()
        )

    def test_the_report_resolves_the_title_available_via_rakuten_tv(self):
        self._refresh().refresh_content(self.movie, country="NL")
        report = ar.title_source_report([self.movie.pk])[self.movie.pk]

        self.assertEqual(report["state"], "available")
        self.assertIn("rakuten_tv", report["available_on"])

    def test_an_unmapped_package_is_logged_at_warning(self):
        """A dropped provider must be observable, never silently lost."""
        source = _source(fx.JW_CATALOGUE_UNMAPPED_ONLY)
        with self.assertLogs(
            "availability.sources.justwatch", level="WARNING"
        ) as captured:
            source.fetch(self.movie, country="NL")

        self.assertTrue(
            any("justwatch_provider_unmapped" in line for line in captured.output),
            captured.output,
        )
