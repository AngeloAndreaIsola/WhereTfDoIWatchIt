"""Legacy scraper ingestion bridge: one legacy row, one canonical projection.

Phase A keeps ``tracker.ScrapedContentMapping`` as the scrapers' write table
but moves every *read* onto the canonical availability models. These tests
pin the write-side contract of that bridge:

- one ``ProviderContent`` per legacy row, identity ``legacy-<pk>``, attributed
  to the ``legacy_scraper`` source and no other;
- a ``ContentMapping`` only when the legacy row resolves to a real TMDb id,
  carrying the legacy confidence and the manual/automatic match method;
- zero ``Offer`` rows, ever — a scrape is a catalogue observation, not a
  commercial offer, so watchability/ranking semantics cannot shift;
- idempotency (re-ingest upserts the same identities) and a deletion mirror
  that removes the projection when the legacy row goes away.
"""
from __future__ import annotations

from datetime import date

from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, SourceType
from availability.models import (
    LEGACY_SCRAPER_SOURCE_SLUG,
    ContentMapping,
    Offer,
    ProviderContent,
    Source,
)
from availability.services.legacy_ingest import (
    delete_scraped_mapping,
    ingest_all,
    ingest_scraped_mapping,
)
from tracker.models import Movie, Provider, ScrapedContentMapping


class LegacyIngestTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="hydra", name="Hydra", category="scraper"
        )

    def _row(self, **overrides):
        defaults = {
            "provider": self.provider,
            "scraped_title": "Scraper Movie",
            "scraped_year": 2022,
            "scraped_url": "https://hydra.example/movie",
            "is_available": True,
            "country_code": "GB",
        }
        defaults.update(overrides)
        return ScrapedContentMapping.objects.create(**defaults)

    def test_unlinked_row_projects_provider_content_only(self):
        mapping = self._row()

        content = ingest_scraped_mapping(mapping)

        self.assertEqual(content.provider_content_id, f"legacy-{mapping.pk}")
        self.assertEqual(content.provider, self.provider)
        self.assertEqual(content.source.slug, LEGACY_SCRAPER_SOURCE_SLUG)
        self.assertEqual(content.title, "Scraper Movie")
        self.assertEqual(content.content_type, ContentType.MOVIE)
        self.assertEqual(content.release_date, date(2022, 1, 1))
        self.assertEqual(content.provider_url, "https://hydra.example/movie")
        self.assertTrue(content.currently_available)
        self.assertEqual(content.metadata["country_code"], "GB")

        self.assertEqual(ContentMapping.objects.count(), 0)
        # A scrape never becomes an offer: no watchability semantics change.
        self.assertEqual(Offer.objects.count(), 0)

    def test_series_rows_keep_the_canonical_series_type(self):
        mapping = self._row(raw_payload={"content_type": "series"})

        content = ingest_scraped_mapping(mapping)

        self.assertEqual(content.content_type, ContentType.SERIES)
        self.assertEqual(content.release_date, date(2022, 1, 1))

    def test_confirmed_linked_row_records_a_manual_mapping(self):
        movie = Movie.objects.create(tmdb_id=900, title="Linked Film", year=2020)
        mapping = self._row(
            movie=movie, is_confirmed=True, confidence_score=0.99
        )

        content = ingest_scraped_mapping(mapping)
        [stored] = ContentMapping.objects.all()

        self.assertEqual(stored.provider_content_id, f"legacy-{mapping.pk}")
        self.assertEqual(stored.tmdb_id, 900)
        self.assertEqual(stored.match_method, MatchMethod.MANUAL)
        self.assertEqual(stored.confidence, 0.99)
        self.assertEqual(stored.content, content)
        self.assertEqual(stored.source.slug, LEGACY_SCRAPER_SOURCE_SLUG)

    def test_unconfirmed_linked_row_records_the_title_year_method(self):
        movie = Movie.objects.create(tmdb_id=901, title="Auto Film", year=2019)
        mapping = self._row(
            movie=movie, is_confirmed=False, confidence_score=0.65
        )

        ingest_scraped_mapping(mapping)
        [stored] = ContentMapping.objects.all()

        self.assertEqual(stored.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(stored.confidence, 0.65)
        self.assertEqual(stored.tmdb_id, 901)

    def test_a_movie_without_a_real_tmdb_id_writes_no_mapping(self):
        movie = Movie.objects.create(
            tmdb_id=0, title="Unlinked Row", year=2018, content_type="movie"
        )
        mapping = self._row(movie=movie)

        content = ingest_scraped_mapping(mapping)

        self.assertEqual(content.title, "Scraper Movie")
        self.assertEqual(ContentMapping.objects.count(), 0)

    def test_unavailable_legacy_rows_read_as_unavailable(self):
        mapping = self._row(is_available=False)

        content = ingest_scraped_mapping(mapping)
        self.assertFalse(content.currently_available)

        # Re-ingesting after the legacy row comes back restores availability.
        ScrapedContentMapping.objects.filter(pk=mapping.pk).update(
            is_available=True
        )
        content = ingest_scraped_mapping(ScrapedContentMapping.objects.get(
            pk=mapping.pk
        ))
        self.assertTrue(content.currently_available)

    def test_ingest_is_idempotent(self):
        mapping = self._row()
        Movie.objects.create(tmdb_id=902, title="Twice", year=2021)
        mapping.movie = Movie.objects.get(tmdb_id=902)
        mapping.is_confirmed = True
        mapping.save()

        first = ingest_scraped_mapping(mapping)
        second = ingest_scraped_mapping(mapping)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ProviderContent.objects.count(), 1)
        self.assertEqual(ContentMapping.objects.count(), 1)

    def test_metadata_normalizes_languages_and_keeps_the_scraped_poster(self):
        mapping = self._row(raw_payload={
            "content_type": "movie",
            "audio_languages": ["en", "EN"],
            "subtitle_languages": ["es"],
            "poster_path": "/scraped-poster.jpg",
        })

        content = ingest_scraped_mapping(mapping)

        self.assertEqual(content.metadata["audio_languages"], ["EN"])
        self.assertEqual(content.metadata["subtitle_languages"], ["ES"])
        self.assertEqual(content.metadata["poster"], "/scraped-poster.jpg")

    def test_a_linked_row_shows_the_movies_poster_not_the_payload_one(self):
        movie = Movie.objects.create(
            tmdb_id=903, title="Posted", year=2017, poster_path="/tmdb.jpg"
        )
        mapping = self._row(
            movie=movie, raw_payload={"poster_path": "/scraped-poster.jpg"}
        )

        content = ingest_scraped_mapping(mapping)

        self.assertEqual(content.metadata["poster"], "/tmdb.jpg")

    def test_ingest_all_backfills_every_legacy_row(self):
        self._row()
        self._row(scraped_title="Other Film", scraped_year=2020)

        count = ingest_all()

        self.assertEqual(count, 2)
        self.assertEqual(ProviderContent.objects.count(), 2)

    def test_delete_scraped_mapping_removes_the_projection(self):
        mapping = self._row()
        ingest_scraped_mapping(mapping)
        self.assertEqual(ProviderContent.objects.count(), 1)

        delete_scraped_mapping(mapping)

        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)

    def test_source_is_created_once_with_the_legacy_slug(self):
        ingest_scraped_mapping(self._row())
        ingest_scraped_mapping(self._row(scraped_title="Second"))

        [source] = Source.objects.filter(slug=LEGACY_SCRAPER_SOURCE_SLUG)
        self.assertEqual(source.type, SourceType.WEB)

    def test_backfill_command_ingests_every_legacy_row(self):
        from django.core.management import call_command

        self._row()
        self._row(scraped_title="Second Film", scraped_year=2021)

        call_command("ingest_legacy_scraper_catalogue")

        self.assertEqual(ProviderContent.objects.count(), 2)
        # Re-running is a no-op projection: still exactly one row per legacy id.
        call_command("ingest_legacy_scraper_catalogue")
        self.assertEqual(ProviderContent.objects.count(), 2)
