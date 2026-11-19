from unittest.mock import patch
from django.test import TestCase, SimpleTestCase

from tracker.models import DiscoveredCatalogueRecord, Provider, ScrapedContentMapping
from tracker.scrapers.base import ScrapedItem
from tracker.scrapers.adapters import AnimeWorldScraper
from tracker.services.catalogue_discovery import _page, _persist_unclassified_observation, run_discovery
from tracker.services.catalogue_identity import normalize_catalogue_url, VERIFIED_HOST_ALIASES


class CatalogueURLIdentityTests(SimpleTestCase):
    def test_removes_only_tracking_noise_and_preserves_meaningful_parts(self):
        self.assertEqual(
            normalize_catalogue_url("hydrahd", "https://EXAMPLE.test:443/movie/a/?season=2&utm_source=x#part"),
            "https://example.test/movie/a/?season=2",
        )
        self.assertNotEqual(
            normalize_catalogue_url("hydrahd", "https://example.test/movie/a?season=1"),
            normalize_catalogue_url("hydrahd", "https://example.test/movie/a?season=2"),
        )

    def test_aliases_are_provider_scoped_and_opt_in(self):
        VERIFIED_HOST_ALIASES["hydrahd"] = {"old.example": "new.example"}
        try:
            self.assertEqual(
                normalize_catalogue_url("hydrahd", "https://old.example/a"),
                normalize_catalogue_url("hydrahd", "https://new.example/a"),
            )
            self.assertNotEqual(
                normalize_catalogue_url("animeworld", "https://old.example/a"),
                normalize_catalogue_url("animeworld", "https://new.example/a"),
            )
        finally:
            VERIFIED_HOST_ALIASES.pop("hydrahd", None)

    def test_hydra_date_pagination_is_partition_specific(self):
        class FakeHydra:
            DOMAINS = ("hydrahd.gd",)
            HEADERS = {}
            def fetch_url(self, url, headers=None):
                self.url = url
                return '<a href="/movies/date/1">1</a><a href="/movies/date/7">7</a>'
            def parse_html(self, html, default_content_type, base_url):
                return [ScrapedItem(title="A", provider_slug="hydrahd", source_url=base_url + "/movie/a",
                                    content_type=default_content_type, raw_payload={})]
        scraper = FakeHydra()
        rows, terminal, pages, evidence = _page(scraper, "hydrahd", "movies", 1)
        self.assertEqual(scraper.url, "https://hydrahd.gd/movies/date/1")
        self.assertIsNone(pages)
        self.assertFalse(terminal)
        self.assertEqual(evidence, "pagination_links_navigation_max=7")
        self.assertEqual(rows[0].raw_payload["catalogue_partition"], "movies")

    def test_animeworld_rss_parses_guid_without_promoting_classification(self):
        rss = '''<rss xmlns:anime="https://www.animeworld.ac/"><channel><lastBuildDate>Tue, 17 Nov 2026 10:00:00 GMT</lastBuildDate>
        <item><guid isPermaLink="false">7214</guid><title>Series</title>
        <anime:name>Original</anime:name><anime:link>https://www.animeworld.ac/play/a</anime:link>
        <anime:type>anime</anime:type></item></channel></rss>'''
        items = AnimeWorldScraper().parse_rss(rss)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].raw_payload["guid"], "7214")
        self.assertEqual(items[0].raw_payload["provenance_state"], "VERIFIED")
        self.assertEqual(items[0].source_url, "https://www.animeworld.ac/play/a")


class PendingCataloguePersistenceTests(TestCase):
    def setUp(self):
        self.provider, _ = Provider.objects.get_or_create(slug="hydrahd", defaults={"name": "HydraHD"})

    def item(self, title, url, **payload):
        return ScrapedItem(title=title, provider_slug="hydrahd", source_url=url,
                           content_type="movie", raw_payload=payload)

    def test_tracking_variants_are_idempotent_and_keep_original_urls(self):
        first = self.item("One", "https://hydrahd.gd/movie/a?utm_campaign=x")
        second = self.item("One", "https://hydrahd.gd/movie/a?fbclid=abc")
        self.assertEqual(_persist_unclassified_observation(provider=self.provider, item=first, partition="movies"), "inserted")
        self.assertEqual(_persist_unclassified_observation(provider=self.provider, item=second, partition="movies"), "source_metadata_updated")
        row = DiscoveredCatalogueRecord.objects.get(provider=self.provider)
        self.assertEqual(row.source_url, first.source_url)
        self.assertEqual(row.source_urls_seen, [first.source_url, second.source_url])
        self.assertEqual(row.classification_state, "PENDING")
        self.assertEqual(row.provenance_state, "SELF_ASSERTED")
        self.assertEqual(row.access_policy_state, "PERMITTED")
        self.assertEqual(_persist_unclassified_observation(provider=self.provider, item=second, partition="movies"), "unchanged")
        self.assertFalse(ScrapedContentMapping.objects.filter(provider=self.provider).exists())

    def test_same_title_different_urls_stay_separate(self):
        for url in ("https://hydrahd.gd/movie/a", "https://hydrahd.gd/movie/b"):
            _persist_unclassified_observation(provider=self.provider, item=self.item("Same title", url), partition="movies")
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.provider).count(), 2)

    def test_explicit_native_guid_deduplicates_across_tracking_urls(self):
        _persist_unclassified_observation(provider=self.provider,
            item=self.item("One", "https://hydrahd.gd/movie/a?utm=x", guid="native-1"), partition="movies")
        _persist_unclassified_observation(provider=self.provider,
            item=self.item("One", "https://hydrahd.gd/movie/b?fbclid=y", guid="native-1"), partition="movies")
        row = DiscoveredCatalogueRecord.objects.get(provider=self.provider)
        self.assertEqual(row.provider_native_id, "native-1")
        self.assertEqual(row.source_urls_seen, ["https://hydrahd.gd/movie/a?utm=x", "https://hydrahd.gd/movie/b?fbclid=y"])

    def test_overlapping_page_observation_is_retained_without_a_second_record(self):
        first = self.item("Overlap", "https://hydrahd.gd/movie/a", guid="same")
        first.raw_payload["listing_page"] = 10
        second = self.item("Overlap", "https://hydrahd.gd/movie/a", guid="same")
        second.raw_payload["listing_page"] = 11
        _persist_unclassified_observation(provider=self.provider, item=first, partition="movies")
        outcome = _persist_unclassified_observation(provider=self.provider, item=second, partition="movies")
        self.assertEqual(outcome, "source_metadata_updated")
        row = DiscoveredCatalogueRecord.objects.get(provider=self.provider)
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=self.provider).count(), 1)
        self.assertEqual([o["page"] for o in row.original_metadata["listing_observations"]], [10, 11])

    def test_unverified_candidate_provenance_blocks_before_any_request(self):
        candidate = Provider.objects.create(slug="cineblog01", name="CineBlog01")
        with patch("tracker.services.catalogue_discovery.try_source_lock", return_value=True), \
             patch("tracker.services.catalogue_discovery.release_source_lock"), \
             patch("tracker.services.catalogue_discovery.resolve_scraper_class") as resolver:
            result = run_discovery("cineblog01", request_budget=3)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["requests_made"], 0)
        resolver.assert_not_called()
        self.assertFalse(DiscoveredCatalogueRecord.objects.filter(provider=candidate).exists())

    def test_animeworld_rss_snapshot_adds_new_guid_and_replays_existing_guid(self):
        from tracker.scrapers.adapters import AnimeWorldScraper
        provider, _ = Provider.objects.get_or_create(slug="animeworld", defaults={"name": "AnimeWorld"})
        parser = AnimeWorldScraper()
        feed_a = '''<rss xmlns:anime="https://www.animeworld.ac/"><channel>
        <item><guid>g1</guid><title>One</title><anime:name>One</anime:name><anime:link>https://www.animeworld.ac/play/one</anime:link><anime:type>anime</anime:type></item>
        </channel></rss>'''
        initial = parser.parse_rss(feed_a)[0]
        self.assertEqual(_persist_unclassified_observation(provider=provider, item=initial, partition="rss-animes"), "inserted")
        feed_b = '''<rss xmlns:anime="https://www.animeworld.ac/"><channel>
        <item><guid>g1</guid><title>One Updated</title><anime:name>One</anime:name><anime:link>https://www.animeworld.ac/play/one</anime:link><anime:type>anime</anime:type></item>
        <item><guid>g2</guid><title>Two</title><anime:name>Two</anime:name><anime:link>https://www.animeworld.ac/play/two</anime:link><anime:type>anime</anime:type></item>
        </channel></rss>'''
        current = parser.parse_rss(feed_b)
        outcomes = [_persist_unclassified_observation(provider=provider, item=item, partition="rss-animes") for item in current]
        replay = [_persist_unclassified_observation(provider=provider, item=item, partition="rss-animes") for item in current]
        self.assertEqual(outcomes, ["updated", "inserted"])
        self.assertEqual(replay, ["unchanged", "unchanged"])
        self.assertEqual(DiscoveredCatalogueRecord.objects.filter(provider=provider).count(), 2)
