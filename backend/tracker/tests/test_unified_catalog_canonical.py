"""The unified catalogue's canonical read path (Phase A).

``GET /api/catalog/unified/`` used to serve its unofficial half by reading
``tracker.ScrapedContentMapping`` directly. It now reads the canonical
availability tables through the availability catalogue boundary, fed by the
legacy ingestion bridge. These tests pin the endpoint contract that had to
survive that swap — response shape, filters, official/unofficial dedupe,
reconciled official watchability — plus the local-only guarantee (no fetch,
no refresh, no TMDb on a read) and the write hooks that keep the bridge fed.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from django.test import TestCase

from availability.domain.enums import MatchMethod
from availability.models import (
    ContentMapping,
    ProviderContent,
)
from availability.services.legacy_ingest import ingest_scraped_mapping
from availability.tests.reconciliation_fixtures import ReconciliationFixtureMixin
from tracker.models import Movie, Provider, ScrapedContentMapping, Subscription
from tracker.scrapers import ContentMatcher, ScrapedItem
from tracker.services.tmdb import TMDbItem
from tracker.tests.availability_factories import local_offer


class UnifiedCatalogCanonicalTestCase(TestCase):
    def setUp(self):
        self.hydra = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper"
        )

    def _scrape(self, **overrides):
        """Write one legacy row and project it through the bridge."""
        defaults = {
            "provider": self.hydra,
            "scraped_title": "Scraper Movie",
            "scraped_year": 2022,
            "scraped_url": "https://hydra.example/movie",
            "is_available": True,
        }
        defaults.update(overrides)
        row = ScrapedContentMapping.objects.create(**defaults)
        ingest_scraped_mapping(row)
        return row

    def _results(self, query=""):
        resp = self.client.get(f"/api/catalog/unified/{query}")
        self.assertEqual(resp.status_code, 200)
        return resp.json()


class UnifiedCatalogContractTests(UnifiedCatalogCanonicalTestCase):
    def test_scraper_item_matches_the_response_contract(self):
        self._scrape(raw_payload={
            "content_type": "movie",
            "audio_languages": ["en", "de"],
            "subtitle_languages": ["es"],
        })

        data = self._results()

        self.assertEqual(data["count"], 1)
        [item] = data["results"]
        self.assertEqual(
            set(item.keys()),
            {
                "id", "tmdb_id", "source", "source_indicator", "title", "year",
                "poster_path", "content_type", "provider", "watchability",
                "unofficial_details", "watch_url",
            },
        )
        self.assertTrue(item["id"].startswith("scraper_"))
        self.assertEqual(item["source"], "scraper")
        self.assertEqual(item["source_indicator"], "Scraper")
        self.assertIsNone(item["tmdb_id"])
        self.assertEqual(item["title"], "Scraper Movie")
        self.assertEqual(item["year"], 2022)
        self.assertEqual(item["poster_path"], "")
        self.assertEqual(item["content_type"], "movie")
        self.assertEqual(item["provider"], {
            "slug": "hydra", "name": "Hydra", "category": "scraper",
        })
        self.assertIsNone(item["watchability"])

        details = item["unofficial_details"]
        self.assertEqual(
            details["scraped_url"], "https://hydra.example/movie"
        )
        self.assertEqual(details["confidence_score"], 0.0)
        self.assertIs(details["is_confirmed"], False)
        self.assertEqual(details["audio_languages"], ["EN", "DE"])
        self.assertEqual(details["subtitle_languages"], ["ES"])
        self.assertEqual(item["watch_url"], "https://hydra.example/movie")

    def test_resolved_rows_show_the_canonical_movie_display(self):
        movie = Movie.objects.create(
            tmdb_id=500, title="Fight Club", year=1999,
            poster_path="/fc.jpg", content_type="movie",
        )
        self._scrape(
            scraped_title="Fight.Club.1999.1080p.WEB",
            scraped_year=1999,
            movie=movie,
            is_confirmed=True,
            confidence_score=1.0,
        )

        # A resolved row is only presented through the scraper half when the
        # request selects scraper providers (otherwise the official half
        # already owns the title) — same dedupe the endpoint always had.
        data = self._results("?providers=hydra")

        self.assertEqual(data["count"], 1)
        [item] = data["results"]
        self.assertEqual(item["source"], "scraper")
        self.assertEqual(item["title"], "Fight Club")
        self.assertEqual(item["year"], 1999)
        self.assertEqual(item["poster_path"], "/fc.jpg")
        self.assertEqual(item["tmdb_id"], 500)
        self.assertEqual(item["content_type"], "movie")
        self.assertTrue(item["unofficial_details"]["is_confirmed"])
        self.assertEqual(item["unofficial_details"]["confidence_score"], 1.0)

    def test_unresolved_rows_are_not_deduplicated_against_official(self):
        movie = Movie.objects.create(tmdb_id=700, title="Dup", year=2015)
        self._scrape(
            scraped_title="Dup", scraped_year=2015,
            movie=movie, is_confirmed=True, confidence_score=1.0,
        )
        self._scrape(scraped_title="Extra Film", scraped_year=2016)

        data = self._results()

        self.assertEqual(data["count"], 2)
        by_source = {item["source"]: item for item in data["results"]}
        self.assertEqual(by_source["official"]["tmdb_id"], 700)
        self.assertEqual(by_source["scraper"]["title"], "Extra Film")


class UnifiedCatalogFilterTests(UnifiedCatalogCanonicalTestCase):
    def setUp(self):
        super().setUp()
        self._scrape(scraped_title="Alpha Quest", scraped_year=2001)
        self._scrape(scraped_title="Beta Chronicle", scraped_year=2020)

    def test_search_filters_on_the_scraped_title(self):
        self.assertEqual(self._results("?q=ALPHA")["count"], 1)
        self.assertEqual(
            self._results("?q=ALPHA")["results"][0]["title"], "Alpha Quest"
        )
        self.assertEqual(self._results("?query=chronicle")["count"], 1)
        self.assertEqual(self._results("?q=nothing-matches")["count"], 0)

    def test_year_range_filters_the_scraped_year(self):
        self.assertEqual(
            {i["title"] for i in self._results("?year_min=2010")["results"]},
            {"Beta Chronicle"},
        )
        self.assertEqual(
            {i["title"] for i in self._results("?year_max=2010")["results"]},
            {"Alpha Quest"},
        )
        self.assertEqual(
            self._results("?year_min=2010&year_max=2030")["count"], 1
        )

    def test_country_filter_matches_row_then_provider_country(self):
        ScrapedContentMapping.objects.filter(
            scraped_title="Alpha Quest"
        ).update(country_code="GB")
        for row in ScrapedContentMapping.objects.filter(
            scraped_title="Alpha Quest"
        ):
            ingest_scraped_mapping(row)

        self.assertEqual(self._results("?country=GB")["count"], 1)
        self.assertEqual(self._results("?country=US")["count"], 0)

        Provider.objects.filter(pk=self.hydra.pk).update(country_code="US")
        self.assertEqual(self._results("?country=US")["count"], 2)

    def test_disabled_or_unavailable_rows_disappear(self):
        self.assertEqual(self._results()["count"], 2)

        Provider.objects.filter(pk=self.hydra.pk).update(is_enabled=False)
        self.assertEqual(self._results()["count"], 0)
        Provider.objects.filter(pk=self.hydra.pk).update(is_enabled=True)

        row = ScrapedContentMapping.objects.get(scraped_title="Alpha Quest")
        ScrapedContentMapping.objects.filter(pk=row.pk).update(
            is_available=False
        )
        ingest_scraped_mapping(ScrapedContentMapping.objects.get(pk=row.pk))
        data = self._results()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["title"], "Beta Chronicle")


class UnifiedCatalogOfficialWatchabilityTests(
    ReconciliationFixtureMixin, TestCase
):
    def setUp(self):
        super().setUp()
        from availability.tests.freshness_helpers import declare_freshness

        declare_freshness(self, "recon-source-a", "recon-source-b")

    def test_official_watchability_reads_reconciled_canonical_offers(self):
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        Subscription.objects.create(
            user_id=1, provider=self.netflix, name="Netflix"
        )
        # Two raw observations of the *same* effective offer: reconciliation
        # presents one, and the endpoint must count it once.
        self.offer(
            tmdb_id=550, provider_content_id="sa-550",
            provider_url="https://a.test/550",
            audio_languages=["EN"], subtitle_languages=["EN"],
        )
        self.offer(
            tmdb_id=550, provider_content_id="sb-550",
            provider_url="https://a.test/550",
        )

        resp = self.client.get("/api/catalog/unified/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        [official] = [
            item for item in data["results"] if item["source"] == "official"
        ]
        watch = official["watchability"]
        counted = [
            offer
            for offer in watch["watchable_offers"] + watch["excluded_offers"]
            if offer["service"] == "recon-netflix"
        ]
        self.assertEqual(len(counted), 1)
        self.assertEqual(watch["status"], "watchable_here")
        self.assertEqual(watch["best_offer"]["service"], "recon-netflix")
        self.assertEqual(watch["best_offer"]["url"], "https://a.test/550")


class UnifiedCatalogLocalReadTests(UnifiedCatalogCanonicalTestCase):
    def test_unified_catalog_never_fetches_refreshes_or_searches(self):
        movie = Movie.objects.create(tmdb_id=101, title="Official Movie", year=2021)
        local_offer(movie, service="netflix", audio_languages=["EN"],
                    subtitle_languages=["EN"])
        self._scrape()

        seams = [
            "tracker.services.streaming_api.StreamingAvailabilityClient.get_availability",
            "availability.services.refresh.AvailabilityRefreshService.refresh_content",
            "availability.services.refresh.AvailabilityRefreshService.refresh_catalogue",
            "tracker.services.tmdb.TMDbClient.search",
            "tracker.services.tmdb.TMDbClient.details",
            "availability.sources.streaming_availability.StreamingAvailabilitySource.fetch",
        ]

        with ExitStack() as stack:
            spies = [
                stack.enter_context(
                    patch(seam, side_effect=AssertionError(f"called {seam}"))
                )
                for seam in seams
            ]
            # A read may not write canonical rows either.
            save_guard = stack.enter_context(
                patch.object(
                    ProviderContent, "save",
                    side_effect=AssertionError("unified catalogue write attempted"),
                )
            )
            data = self._results()

        for spy in spies:
            spy.assert_not_called()
        save_guard.assert_not_called()
        sources = {item["source"] for item in data["results"]}
        self.assertEqual(sources, {"official", "scraper"})


class UnifiedCatalogWriteHookTests(UnifiedCatalogCanonicalTestCase):
    def test_matcher_links_locally_and_bridges_the_canonical_row(self):
        movie = Movie.objects.create(
            tmdb_id=800, title="Local Exact", year=2011
        )
        matcher = ContentMatcher(tmdb_client=MagicMock())
        item = ScrapedItem(
            title="Local Exact", provider_slug="hydra", year=2011
        )

        matched, score, _candidates, _confirmed = matcher.match_item(item)

        self.assertEqual(matched, movie)
        self.assertEqual(score, 1.0)
        matcher.tmdb_client.search.assert_not_called()

        legacy = ScrapedContentMapping.objects.get(provider=self.hydra)
        self.assertEqual(legacy.movie, movie)
        content = ProviderContent.objects.get(
            provider=self.hydra,
            provider_content_id=f"legacy-{legacy.pk}",
        )
        self.assertEqual(content.title, "Local Exact")
        mapping = ContentMapping.objects.get(provider=self.hydra)
        self.assertEqual(mapping.tmdb_id, 800)
        self.assertEqual(mapping.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(mapping.confidence, 1.0)

    def test_matcher_bridges_unresolved_rows_without_a_mapping(self):
        matcher = ContentMatcher(tmdb_client=MagicMock())
        matcher.tmdb_client.search.return_value = []
        item = ScrapedItem(
            title="No Match Film", provider_slug="hydra", year=2012
        )

        matched, score, _candidates, _confirmed = matcher.match_item(item)

        self.assertIsNone(matched)
        self.assertEqual(score, 0.0)
        legacy = ScrapedContentMapping.objects.get(provider=self.hydra)
        self.assertIsNone(legacy.movie)
        self.assertTrue(
            ProviderContent.objects.filter(
                provider=self.hydra,
                provider_content_id=f"legacy-{legacy.pk}",
            ).exists()
        )
        self.assertEqual(ContentMapping.objects.count(), 0)

    @patch("tracker.views.TMDbClient")
    def test_confirm_match_bridges_the_manual_resolution(self, mock_client):
        row = ScrapedContentMapping.objects.create(
            provider=self.hydra,
            scraped_title="Confirm Me",
            scraped_year=2013,
            scraped_url="https://hydra.example/confirm",
            is_available=True,
        )
        mock_client.return_value.details.return_value = TMDbItem(
            tmdb_id=31337, title="Confirmed Film", year=2013,
            content_type="movie", overview="", poster_path="",
            genres=[], genre_ids=[], cast=[], seasons_data=[],
            rating=0.0, director=[],
        )

        resp = self.client.post(
            "/api/scrapers/confirm-match/",
            {"mapping_id": row.pk, "tmdb_id": 31337},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        mapping = ContentMapping.objects.get(provider=self.hydra)
        self.assertEqual(mapping.tmdb_id, 31337)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(mapping.confidence, 1.0)

        data = self._results("?providers=hydra")
        [item] = data["results"]
        self.assertEqual(item["title"], "Confirmed Film")
        self.assertEqual(item["year"], 2013)
        self.assertEqual(item["tmdb_id"], 31337)
        self.assertTrue(item["unofficial_details"]["is_confirmed"])

    @patch("tracker.views.ContentMatcher")
    @patch("tracker.services.scraper_selection.resolve_scraper_class")
    def test_scraper_run_bridges_every_saved_row(
        self, resolve_cls, matcher_cls
    ):
        item = ScrapedItem(
            title="Run Film",
            provider_slug="hydra",
            year=2023,
            source_url="https://hydra.example/run",
            availability=True,
            raw_payload={"audio_languages": ["en"]},
        )
        scraper_instance = MagicMock()
        scraper_instance.run.return_value = [item]
        resolve_cls.return_value = MagicMock(return_value=scraper_instance)
        matcher_cls.return_value.match_item.return_value = (
            None, 0.0, [], False
        )

        resp = self.client.post(
            "/api/scrapers/run/", {"provider_slug": "hydra"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)

        legacy = ScrapedContentMapping.objects.get(
            provider=self.hydra, scraped_title="Run Film"
        )
        content = ProviderContent.objects.get(
            provider=self.hydra,
            provider_content_id=f"legacy-{legacy.pk}",
        )
        self.assertTrue(content.currently_available)
        self.assertEqual(content.metadata["audio_languages"], ["EN"])
        self.assertEqual(content.provider_url, "https://hydra.example/run")

    def test_deleting_a_legacy_row_removes_its_canonical_projection(self):
        row = self._scrape()
        self.assertEqual(ProviderContent.objects.count(), 1)

        row.delete()

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)

    def test_provider_delete_clears_legacy_and_canonical_rows(self):
        self._scrape()

        resp = self.client.delete("/api/providers/hydra/")

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(Provider.objects.filter(slug="hydra").count(), 0)
        self.assertEqual(ScrapedContentMapping.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
