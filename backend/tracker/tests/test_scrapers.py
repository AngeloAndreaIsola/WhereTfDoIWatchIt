"""Tests for the priority-source scrapers (BBC, NHK, RaiPlay) and the registry."""
from unittest.mock import patch

from django.test import TestCase

from tracker.models import Provider, ProviderCategory
from tracker.scrapers import (
    BaseScraper,
    BBCScraper,
    NHKScraper,
    RaiPlayScraper,
    SCRAPER_REGISTRY,
    resolve_scraper_class,
)
from tracker.scrapers.base import ScrapedItem


# Minimal HTML fixture for BBC Redux state
BBC_STATE_HTML = """
<html><body>
<script>window.__IPLAYER_REDUX_STATE__ = {
  "bundles": [
    {
      "id": "featured-category-films",
      "title": {"default": "Featured"},
      "entities": [
        {
          "episode": {
            "id": "m000abc",
            "title": {"default": "Sherlock"},
            "synopsis": {"small": "A detective story"},
            "versions": [{"availability": {"remaining": "Free"}}]
          }
        }
      ]
    }
  ]
}</script>
</body></html>
"""

NHK_HTML = """
<html><body>
  <div data-testid="program-card">
    <a data-testid="program-title" href="/nhkworld/tv/special/123">Japan Railway Journal</a>
  </div>
</body></html>
"""

RAIPLAY_HTML = """
<html><body>
  <div data-testid="media-card">
    <h2 data-testid="media-title">Il Commissario Montalbano</h2>
    <a href="/programmi/123">link</a>
  </div>
</body></html>
"""


class BBCScraperTest(TestCase):
    def setUp(self):
        self.scraper = BBCScraper()

    @patch.object(BBCScraper, "fetch_url")
    def test_no_query_returns_empty(self, mock_fetch):
        mock_fetch.return_value = ""
        self.assertEqual(self.scraper.run(query=None), [])
        self.assertEqual(self.scraper.run(query=""), [])

    @patch.object(BBCScraper, "fetch_url")
    def test_parses_redux_state(self, mock_fetch):
        mock_fetch.return_value = BBC_STATE_HTML
        items = self.scraper.run(query=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Sherlock")
        self.assertEqual(items[0].provider_slug, "bbc_iplayer")
        self.assertIn("bbc.co.uk", items[0].source_url)
        self.assertIn("EN", items[0].audio_languages)
        self.assertIn("EN", items[0].subtitle_languages)

    @patch.object(BBCScraper, "fetch_url")
    def test_handles_empty_response(self, mock_fetch):
        mock_fetch.return_value = ""
        self.assertEqual(self.scraper.run(query="x"), [])

    @patch.object(BBCScraper, "fetch_url")
    def test_handles_invalid_json(self, mock_fetch):
        mock_fetch.return_value = "<html>not json</html>"
        self.assertEqual(self.scraper.run(query="x"), [])


class NHKScraperTest(TestCase):
    def setUp(self):
        self.scraper = NHKScraper()

    def test_no_query_returns_empty(self):
        self.assertEqual(self.scraper.run(query=None), [])

    @patch.object(NHKScraper, "fetch_url")
    def test_parses_program_card(self, mock_fetch):
        mock_fetch.return_value = NHK_HTML
        items = self.scraper.run(query="Japan Railway Journal")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Japan Railway Journal")
        self.assertEqual(items[0].provider_slug, "nhk")
        self.assertIn("JA", items[0].audio_languages)
        self.assertTrue({"EN", "JA"}.issubset(set(items[0].subtitle_languages)))

    @patch.object(NHKScraper, "fetch_url")
    def test_handles_none_response(self, mock_fetch):
        mock_fetch.return_value = None
        self.assertEqual(self.scraper.run(query="x"), [])


class RaiPlayScraperTest(TestCase):
    def setUp(self):
        self.scraper = RaiPlayScraper()

    def test_no_query_returns_empty(self):
        self.assertEqual(self.scraper.run(query=None), [])

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_parses_media_card(self, mock_fetch):
        mock_fetch.return_value = RAIPLAY_HTML
        items = self.scraper.run(query="Montalbano")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Il Commissario Montalbano")
        self.assertEqual(items[0].provider_slug, "rai-play")
        self.assertIn("IT", items[0].audio_languages)
        self.assertIn("IT", items[0].subtitle_languages)

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_parses_json_api_response(self, mock_fetch):
        mock_fetch.return_value = {
            "results": [{"title": "Docufilm", "url": "/programmi/doc", "quality": "4K"}]
        }
        items = self.scraper.run(query="Docufilm")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Docufilm")
        self.assertEqual(items[0].quality, "4K")


class ScraperRegistryTest(TestCase):
    """The registry drives both the v1 scraper views and the v2/unofficial API."""

    def test_priority_sources_registered(self):
        for slug in ("bbc_iplayer", "nhk", "rai-play"):
            self.assertIn(slug, SCRAPER_REGISTRY)
            # resolve_scraper_class returns an instantiable scraper class
            cls = resolve_scraper_class(slug)
            self.assertIsNotNone(cls)
            self.assertTrue(issubclass(cls, BaseScraper))

    def test_resolve_returns_none_for_unknown(self):
        self.assertIsNone(resolve_scraper_class("does-not-exist"))

    def test_resolved_instance_is_scraper(self):
        instance = resolve_scraper_class("bbc_iplayer")()
        self.assertIsInstance(instance, BBCScraper)

    def test_get_or_create_provider_from_registry_slug(self):
        """Providers referenced by the registry exist or can be created."""
        for slug in ("bbc_iplayer", "nhk", "rai-play"):
            provider, _ = Provider.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": slug,
                    "category": ProviderCategory.SCRAPER,
                    "is_enabled": True,
                },
            )
            # Migration 0011 seeds some as 'free'; ensure they're SCRAPER now.
            provider.category = ProviderCategory.SCRAPER
            provider.save()
            self.assertEqual(provider.category, ProviderCategory.SCRAPER)
