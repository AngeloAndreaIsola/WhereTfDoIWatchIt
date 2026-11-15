"""AvailabilitySource interface, registry, and source-result semantics (Phase 4).

Covers §2-§6 (interface, normalized results, capabilities, registry) and the
§13 source-interface/registry/normalization requirements for the reference
implementation. Sources return persistence-free values only; the database
``Source`` row is created by the refresh service, never by the registry.
"""
from unittest.mock import MagicMock

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType, SourceType, SyncMode
from availability.domain.models import ProviderOffer
from availability.models import Source
from availability.services.normalize import normalize_content_type
from availability.sources import (
    DEFAULT_SOURCE_SLUG,
    AvailabilitySource,
    ContentRequest,
    SourceCapabilities,
    SourceFetchError,
    SourceFetchResult,
    SourceRegistry,
    StreamingAvailabilitySource,
    UnknownSourceError,
    source_registry,
)
from tracker.models import Movie
from tracker.services.streaming_api import StreamingOffer, StreamingResult

from .source_stubs import StubSource, content_request


def _offer(country="us", service_id="netflix", offer_type="subscription", **kwargs):
    kwargs.setdefault("service_name", service_id.title())
    kwargs.setdefault("quality", "4K")
    kwargs.setdefault("link", "https://example.test/watch")
    kwargs.setdefault("audio_languages", ["en"])
    kwargs.setdefault("subtitle_languages", ["en"])
    return StreamingOffer(country=country, service_id=service_id, offer_type=offer_type, **kwargs)


class SourceRegistryTests(TestCase):
    def test_streaming_availability_source_is_registered(self):
        source = source_registry.get("streaming_availability")

        self.assertIsInstance(source, StreamingAvailabilitySource)
        self.assertEqual(source.slug, "streaming_availability")

    def test_default_source_slug_is_registered(self):
        self.assertIn(DEFAULT_SOURCE_SLUG, source_registry.slugs())

    def test_registry_resolves_code_without_database_rows(self):
        # The DB Source row only identifies provenance; instantiating the
        # implementation must never depend on data.
        self.assertFalse(Source.objects.filter(slug="streaming_availability").exists())

        source = source_registry.get("streaming_availability")

        self.assertIsInstance(source, AvailabilitySource)
        self.assertFalse(Source.objects.filter(slug="streaming_availability").exists())

    def test_unknown_slug_raises_a_useful_error(self):
        with self.assertRaises(UnknownSourceError) as ctx:
            source_registry.get("not_a_registered_source")

        message = str(ctx.exception)
        self.assertIn("not_a_registered_source", message)
        self.assertIn("streaming_availability", message)

    def test_duplicate_registration_is_rejected(self):
        registry = SourceRegistry()
        registry.register("stub_source", StubSource)

        with self.assertRaises(ValueError):
            registry.register("stub_source", StubSource)

    def test_registry_get_returns_fresh_instances(self):
        first = source_registry.get("streaming_availability")
        second = source_registry.get("streaming_availability")

        self.assertIsNot(first, second)


class SourceInterfaceConformanceTests(TestCase):
    def setUp(self):
        self.client_mock = MagicMock()

    def test_streaming_availability_conforms_to_the_interface(self):
        source = StreamingAvailabilitySource(client=self.client_mock)

        self.assertIsInstance(source, AvailabilitySource)
        self.assertIsInstance(source.capabilities, SourceCapabilities)
        self.assertEqual(source.source_type, SourceType.API)
        self.assertEqual(source.name, "Streaming Availability API")

    def test_stub_source_conforms_to_the_interface(self):
        self.assertIsInstance(StubSource(), AvailabilitySource)

    def test_non_source_objects_do_not_conform(self):
        class NotASource:
            slug = "nope"

        self.assertNotIsInstance(NotASource(), AvailabilitySource)
        self.assertNotIsInstance(object(), AvailabilitySource)

    def test_fetch_returns_a_source_fetch_result_of_normalized_offers(self):
        movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)
        self.client_mock.get_availability.return_value = StreamingResult(
            tmdb_id=550, offers=[_offer()]
        )
        source = StreamingAvailabilitySource(client=self.client_mock)

        result = source.fetch(ContentRequest.from_content(movie))

        self.assertIsInstance(result, SourceFetchResult)
        self.assertIsInstance(result.offers[0], ProviderOffer)

    def test_content_request_is_built_without_touching_persistence(self):
        movie = Movie.objects.create(
            tmdb_id=1396,
            title="Breaking Bad",
            year=2008,
            content_type="series",
            seasons_data=[{"season_number": 1, "name": "Season 1"}],
        )

        request = ContentRequest.from_content(movie)

        self.assertIsInstance(request, ContentRequest)
        self.assertEqual(request.tmdb_id, 1396)
        self.assertEqual(request.content_type, "series")
        self.assertEqual(request.title, "Breaking Bad")
        self.assertEqual(request.year, 2008)
        self.assertEqual(request.seasons_data, [{"season_number": 1, "name": "Season 1"}])
        self.assertFalse(hasattr(request, "pk"))

    def test_source_fetch_error_exists_for_failure_vs_empty_distinction(self):
        # §12: retrieval failure raises; an empty result is a normal return.
        self.assertTrue(issubclass(SourceFetchError, Exception))
        empty = SourceFetchResult()
        self.assertEqual(empty.offers, [])
        self.assertFalse(empty.authoritative)


class StreamingAvailabilityCapabilitiesTests(TestCase):
    def test_declares_its_scope_and_limits(self):
        caps = StreamingAvailabilitySource.capabilities

        self.assertEqual(
            caps.supported_content_types,
            frozenset({ContentType.MOVIE, ContentType.SERIES, ContentType.SEASON}),
        )
        self.assertTrue(caps.supports_seasons)
        self.assertFalse(caps.supports_episodes)
        self.assertTrue(caps.supports_tmdb_ids)
        self.assertTrue(caps.supports_languages)
        self.assertFalse(caps.supports_catalogue_sync)
        self.assertFalse(caps.supports_incremental_sync)
        self.assertFalse(caps.supports_authoritative_snapshots)


class SourceResultSemanticsTests(TestCase):
    """§9/§13: what every source result must communicate to the refresh layer."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=550, title="Fight Club", year=1999)

    def _source(self, result):
        client = MagicMock()
        client.get_availability.return_value = result
        return StreamingAvailabilitySource(client=client), client

    def test_sync_semantics_defaults_are_conservative(self):
        source, _client = self._source(StreamingResult(tmdb_id=550, offers=[_offer()]))

        result = source.fetch(ContentRequest.from_content(self.movie))

        self.assertFalse(result.authoritative)
        self.assertFalse(result.complete_snapshot)
        self.assertEqual(result.sync_mode, SyncMode.TARGETED)
        self.assertIsNone(result.cursor)

    def test_full_offer_type_matrix_normalizes(self):
        types = ["subscription", "free", "avod", "rent", "buy"]
        result_payload = StreamingResult(
            tmdb_id=550,
            offers=[_offer(service_id=f"svc{i}", offer_type=t) for i, t in enumerate(types)],
        )
        source, _client = self._source(result_payload)

        result = source.fetch(self.movie)

        self.assertEqual(
            [o.offer_type for o in result.offers],
            [
                OfferType.SUBSCRIPTION,
                OfferType.FREE,
                OfferType.FREE_WITH_ADS,
                OfferType.RENT,
                OfferType.BUY,
            ],
        )

    def test_provider_content_ids_are_deterministic(self):
        result_payload = StreamingResult(tmdb_id=550, offers=[_offer()])
        source, _client = self._source(result_payload)

        first = source.fetch(self.movie)
        second = source.fetch(self.movie)

        self.assertEqual(
            [o.provider_content_id for o in first.offers],
            [o.provider_content_id for o in second.offers],
        )
        self.assertEqual(
            [o.source_record_id for o in first.offers],
            [o.source_record_id for o in second.offers],
        )

    def test_source_provenance_travels_on_the_result(self):
        source, _client = self._source(StreamingResult(tmdb_id=550, offers=[_offer()]))

        result = source.fetch(self.movie)

        offer = result.offers[0]
        self.assertEqual(offer.source_record_id, "netflix:US:movie::subscription")
        self.assertEqual(offer.source_version, "v4")
        # Contents carry the source id for future ContentMapping seeding.
        self.assertEqual(result.contents[0].source_id, "streaming_availability")
        self.assertEqual(result.contents[0].external_ids.get("tmdb"), "550")

    def test_episode_stays_distinct_from_season_at_the_canonical_layer(self):
        self.assertEqual(normalize_content_type("episode"), ContentType.EPISODE)
        self.assertNotEqual(ContentType.EPISODE, ContentType.SEASON)

    def test_fetch_requires_targeted_content(self):
        source, _client = self._source(StreamingResult(tmdb_id=550, offers=[]))

        with self.assertRaises(ValueError):
            source.fetch(None)

    def test_scope_hints_are_accepted_for_interface_compatibility(self):
        source, client = self._source(StreamingResult(tmdb_id=550, offers=[_offer()]))

        result = source.fetch(
            content_request(),
            provider="netflix",
            country="us",
            cursor=None,
        )

        client.get_availability.assert_called_once_with(550, content_type="movie")
        self.assertEqual(len(result.offers), 1)
