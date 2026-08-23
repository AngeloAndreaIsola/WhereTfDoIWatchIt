"""Tests for UniversalScraper and generic crawling engine."""
from unittest.mock import Mock, patch
from django.test import TestCase

from tracker.models import Provider
from tracker.scrapers import UniversalScraper, ScrapedItem, validate_provider_config
from tracker.scrapers.configs import ProviderConfig, SelectorConfig, CrawlLimits, merge_provider_config


class UniversalScraperTest(TestCase):
    def setUp(self):
        self.scraper = UniversalScraper(provider_slug="kisskh")

    def test_no_query_returns_empty(self):
        result = self.scraper.run(query=None)
        self.assertEqual(result, [])
        result = self.scraper.run(query="")
        self.assertEqual(result, [])

    @patch.object(UniversalScraper, '_fetch_url_with_config')
    def test_parse_json_list_discovery(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "id": 101,
                "title": "Anime Show S1",
                "year": 2021,
                "season": 1,
                "episode": 1,
                "audio": "Japanese",
                "subtitles": "Sub ITA",
                "res": "1080p",
                "hdr": "HDR10",
                "url": "https://kisskh.co/Drama/101",
            },
            {
                "id": 102,
                "title": "Anime Show S1",
                "year": 2021,
                "season": 1,
                "episode": 2,
                "audio": "ja",
                "subtitles": "it",
                "res": "1080p",
                "hdr": "none",
                "url": "https://kisskh.co/Drama/102",
            },
            {
                "id": 103,
                "title": "Anime Movie",
                "year": 2022,
                "audio": "ja",
                "subtitles": "en",
                "res": "4k",
                "url": "https://kisskh.co/Drama/103",
            },
        ]

        items = self.scraper.run(query="Anime")

        # Must discover multiple items (3 > 1 or 2)
        self.assertEqual(len(items), 3)

        # First item checks
        self.assertEqual(items[0].title, "Anime Show S1")
        self.assertEqual(items[0].year, 2021)
        self.assertEqual(items[0].season, 1)
        self.assertEqual(items[0].episode, 1)
        self.assertIn("JA", items[0].audio_languages)
        self.assertIn("IT", items[0].subtitle_languages)
        self.assertEqual(items[0].quality, "1080p")
        self.assertEqual(items[0].hdr, "HDR10")

        # Third item checks
        self.assertEqual(items[2].title, "Anime Movie")
        self.assertEqual(items[2].quality, "4K")
        self.assertIn("EN", items[2].subtitle_languages)

    @patch.object(UniversalScraper, '_fetch_url_with_config')
    def test_parse_html_catalog(self, mock_fetch):
        html_data = """
        <html>
            <body>
                <div class="item">
                    <a class="name" href="play/naruto-ep1">Naruto</a>
                    <span class="year">Anno: 2002</span>
                    <span class="audio">Audio: Japanese</span>
                    <span class="sub">Subtitles: Italian</span>
                    <span class="res">1080p</span>
                </div>
                <div class="item">
                    <a class="name" href="play/naruto-shippuden">Naruto Shippuden</a>
                    <span class="year">Anno: 2007</span>
                    <span class="audio">JA</span>
                    <span class="sub">IT, EN</span>
                    <span class="res">4K</span>
                </div>
            </body>
        </html>
        """
        mock_fetch.return_value = {"html": html_data}

        scraper = UniversalScraper(provider_slug="animeworld")
        items = scraper.run(query="Naruto")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Naruto")
        self.assertEqual(items[0].year, 2002)
        self.assertIn("JA", items[0].audio_languages)
        self.assertIn("IT", items[0].subtitle_languages)

        self.assertEqual(items[1].title, "Naruto Shippuden")
        self.assertEqual(items[1].year, 2007)
        self.assertEqual(items[1].quality, "4K")
        self.assertIn("EN", items[1].subtitle_languages)

    def test_deduplication(self):
        items = [
            ScrapedItem(title="Movie A", provider_slug="test", year=2020, source_url="https://site.com/1"),
            ScrapedItem(title="Movie A", provider_slug="test", year=2020, source_url="https://site.com/1"),
            ScrapedItem(title="Movie A", provider_slug="test", year=2020, source_url="https://site.com/2"),
        ]
        deduped = UniversalScraper.deduplicate_items(items)
        self.assertEqual(len(deduped), 2)

    def test_provider_config_merge_and_validation(self):
        base_cfg = ProviderConfig(provider_slug="test", domain="test.com", search_url_template="https://test.com/s?q={query}")
        db_override = {
            "limits": {"max_pages": 10},
            "selectors": {"container": ".my-card"},
        }

        merged = merge_provider_config(base_cfg, db_override)
        self.assertEqual(merged.limits.max_pages, 10)
        self.assertEqual(merged.selectors.container, ".my-card")

        is_valid, err = validate_provider_config(db_override)
        self.assertTrue(is_valid)
        self.assertIsNone(err)

        is_valid_bad, err_bad = validate_provider_config({"invalid_field": True})
        self.assertFalse(is_valid_bad)
        self.assertIn("Unknown top-level config keys", err_bad)

    def test_provider_model_get_merged_config(self):
        provider = Provider.objects.create(
            slug="custom_provider",
            name="Custom Provider",
            domain="custom.co",
            config={"limits": {"max_pages": 7}}
        )
        cfg = provider.get_merged_config()
        self.assertEqual(cfg.limits.max_pages, 7)
        self.assertEqual(cfg.domain, "custom.co")
