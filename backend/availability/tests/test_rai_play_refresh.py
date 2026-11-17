"""RaiPlay through AvailabilityRefreshService: mapping, persistence, semantics.

End-to-end path exercised here (fixture API, zero network):

    FakeRaiPlayApi -> RaiPlaySource.fetch() -> local title/year mapping ->
    OfferRepository -> availability.Offer + ProviderContent + ContentMapping
    + SyncRun

Fixture catalogue totals (see test_rai_play for the breakdown): 4 content
records, 3 offers. Local catalog holds Follemente (movie 2024), Normal People
(series 2020) and Settembre (movie 2022); ``Sul cammino di Francesco…`` is
deliberately absent so the unresolved-content contract is observable.

The last class covers Phase 12 §36: the same RaiPlay title observed through
Streaming Availability *and* through the dedicated collector stays two
distinguishable ``Offer`` rows under one ``Provider`` row.
"""
import datetime as dt

from django.test import TestCase

from availability.domain.enums import ContentType, MatchMethod, OfferType, SyncStatus
from availability.models import (
    ContentMapping,
    Country,
    Offer,
    ProviderContent,
    Source,
    SyncCursor,
    SyncRun,
)
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import RaiPlayHttpBudgetExceeded, SourceFetchError
from availability.sources.rai_play import RaiPlayClient, RaiPlayHttpBudget, RaiPlaySource
from availability.tests.source_stubs import (
    StubSource,
    fetch_result,
    normalized_offer,
)
from tracker.models import Movie, Provider, RefreshRun
from tracker.services.bulk_refresh import _ensure_declared_countries, _refresh_one_source

from .rai_play_fixtures import (
    FOLLEMENTE_ID,
    NORMAL_PEOPLE_ID,
    SETTEMBRE,
    SETTEMBRE_ID,
    SUL_CAMMINO_ID,
    FakeRaiPlayApi,
)

TOTAL_CONTENTS = 4
TOTAL_OFFERS = 3


class RaiPlayRefreshTestCase(TestCase):
    def setUp(self):
        self.api = FakeRaiPlayApi()
        self.source = RaiPlaySource(client=RaiPlayClient(requester=self.api))
        self.service = AvailabilityRefreshService(source=self.source)
        # The provider row is seeded by tracker migration 0011; a scheduled
        # run initializes the country from the declared capabilities.
        self.provider = Provider.objects.get(slug="raiplay")
        _ensure_declared_countries(self.source.slug, self.source.capabilities)

        self.follemente = Movie.objects.create(
            tmdb_id=9001, title="Follemente", year=2024, content_type="movie"
        )
        self.normal_people = Movie.objects.create(
            tmdb_id=9002, title="Normal People", year=2020, content_type="series"
        )
        self.settembre = Movie.objects.create(
            tmdb_id=9003, title="Settembre", year=2022, content_type="movie"
        )

    def _mapping(self, provider_content_id):
        return ContentMapping.objects.get(
            provider=self.provider, provider_content_id=provider_content_id
        )


class EndToEndRefreshTests(RaiPlayRefreshTestCase):
    def test_contents_and_offers_are_persisted(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(result.discovered, TOTAL_OFFERS)
        self.assertEqual(result.processed, TOTAL_OFFERS)
        self.assertEqual(result.created, TOTAL_OFFERS)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(result.unresolved, 0)
        self.assertEqual(result.deactivated, 0)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        self.assertEqual(
            set(Offer.objects.values_list("tmdb_id", flat=True)),
            {9001, 9002, 9003},
        )

    def test_identity_and_key_fields_are_canonical(self):
        self.service.refresh_catalogue()
        movie_offer = Offer.objects.get(
            content_type=ContentType.MOVIE, provider_content_id=FOLLEMENTE_ID
        )
        self.assertEqual(
            movie_offer.identity,
            f"raiplay|IT|movie|9001|||free|{FOLLEMENTE_ID}",
        )
        self.assertEqual(movie_offer.provider, self.provider)
        self.assertEqual(movie_offer.country.code, "IT")
        self.assertEqual(movie_offer.source.slug, "rai_play")
        self.assertEqual(movie_offer.offer_type, OfferType.FREE)
        self.assertEqual(movie_offer.quality, "")
        self.assertIsNone(movie_offer.audio_languages)
        self.assertEqual(movie_offer.subtitle_languages, [])
        self.assertEqual(
            movie_offer.available_from, dt.datetime(2026, 9, 16, tzinfo=dt.timezone.utc)
        )
        self.assertEqual(
            movie_offer.source_record_id,
            "ContentItem-05d711e6-e70a-401f-8dc4-982fa42e85ae",
        )
        self.assertEqual(movie_offer.source_version, "rai-play-ssr-v1")
        self.assertTrue(movie_offer.currently_available)

    def test_series_offer_survives_without_window_or_subtitle_metadata(self):
        self.service.refresh_catalogue()
        series_offer = Offer.objects.get(provider_content_id=NORMAL_PEOPLE_ID)
        self.assertEqual(series_offer.content_type, ContentType.SERIES)
        self.assertEqual(series_offer.tmdb_id, 9002)
        self.assertIsNone(series_offer.available_from)
        self.assertIsNone(series_offer.available_until)
        self.assertIsNone(series_offer.subtitle_languages)
        self.assertIsNone(series_offer.season_number)
        self.assertIsNone(series_offer.episode_number)

    def test_sync_run_lifecycle_and_counters(self):
        result = self.service.refresh_catalogue()

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country.code, "IT")
        self.assertEqual(run.source.slug, "rai_play")
        self.assertEqual(run.mode, "full")
        # RaiPlay never claims an authoritative snapshot.
        self.assertFalse(run.authoritative)
        self.assertEqual(run.items_discovered, TOTAL_OFFERS)
        self.assertEqual(run.items_processed, TOTAL_OFFERS)
        self.assertEqual(run.offers_created, TOTAL_OFFERS)
        self.assertEqual(run.offers_updated, 0)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(SyncRun.objects.count(), 1)

    def test_source_row_is_created_on_first_refresh(self):
        self.service.refresh_catalogue()
        source_row = Source.objects.get(slug="rai_play")
        self.assertEqual(source_row.name, "RaiPlay")
        self.assertTrue(source_row.enabled)

    def test_no_cursor_is_invented_for_a_sitemap_walk(self):
        self.service.refresh_catalogue()
        self.assertEqual(SyncCursor.objects.count(), 0)

    def test_the_seeded_provider_row_is_reused_not_duplicated(self):
        before = Provider.objects.filter(slug="raiplay").count()
        self.service.refresh_catalogue()
        self.assertEqual(before, 1)
        self.assertEqual(Provider.objects.filter(slug="raiplay").count(), 1)
        self.assertEqual(
            set(Offer.objects.values_list("provider_id", flat=True)),
            {self.provider.pk},
        )


class MappingTests(RaiPlayRefreshTestCase):
    def test_movies_map_by_title_and_year_and_series_by_title_alone(self):
        self.service.refresh_catalogue()

        self.assertEqual(ContentMapping.objects.count(), 3)
        # Exact title + production year resolved uniquely -> strongest tier.
        follemente = self._mapping(FOLLEMENTE_ID)
        self.assertEqual(follemente.tmdb_id, 9001)
        self.assertEqual(follemente.confidence, 0.9)
        self.assertEqual(follemente.match_method, MatchMethod.TITLE_YEAR)
        self.assertEqual(follemente.content_type, ContentType.MOVIE)
        # A series offer carries no release date, so only the title is matched.
        normal_people = self._mapping(NORMAL_PEOPLE_ID)
        self.assertEqual(normal_people.tmdb_id, 9002)
        self.assertEqual(normal_people.confidence, 0.7)
        self.assertEqual(normal_people.content_type, ContentType.SERIES)

    def test_content_without_a_local_match_keeps_its_provider_row(self):
        # Sul cammino has no offer at all (its video is gone) but its
        # catalogue row is still discovered; settembre has an offer that can
        # never resolve, which is the unresolved-offer contract.
        self.settembre.delete()

        with self.assertLogs("availability.services.refresh", level="WARNING") as logs:
            result = self.service.refresh_catalogue()

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.unresolved, 1)
        self.assertEqual(Offer.objects.count(), 2)
        self.assertFalse(
            ContentMapping.objects.filter(provider_content_id=SETTEMBRE_ID).exists()
        )
        self.assertFalse(
            Offer.objects.filter(provider_content_id=SETTEMBRE_ID).exists()
        )
        self.assertTrue(
            ProviderContent.objects.filter(provider_content_id=SETTEMBRE_ID).exists()
        )
        self.assertTrue(
            ProviderContent.objects.filter(provider_content_id=SUL_CAMMINO_ID).exists()
        )
        self.assertTrue(
            any("availability_mapping_unresolved" in line
                and SETTEMBRE_ID in line for line in logs.output)
        )

    def test_a_preexisting_manual_mapping_is_reused_and_never_overwritten(self):
        Source.objects.get_or_create(slug="rai_play", defaults={"name": "RaiPlay"})
        ContentMapping.objects.create(
            provider=self.provider,
            source=Source.objects.get(slug="rai_play"),
            provider_content_id=SETTEMBRE_ID,
            content_type=ContentType.MOVIE,
            tmdb_id=8888,
            confidence=1.0,
            match_method=MatchMethod.MANUAL,
        )

        self.service.refresh_catalogue()

        mapping = self._mapping(SETTEMBRE_ID)
        self.assertEqual(mapping.tmdb_id, 8888)
        self.assertEqual(mapping.match_method, MatchMethod.MANUAL)
        self.assertEqual(
            set(Offer.objects.filter(provider_content_id=SETTEMBRE_ID)
                .values_list("tmdb_id", flat=True)),
            {8888},
        )

    def test_second_run_reuses_mappings_and_is_idempotent(self):
        first = self.service.refresh_catalogue()
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, TOTAL_OFFERS)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        self.assertEqual(ContentMapping.objects.count(), 3)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(SyncRun.objects.count(), 2)
        self.assertEqual(SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 2)


class ObservationLifecycleTests(RaiPlayRefreshTestCase):
    def test_reobservation_moves_last_seen_and_never_first_seen(self):
        self.service.refresh_catalogue()
        content_before = ProviderContent.objects.get(
            provider_content_id=FOLLEMENTE_ID
        )
        offer_before = Offer.objects.get(provider_content_id=FOLLEMENTE_ID)

        self.service.refresh_catalogue()

        content_after = ProviderContent.objects.get(provider_content_id=FOLLEMENTE_ID)
        offer_after = Offer.objects.get(provider_content_id=FOLLEMENTE_ID)
        self.assertEqual(content_after.pk, content_before.pk)
        self.assertEqual(content_after.first_seen, content_before.first_seen)
        self.assertGreater(content_after.last_seen, content_before.last_seen)
        self.assertEqual(offer_after.pk, offer_before.pk)
        self.assertEqual(offer_after.first_seen, offer_before.first_seen)
        self.assertGreater(offer_after.last_seen, offer_before.last_seen)

    def test_absence_is_never_evidence_for_offers_or_contents(self):
        # Second fetch: settembre disappears from the catalogue entirely.
        self.service.refresh_catalogue()
        api = self.service.source.client.requester
        api.pages[SETTEMBRE] = None

        result = self.service.refresh_catalogue()

        self.assertEqual(result.deactivated, 0)
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        self.assertEqual(
            set(Offer.objects.filter(provider_content_id=SETTEMBRE_ID)
                .values_list("currently_available", flat=True)),
            {True},
        )
        content = ProviderContent.objects.get(provider_content_id=SETTEMBRE_ID)
        self.assertTrue(content.currently_available)

    def test_a_disabled_country_skips_every_offer_but_still_maps_content(self):
        Country.objects.filter(code="IT").update(enabled=False)

        result = self.service.refresh_catalogue()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, TOTAL_OFFERS)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        # Mapping is country-independent: content identity still gets recorded.
        self.assertEqual(ContentMapping.objects.count(), 3)


class MultiSourceTests(RaiPlayRefreshTestCase):
    """Phase 12 §36: one title, two sources, one provider."""

    def _refresh_streaming_availability(self, provider_content_id):
        source = StubSource(
            slug="streaming_availability",
            name="Streaming Availability",
            response=fetch_result(
                normalized_offer(
                    provider_id="raiplay",
                    provider_content_id=provider_content_id,
                    tmdb_id=9001,
                    country="IT",
                    content_type=ContentType.MOVIE,
                    offer_type=OfferType.FREE,
                )
            ),
        )
        return AvailabilityRefreshService(source=source).refresh_content(
            self.follemente
        )

    def test_the_same_title_from_two_sources_stays_distinguishable(self):
        first = self._refresh_streaming_availability("sa-88213")
        second = self.service.refresh_catalogue()

        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, TOTAL_OFFERS)
        # Both descriptions of the same movie survive as distinct rows.
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS + 1)

        by_source = {
            offer.source.slug: offer
            for offer in Offer.objects.filter(provider_content_id__in=[
                FOLLEMENTE_ID, "sa-88213"
            ]).select_related("source", "provider", "country")
        }
        self.assertEqual(set(by_source), {"streaming_availability", "rai_play"})
        for offer in by_source.values():
            self.assertEqual(offer.provider, self.provider)
            self.assertEqual(offer.country.code, "IT")
            self.assertEqual(offer.tmdb_id, 9001)
        self.assertNotEqual(
            by_source["streaming_availability"].provider_content_id,
            by_source["rai_play"].provider_content_id,
        )

    def test_one_provider_row_and_one_content_row_are_shared_across_sources(self):
        self._refresh_streaming_availability("sa-88213")
        self.service.refresh_catalogue()

        self.assertEqual(Provider.objects.filter(slug="raiplay").count(), 1)
        # A second source describing the same provider content shares the row.
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)

    def test_each_source_gets_its_own_source_row_and_sync_run(self):
        self._refresh_streaming_availability("sa-88213")
        self.service.refresh_catalogue()

        # Membership, not equality: the aggregator sources are seeded by
        # migration 0006 and are not part of what this test writes.
        written = set(Source.objects.values_list("slug", flat=True))
        self.assertTrue(
            {"streaming_availability", "rai_play"}.issubset(written))
        self.assertEqual(
            {run.source.slug for run in SyncRun.objects.select_related("source")},
            {"streaming_availability", "rai_play"},
        )

    def test_identical_provider_content_ids_converge_on_one_canonical_row(self):
        # Identity has no source slot: two sources reporting the same provider
        # content id describe the SAME provider content, so the last writer's
        # provenance follows and only one row exists.
        self._refresh_streaming_availability(FOLLEMENTE_ID)
        self.service.refresh_catalogue()

        matching = Offer.objects.filter(provider_content_id=FOLLEMENTE_ID)
        self.assertEqual(matching.count(), 1)
        self.assertEqual(matching.get().source.slug, "rai_play")


class FailureTests(RaiPlayRefreshTestCase):
    def test_fetch_failure_writes_nothing_at_all(self):
        self.api.failures["programmi.09.2026"] = SourceFetchError("network down")

        with self.assertRaises(SourceFetchError):
            self.service.refresh_catalogue()

        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(ContentMapping.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertFalse(Source.objects.filter(slug="rai_play").exists())

    def test_targeted_refresh_against_a_catalogue_source_fails_loudly(self):
        with self.assertRaisesMessage(ValueError, "catalogue-driven"):
            self.service.refresh_content(self.follemente)

    def test_unknown_scope_hint_is_an_error_not_a_silent_noop(self):
        with self.assertRaisesMessage(ValueError, "provider"):
            self.service.refresh_catalogue(provider="netflix")
        with self.assertRaisesMessage(ValueError, "country"):
            self.service.refresh_catalogue(country="US")

    def test_the_declared_country_is_initialized_from_capabilities(self):
        Country.objects.filter(code="IT").delete()
        with self.assertLogs("tracker.services.bulk_refresh", level="WARNING") as logs:
            _ensure_declared_countries(self.source.slug, self.source.capabilities)
        self.assertTrue(Country.objects.filter(code="IT").exists())
        self.assertIn(
            "catalogue_refresh_country_initialized source=rai_play country=IT",
            "\n".join(logs.output),
        )


class BudgetExhaustionLifecycleTests(RaiPlayRefreshTestCase):
    def test_sitemap_stage_exhaustion_preserves_database_and_cursor(self):
        # Seed an existing cursor that must be preserved exactly
        initial_cursor = '{"v":1,"parts":{"programmes":"https://www.raiplay.it/programmi/follemente"}}'
        italy = Country.objects.get(code="IT")
        source_row, _ = Source.objects.get_or_create(slug="rai_play", defaults={"name": "RaiPlay"})
        SyncCursor.objects.create(
            provider=self.provider,
            source=source_row,
            country=italy,
            cursor=initial_cursor,
        )

        # Budget exhaustion at sitemap stage
        def failing_requester(url):
            raise RaiPlayHttpBudgetExceeded(
                limit=1,
                attempted=1,
                blocked_url=url,
                stage="sitemap_index",
            )

        source = RaiPlaySource(client=RaiPlayClient(requester=failing_requester))
        result = _refresh_one_source("rai_play", source)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "budget_exhausted")
        self.assertEqual(result.error_reason, "budget_exhausted")
        self.assertEqual(result.requests_attempted, 1)
        self.assertEqual(result.requests_limit, 1)
        self.assertIn("sitemap_index", result.error)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

        # Cursor preserved byte-for-byte
        cursor_row = SyncCursor.objects.get(provider=self.provider, source=source_row, country=italy)
        self.assertEqual(cursor_row.cursor, initial_cursor)

        run = RefreshRun.objects.get(service="rai_play")
        self.assertIn("RaiPlayHttpBudgetExceeded", run.error)
        self.assertIsNotNone(run.finished_at)

    def test_programme_stage_exhaustion_preserves_database_and_cursor(self):
        initial_cursor = '{"v":1,"parts":{"programmes":"https://www.raiplay.it/programmi/follemente"}}'
        italy = Country.objects.get(code="IT")
        source_row, _ = Source.objects.get_or_create(slug="rai_play", defaults={"name": "RaiPlay"})
        SyncCursor.objects.create(
            provider=self.provider,
            source=source_row,
            country=italy,
            cursor=initial_cursor,
        )

        def failing_requester(url):
            if "programmi" in url and "sitemap" not in url:
                raise RaiPlayHttpBudgetExceeded(
                    limit=3,
                    attempted=3,
                    blocked_url=url,
                    stage="programme",
                )
            return self.api(url)

        source = RaiPlaySource(client=RaiPlayClient(requester=failing_requester))
        result = _refresh_one_source("rai_play", source)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "budget_exhausted")
        self.assertEqual(result.requests_attempted, 3)
        self.assertEqual(result.requests_limit, 3)
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

        cursor_row = SyncCursor.objects.get(provider=self.provider, source=source_row, country=italy)
        self.assertEqual(cursor_row.cursor, initial_cursor)

    def test_video_stage_exhaustion_preserves_database_and_cursor(self):
        initial_cursor = '{"v":1,"parts":{"programmes":"https://www.raiplay.it/programmi/follemente"}}'
        italy = Country.objects.get(code="IT")
        source_row, _ = Source.objects.get_or_create(slug="rai_play", defaults={"name": "RaiPlay"})
        SyncCursor.objects.create(
            provider=self.provider,
            source=source_row,
            country=italy,
            cursor=initial_cursor,
        )

        def failing_requester(url):
            if "video" in url:
                raise RaiPlayHttpBudgetExceeded(
                    limit=5,
                    attempted=5,
                    blocked_url=url,
                    stage="video",
                )
            return self.api(url)

        source = RaiPlaySource(client=RaiPlayClient(requester=failing_requester))
        result = _refresh_one_source("rai_play", source)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "budget_exhausted")
        self.assertEqual(ProviderContent.objects.count(), 0)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(SyncRun.objects.count(), 0)

        cursor_row = SyncCursor.objects.get(provider=self.provider, source=source_row, country=italy)
        self.assertEqual(cursor_row.cursor, initial_cursor)

    def test_legacy_offers_not_deactivated_on_exhaustion(self):
        # Create an existing legacy offer
        legacy_source = Source.objects.create(slug="streaming_availability", name="Streaming Availability")
        italy = Country.objects.get(code="IT")
        legacy_offer = Offer.objects.create(
            identity="raiplay|IT|movie|9001|||free|sa-legacy-1",
            provider=self.provider,
            source=legacy_source,
            country=italy,
            tmdb_id=9001,
            provider_content_id="sa-legacy-1",
            content_type=ContentType.MOVIE,
            offer_type=OfferType.FREE,
            currently_available=True,
        )

        def failing_requester(url):
            raise RaiPlayHttpBudgetExceeded(
                limit=1,
                attempted=1,
                blocked_url=url,
                stage="sitemap_index",
            )

        source = RaiPlaySource(client=RaiPlayClient(requester=failing_requester))
        result = _refresh_one_source("rai_play", source)

        self.assertEqual(result.status, "failed")
        legacy_offer.refresh_from_db()
        self.assertTrue(legacy_offer.currently_available)
        self.assertEqual(Offer.objects.count(), 1)

    def test_successful_replay_after_budget_exhaustion(self):
        call_count = [0]

        def flaky_requester(url):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RaiPlayHttpBudgetExceeded(
                    limit=1,
                    attempted=1,
                    blocked_url=url,
                    stage="sitemap",
                )
            return self.api(url)

        # First run: budget exhausted
        source1 = RaiPlaySource(client=RaiPlayClient(requester=flaky_requester))
        result1 = _refresh_one_source("rai_play", source1)
        self.assertEqual(result1.status, "failed")
        self.assertEqual(result1.reason, "budget_exhausted")
        self.assertEqual(Offer.objects.count(), 0)

        # Second run: sufficient budget, successful replay
        source2 = RaiPlaySource(client=RaiPlayClient(requester=self.api))
        result2 = _refresh_one_source("rai_play", source2)
        self.assertEqual(result2.status, "succeeded")
        self.assertEqual(Offer.objects.count(), TOTAL_OFFERS)
        self.assertEqual(ProviderContent.objects.count(), TOTAL_CONTENTS)
        self.assertEqual(SyncRun.objects.filter(status=SyncStatus.SUCCEEDED).count(), 1)
