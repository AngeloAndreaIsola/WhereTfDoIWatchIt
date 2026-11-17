from unittest.mock import patch

from django.test import TestCase

from tracker.models import CatalogueDiscoveryState, Movie, Provider, ScrapedContentMapping
from tracker.scrapers.base import ScrapedItem
from tracker.services.catalogue_discovery import CatalogueDiscoveryError, PROVIDERS, run_discovery
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

    def test_replaying_a_page_reports_persisted_items_as_unchanged(self):
        slug = "kisskh"

        def one_page(_scraper, provider_slug, part, page, query=None):
            _scraper.session.get("https://fixture.invalid/page")
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"

        with patch("tracker.services.catalogue_discovery._page", side_effect=one_page), \
             patch("tracker.services.catalogue_discovery._parts", return_value=["2"]), \
             patch("requests.Session.get", return_value=object()):
            first = run_discovery(slug, request_budget=2, delay=0)
            state = CatalogueDiscoveryState.objects.get(provider=self.providers[slug])
            state.cursor = {}
            state.save(update_fields=["cursor", "updated_at"])
            replay = run_discovery(slug, request_budget=2, delay=0)

        self.assertEqual(first["items_inserted"], 1)
        self.assertEqual(replay["items_unchanged"], 1)
        self.assertEqual(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).count(), 1)

    def test_failed_run_exposes_dispatched_request_count_and_durable_report(self):
        slug = "animeworld"

        def failed_page(scraper, provider_slug, part, page, query=None):
            scraper.session.get("https://fixture.invalid/page")
            raise RuntimeError("fixture page failure")

        with patch("tracker.services.catalogue_discovery._page", side_effect=failed_page), \
             patch("requests.Session.get", return_value=object()):
            with self.assertRaises(CatalogueDiscoveryError) as caught:
                run_discovery(slug, request_budget=3, delay=0)

        self.assertEqual(caught.exception.report["requests_made"], 1)
        state = CatalogueDiscoveryState.objects.get(provider=self.providers[slug])
        self.assertEqual(state.report["requests_dispatched"], 1)
        self.assertEqual(state.status, "FAILED")

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

    def test_native_ids_preserve_distinct_same_title_releases_and_are_idempotent(self):
        provider = self.providers["animeworld"]
        for native_id, url in (("release-a", "https://animeworld.example/a"), ("release-b", "https://animeworld.example/b")):
            persist_scraped_item(
                provider=provider,
                item=ScrapedItem(
                    title="Same Name", year=2020, content_type="movie",
                    provider_slug="animeworld", source_url=url,
                    raw_payload={"id": native_id, "unverified_discovery": True},
                ),
            )
        persist_scraped_item(
            provider=provider,
            item=ScrapedItem(
                title="Same Name", year=2020, content_type="movie",
                provider_slug="animeworld", source_url="https://animeworld.example/a-new-url",
                raw_payload={"id": "release-a", "unverified_discovery": True},
            ),
        )
        rows = ScrapedContentMapping.objects.filter(provider=provider, scraped_title="Same Name", scraped_year=2020)
        self.assertEqual(rows.count(), 2)
        self.assertSetEqual(set(rows.values_list("provider_content_id", flat=True)), {"release-a", "release-b"})
        self.assertEqual(rows.get(provider_content_id="release-a").scraped_url, "https://animeworld.example/a-new-url")

    def test_native_id_promotes_existing_legacy_row_without_losing_manual_match(self):
        provider = self.providers["animeworld"]
        movie = Movie.objects.create(tmdb_id=992020, title="Same Name", year=2020)
        legacy_item = ScrapedItem(
            title="Same Name", year=2020, content_type="movie", provider_slug="animeworld",
            source_url="https://animeworld.example/title", raw_payload={"unverified_discovery": True},
        )
        mapping = persist_scraped_item(provider=provider, item=legacy_item)
        mapping.movie = movie
        mapping.is_confirmed = True
        mapping.save(update_fields=["movie", "is_confirmed"])
        upgraded = persist_scraped_item(
            provider=provider,
            item=ScrapedItem(
                title="Same Name", year=2020, content_type="movie", provider_slug="animeworld",
                source_url=legacy_item.source_url,
                raw_payload={"id": "native-992020", "unverified_discovery": True},
            ),
        )
        self.assertEqual(upgraded.pk, mapping.pk)
        self.assertEqual(upgraded.provider_content_id, "native-992020")
        self.assertEqual(upgraded.movie_id, movie.pk)
        self.assertTrue(upgraded.is_confirmed)

    def test_dry_run_does_not_create_state_or_mappings(self):
        slug = "animeworld"
        def page(_scraper, provider_slug, part, page, query=None):
            return [fixture_item(provider_slug, part=part)], True, 1, "fixture_terminal_page"
        with patch("tracker.services.catalogue_discovery._page", side_effect=page):
            result = run_discovery(slug, request_budget=2, delay=0, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(CatalogueDiscoveryState.objects.filter(provider=self.providers[slug]).exists())
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.providers[slug]).exists())
