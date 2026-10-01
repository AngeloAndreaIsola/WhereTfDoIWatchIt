"""CBC Gem through AvailabilityRefreshService: mapping, persistence, semantics.

End-to-end path exercised here (fixture API, zero network):

    FakeCbcApi -> CBCGemSource.fetch() -> local title/year mapping ->
    OfferRepository -> availability.Offer + ContentMapping + SyncRun

Fixture catalogue totals (see test_cbc_gem_source for the breakdown): 5
content records, 66 offers. Local catalog contains North of North (series),
Murdoch Mysteries (series) and Beirut (movie); Coronation Street is
deliberately absent so the unmapped-content contract is observable.
"""
import datetime as dt

from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, OfferType, SyncStatus
from availability.models import ContentMapping, Country, Offer, Source, SyncCursor, SyncRun
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import SourceFetchError
from availability.sources.cbc_gem import CBCGemClient, CBCGemSource
from tracker.models import Movie, Provider

from .cbc_fixtures import FakeCbcApi

TOTAL_OFFERS = 66
MAPPED_OFFERS = 43   # north 19 + murdoch 23 + beirut 1
UNMAPPED_OFFERS = 23  # coronation-street


class CBCGemRefreshTestCase(TestCase):
    def setUp(self):
        self.api = FakeCbcApi()
        self.source = CBCGemSource(client=CBCGemClient(requester=self.api))
        self.service = AvailabilityRefreshService(source=self.source)
        self.provider, _ = Provider.objects.get_or_create(
            slug="cbc_gem", defaults={"name": "CBC Gem"}
        )
        Country.objects.get_or_create(code="CA", defaults={"name": "Canada"})

        self.north = Movie.objects.create(
            tmdb_id=9001, title="North of North", year=2024, content_type="series"
        )
        self.murdoch = Movie.objects.create(
            tmdb_id=9002, title="Murdoch Mysteries", year=2008, content_type="series"
        )
        self.beirut = Movie.objects.create(
            tmdb_id=9003, title="Beirut", year=2018, content_type="movie"
        )

    def _mapping(self, provider_content_id):
        return ContentMapping.objects.get(
            provider=self.provider, provider_content_id=provider_content_id
        )


class EndToEndRefreshTests(CBCGemRefreshTestCase):
    def test_resolved_offers_are_persisted_and_unresolved_are_skipped(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        self.assertEqual(result.processed, MAPPED_OFFERS)
        self.assertEqual(result.created, MAPPED_OFFERS)
        self.assertEqual(result.skipped, UNMAPPED_OFFERS)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="beirut")},
            {9003},
        )
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="north-of-north")},
            {9001},
        )
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="murdoch-mysteries")},
            {9002},
        )

    def test_offer_granularity_survives_persistence(self):
        self.service.refresh_catalogue()
        breakdown = {}
        for offer in Offer.objects.all():
            breakdown[offer.content_type] = breakdown.get(offer.content_type, 0) + 1
        self.assertEqual(breakdown, {
            "series": 2, "movie": 1, "season": 4, "episode": 36,
        })

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        movie_offer = Offer.objects.get(
            content_type=ContentType.MOVIE, provider_content_id="beirut"
        )
        self.assertEqual(
            movie_offer.identity, "cbc_gem|CA|movie|9003|||free|beirut"
        )
        self.assertEqual(movie_offer.provider, self.provider)
        self.assertEqual(movie_offer.country.code, "CA")
        self.assertEqual(movie_offer.source.slug, "cbc_gem")
        self.assertEqual(movie_offer.offer_type, OfferType.FREE)
        self.assertEqual(movie_offer.audio_languages, ["EN"])
        self.assertIsNone(movie_offer.subtitle_languages)
        self.assertIsNone(movie_offer.available_until)
        self.assertEqual(
            movie_offer.available_from.replace(tzinfo=dt.timezone.utc),
            dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(movie_offer.currently_available)

    def test_sync_run_lifecycle_and_counters(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "CA")
        self.assertEqual(run.source.slug, "cbc_gem")
        self.assertEqual(run.mode, "full")
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_discovered, MAPPED_OFFERS)
        self.assertEqual(run.items_processed, MAPPED_OFFERS)
        self.assertEqual(run.offers_created, MAPPED_OFFERS)
        self.assertEqual(run.offers_updated, 0)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_source_row_is_created_on_first_refresh(self):
        self.service.refresh_catalogue()
        source_row = Source.objects.get(slug="cbc_gem")
        self.assertEqual(source_row.name, "CBC Gem")
        self.assertTrue(source_row.enabled)

    def test_no_cursor_is_invented_for_page_number_apis(self):
        self.service.refresh_catalogue()
        self.assertEqual(SyncCursor.objects.count(), 0)


class MappingTests(CBCGemRefreshTestCase):
    def test_mappings_are_created_with_tiered_confidence(self):
        self.service.refresh_catalogue()

        self.assertEqual(ContentMapping.objects.count(), 3)
        beirut = self._mapping("beirut")
        # Exact title + production year resolved uniquely -> strongest tier.
        self.assertEqual(beirut.tmdb_id, 9003)
        self.assertEqual(beirut.confidence, 0.9)
        self.assertEqual(beirut.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(beirut.content_type, ContentType.MOVIE)
        self.assertIsNotNone(beirut.last_verified)
        # Series carry no trustworthy provider year -> title-only tier.
        north = self._mapping("north-of-north")
        self.assertEqual(north.tmdb_id, 9001)
        self.assertEqual(north.confidence, 0.7)
        self.assertEqual(north.content_type, ContentType.SERIES)

    def test_unmapped_content_persists_no_offers_and_no_mapping(self):
        with self.assertLogs("availability.services.refresh", level="WARNING") as logs:
            result = self.service.refresh_catalogue()

        self.assertEqual(result.skipped, UNMAPPED_OFFERS)
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id="coronation-street").exists()
        )
        self.assertFalse(
            Offer.objects.filter(provider_content_id="coronation-street").exists()
        )
        self.assertTrue(
            any("availability_mapping_unresolved" in line
                and "coronation-street" in line for line in logs.output)
        )

    def test_preexisting_mapping_is_reused_without_local_catalog_entry(self):
        Source.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="cbc_gem"),
            provider_content_id="coronation-street",
            content_type=ContentType.SERIES,
            tmdb_id=777,
            confidence=0.8,
            match_method=MatchMethod.PROVIDER_MAPPING,
        )

        result = self.service.refresh_catalogue()

        self.assertEqual(result.processed, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(
                provider_content_id="coronation-street")},
            {777},
        )
        mapping = self._mapping("coronation-street")
        self.assertEqual(mapping.confidence, 0.8)
        self.assertIsNotNone(mapping.last_verified)

    def test_manual_mapping_never_overwritten_by_local_match(self):
        Source.objects.get_or_create(slug="cbc_gem", defaults={"name": "CBC Gem"})
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="cbc_gem"),
            provider_content_id="beirut",
            content_type=ContentType.MOVIE,
            tmdb_id=8888,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        self.service.refresh_catalogue()

        mapping = self._mapping("beirut")
        self.assertEqual(mapping.tmdb_id, 8888)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(
            {o.tmdb_id for o in Offer.objects.filter(provider_content_id="beirut")},
            {8888},
        )

    def test_provider_year_mismatch_still_maps_by_unique_title(self):
        Movie.objects.filter(pk=self.beirut.pk).update(year=2015)

        self.service.refresh_catalogue()

        mapping = self._mapping("beirut")
        self.assertEqual(mapping.tmdb_id, 9003)
        self.assertEqual(mapping.confidence, 0.7)

    def test_duplicate_local_titles_resolve_through_the_year_tiebreak(self):
        Movie.objects.create(tmdb_id=9004, title="Beirut", year=2010, content_type="movie")

        self.service.refresh_catalogue()

        mapping = self._mapping("beirut")
        self.assertEqual(mapping.tmdb_id, 9003)  # the 2018 row, uniquely
        self.assertEqual(mapping.confidence, 0.9)

    def test_ambiguous_local_titles_stay_unmapped(self):
        self.beirut.delete()
        Movie.objects.create(tmdb_id=9005, title="Beirut", year=2015, content_type="movie")
        Movie.objects.create(tmdb_id=9006, title="Beirut", year=2016, content_type="movie")

        result = self.service.refresh_catalogue()

        self.assertFalse(ContentMapping.objects.filter(provider_content_id="beirut").exists())
        self.assertFalse(Offer.objects.filter(provider_content_id="beirut").exists())
        self.assertEqual(result.created, MAPPED_OFFERS - 1)

    def test_second_run_reuses_mappings_and_is_idempotent(self):
        first = self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, MAPPED_OFFERS)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, MAPPED_OFFERS)
        self.assertEqual(second.skipped, UNMAPPED_OFFERS)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(ContentMapping.objects.count(), 3)
        self.assertEqual(SyncRun.objects.count(), 2)
        self.assertEqual(SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 2)


class NonDeactivationTests(CBCGemRefreshTestCase):
    def test_item_disappearing_from_the_catalogue_is_never_deactivated(self):
        self.service.refresh_catalogue()
        beirut_before = set(
            Offer.objects.filter(provider_content_id="beirut")
            .values_list("pk", flat=True)
        )
        self.assertTrue(beirut_before)

        # Second fetch: beirut no longer in any category index.
        self.api.categories["all-films"]["1"]["results"] = [
            card for card in self.api.categories["all-films"]["1"]["results"]
            if card["url"] != "beirut"
        ]
        result = self.service.refresh_catalogue()

        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), MAPPED_OFFERS)
        self.assertEqual(
            set(
                Offer.objects.filter(provider_content_id="beirut")
                .values_list("currently_available", flat=True)
            ),
            {True},
        )


class FailureTests(CBCGemRefreshTestCase):
    def test_fetch_failure_writes_nothing_at_all(self):
        self.api.failures["/category/"] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="cbc_gem").exists())

    def test_targeted_refresh_against_a_catalogue_source_fails_loudly(self):
        with self.assertRaises(ValueError):
            self.service.refresh_content(self.beirut)

    def test_unknown_scope_hint_is_an_error_not_a_silent_noop(self):
        with self.assertRaisesMessage(ValueError, "provider"):
            self.service.refresh_catalogue(provider="netflix")
        with self.assertRaisesMessage(ValueError, "country"):
            self.service.refresh_catalogue(country="US")

    def test_catalogue_refresh_requires_the_capability(self):
        from availability.sources.streaming_availability import (
            StreamingAvailabilitySource,
        )

        targeted = AvailabilityRefreshService(source=StreamingAvailabilitySource())
        with self.assertRaisesMessage(ValueError, "catalogue sync"):
            targeted.refresh_catalogue()


class CountryScopeTests(CBCGemRefreshTestCase):
    def test_disabled_country_skips_every_offer_but_still_maps_content(self):
        Country.objects.filter(code="CA").update(enabled=False)

        result = self.service.refresh_catalogue()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        # Mapping is country-independent: content identity still gets recorded.
        self.assertEqual(ContentMapping.objects.count(), 3)
