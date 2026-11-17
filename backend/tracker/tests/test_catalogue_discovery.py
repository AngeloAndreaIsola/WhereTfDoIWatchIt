from unittest.mock import patch

from django.test import TestCase

from tracker.models import CatalogueDiscoveryState, Provider, ScrapedContentMapping
from tracker.scrapers.base import ScrapedItem
from tracker.services.catalogue_discovery import PROVIDERS, run_discovery
from tracker.services.scraped_mapping import persist_scraped_item


def fixture_item(slug, page=1, part=""):
    return ScrapedItem(
        title=f"Fixture {slug} {part} {page}", provider_slug=slug, year=2020,
        content_type="series" if (slug == "hydrahd" and part == "tv-shows") or (slug == "kisskh" and part in ("1", "3")) else "movie",
        source_url=f"https://{slug}.example/title/{part}/{page}",
        raw_payload={"id": f"{slug}-{part}-{page}", "unverified_discovery": True},
    )


class CatalogueDiscoveryTests(TestCase):
    def setUp(self):
        self.providers = {
            slug: Provider.objects.get_or_create(slug=slug, defaults={"name": slug, "category": "scraper"})[0]
            for slug in PROVIDERS
        }

    def test_fixture_walks_all_six_provider_strategies_and_marks_discoveries_unverified(self):
        for slug in sorted(PROVIDERS):
            with self.subTest(provider=slug):
                def page(_scraper, provider_slug, part, page, query=None):
                    _scraper.session.get("https://fixture.invalid/page")
                    return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"

                with patch("tracker.services.catalogue_discovery._page", side_effect=page), patch("requests.Session.get", return_value=object()):
                    result = run_discovery(slug, request_budget=10, delay=0)
                self.assertEqual(result["status"], "COMPLETED")
                self.assertEqual(result["pages_scanned"], len(result["checkpoint"]["done"]))
                mappings = ScrapedContentMapping.objects.filter(provider=self.providers[slug])
                self.assertEqual(mappings.count(), result["pages_scanned"])
                self.assertTrue(all(mapping.raw_payload["unverified_discovery"] for mapping in mappings))
                self.assertFalse(any(mapping.is_confirmed for mapping in mappings))
                self.assertEqual(CatalogueDiscoveryState.objects.get(provider=self.providers[slug]).status, "COMPLETED")

    def test_budget_exhaustion_is_partial_and_resume_uses_committed_checkpoint(self):
        slug = "animeworld"

        def pages(_scraper, provider_slug, part, page, query=None):
            _scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, page)], page == 2, 2, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery._page", side_effect=pages), patch("requests.Session.get", return_value=object()):
            first = run_discovery(slug, request_budget=1, delay=0)
            self.assertEqual(first["status"], "PARTIAL")
            second = run_discovery(slug, request_budget=2, delay=0)
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(second["pages_scanned"], 1)
        self.assertEqual(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).count(), 2)

    def test_targeted_results_are_never_reported_complete(self):
        slug = "kisskh"
        with patch("tracker.services.catalogue_discovery.resolve_scraper_class") as resolve:
            scraper = resolve.return_value.return_value
            scraper.run.return_value = [fixture_item(slug)]
            result = run_discovery(slug, mode="targeted", targeted_query="fixture", delay=0)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["items_seen"], 1)

    def test_movie_and_series_with_same_title_year_keep_separate_identities(self):
        provider = self.providers["animeworld"]
        for kind in ("movie", "series"):
            persist_scraped_item(
                provider=provider,
                item=ScrapedItem(
                    title="Same Name", year=2020, content_type=kind,
                    provider_slug="animeworld", source_url=f"https://animeworld.example/{kind}",
                    raw_payload={"unverified_discovery": True},
                ),
            )
        rows = ScrapedContentMapping.objects.filter(provider=provider, scraped_title="Same Name", scraped_year=2020)
        self.assertEqual(rows.count(), 2)
        self.assertSetEqual(set(rows.values_list("scraped_content_type", flat=True)), {"movie", "series"})

    def test_dry_run_does_not_create_state_or_mappings(self):
        slug = "animeworld"
        def page(_scraper, provider_slug, part, page, query=None):
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"
        with patch("tracker.services.catalogue_discovery._page", side_effect=page):
            result = run_discovery(slug, request_budget=2, delay=0, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.providers[slug]).exists())
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())
