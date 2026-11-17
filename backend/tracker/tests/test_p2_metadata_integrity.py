"""P2 regressions for truthful scraper metadata and refresh outcomes.

All tests are local and fixture-based.  They do not issue HTTP requests.
"""
from unittest.mock import patch
from importlib import import_module
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from tracker.models import (
    Movie,
    Provider,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    ScrapedContentMapping,
    AvailabilityResolutionReason,
)
from tracker.scrapers import AnimeWorldScraper, GenericURLAdapter, HydraHDScraper, KissKHScraper
from tracker.scrapers.base import ScrapedItem
from tracker.services.bulk_refresh import permanently_absent_scopes
from tracker.services.scraped_mapping import persist_scraped_item
from tracker.services.availability_report import title_source_report


class UnknownScraperMetadataTests(SimpleTestCase):
    def test_adapters_do_not_invent_playback_metadata(self):
        kisskh = KissKHScraper().parse_items([{"id": "1", "title": "Plain", "type": "Movie"}])[0]
        generic = GenericURLAdapter("fixture", "fixture.example").parse_items([
            {"id": "1", "title": "Plain"}
        ])[0]
        hydra_json = HydraHDScraper().parse_items([{"id": "1", "title": "Plain"}])[0]
        hydra_html = HydraHDScraper().parse_html(
            '<div><a class="hthis" href="/movie/plain"></a>'
            '<div class="title detz">Plain</div></div>'
        )[0]
        anime = AnimeWorldScraper().parse_html(
            '<div><a class="name" href="/play/plain">Plain</a></div>'
        )[0]

        for item in (kisskh, generic, hydra_json, hydra_html, anime):
            self.assertEqual(item.audio_languages, [])
            self.assertEqual(item.subtitle_languages, [])
            self.assertIsNone(item.quality)

    def test_observed_metadata_is_preserved(self):
        item = KissKHScraper().parse_items([
            {
                "id": "1", "title": "Observed", "type": "Movie", "audio": ["Italian"],
                "subtitles": ["English"], "quality": "4k",
            }
        ])[0]
        self.assertEqual(item.audio_languages, ["IT"])
        self.assertEqual(item.subtitle_languages, ["EN"])
        self.assertEqual(item.quality, "4K")


class ScraperMappingPersistenceTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="p2-fixture", name="P2 fixture", category="scraper"
        )

    @patch("tracker.services.scraped_mapping.ingest_scraped_mapping")
    def test_raw_observation_refreshes_url_without_erasing_confirmed_mapping(self, ingest):
        movie = Movie.objects.create(tmdb_id=9_420_001, title="Canonical")
        existing = ScrapedContentMapping.objects.create(
            provider=self.provider,
            scraped_title="Source title",
            scraped_year=2020,
            scraped_url="https://old.example/title",
            movie=movie,
            is_confirmed=True,
            confidence_score=1.0,
        )
        item = ScrapedItem(
            title="Source title", provider_slug=self.provider.slug, year=2020,
            source_url="https://new.example/title", raw_payload={"id": "new"},
        )

        mapping = persist_scraped_item(provider=self.provider, item=item)
        mapping.refresh_from_db()

        self.assertEqual(mapping.pk, existing.pk)
        self.assertEqual(mapping.scraped_url, "https://new.example/title")
        self.assertEqual(mapping.movie_id, movie.pk)
        self.assertTrue(mapping.is_confirmed)
        ingest.assert_called_once_with(mapping)


class NegativeObservationTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=9_420_002, title="Absent")

    def test_title_not_found_is_a_successful_negative_not_a_failure(self):
        request = RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )

        self.assertEqual(request.status, RefreshRequestStatus.DONE)
        self.assertEqual(request.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)
        self.assertEqual(
            permanently_absent_scopes(self.movie.tmdb_id),
            {"streaming_availability"},
        )

    def test_transport_failure_is_not_a_permanent_negative(self):
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.FAILED,
            last_error="Timeout: upstream unavailable",
        )
        self.assertEqual(permanently_absent_scopes(self.movie.tmdb_id), set())

    def test_0044_backfills_a_historical_typed_404_without_touching_failures(self):
        historical = RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.FAILED,
            last_error="UpstreamTitleNotFound: historical 404",
        )
        transient = RefreshRequest.objects.create(
            movie=self.movie,
            source="watchmode",
            status=RefreshRequestStatus.FAILED,
            last_error="Timeout: temporary outage",
        )

        migration = import_module("tracker.migrations.0044_refreshrequest_outcome")
        from django.apps import apps
        migration.backfill_title_not_found(apps, None)

        historical.refresh_from_db()
        transient.refresh_from_db()
        self.assertEqual(historical.status, RefreshRequestStatus.DONE)
        self.assertEqual(historical.outcome, RefreshRequestOutcome.TITLE_NOT_FOUND)
        self.assertEqual(transient.status, RefreshRequestStatus.FAILED)
        self.assertEqual(transient.outcome, RefreshRequestOutcome.UNKNOWN)

    def test_newer_transport_failure_does_not_reuse_an_old_negative_answer(self):
        now = timezone.now()
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.NO_OFFER,
            requested_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1),
        )
        RefreshRequest.objects.create(
            movie=self.movie,
            source="streaming_availability",
            status=RefreshRequestStatus.FAILED,
            last_error="Timeout: temporary outage",
            requested_at=now,
            completed_at=now,
        )

        row = next(
            entry for entry in title_source_report([self.movie.pk])[self.movie.pk]["sources"]
            if entry["source"] == "streaming_availability"
        )
        self.assertEqual(row["reason"], AvailabilityResolutionReason.SOURCE_UNAVAILABLE)
