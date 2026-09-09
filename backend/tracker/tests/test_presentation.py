from unittest.mock import MagicMock, patch

from django.test import TestCase

from tracker.models import ProviderCategory
from tracker.services.offers import Offer
from tracker.services.presentation import PresentedOffer, present_offer


def _make_provider(**overrides):
    prov = MagicMock()
    prov.slug = overrides.get("slug", "test")
    prov.name = overrides.get("name", "Test Provider")
    prov.source_indicator = overrides.get("source_indicator", "Official")
    prov.country_code = overrides.get("country_code", "")
    prov.category = overrides.get("category", ProviderCategory.OFFICIAL)
    return prov


class PresentOfferTests(TestCase):
    def test_known_provider_slug(self):
        prov = _make_provider(
            slug="netflix", name="Netflix", source_indicator="Official",
            country_code="US", category=ProviderCategory.OFFICIAL,
        )
        provider_map = {"netflix": prov}
        offer = Offer(service="netflix", country="US")
        result = present_offer(offer, provider_map)
        self.assertEqual(
            result,
            PresentedOffer(
                service_display="Netflix", source_indicator="Official",
                country="US", is_official=True,
            ),
        )

    def test_country_fallback_from_provider(self):
        prov = _make_provider(
            slug="netflix", name="Netflix", source_indicator="Official",
            country_code="GB", category=ProviderCategory.OFFICIAL,
        )
        provider_map = {"netflix": prov}
        offer = Offer(service="netflix", country="")
        result = present_offer(offer, provider_map)
        self.assertEqual(result.country, "GB")

    @patch("tracker.services.streaming_api.service_display_name")
    def test_unknown_slug_legacy_fallback(self, mock_display):
        mock_display.return_value = "Prime Video"
        provider_map = {}
        offer = Offer(service="prime", country="US")
        result = present_offer(offer, provider_map)
        self.assertEqual(result.service_display, "Prime Video")
        self.assertEqual(result.source_indicator, "Official")
        self.assertTrue(result.is_official)

    def test_case_whitespace_normalization(self):
        prov = _make_provider(
            slug="netflix", name="Netflix", source_indicator="Official",
            country_code="US", category=ProviderCategory.OFFICIAL,
        )
        provider_map = {"netflix": prov}
        offer = Offer(service=" Netflix ", country="US")
        result = present_offer(offer, provider_map)
        self.assertEqual(result.service_display, "Netflix")

    def test_is_official_true_for_official_category(self):
        prov = _make_provider(category=ProviderCategory.OFFICIAL)
        provider_map = {"test": prov}
        offer = Offer(service="test", country="US")
        result = present_offer(offer, provider_map)
        self.assertTrue(result.is_official)

    def test_is_official_true_for_bvod_category(self):
        prov = _make_provider(category=ProviderCategory.BVOD)
        provider_map = {"test": prov}
        offer = Offer(service="test", country="US")
        result = present_offer(offer, provider_map)
        self.assertTrue(result.is_official)

    def test_is_official_true_for_avod_category(self):
        prov = _make_provider(category=ProviderCategory.AVOD)
        provider_map = {"test": prov}
        offer = Offer(service="test", country="US")
        result = present_offer(offer, provider_map)
        self.assertTrue(result.is_official)

    def test_is_official_false_for_scraper_category(self):
        prov = _make_provider(category=ProviderCategory.SCRAPER)
        provider_map = {"test": prov}
        offer = Offer(service="test", country="US")
        result = present_offer(offer, provider_map)
        self.assertFalse(result.is_official)