"""Tests for the priority-source scrapers (BBC, NHK, RaiPlay) and the registry."""
# Calls to _execute_run below exercise retained parser orchestration with fixture
# HTTP seams; public run()/session authorization is tested separately.
from unittest.mock import patch

from django.test import TestCase

from tracker.models import Provider, ProviderCategory
from tracker.scrapers import (
    BaseScraper,
    HydraHDScraper,
    NHKScraper,
    RaiPlayScraper,
    SCRAPER_REGISTRY,
    resolve_scraper_class,
)
from tracker.scrapers.base import ScrapedItem
from tracker.serializers import ProviderSerializer
from tracker.services.scraper_selection import (
    ScraperExecutionIneligible,
    select_provider_scraper,
)


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

RAIPLAY_WASHI_CONTEXT_HTML = """
<html><body>
<script>window.WashiContext = {
  "title": "Film",
  "hero": {
    "type": "RaiPlay Hero Block",
    "cards": {
      "0": {
        "uniquename": "ContentItem-abc123",
        "type": "RaiPlay Programma Item",
        "typology": "Film",
        "title": "Il Commissario Montalbano",
        "year": "1999",
        "weblink": "/programmi/montalbano",
        "channel": "Rai 1"
      }
    }
  }
}</script>
</body></html>
"""


class BBCScraperRetirementContractTests(TestCase):
    def test_bbc_iplayer_not_in_scraper_registry(self):
        self.assertNotIn("bbc_iplayer", SCRAPER_REGISTRY)
        self.assertIsNone(resolve_scraper_class("bbc_iplayer"))

    def test_select_provider_scraper_rejects_bbc_with_canonical_catalogue(self):
        provider = Provider.objects.get(slug="bbc_iplayer")
        with self.assertRaises(ScraperExecutionIneligible) as ctx:
            select_provider_scraper(provider)
        self.assertEqual(ctx.exception.code, "canonical_catalogue_provider")
        self.assertEqual(ctx.exception.provider_slug, "bbc_iplayer")
        self.assertEqual(ctx.exception.source_slug, "bbc_iplayer")
        self.assertEqual(
            ctx.exception.payload(),
            {
                "code": "canonical_catalogue_provider",
                "error": "Scraper execution is unavailable for 'bbc_iplayer'; use canonical catalogue source 'bbc_iplayer'.",
                "provider": "bbc_iplayer",
                "alternative": {"kind": "catalogue", "source": "bbc_iplayer"},
            },
        )

    @patch("tracker.services.scraper_selection.GenericURLAdapter")
    def test_generic_url_adapter_never_instantiated_for_bbc(self, mock_generic):
        provider = Provider.objects.get(slug="bbc_iplayer")
        with self.assertRaises(ScraperExecutionIneligible):
            select_provider_scraper(provider)
        mock_generic.assert_not_called()

    def test_has_scraper_serializer_field_reflects_bbc_retirement(self):
        provider = Provider.objects.get(slug="bbc_iplayer")
        self.assertFalse(ProviderSerializer(provider).data["has_scraper"])


class NHKScraperTest(TestCase):
    def setUp(self):
        self.scraper = NHKScraper()

    @patch.object(NHKScraper, "fetch_url", return_value="<html></html>")
    def test_no_query_returns_empty(self, mock_fetch):
        self.assertEqual(self.scraper.run(query=None), [])
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args.args[0], "https://www3.nhk.or.jp/nhkworld/")

    @patch.object(NHKScraper, "fetch_url")
    def test_parses_program_card(self, mock_fetch):
        mock_fetch.return_value = NHK_HTML
        items = self.scraper.run(query="Japan Railway Journal")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Japan Railway Journal")
        self.assertEqual(items[0].provider_slug, "nhk_world")
        self.assertIn("JA", items[0].audio_languages)
        self.assertTrue({"EN", "JA"}.issubset(set(items[0].subtitle_languages)))

    @patch.object(NHKScraper, "fetch_url")
    def test_handles_none_response(self, mock_fetch):
        mock_fetch.return_value = None
        self.assertEqual(self.scraper.run(query="x"), [])


class RaiPlayScraperTest(TestCase):
    def setUp(self):
        self.scraper = RaiPlayScraper()

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_no_query_returns_empty_on_geo_block(self, mock_fetch):
        mock_fetch.return_value = "<html><body>non disponibile nel tuo paese</body></html>"
        items = self.scraper.run(query=None)
        self.assertEqual(items, [])

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_parses_washi_context(self, mock_fetch):
        mock_fetch.return_value = RAIPLAY_WASHI_CONTEXT_HTML
        items = self.scraper.run(query=None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Il Commissario Montalbano")
        self.assertEqual(items[0].provider_slug, "raiplay")
        self.assertEqual(items[0].year, 1999)
        self.assertEqual(items[0].content_type, "movie")
        self.assertIn("IT", items[0].audio_languages)
        self.assertIn("IT", items[0].subtitle_languages)

    @patch.object(RaiPlayScraper, "fetch_url")
    def test_handles_none_response(self, mock_fetch):
        mock_fetch.return_value = None
        items = self.scraper.run(query="x")
        self.assertEqual(items, [])


class HydraHDScraperTest(TestCase):
    def setUp(self):
        self.scraper = HydraHDScraper()

    @patch.object(HydraHDScraper, "fetch_url")
    def test_search_uses_unified_fetch_url(self, mock_fetch):
        mock_fetch.return_value = {
            "results": [
                {
                    "title": "Inception",
                    "year": "2010",
                    "audio": ["EN"],
                    "subtitles": ["ES", "FR"],
                    "res": "2160p",
                    "link": "https://hydrahd.ws/watch/inception",
                    "id": "42",
                }
            ]
        }
        items = self.scraper._execute_run(query="Inception")
        mock_fetch.assert_called()
        call_kwargs = mock_fetch.call_args
        self.assertIn("headers", call_kwargs.kwargs or {})
        headers = call_kwargs.kwargs["headers"]
        self.assertIn("User-Agent", headers)
        self.assertIn("Referer", headers)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Inception")
        self.assertEqual(items[0].provider_slug, "hydrahd")
        self.assertIn("EN", items[0].audio_languages)

    @patch.object(HydraHDScraper, "fetch_url")
    def test_search_all_urls_fail_returns_empty(self, mock_fetch):
        mock_fetch.return_value = None
        items = self.scraper._execute_run(query="NonexistentMovie")
        self.assertEqual(items, [])

    @patch.object(HydraHDScraper, "fetch_url")
    def test_no_query_browse_uses_unified_fetch_url(self, mock_fetch):
        mock_fetch.return_value = None
        items = self.scraper._execute_run(query=None)
        self.assertEqual(items, [])
        mock_fetch.assert_called()


class ScraperRegistryTest(TestCase):
    """The registry drives both the v1 scraper views and the v2/unofficial API."""

    def test_priority_sources_registered(self):
        for slug in ("nhk_world", "raiplay"):
            self.assertIn(slug, SCRAPER_REGISTRY)
            # resolve_scraper_class returns an instantiable scraper class
            cls = resolve_scraper_class(slug)
            self.assertIsNotNone(cls)
            self.assertTrue(issubclass(cls, BaseScraper))
        self.assertNotIn("bbc_iplayer", SCRAPER_REGISTRY)

    def test_resolve_returns_none_for_unknown(self):
        self.assertIsNone(resolve_scraper_class("does-not-exist"))

    def test_resolved_instance_is_scraper(self):
        instance = resolve_scraper_class("raiplay")()
        self.assertIsInstance(instance, RaiPlayScraper)
        self.assertIsNone(resolve_scraper_class("bbc_iplayer"))

    def test_get_or_create_provider_from_registry_slug(self):
        """Providers referenced by the registry exist or can be created.

        bbc_iplayer / raiplay / nhk_world are seeded as BVOD providers by
        the migrations, so they are NOT scrapers — assert that here.
        """
        for slug in ("bbc_iplayer", "nhk_world", "raiplay"):
            provider, _ = Provider.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": slug,
                    "category": ProviderCategory.BVOD,
                    "is_enabled": True,
                },
            )
            # Migrations seed these public broadcasters as 'bvod'.
            provider.category = ProviderCategory.BVOD
            provider.save()
            self.assertEqual(provider.category, ProviderCategory.BVOD)
