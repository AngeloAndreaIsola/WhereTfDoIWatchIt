"""Tests for Phase C v2/unofficial API endpoints (catalog, providers, offers)."""
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.test import APIClient

from tracker.models import (
    Movie,
    Provider,
    ProviderCategory,
    ScrapedContentMapping,
)


def _make_provider(slug, category=ProviderCategory.SCRAPER, country="IT"):
    provider, _ = Provider.objects.get_or_create(
        slug=slug,
        defaults={
            "name": slug,
            "category": category,
            "is_enabled": True,
            "country_code": country,
            "domain": f"{slug}.example.com",
        },
    )
    return provider


class UnofficialCatalogEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.provider = _make_provider("rai-play", ProviderCategory.SCRAPER, "IT")
        self.movie = Movie.objects.create(tmdb_id=123, title="Test Movie", year=2021)
        ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Test Movie",
            scraped_year=2021,
            scraped_url="https://raiplay.it/test",
            movie=self.movie,
            is_confirmed=True,
            confidence_score=0.95,
            is_available=True,
            raw_payload={"audio_languages": ["IT"], "subtitle_languages": ["IT"], "country": "IT"},
        )

    def test_catalog_returns_scraped_items(self):
        resp = self.client.get("/api/v2/unofficial/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        item = data["results"][0]
        self.assertEqual(item["scraped_title"], "Test Movie")
        self.assertEqual(item["source_indicator"], "Scraper")
        self.assertEqual(item["provider_category"], "scraper")

    def test_catalog_filters_by_provider_slug(self):
        resp = self.client.get("/api/v2/unofficial/?provider=rai-play")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)

        resp = self.client.get("/api/v2/unofficial/?provider=bbc")
        self.assertEqual(resp.json()["count"], 0)

    def test_catalog_filters_by_country(self):
        resp = self.client.get("/api/v2/unofficial/?country=IT")
        self.assertEqual(resp.json()["count"], 1)
        resp = self.client.get("/api/v2/unofficial/?country=GB")
        self.assertEqual(resp.json()["count"], 0)

    def test_catalog_filters_by_audio_language(self):
        resp = self.client.get("/api/v2/unofficial/?audio_lang=IT")
        self.assertEqual(resp.json()["count"], 1)
        resp = self.client.get("/api/v2/unofficial/?audio_lang=JP")
        self.assertEqual(resp.json()["count"], 0)

    def test_catalog_title_query(self):
        resp = self.client.get("/api/v2/unofficial/?q=Test")
        self.assertEqual(resp.json()["count"], 1)
        resp = self.client.get("/api/v2/unofficial/?q=Nope")
        self.assertEqual(resp.json()["count"], 0)


class UnofficialProvidersEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _make_provider("rai-play", ProviderCategory.SCRAPER, "IT")
        _make_provider("bbc", ProviderCategory.SCRAPER, "GB")

    def test_providers_lists_scrapers(self):
        resp = self.client.get("/api/v2/unofficial/providers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        slugs = {p["slug"] for p in data["results"]}
        self.assertIn("rai-play", slugs)
        self.assertIn("bbc", slugs)
        for p in data["results"]:
            self.assertEqual(p["category"], "scraper")


class UnofficialMovieOffersEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.provider = _make_provider("rai-play", ProviderCategory.SCRAPER, "IT")
        self.movie = Movie.objects.create(tmdb_id=999, title="Rank Me", year=2020)
        ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Rank Me",
            scraped_year=2020,
            scraped_url="https://raiplay.it/rankme",
            movie=self.movie,
            is_confirmed=True,
            is_available=True,
            raw_payload={
                "audio_languages": ["JA"],
                "subtitle_languages": ["EN"],
                "country": "IT",
            },
        )

    def test_offers_returns_ranked_unofficial_offers(self):
        resp = self.client.get("/api/v2/unofficial/movie/999/offers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["movie"]["tmdb_id"], 999)
        self.assertEqual(len(data["offers"]), 1)
        offer = data["offers"][0]
        self.assertEqual(offer["service"], "rai-play")
        self.assertEqual(offer["offer_type"], "free")
        self.assertEqual(offer["source_indicator"], "Scraper")

    def test_offers_excludes_bvod_category_providers_by_default(self):
        bvod_provider, _ = Provider.objects.get_or_create(
            slug="bbc_iplayer",
            defaults={
                "name": "BBC iPlayer",
                "category": ProviderCategory.BVOD,
                "is_enabled": True,
                "country_code": "GB",
            },
)
        bvod_provider.category = ProviderCategory.BVOD
        bvod_provider.save()
        ScrapedContentMapping.objects.create(
            provider=bvod_provider,
            scraped_title="Rank Me",
            scraped_year=2020,
            scraped_url="https://bbc.co.uk/iplayer/rankme",
            movie=self.movie,
            is_confirmed=True,
            is_available=True,
        )
        resp = self.client.get("/api/v2/unofficial/movie/999/offers/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        services = [o["service"] for o in data["offers"]]
        self.assertNotIn("bbc_iplayer", services)
        self.assertIn("rai-play", services)

    def test_offers_404_for_untracked_movie(self):
        resp = self.client.get("/api/v2/unofficial/movie/1/offers/")
        self.assertEqual(resp.status_code, 404)


class UnofficialScrapeEndpointTests(TestCase):
    """The scrape endpoint triggers a scraper; mock the network + matcher."""

    def setUp(self):
        self.client = APIClient()
        self.provider = _make_provider("rai-play", ProviderCategory.SCRAPER, "IT")

    @patch("tracker.api_v2.ContentMatcher")
    @patch("tracker.api_v2.resolve_scraper_class")
    def test_scrape_returns_matched_results(self, mock_resolve, mock_matcher_cls):
        from tracker.scrapers.base import ScrapedItem

        fake_scraper = MagicMock()
        fake_scraper.run.return_value = [
            ScrapedItem(
                title="Scraped Title",
                provider_slug="rai-play",
                year=2022,
                source_url="https://raiplay.it/x",
                audio_languages=["IT"],
                subtitle_languages=["IT"],
            )
        ]
        mock_resolve.return_value = lambda: fake_scraper

        fake_matcher = MagicMock()
        movie = Movie.objects.create(tmdb_id=555, title="Scraped Title", year=2022)
        fake_matcher.match_item.return_value = (movie, 0.9, [], False)
        mock_matcher_cls.return_value = fake_matcher

        resp = self.client.post(
            "/api/v2/unofficial/scrape/",
            data={"provider_slug": "rai-play", "query": "Scraped Title"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["scraped_title"], "Scraped Title")
        self.assertEqual(data["results"][0]["matched_movie"]["tmdb_id"], 555)

    def test_scrape_requires_provider_slug(self):
        resp = self.client.post("/api/v2/unofficial/scrape/", data={}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_scrape_404_unknown_provider(self):
        resp = self.client.post(
            "/api/v2/unofficial/scrape/",
            data={"provider_slug": "nope"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
