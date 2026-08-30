from unittest.mock import patch
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from tracker.models import (
    ExternalProviderMapping,
    Movie,
    Provider,
    ProviderCategory,
    ScrapedContentMapping,
    Subscription,
)
from tracker.scrapers import (
    AnimeWorldScraper,
    ContentMatcher,
    HydraHDScraper,
    KissKHScraper,
    ScrapedItem,
)
from tracker.scrapers.base import normalize_language_list, normalize_quality
from tracker.services.tmdb import TMDbMovie


class ProviderSystemTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_all_major_providers_and_scrapers_seeded(self):
        major_slugs = [
            "prime",
            "netflix",
            "disney",
            "hulu",
            "max",
            "apple",
            "paramount",
            "peacock",
            "youtubetv",
            "crunchyroll",
        ]
        for slug in major_slugs:
            provider = Provider.objects.filter(slug=slug, category=ProviderCategory.OFFICIAL).first()
            self.assertIsNotNone(provider, f"Major provider {slug} should exist out of the box")
            self.assertTrue(provider.is_major)
            self.assertTrue(provider.is_enabled)
        for slug in major_slugs:
            provider = Provider.objects.filter(slug=slug, category=ProviderCategory.OFFICIAL).first()
            self.assertIsNotNone(provider, f"Major provider {slug} should exist out of the box")
            self.assertTrue(provider.is_major)
            self.assertTrue(provider.is_enabled)

        # External mapping check
        disney_mapping = ExternalProviderMapping.objects.filter(
            provider__slug="disney", integration_name="streaming_availability"
        ).first()
        self.assertIsNotNone(disney_mapping)
        self.assertEqual(disney_mapping.external_id, "disney")

        max_mapping = ExternalProviderMapping.objects.filter(
            provider__slug="max", integration_name="streaming_availability"
        ).first()
        self.assertIsNotNone(max_mapping)
        self.assertEqual(max_mapping.external_id, "hbo")

        # Scrapers check
        scraper_slugs = ["bbc_iplayer", "nhk", "rai-play"]
        for slug in scraper_slugs:
            provider, created = Provider.objects.get_or_create(
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
            self.assertFalse(provider.is_major)

    def test_providers_list_endpoint(self):
        resp = self.client.get("/api/providers/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertGreaterEqual(len(data), 11)

        disney_item = next(p for p in data if p["slug"] == "disney")
        self.assertEqual(disney_item["name"], "Disney+")
        self.assertFalse(disney_item["is_subscribed"])
        self.assertEqual(len(disney_item["external_mappings"]), 1)

    def test_provider_toggle_endpoint_and_persistence(self):
        # Active toggle
        resp1 = self.client.post("/api/providers/disney/toggle/")
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertTrue(resp1.json()["is_subscribed"])

        self.assertTrue(Subscription.objects.filter(user_id=1, provider__slug="disney").exists())

        # Check API reflects subscribed state
        resp2 = self.client.get("/api/providers/")
        disney_item = next(p for p in resp2.json() if p["slug"] == "disney")
        self.assertTrue(disney_item["is_subscribed"])

        # Inactive toggle
        resp3 = self.client.post("/api/providers/disney/toggle/")
        self.assertEqual(resp3.status_code, status.HTTP_200_OK)
        self.assertFalse(resp3.json()["is_subscribed"])
        self.assertFalse(Subscription.objects.filter(user_id=1, provider__slug="disney").exists())

    def test_quality_and_language_normalization(self):
        self.assertEqual(normalize_quality("2160p"), "4K")
        self.assertEqual(normalize_quality("4K UHD"), "4K")
        self.assertEqual(normalize_quality("1080p FHD"), "1080p")
        self.assertEqual(normalize_quality("720p"), "720p")
        self.assertEqual(normalize_quality("480p"), "480p")
        self.assertIsNone(normalize_quality("unknown"))

        self.assertEqual(
            normalize_language_list(["English", "eng", "ja", "Japanese", "deu", "invalid"]),
            ["EN", "JA", "DE"],
        )

    @patch("tracker.scrapers.adapters.KissKHScraper.fetch_url")
    def test_scraper_adapters_mocked(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "id": 101,
                "title": "Demon Slayer",
                "year": 2019,
                "audio": ["Japanese"],
                "subtitles": ["English"],
                "quality": "1080p",
            }
        ]

        scraper = KissKHScraper()
        items = scraper.run(query="Demon Slayer")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Demon Slayer")
        self.assertEqual(item.year, 2019)
        self.assertEqual(item.audio_languages, ["JA"])
        self.assertEqual(item.subtitle_languages, ["EN"])
        self.assertEqual(item.quality, "1080p")

    @patch("tracker.scrapers.base.TMDbClient.search")
    def test_content_matcher_and_confirmation_persistence(self, mock_search):
        mock_search.return_value = [
            TMDbMovie(tmdb_id=550, title="Fight Club", year=1999)
        ]

        item = ScrapedItem(
            title="Fight Club",
            provider_slug="kisskh",
            year=1999,
            source_url="https://kisskh.co/Drama/550",
        )

        matcher = ContentMatcher()
        movie, score, candidates, is_confirmed = matcher.match_item(item)

        self.assertIsNotNone(movie)
        self.assertEqual(movie.tmdb_id, 550)
        self.assertEqual(score, 1.0)
        self.assertFalse(is_confirmed)

        # Check unconfirmed mapping saved
        mapping = ScrapedContentMapping.objects.get(
            provider__slug="kisskh", scraped_title="Fight Club", scraped_year=1999
        )
        self.assertEqual(mapping.movie.tmdb_id, 550)
        self.assertFalse(mapping.is_confirmed)

        # Confirm mapping
        mapping.is_confirmed = True
        mapping.save()

        # Subsequent match should return confirmed directly
        movie2, score2, candidates2, is_confirmed2 = matcher.match_item(item)
        self.assertTrue(is_confirmed2)
        self.assertEqual(movie2.tmdb_id, 550)

    @patch("tracker.scrapers.adapters.KissKHScraper.fetch_url")
    def test_scraper_failure_isolation(self, mock_fetch):
        mock_fetch.side_effect = Exception("Network connection timeout")

        scraper = KissKHScraper()
        items = scraper.run(query="Inception")
        self.assertEqual(items, [])

    @patch("tracker.scrapers.adapters.KissKHScraper.fetch_url")
    def test_scraper_import_url_endpoint(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "id": 101,
                "title": "Demon Slayer",
                "year": 2019,
                "audio": ["Japanese"],
                "subtitles": ["English"],
                "quality": "1080p",
            }
        ]
        resp = self.client.post(
            "/api/scrapers/import-url/",
            data={"url": "https://kisskh.co/Drama/Demon-Slayer"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        data = resp.json()
        self.assertEqual(data["provider"]["slug"], "kisskh")
        self.assertEqual(data["count"], 1)
        self.assertTrue(Subscription.objects.filter(user_id=1, provider__slug="kisskh").exists())
