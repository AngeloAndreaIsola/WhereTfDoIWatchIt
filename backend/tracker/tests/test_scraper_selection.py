"""Fail-closed scraper-selection coverage for every HTTP execution path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from io import StringIO

from django.test import TestCase
from django.core.management import call_command
from rest_framework.test import APIClient

from tracker.models import Provider, Subscription
from tracker.scrapers import SCRAPER_REGISTRY
from tracker.scrapers.adapters import AnimeWorldScraper, HydraHDScraper, KissKHScraper
from tracker.scrapers.nhk import NHKScraper
from tracker.scrapers.rai_play import RaiPlayScraper
from tracker.serializers import ProviderSerializer
from tracker.services.scraper_selection import (
    ScraperExecutionIneligible,
    select_provider_scraper,
)


class ScraperSelectionUnitTests(TestCase):
    def test_current_registry_entries_remain_eligible(self):
        expected = {
            "nhk_world": NHKScraper,
            "raiplay": RaiPlayScraper,
            "kisskh": KissKHScraper,
            "hydrahd": HydraHDScraper,
            "animeworld": AnimeWorldScraper,
        }
        for slug, scraper_class in expected.items():
            with self.subTest(slug=slug):
                provider, _ = Provider.objects.get_or_create(
                    slug=slug,
                    defaults={"name": slug, "category": "scraper"},
                )
                selection = select_provider_scraper(provider)
                self.assertEqual(selection.kind, "registered")
                self.assertIsInstance(selection.scraper, scraper_class)

    def test_new_listing_scrapers_are_registered_without_aliases(self):
        from tracker.scrapers import (
            AltaDefinizioneScraper,
            CineBlog01Scraper,
            StreamingCommunityScraper,
        )

        expected = {
            "streamingcommunity": StreamingCommunityScraper,
            "cineblog01": CineBlog01Scraper,
            "altadefinizione": AltaDefinizioneScraper,
        }
        for slug, scraper_class in expected.items():
            with self.subTest(slug=slug):
                provider, _ = Provider.objects.get_or_create(
                    slug=slug,
                    defaults={"name": slug, "category": "scraper"},
                )
                selection = select_provider_scraper(provider)
                self.assertEqual(selection.kind, "registered")
                self.assertIsInstance(selection.scraper, scraper_class)

    def test_metadata_importers_cannot_fall_through_to_generic_crawling(self):
        for slug in ("bilibili_tv", "ok_ru"):
            with self.subTest(slug=slug):
                provider = Provider.objects.create(
                    slug=slug,
                    name=slug,
                    category="scraper",
                    domain=f"{slug}.example.test",
                )
                with self.assertRaises(ScraperExecutionIneligible) as ctx:
                    select_provider_scraper(provider)
                self.assertEqual(ctx.exception.code, "discovery_only_provider")

    def test_unofficial_provider_seed_is_idempotent_and_preserves_existing_category(self):
        Provider.objects.create(
            slug="streamingcommunity", name="Existing source", category="bvod"
        )

        call_command("seed_unofficial_providers", stdout=StringIO())
        call_command("seed_unofficial_providers", stdout=StringIO())

        for slug in (
            "kisskh", "hydrahd", "animeworld", "streamingcommunity",
            "cineblog01", "altadefinizione",
        ):
            with self.subTest(slug=slug):
                self.assertEqual(Provider.objects.filter(slug=slug).count(), 1)
        self.assertEqual(
            Provider.objects.get(slug="streamingcommunity").category, "bvod"
        )

    def test_retired_bbc_scraper_raises_canonical_catalogue_ineligible(self):
        provider = Provider.objects.get(slug="bbc_iplayer")
        with self.assertRaises(ScraperExecutionIneligible) as ctx:
            select_provider_scraper(provider)
        self.assertEqual(ctx.exception.code, "canonical_catalogue_provider")
        self.assertEqual(ctx.exception.provider_slug, "bbc_iplayer")
        self.assertEqual(ctx.exception.source_slug, "bbc_iplayer")

    def test_existing_named_scrapers_keep_serializer_contract(self):
        for slug in ("raiplay", "nhk_world"):
            with self.subTest(slug=slug):
                self.assertTrue(ProviderSerializer(Provider.objects.get(slug=slug)).data["has_scraper"])
        self.assertFalse(ProviderSerializer(Provider.objects.get(slug="bbc_iplayer")).data["has_scraper"])


class ScraperSelectionEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _without_registry_entry(self, slug):
        """Return a patch context that simulates a future scraper retirement."""
        entries = dict(SCRAPER_REGISTRY)
        entries.pop(slug)
        return patch.dict(SCRAPER_REGISTRY, entries, clear=True)

    def _assert_ineligible(self, response, *, code, provider, source=None):
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], code)
        self.assertEqual(response.json()["provider"], provider)
        alternative = response.json()["alternative"]
        if source is None:
            self.assertIsNone(alternative)
        else:
            self.assertEqual(alternative, {"kind": "catalogue", "source": source})

    def test_provider_refresh_keeps_bbc_on_canonical_collection_after_registry_removal(self):
        with patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post("/api/providers/bbc_iplayer/refresh/")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["kind"], "catalogue")
        self.assertEqual(response.json()["source"], "bbc_iplayer")
        generic.assert_not_called()

    def test_scraper_run_rejects_bbc_without_generic_fallback(self):
        with patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post(
                "/api/scrapers/run/", {"provider_slug": "bbc_iplayer"}, format="json"
            )

        self._assert_ineligible(
            response, code="canonical_catalogue_provider", provider="bbc_iplayer", source="bbc_iplayer"
        )
        generic.assert_not_called()

    def test_import_url_rejects_bbc_without_generic_fallback(self):
        with patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post(
                "/api/scrapers/import-url/",
                {"url": "https://www.bbc.co.uk/iplayer/episodes/b006m86d/the-thick-of-it"},
                format="json",
            )

        self._assert_ineligible(
            response, code="canonical_catalogue_provider", provider="bbc_iplayer", source="bbc_iplayer"
        )
        generic.assert_not_called()

    def test_v2_scrape_rejects_bbc_without_generic_fallback(self):
        with patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post(
                "/api/v2/unofficial/scrape/", {"provider_slug": "bbc_iplayer"}, format="json"
            )

        self._assert_ineligible(
            response, code="canonical_catalogue_provider", provider="bbc_iplayer", source="bbc_iplayer"
        )
        generic.assert_not_called()

    def test_import_url_rejects_raiplay_without_generic_fallback(self):
        with self._without_registry_entry("raiplay"), patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post(
                "/api/scrapers/import-url/",
                {"url": "https://www.raiplay.it/film/example"},
                format="json",
            )

        self._assert_ineligible(
            response, code="canonical_catalogue_provider", provider="raiplay", source="rai_play"
        )
        generic.assert_not_called()
        self.assertFalse(Subscription.objects.filter(provider__slug="raiplay").exists())

    def test_v2_scrape_rejects_retired_nhk_without_generic_fallback(self):
        with self._without_registry_entry("nhk_world"), patch(
            "tracker.services.scraper_selection.GenericURLAdapter"
        ) as generic:
            response = self.client.post(
                "/api/v2/unofficial/scrape/", {"provider_slug": "nhk_world"}, format="json"
            )

        self._assert_ineligible(
            response, code="retired_named_scraper", provider="nhk_world"
        )
        generic.assert_not_called()

    def test_valid_import_uses_generic_adapter_and_keeps_import_contract(self):
        fake = MagicMock()
        fake.run.return_value = []
        with patch(
            "tracker.services.scraper_selection.GenericURLAdapter", return_value=fake
        ) as generic:
            response = self.client.post(
                "/api/scrapers/import-url/",
                {"url": "https://custom-source.example/title"},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["provider"]["slug"], "custom-source")
        generic.assert_called_once_with(provider_slug="custom-source", domain="custom-source.example")
        self.assertTrue(Subscription.objects.filter(provider__slug="custom-source").exists())
