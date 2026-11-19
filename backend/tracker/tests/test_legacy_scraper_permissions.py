"""Execution permission regressions: fixture-only, with DNS/HTTP tripwires."""
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch

import requests
from django.test import TestCase
from rest_framework.test import APIClient

from availability.models import ContentMapping, Offer, ProviderContent
from tracker.models import CatalogueDiscoveryState, Movie, Provider, ScrapedContentMapping
from tracker.scrapers import resolve_scraper_class
from tracker.scrapers.adapters import GenericURLAdapter
from tracker.scrapers.base import ContentMatcher, ScrapedItem
from tracker.services.discovery_policy import DiscoveryPermission, PERMISSIONS, UNOFFICIAL_PROVIDERS
from tracker.services.scraper_policy import ScraperExecutionIneligible, require_legacy_execution
from tracker.services.scraper_runner import run_provider_scraper
from tracker.services.scraper_selection import select_provider_scraper


class LegacyScraperPermissionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.providers = []
        for slug in sorted(UNOFFICIAL_PROVIDERS):
            provider, _ = Provider.objects.update_or_create(
                slug=slug, defaults={"name": slug, "category": "scraper", "is_enabled": True,
                                     "domain": f"{slug}.example.test", "config": {}},
            )
            self.providers.append(provider)
        self.movie = Movie.objects.create(tmdb_id=9234501, title="Saved fixture", year=2020)
        self.saved = ScrapedContentMapping.objects.create(
            provider=self.providers[0], scraped_title="Saved fixture", scraped_year=2020,
            scraped_url="https://example.test/saved", movie=self.movie, is_confirmed=True,
        )
        CatalogueDiscoveryState.objects.create(
            provider=self.providers[0], cursor={"historical": {"page": 42}}, status="PARTIAL",
        )
        self.baseline = self.snapshot()
        self.http = self.enterContext(patch("requests.adapters.HTTPAdapter.send", side_effect=AssertionError("HTTP forbidden")))
        self.dns = self.enterContext(patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")))
        self.search = self.enterContext(patch("tracker.services.tmdb.TMDbClient.search", side_effect=AssertionError("TMDb forbidden")))
        self.details = self.enterContext(patch("tracker.services.tmdb.TMDbClient.details", side_effect=AssertionError("TMDb forbidden")))

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Movie, ProviderContent, ContentMapping, Offer,
                          ScrapedContentMapping, CatalogueDiscoveryState)
        }

    def tearDown(self):
        self.http.assert_not_called()
        self.dns.assert_not_called()
        self.search.assert_not_called()
        self.details.assert_not_called()
        self.assertEqual(self.snapshot(), self.baseline)
        self.assertEqual(Offer.objects.count(), 0)
        for provider in self.providers:
            provider.refresh_from_db()
            self.assertIsNone(provider.last_scraped_at)

    def test_all_legacy_http_routes_refuse_all_six_sources(self):
        for provider in self.providers:
            for path in (f"/api/providers/{provider.slug}/refresh/", "/api/scrapers/run/",
                         "/api/v2/unofficial/scrape/", "/api/scrapers/import-url/"):
                with self.subTest(provider=provider.slug, path=path):
                    response = self.client.post(path, {
                        "provider_slug": provider.slug, "url": f"https://{provider.domain}/saved",
                    }, format="json")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()["code"], "source_permission_unverified")

    def test_direct_execution_and_transport_cannot_bypass_selection(self):
        for provider in self.providers:
            scraper = resolve_scraper_class(provider.slug)()
            prepared = requests.Request("GET", "https://example.test/").prepare()
            for operation in (
                lambda: select_provider_scraper(provider), lambda: run_provider_scraper(provider),
                lambda: scraper.run(), lambda: scraper._execute_run(),
                lambda: scraper.fetch_url("https://example.test/"),
                lambda: scraper.session.get("https://example.test/"),
                lambda: scraper.session.request("POST", "https://example.test/"),
                lambda: scraper.session.send(prepared),
            ):
                with self.subTest(provider=provider.slug, operation=operation):
                    with self.assertRaises(ScraperExecutionIneligible):
                        operation()

    def test_matcher_refuses_both_local_association_and_tmdb_search(self):
        for provider in self.providers:
            for title in ("Saved fixture", "Unknown fixture"):
                with self.subTest(provider=provider.slug, title=title):
                    item = ScrapedItem(title=title, year=2020, provider_slug=provider.slug)
                    with self.assertRaises(ScraperExecutionIneligible):
                        ContentMatcher().match_item(item)
                    with self.assertRaises(ScraperExecutionIneligible):
                        ContentMatcher._upsert_mapping(provider, item, movie=self.movie)

    def test_permissions_deny_expiry_disable_and_self_approval_all_fail_closed(self):
        today = date.today()
        verified = DiscoveryPermission(
            "fixture", status="VERIFIED", hostname="example.test", paths=(r"/.*",),
            reviewed_on=today, expires_on=today + timedelta(days=1), evidence="fixture only",
        )
        cases = (
            ({}, "UNVERIFIED", None, "source_permission_unverified"),
            ({}, "DENIED", None, "source_permission_denied"),
            ({}, "VERIFIED", today - timedelta(days=1), "source_permission_unverified"),
            ({"discovery_permission": "DENIED"}, "VERIFIED", None, "source_permission_denied"),
            ({"discovery_permission": "VERIFIED", "allow_scraping": True}, "UNVERIFIED", None, "source_permission_unverified"),
            ({}, "VERIFIED", None, "legacy_request_envelope_unavailable"),
        )
        for provider in self.providers:
            for config, state, expiry, code in cases:
                with self.subTest(provider=provider.slug, state=state, code=code):
                    provider.config = config
                    permission = replace(verified, provider=provider.slug, status=state,
                                         expires_on=expiry or verified.expires_on)
                    with patch.dict(PERMISSIONS, {provider.slug: permission}):
                        with self.assertRaises(ScraperExecutionIneligible) as error:
                            select_provider_scraper(provider)
                    self.assertEqual(error.exception.code, code)
            provider.is_enabled = False
            with self.assertRaises(ScraperExecutionIneligible) as error:
                require_legacy_execution(provider)
            self.assertEqual(error.exception.code, "source_disabled")
            provider.is_enabled = True

    def test_registry_removal_and_category_change_cannot_authorize_source(self):
        for provider in self.providers:
            provider.category = "bvod"
            with patch("tracker.services.scraper_selection.resolve_scraper_class", return_value=None):
                with self.assertRaises(ScraperExecutionIneligible):
                    select_provider_scraper(provider)
        with self.assertRaises(ScraperExecutionIneligible):
            GenericURLAdapter("unknown-source", "example.test").run()

    def test_review_and_catalogue_reads_preserve_saved_observations_without_tmdb(self):
        pending = ScrapedContentMapping.objects.create(
            provider=self.providers[1], scraped_title="Pending fixture", scraped_year=2021,
        )
        self.baseline = self.snapshot()
        response = self.client.get("/api/scrapers/unresolved/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(pending.id, [row["id"] for row in response.json()])
        self.assertTrue(all(row["candidates"] == [] for row in response.json()))
        for path in ("/api/providers/", "/api/v2/unofficial/", "/api/catalog/unified/?scope=unofficial"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(Provider.objects.filter(slug__in=UNOFFICIAL_PROVIDERS).count(), 6)

    def test_legacy_confirmation_cannot_request_tmdb_or_change_saved_mapping(self):
        response = self.client.post("/api/scrapers/confirm-match/", {
            "mapping_id": self.saved.id, "tmdb_id": 99999,
        }, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "source_permission_unverified")
