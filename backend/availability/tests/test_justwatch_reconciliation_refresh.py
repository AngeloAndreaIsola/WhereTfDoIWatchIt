"""Combined regression: JustWatch refresh -> canonical persistence -> reconciliation.

Covers the three layers named in issue #192 together:
a JustWatch-sourced observation is persisted by the refresh service (ProviderContent,
ContentMapping, Offer) and then reconciled into an effective local offer, keyed off
the series TMDb id (the #97 / #203 parent-key invariant), never the child season id.
"""
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType
from availability.domain.models import DiscoveredContent, ProviderOffer
from availability.models import ContentMapping, Country, Offer, ProviderContent, Source
from availability.services.reconciliation import AvailabilityReconciliationService
from availability.services.refresh import AvailabilityRefreshService
from availability.sources.base import SourceFetchResult
from availability.sources.justwatch import JustWatchSource
from tracker.models import Movie, Provider

JW_PROVIDER_CONTENT_ID = "jw-season-1"
JW_URL = "https://example.test/justwatch/season-1"
SERIES_TMDB = 481991


class JustWatchRefreshReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.country, _ = Country.objects.get_or_create(
            code="NL", defaults={"name": "Netherlands"},
        )
        cls.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"},
        )
        cls.source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JustWatch"},
        )
        cls.series = Movie.objects.create(
            tmdb_id=SERIES_TMDB, content_type=ContentType.SERIES,
            title="Synthetic Series", year=2025,
        )

    def _content(self):
        return DiscoveredContent(
            provider_id="netflix", provider_content_id=JW_PROVIDER_CONTENT_ID,
            content_type=ContentType.SERIES, title=self.series.title, url=JW_URL,
            external_ids={"tmdb": str(SERIES_TMDB)},
        )

    def _offer(self):
        return ProviderOffer(
            provider_id="netflix", provider_content_id=JW_PROVIDER_CONTENT_ID,
            country="NL", content_type=ContentType.SERIES,
            tmdb_id=SERIES_TMDB, season_number=None, episode_number=None,
            offer_type=OfferType.SUBSCRIPTION,
        )

    def test_justwatch_refresh_persists_then_reconciles_to_series_key(self):
        result = SourceFetchResult(contents=[self._content()], offers=[self._offer()])
        with patch.object(JustWatchSource, "fetch", return_value=result):
            AvailabilityRefreshService(source=JustWatchSource()).refresh_catalogue(
                provider="netflix", country="NL",
            )

        # Refresh: one provider-native row, one mapping keyed to the series id, one offer.
        content_row = ProviderContent.objects.get(provider_content_id=JW_PROVIDER_CONTENT_ID)
        self.assertEqual(content_row.content_type, ContentType.SERIES)
        mapping = ContentMapping.objects.get(provider_content_id=JW_PROVIDER_CONTENT_ID)
        self.assertEqual(mapping.tmdb_id, SERIES_TMDB)
        self.assertEqual(mapping.provider.slug, "netflix")
        offer_row = Offer.objects.get(provider_content_id=JW_PROVIDER_CONTENT_ID)
        self.assertEqual(offer_row.tmdb_id, SERIES_TMDB)
        self.assertEqual(offer_row.provider.slug, "netflix")
        self.assertEqual(offer_row.source.slug, "justwatch")

        # Reconciliation: the JustWatch observation reconciles to an effective offer
        # keyed off the series TMDb id (parent-key invariant), not a child id.
        reconciled = AvailabilityReconciliationService().reconcile(
            content_types=(ContentType.SERIES,), tmdb_id=SERIES_TMDB, country="NL",
        )
        self.assertEqual(len(reconciled.offers), 1)
        self.assertIn(offer_row.pk, reconciled.offers[0].offer_ids)
        self.assertEqual(reconciled.offers[0].tmdb_id, SERIES_TMDB)
        self.assertTrue(reconciled.offers[0].currently_available)
