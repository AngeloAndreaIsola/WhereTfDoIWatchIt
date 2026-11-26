"""Tests for Phase C v2/unofficial API endpoints (catalog, providers, offers).

Pins the legacy ``ScrapedContentMapping`` consumer contract that survived the
read-path migration: exact response fields, nested provider/movie payloads,
the ``filters`` echo, raw_payload reconstruction, the ``available`` tri-state,
``include_bvod``, language filtering, and the per-movie offers shape.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from tracker.models import (
    Movie,
    Preference,
    Provider,
    ProviderCategory,
    ScrapedContentMapping,
)
from tracker.serializers import MovieSerializer, ProviderSerializer
from availability.services.legacy_ingest import ingest_scraped_mapping


def _make_provider(slug, category=ProviderCategory.SCRAPER, country="IT"):
    """Create a Provider for testing. If the provider already exists (e.g. from a
    seed migration), the category is force-set so the test gets the category it asked for.
    See references/test-provider-seed-conflict.md for the rationale."""
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
    # Ensure category is correct even if provider already exists from a seed migration
    if provider.category != category:
        provider.category = category
        provider.save()
    return provider


def _ingest(provider, movie, *, title="Scraper Row", year=1999,
            is_available=True, is_confirmed=False, confidence=0.0,
            country_code="IT", payload=None):
    """Write one legacy row and project it through the ingestion bridge —
    the same path production scraper writes take."""
    row = ScrapedContentMapping.objects.create(
        provider=provider,
        movie=movie,
        scraped_title=title,
        scraped_year=year,
        scraped_url=f"https://{provider.slug}.example/watch",
        country_code=country_code,
        is_available=is_available,
        is_confirmed=is_confirmed,
        confidence_score=confidence,
        raw_payload=payload if payload is not None else {
            "audio_languages": ["EN"],
            "subtitle_languages": ["IT", "EN"],
        },
    )
    ingest_scraped_mapping(row)
    return row


class UnofficialCatalogContractTests(TestCase):
    """GET /api/v2/unofficial/ keeps the legacy catalog contract."""

    def setUp(self):
        self.client = APIClient()
        self.movie = Movie.objects.create(
            tmdb_id=603, title="The Matrix", year=1999
        )
        self.provider = _make_provider("matrixhd")

    def _results(self, query=""):
        response = self.client.get(f"/api/v2/unofficial/{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_response_matches_the_legacy_contract(self):
        _ingest(self.provider, self.movie, title="The Matrix",
                is_confirmed=True, confidence=0.87)

        data = self._results()

        self.assertEqual(
            set(data.keys()), {"count", "filters", "results"}
        )
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["filters"], {
            "provider": None,
            "country": None,
            "audio_lang": None,
            "subtitle_lang": None,
            "available_only": True,
            "include_bvod": False,
        })

        [item] = data["results"]
        self.assertEqual(
            set(item.keys()),
            {
                "id", "provider", "scraped_title", "scraped_year",
                "scraped_url", "country_code", "movie", "is_confirmed",
                "confidence_score", "raw_payload", "created_at",
                "updated_at", "last_scraped_at", "is_available",
                "source_indicator", "provider_category",
            },
        )
        self.assertIsInstance(item["id"], int)
        self.assertEqual(item["provider"], ProviderSerializer(self.provider).data)
        self.assertEqual(item["movie"], MovieSerializer(self.movie).data)
        self.assertEqual(item["scraped_title"], "The Matrix")
        self.assertEqual(item["scraped_year"], 1999)
        self.assertEqual(item["scraped_url"], "https://matrixhd.example/watch")
        self.assertEqual(item["country_code"], "IT")
        self.assertTrue(item["is_confirmed"])
        self.assertAlmostEqual(item["confidence_score"], 0.87)
        self.assertTrue(item["is_available"])
        self.assertEqual(item["source_indicator"], "Scraper")
        self.assertEqual(item["provider_category"], "scraper")
        self.assertIsNotNone(item["created_at"])
        self.assertIsNotNone(item["updated_at"])
        self.assertIsNotNone(item["last_scraped_at"])

    def test_raw_payload_is_reconstructed_for_consumers(self):
        _ingest(self.provider, self.movie)

        [item] = self._results()["results"]

        # The fields consumers filter and render on: languages plus the
        # scrape country when one was recorded.
        self.assertEqual(item["raw_payload"], {
            "audio_languages": ["EN"],
            "subtitle_languages": ["IT", "EN"],
            "country": "IT",
        })
        # Quality lives in Provider.config, not scrape metadata — documented
        # drift from the legacy adapter-specific payload.
        self.assertNotIn("quality", item["raw_payload"])

    def test_available_false_returns_both_availability_states(self):
        _ingest(self.provider, self.movie, title="Available Row", is_available=True)
        _ingest(self.provider, self.movie, title="Gone Row", is_available=False)

        default = self._results()
        self.assertEqual(default["count"], 1)
        self.assertTrue(default["results"][0]["is_available"])

        both = self._results("?available=false")
        self.assertEqual(both["count"], 2)
        self.assertEqual(both["filters"]["available_only"], False)
        self.assertEqual(
            {r["is_available"] for r in both["results"]}, {True, False}
        )

    def test_include_bvod_adds_bvod_category_rows(self):
        bvod = _make_provider("tivu-play", category=ProviderCategory.BVOD)
        _ingest(self.provider, self.movie, title="Scraper Row")
        _ingest(bvod, self.movie, title="BVOD Row")

        default = self._results()
        self.assertEqual(default["count"], 1)
        self.assertEqual(default["results"][0]["provider"]["slug"], "matrixhd")

        with_bvod = self._results("?include_bvod=true")
        self.assertEqual(with_bvod["count"], 2)
        self.assertTrue(with_bvod["filters"]["include_bvod"])
        self.assertEqual(
            {r["provider"]["slug"] for r in with_bvod["results"]},
            {"matrixhd", "tivu-play"},
        )

    def test_language_filters_use_the_reconstructed_payload(self):
        _ingest(self.provider, self.movie, title="English Row", payload={
            "audio_languages": ["EN"], "subtitle_languages": ["IT"],
        })
        _ingest(self.provider, self.movie, title="German Row", payload={
            "audio_languages": ["DE"], "subtitle_languages": ["DE"],
        })
        _ingest(self.provider, self.movie, title="Untagged Row", payload={})

        def titles(query):
            return {r["scraped_title"] for r in self._results(query)["results"]}

        # Rows without language metadata are never filtered out.
        self.assertEqual(
            titles("?audio_lang=en"), {"English Row", "Untagged Row"}
        )
        self.assertEqual(
            titles("?audio_lang=DE"), {"German Row", "Untagged Row"}
        )
        self.assertEqual(
            titles("?subtitle_lang=IT"), {"English Row", "Untagged Row"}
        )
        filters = self._results("?audio_lang=en")["filters"]
        self.assertEqual(filters["audio_lang"], "EN")

    def test_provider_country_and_search_filters(self):
        other = _make_provider("other-scraper", country="GB")
        _ingest(self.provider, self.movie, title="Matrix Row")
        _ingest(other, self.movie, title="Other Title", country_code="")

        by_provider = self._results("?provider=matrixhd")
        self.assertEqual(by_provider["count"], 1)
        self.assertEqual(by_provider["filters"]["provider"], "matrixhd")
        self.assertEqual(by_provider["results"][0]["scraped_title"], "Matrix Row")

        by_country = self._results("?country=IT")
        self.assertEqual(by_country["count"], 1)
        self.assertEqual(by_country["results"][0]["scraped_title"], "Matrix Row")

        by_search = self._results("?q=other")
        self.assertEqual(by_search["count"], 1)
        self.assertEqual(by_search["results"][0]["scraped_title"], "Other Title")

    def test_unresolved_row_reports_null_movie_and_zero_confidence(self):
        _ingest(self.provider, None, title="Loose Row", year=2020,
                is_confirmed=True, confidence=0.9)

        [item] = self._results()["results"]

        # Unresolved rows stay reviewable: no movie link, and the legacy
        # confidence/confirmation live on the ContentMapping that only a
        # linked row has, so both degrade to their neutral values.
        self.assertIsNone(item["movie"])
        self.assertFalse(item["is_confirmed"])
        self.assertEqual(item["confidence_score"], 0.0)
        self.assertIsInstance(item["id"], int)


class UnofficialOffersContractTests(TestCase):
    """GET /api/v2/unofficial/movie/<id>/offers/ keeps the legacy offer shape."""

    def setUp(self):
        self.client = APIClient()
        self.movie = Movie.objects.create(
            tmdb_id=603, title="The Matrix", year=1999
        )
        self.scraper = _make_provider("matrixhd")
        self.bvod = _make_provider("tivu-play", category=ProviderCategory.BVOD)

    def _offers(self, query=""):
        response = self.client.get(
            f"/api/v2/unofficial/movie/{self.movie.tmdb_id}/offers/{query}"
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_response_matches_the_legacy_contract(self):
        _ingest(self.scraper, self.movie)

        data = self._offers()

        # P9: `availability` is additive. The rest of this test still pins the
        # legacy keys exactly, so nothing was dropped — the verdict is presented
        # alongside, and its contents are covered in
        # tracker.tests.test_detail_availability_state.
        self.assertEqual(
            set(data.keys()),
            {"movie", "offers", "selected_season", "availability"},
        )
        self.assertIsNone(data["selected_season"])
        self.assertEqual(
            set(data["movie"].keys()),
            {"tmdb_id", "title", "year", "poster_path", "content_type"},
        )
        self.assertEqual(data["movie"]["tmdb_id"], 603)
        self.assertEqual(data["movie"]["content_type"], "movie")

        [offer] = data["offers"]
        # `price`/`prices` are additive: scraper rows carry no structured price,
        # so the legacy keys are all still present and the new ones are empty.
        self.assertEqual(
            set(offer.keys()),
            {
                "country", "service", "offer_type", "quality", "url",
                "audio_languages", "subtitle_languages",
                "is_language_match", "ranking",
                "service_display", "source_indicator",
                "price", "prices",
                "availability_evidence", "last_seen_at",
                "territorial_scope", "locality", "is_accessible",
                "is_entitled", "rejection_reasons", "entitlement", "access_type", "access_label",
                "requires_payment", "playback_verified", "is_addon", "required_base", "required_base_display",
            },
        )
        self.assertIsNone(offer["price"])
        self.assertEqual(offer["prices"], [])
        self.assertEqual(offer["service"], "matrixhd")
        self.assertEqual(offer["service_display"], self.scraper.name)
        self.assertEqual(offer["source_indicator"], "Scraper")
        self.assertEqual(offer["offer_type"], "free")
        self.assertEqual(offer["country"], "IT")
        self.assertEqual(offer["url"], "https://matrixhd.example/watch")
        self.assertEqual(offer["audio_languages"], ["EN"])
        self.assertEqual(offer["subtitle_languages"], ["IT", "EN"])
        self.assertEqual(offer["territorial_scope"], "country")

    def test_scraped_country_scope_is_exposed_as_reachability_not_provenance(self):
        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "current_country": "NL",
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
                "candidate_countries": [],
                "vpn_enabled": False,
            },
        )
        _ingest(self.bvod, self.movie, country_code="US")

        [offer] = self._offers(
            "?include_bvod=true&current_country=NL"
        )["offers"]

        self.assertEqual(offer["country"], "US")
        self.assertEqual(offer["territorial_scope"], "country")
        self.assertEqual(offer["locality"], "out_of_reach")
        self.assertFalse(offer["is_accessible"])
        self.assertTrue(any("Available in US" in reason for reason in offer["rejection_reasons"]))

        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "current_country": "NL",
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
                "candidate_countries": [],
                "vpn_enabled": True,
            },
        )
        [vpn_offer] = self._offers(
            "?include_bvod=true&current_country=NL"
        )["offers"]
        self.assertEqual(vpn_offer["locality"], "vpn")
        self.assertFalse(vpn_offer["is_accessible"])
        self.assertEqual(vpn_offer["entitlement"]["verdict"], "UNKNOWN")

    def test_missing_scraped_country_is_unknown_in_v2_offer_response(self):
        Preference.objects.update_or_create(
            user_id=1,
            defaults={
                "current_country": "NL",
                "audio_languages": ["EN"],
                "subtitle_languages": ["EN"],
                "candidate_countries": [],
                "vpn_enabled": False,
            },
        )
        _ingest(self.bvod, self.movie, country_code="")

        [offer] = self._offers(
            "?include_bvod=true&current_country=NL"
        )["offers"]

        self.assertEqual(offer["country"], "")
        self.assertEqual(offer["territorial_scope"], "unknown")
        self.assertEqual(offer["locality"], "unknown")
        self.assertFalse(offer["is_accessible"])

    def test_include_bvod_adds_bvod_rows(self):
        _ingest(self.scraper, self.movie, title="Scraper Row")
        _ingest(self.bvod, self.movie, title="BVOD Row")

        default = self._offers()
        self.assertEqual(
            {o["service"] for o in default["offers"]}, {"matrixhd"}
        )

        with_bvod = self._offers("?include_bvod=true")
        self.assertEqual(
            {o["service"] for o in with_bvod["offers"]},
            {"matrixhd", "tivu-play"},
        )

    def test_unavailable_row_never_becomes_an_offer(self):
        _ingest(self.scraper, self.movie, is_available=False)

        data = self._offers()

        self.assertEqual(data["offers"], [])
        self.assertEqual(data["movie"]["tmdb_id"], 603)

    def test_disabled_provider_never_becomes_an_offer(self):
        _ingest(self.scraper, self.movie)
        self.scraper.is_enabled = False
        self.scraper.save()

        data = self._offers()

        self.assertEqual(data["offers"], [])
