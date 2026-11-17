"""AvailabilityRefreshService: persistence through the write boundary (§10-§12, §17-§18).

Counter semantics asserted here (per SyncRun = one provider+country group):
items_discovered == items_processed == offers in the group; offers_created +
offers_updated == processed; offers_deactivated is 0 for non-authoritative
sources and equals the number of rows deactivated for authoritative ones.
"""
import datetime as dt
from unittest.mock import MagicMock

import requests
from django.test import TestCase

from availability.domain.enums import (
    ContentType,
    OfferType,
    SyncMode,
    SyncStatus,
)
from availability.models import Country, Offer, Source, SyncCursor, SyncRun
from availability.persistence.repositories import InactiveSyncError, OfferRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import ContentRequest, SourceFetchError

from .source_stubs import StubSource, fetch_result as _response
from .source_stubs import normalized_offer as _normalized
from .test_source_streaming_availability import _offer as streaming_offer
from tracker.models import ExternalProviderMapping, Provider
from tracker.services.streaming_api import StreamingResult


class RefreshServiceTestCase(TestCase):
    def setUp(self):
        from tracker.models import Movie

        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.provider, _ = Provider.objects.get_or_create(
            slug="netflix", defaults={"name": "Netflix"}
        )
        self.country, _ = Country.objects.get_or_create(
            code="US", defaults={"name": "United States"}
        )
        self.source_row, _ = Source.objects.get_or_create(
            slug="stub_source", defaults={"name": "Stub Source"}
        )
        self.repo = OfferRepository()

    def _service(self, response=None, error=None, source=None, **kwargs):
        source = source or StubSource(response=response, error=error)
        return AvailabilityRefreshService(source=source, **kwargs), source

    def _existing_offer(self, provider_content_id="GONE", **overrides):
        offer, _created = self.repo.upsert(
            provider=self.provider,
            country=self.country,
            source=self.source_row,
            content_type=ContentType.MOVIE,
            tmdb_id=overrides.pop("tmdb_id", 999),
            offer_type=OfferType.SUBSCRIPTION,
            provider_content_id=provider_content_id,
            **overrides,
        )
        return offer


class RefreshPersistenceTests(RefreshServiceTestCase):
    def test_refresh_persists_normalized_offers(self):
        service, _source = self._service(_response(_normalized()))

        result = service.refresh_content(self.movie)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.processed, 1)
        offer = Offer.objects.get()
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.country, self.country)
        self.assertEqual(offer.source.slug, "stub_source")
        self.assertEqual(offer.content_type, ContentType.MOVIE)
        self.assertEqual(offer.tmdb_id, 550)
        self.assertEqual(offer.offer_type, OfferType.SUBSCRIPTION)
        self.assertTrue(offer.currently_available)
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertEqual(
            offer.identity, "netflix|US|movie|550|||subscription|550"
        )

    def test_same_normalized_offer_twice_creates_one_row(self):
        service, _source = self._service(_response(_normalized()))

        first = service.refresh_content(self.movie)
        second = service.refresh_content(self.movie)

        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.updated, 1)
        self.assertEqual(Offer.objects.count(), 1)

    def test_first_seen_is_preserved_and_last_seen_advances(self):
        service, _source = self._service(_response(_normalized()))
        service.refresh_content(self.movie)
        offer = Offer.objects.get()
        first_seen, last_seen = offer.first_seen, offer.last_seen

        # Simulate the offer having been last observed an hour ago.
        import datetime as dt

        Offer.objects.filter(pk=offer.pk).update(last_seen=last_seen - dt.timedelta(hours=1))
        service.refresh_content(self.movie)

        offer.refresh_from_db()
        self.assertEqual(offer.first_seen, first_seen)
        self.assertGreater(offer.last_seen, last_seen)
        self.assertTrue(offer.currently_available)

    def test_reappearing_inactive_offer_becomes_active_again(self):
        offer = self._existing_offer(provider_content_id="550", tmdb_id=550)
        Offer.objects.filter(pk=offer.pk).update(currently_available=False)

        service, _source = self._service(_response(_normalized()))
        service.refresh_content(self.movie)

        offer.refresh_from_db()
        self.assertTrue(offer.currently_available)
        self.assertEqual(Offer.objects.count(), 1)

    def test_different_country_creates_a_distinct_offer(self):
        Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
        service, _source = self._service(
            _response(_normalized(), _normalized(country="IT"))
        )

        service.refresh_content(self.movie)

        self.assertEqual(Offer.objects.count(), 2)
        self.assertEqual(
            set(Offer.objects.values_list("country__code", flat=True)), {"US", "IT"}
        )

    def test_different_season_creates_a_distinct_offer(self):
        from tracker.models import Movie

        series = Movie.objects.create(
            tmdb_id=1396, title="Breaking Bad", year=2008, content_type="series"
        )
        service, _source = self._service(
            _response(
                _normalized(content_type=ContentType.SERIES, tmdb_id=1396, provider_content_id="1396"),
                _normalized(content_type=ContentType.SEASON, tmdb_id=1396, season_number=1,
                            provider_content_id="1396:season:1"),
                _normalized(content_type=ContentType.SEASON, tmdb_id=1396, season_number=2,
                            provider_content_id="1396:season:2"),
            )
        )

        service.refresh_content(series)

        self.assertEqual(Offer.objects.count(), 3)
        self.assertEqual(
            set(Offer.objects.values_list("season_number", flat=True)), {None, 1, 2}
        )

    def test_different_offer_type_creates_a_distinct_offer(self):
        # Same content, same provider content id — only the offer type differs.
        service, _source = self._service(
            _response(_normalized(), _normalized(offer_type=OfferType.FREE))
        )

        service.refresh_content(self.movie)

        self.assertEqual(Offer.objects.count(), 2)
        self.assertEqual(
            set(Offer.objects.values_list("offer_type", flat=True)),
            {OfferType.SUBSCRIPTION, OfferType.FREE},
        )

    def test_unknown_language_is_persisted_as_null_and_empty_as_empty(self):
        service, _source = self._service(
            _response(
                _normalized(provider_content_id="a", audio_languages=None, subtitle_languages=None),
                _normalized(provider_content_id="b", audio_languages=[], subtitle_languages=[]),
            )
        )

        service.refresh_content(self.movie)

        null_row = Offer.objects.get(provider_content_id="a")
        empty_row = Offer.objects.get(provider_content_id="b")
        self.assertIsNone(null_row.audio_languages)
        self.assertIsNone(null_row.subtitle_languages)
        self.assertEqual(empty_row.audio_languages, [])
        self.assertEqual(empty_row.subtitle_languages, [])


class ProviderAndCountryResolutionTests(RefreshServiceTestCase):
    def test_provider_resolves_by_slug(self):
        service, _source = self._service(_response(_normalized(provider_id="netflix")))

        service.refresh_content(self.movie)

        self.assertEqual(Offer.objects.get().provider, self.provider)

    def test_provider_resolves_through_external_mapping(self):
        mapped, _ = Provider.objects.get_or_create(
            slug="canal-plus-fr", defaults={"name": "Canal+"}
        )
        ExternalProviderMapping.objects.create(
            provider=mapped, integration_name="stub_source", external_id="canalplus"
        )
        service, _source = self._service(
            _response(_normalized(provider_id="canalplus", provider_content_id="c1"))
        )

        service.refresh_content(self.movie)

        self.assertEqual(Offer.objects.get().provider, mapped)

    def test_unsupported_provider_is_skipped_not_created(self):
        service, _source = self._service(
            _response(_normalized(provider_id="unknown-service"))
        )

        result = service.refresh_content(self.movie)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.created, 0)
        self.assertFalse(Provider.objects.filter(slug="unknown-service").exists())
        self.assertEqual(Offer.objects.count(), 0)
        self.assertEqual(Source.objects.filter(slug="stub_source").count(), 1)

    def test_unsupported_country_is_skipped_not_created(self):
        service, _source = self._service(_response(_normalized(country="ZZ")))

        result = service.refresh_content(self.movie)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(Offer.objects.count(), 0)
        self.assertFalse(Country.objects.filter(code="ZZ").exists())

    def test_missing_tmdb_is_skipped(self):
        service, _source = self._service(_response(_normalized(tmdb_id=None)))

        result = service.refresh_content(self.movie)

        self.assertEqual(result.skipped, 1)
        self.assertEqual(Offer.objects.count(), 0)


class SyncRunLifecycleTests(RefreshServiceTestCase):
    def test_successful_refresh_creates_a_succeeded_sync_run(self):
        service, _source = self._service(_response(_normalized()))

        result = service.refresh_content(self.movie)

        self.assertEqual(len(result.sync_runs), 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.status, SyncStatus.SUCCEEDED)
        self.assertEqual(run.provider, self.provider)
        self.assertEqual(run.country, self.country)
        self.assertEqual(run.source.slug, "stub_source")
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.completed_at)
        self.assertFalse(run.authoritative)

    def test_sync_counters_are_correct(self):
        self._existing_offer(provider_content_id="EXISTS-OLD", tmdb_id=550)
        service, _source = self._service(
            _response(
                _normalized(provider_content_id="NEW1"),
                _normalized(provider_content_id="EXISTS-OLD"),
            )
        )

        result = service.refresh_content(self.movie)

        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertEqual(run.items_discovered, 2)
        self.assertEqual(run.items_processed, 2)
        self.assertEqual(run.offers_created, 1)
        self.assertEqual(run.offers_updated, 1)
        self.assertEqual(run.offers_deactivated, 0)
        self.assertEqual(run.error_count, 0)
        self.assertEqual(run.last_error, "")
        self.assertEqual(result.discovered, 2)
        self.assertEqual(result.created, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.deactivated, 0)

    def test_successful_authoritative_refresh_deactivates_unseen_offers(self):
        unseen = self._existing_offer(provider_content_id="GONE", tmdb_id=777)
        service, _source = self._service(
            _response(
                _normalized(), _normalized(provider_content_id="NEW"),
                authoritative=True, complete_snapshot=True,
            )
        )

        result = service.refresh_content(self.movie)

        unseen.refresh_from_db()
        self.assertFalse(unseen.currently_available)
        self.assertEqual(result.deactivated, 1)
        run = result.sync_runs[0]
        run.refresh_from_db()
        self.assertTrue(run.authoritative)
        self.assertEqual(run.offers_deactivated, 1)
        # The newly observed offer stays active.
        self.assertTrue(
            Offer.objects.get(provider_content_id="NEW").currently_available
        )

    def test_successful_non_authoritative_refresh_does_not_deactivate(self):
        unseen = self._existing_offer(provider_content_id="GONE", tmdb_id=777)
        service, _source = self._service(
            _response(_normalized(), authoritative=False)
        )

        result = service.refresh_content(self.movie)

        unseen.refresh_from_db()
        self.assertTrue(unseen.currently_available)
        self.assertEqual(result.deactivated, 0)

    def test_authoritative_deactivation_requires_succeeded_status(self):
        # Repository-level guard: even an authoritative run must not deactivate
        # unless it has succeeded (defensive; the service marks first).
        run = None
        from availability.persistence.repositories import SyncRunRepository
        from availability.domain.enums import SyncMode

        runs = SyncRunRepository()
        run = runs.start(
            provider=self.provider,
            source=self.source_row,
            country=self.country,
            mode=SyncMode.FULL,
            authoritative=True,
        )
        self._existing_offer(provider_content_id="KEEP", tmdb_id=550)
        with self.assertRaises(InactiveSyncError):
            self.repo.deactivate_unseen(run, set())

    def test_fetch_failure_does_not_deactivate_and_creates_no_sync_run(self):
        existing = self._existing_offer(provider_content_id="KEEP", tmdb_id=550)
        before = existing.last_seen
        service, _source = self._service(error=requests.HTTPError("timeout"))

        with self.assertRaises(requests.HTTPError):
            service.refresh_content(self.movie)

        existing.refresh_from_db()
        self.assertTrue(existing.currently_available)
        self.assertEqual(existing.last_seen, before)
        self.assertEqual(Offer.objects.count(), 1)
        from availability.models import SyncRun

        self.assertEqual(SyncRun.objects.count(), 0)

    def test_failed_persistence_rolls_back_and_marks_sync_run_failed(self):
        existing = self._existing_offer(provider_content_id="KEEP", tmdb_id=550)
        before = existing.last_seen
        # A valid offer plus a malformed one: the upsert of the second raises,
        # so the first write must roll back with the refresh unit.
        service, _source = self._service(
            _response(
                _normalized(provider_content_id="WILL-ROLLBACK"),
                _normalized(content_type="bogus_type", provider_content_id="BAD"),
            )
        )

        with self.assertRaises(ValueError):
            service.refresh_content(self.movie)

        from availability.models import SyncRun

        run = SyncRun.objects.get()
        self.assertEqual(run.status, SyncStatus.FAILED)
        self.assertGreaterEqual(run.error_count, 1)
        self.assertIn("bogus_type", run.last_error)
        self.assertFalse(Offer.objects.filter(provider_content_id="WILL-ROLLBACK").exists())
        existing.refresh_from_db()
        self.assertTrue(existing.currently_available)
        self.assertEqual(existing.last_seen, before)


class AuthoritativeSemanticsTests(RefreshServiceTestCase):
    """§9: only an authoritative, complete, non-incremental result deactivates."""

    def _unseen(self):
        return self._existing_offer(provider_content_id="GONE", tmdb_id=777)

    def _run_of(self, result):
        run = result.sync_runs[0]
        run.refresh_from_db()
        return run

    def test_authoritative_claim_without_complete_snapshot_does_not_deactivate(self):
        unseen = self._unseen()
        service, _source = self._service(
            _response(_normalized(), authoritative=True, complete_snapshot=False)
        )

        result = service.refresh_content(self.movie)

        unseen.refresh_from_db()
        self.assertTrue(unseen.currently_available)
        self.assertEqual(result.deactivated, 0)
        self.assertFalse(self._run_of(result).authoritative)

    def test_incremental_result_never_deactivates_unseen_offers(self):
        unseen = self._unseen()
        service, _source = self._service(
            _response(
                _normalized(),
                authoritative=True,
                complete_snapshot=True,
                sync_mode=SyncMode.INCREMENTAL,
            )
        )

        result = service.refresh_content(self.movie)

        unseen.refresh_from_db()
        self.assertTrue(unseen.currently_available)
        self.assertEqual(result.deactivated, 0)
        run = self._run_of(result)
        self.assertEqual(run.mode, SyncMode.INCREMENTAL)
        self.assertFalse(run.authoritative)

    def test_capability_gates_authoritativeness_not_the_source_name(self):
        from availability.sources.base import SourceCapabilities

        unseen = self._unseen()
        source = StubSource(
            response=_response(_normalized(), authoritative=True, complete_snapshot=True),
            capabilities=SourceCapabilities(supports_authoritative_snapshots=False),
        )
        service, _source = self._service(source=source)

        result = service.refresh_content(self.movie)

        unseen.refresh_from_db()
        self.assertTrue(unseen.currently_available)
        self.assertEqual(result.deactivated, 0)
        self.assertFalse(self._run_of(result).authoritative)

    def test_sync_run_mode_comes_from_the_result_not_a_hardcode(self):
        service, _source = self._service(_response(_normalized(), sync_mode=SyncMode.FULL))
        result = service.refresh_content(self.movie)
        self.assertEqual(self._run_of(result).mode, SyncMode.FULL)

        service, _source = self._service(_response(_normalized()))
        result = service.refresh_content(self.movie)
        self.assertEqual(self._run_of(result).mode, SyncMode.TARGETED)


class FetchOutcomeTests(RefreshServiceTestCase):
    """§12: a failed fetch and a successful empty result are different things."""

    def test_empty_successful_result_is_not_a_failure(self):
        service, source = self._service(_response())

        result = service.refresh_content(self.movie)

        self.assertEqual(result.discovered, 0)
        self.assertEqual(result.created, 0)
        self.assertEqual(Offer.objects.count(), 0)
        # No provider+country group exists, so no SyncRun lifecycle is started.
        self.assertEqual(SyncRun.objects.count(), 0)
        self.assertEqual(len(source.fetches), 1)

    def test_source_fetch_error_propagates_without_creating_a_sync_run(self):
        existing = self._existing_offer(provider_content_id="KEEP", tmdb_id=550)
        before = existing.last_seen
        service, _source = self._service(error=SourceFetchError("upstream 500"))

        with self.assertRaises(SourceFetchError):
            service.refresh_content(self.movie)

        existing.refresh_from_db()
        self.assertTrue(existing.currently_available)
        self.assertEqual(existing.last_seen, before)
        self.assertEqual(SyncRun.objects.count(), 0)

    def test_refresh_passes_a_persistence_free_content_request(self):
        service, source = self._service(_response(_normalized()))

        service.refresh_content(self.movie)

        request = source.fetches[0]
        self.assertIsInstance(request, ContentRequest)
        self.assertEqual(request.tmdb_id, 550)
        self.assertEqual(request.content_type, "movie")
        self.assertEqual(request.title, "Fight Club")
        self.assertEqual(request.year, 1999)
        self.assertEqual(request.seasons_data, [])


class CapabilitySkipTests(RefreshServiceTestCase):
    def test_offers_outside_declared_content_types_are_skipped(self):
        from availability.sources.base import SourceCapabilities

        source = StubSource(
            response=_response(
                _normalized(provider_content_id="M"),
                _normalized(
                    content_type=ContentType.SERIES,
                    tmdb_id=550,
                    provider_content_id="S",
                ),
            ),
            capabilities=SourceCapabilities(
                supported_content_types=frozenset({ContentType.MOVIE}),
            ),
        )
        service, _source = self._service(source=source)

        result = service.refresh_content(self.movie)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(Offer.objects.count(), 1)
        self.assertTrue(Offer.objects.filter(provider_content_id="M").exists())


class CursorPersistenceTests(RefreshServiceTestCase):
    def test_source_cursor_is_persisted_per_group(self):
        service, _source = self._service(_response(_normalized(), cursor="page-2"))

        service.refresh_content(self.movie)

        cursor = SyncCursor.objects.get()
        self.assertEqual(cursor.provider, self.provider)
        self.assertEqual(cursor.country, self.country)
        self.assertEqual(cursor.source.slug, "stub_source")
        self.assertEqual(cursor.cursor, "page-2")
        # Persisting a checkpoint records progress, not completion. This
        # previously asserted ``last_successful_sync``, which conflated the two
        # and let a partial run look like a finished traversal.
        self.assertIsNotNone(cursor.progress_at)
        self.assertIsNone(cursor.last_successful_sync)

    def test_results_without_a_cursor_write_no_cursor_rows(self):
        service, _source = self._service(_response(_normalized()))

        service.refresh_content(self.movie)

        self.assertEqual(SyncCursor.objects.count(), 0)

    def test_failed_refresh_persists_no_cursor(self):
        service, _source = self._service(
            _response(
                _normalized(provider_content_id="OK"),
                _normalized(content_type="bogus_type", provider_content_id="BAD"),
                cursor="page-2",
            )
        )

        with self.assertRaises(ValueError):
            service.refresh_content(self.movie)

        self.assertEqual(SyncCursor.objects.count(), 0)

    def test_a_zero_offer_catalogue_batch_still_persists_its_checkpoint(self):
        from availability.sources.base import SourceCapabilities

        Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        source = StubSource(
            response=_response(cursor='{"v":1,"cycle":0,"queues":{}}'),
            capabilities=SourceCapabilities(
                supported_providers=frozenset({"tubi"}),
                supported_countries=frozenset({"US"}),
                supports_catalogue_sync=True,
            ),
        )
        service = AvailabilityRefreshService(source=source)

        result = service.refresh_catalogue()

        self.assertEqual(result.processed, 0)
        cursor = SyncCursor.objects.get()
        self.assertEqual(cursor.provider.slug, "tubi")
        self.assertEqual(cursor.country.code, "US")
        self.assertEqual(cursor.cursor, '{"v":1,"cycle":0,"queues":{}}')

    def test_a_catalogue_refresh_resumes_from_the_stored_checkpoint(self):
        from availability.sources.base import SourceCapabilities

        Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        source = StubSource(
            response=_response(cursor="checkpoint-2"),
            capabilities=SourceCapabilities(
                supported_providers=frozenset({"tubi"}),
                supported_countries=frozenset({"US"}),
                supports_catalogue_sync=True,
            ),
        )
        service = AvailabilityRefreshService(source=source)

        service.refresh_catalogue()
        service.refresh_catalogue()

        self.assertEqual(source.fetch_calls[0]["cursor"], None)
        self.assertEqual(source.fetch_calls[1]["cursor"], "checkpoint-2")

    def test_a_failed_catalogue_batch_persists_no_checkpoint(self):
        from availability.sources.base import SourceCapabilities

        Provider.objects.get_or_create(slug="tubi", defaults={"name": "Tubi"})
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        source = StubSource(
            response=_response(
                _normalized(provider_content_id="OK"),
                _normalized(content_type="bogus_type", provider_content_id="BAD"),
                cursor="checkpoint-2",
            ),
            capabilities=SourceCapabilities(
                supported_providers=frozenset({"tubi"}),
                supported_countries=frozenset({"US"}),
                supports_catalogue_sync=True,
            ),
        )
        service = AvailabilityRefreshService(source=source)

        with self.assertRaises(ValueError):
            service.refresh_catalogue()

        self.assertEqual(SyncCursor.objects.count(), 0)


class RealSourceIntegrationTests(TestCase):
    """AvailabilityRefreshService + StreamingAvailabilitySource (mocked client)."""

    def setUp(self):
        from tracker.models import Movie

        self.movie = Movie.objects.create(tmdb_id=100, title="Se7en", year=1995)
        self.provider, _ = Provider.objects.get_or_create(
            slug="prime", defaults={"name": "Prime Video"}
        )
        Country.objects.get_or_create(code="US", defaults={"name": "United States"})
        self.client_mock = MagicMock()
        self.client_mock.get_availability.return_value = StreamingResult(
            tmdb_id=100,
            offers=[
                streaming_offer(
                    country="us",
                    service_id="prime",
                    audio_languages=["eng"],
                    subtitle_languages=["eng"],
                )
            ],
        )
        from availability.sources.streaming_availability import StreamingAvailabilitySource

        source = StreamingAvailabilitySource(client=self.client_mock)
        self.service = AvailabilityRefreshService(source=source)

    def test_end_to_end_write_path(self):
        result = self.service.refresh_content(self.movie)

        self.assertEqual(result.created, 1)
        offer = Offer.objects.get()
        self.assertEqual(offer.provider, self.provider)
        self.assertEqual(offer.country.code, "US")
        self.assertEqual(offer.source.slug, "streaming_availability")
        self.assertEqual(offer.tmdb_id, 100)
        self.assertEqual(offer.audio_languages, ["EN"])
        self.assertFalse(result.sync_runs[0].authoritative)
        result.sync_runs[0].refresh_from_db()
        self.assertEqual(result.sync_runs[0].status, SyncStatus.SUCCEEDED)
