"""Country-aware refresh freshness regressions at the tracker boundary."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from django.test import TestCase

from availability.domain.enums import ContentType
from availability.sources.streaming_availability import StreamingAvailabilityClient, StreamingAvailabilitySource
from tracker.models import Movie
from tracker.services.bulk_refresh import is_stale
from tracker.tests.availability_factories import local_offer
from tracker.services.streaming_api import StreamingResult, StreamingOffer, UpstreamTitleNotFound


class CountryAwareRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._socket_patcher = patch("socket.socket", side_effect=RuntimeError("Network access forbidden in tests"))
        cls._socket_patcher.start()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        try:
            cls._socket_patcher.stop()
        except Exception:
            pass

    def setUp(self):
        self.movie, _ = Movie.objects.get_or_create(
            tmdb_id=9201, title="Country Scope", year=2024, defaults={"content_type": ContentType.MOVIE}
        )
        self.series_collision = Movie(
            tmdb_id=9201,
            title="Country Scope Series",
            year=2024,
            content_type=ContentType.SERIES,
        )

    def test_fresh_us_evidence_does_not_suppress_nl_refresh(self):
        local_offer(self.movie, country="US")

        self.assertFalse(is_stale(self.movie, country="US"))
        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_fresh_nl_evidence_satisfies_nl_refresh(self):
        local_offer(self.movie, country="NL")

        self.assertFalse(is_stale(self.movie, country="NL"))

    def test_recent_jp_evidence_does_not_suppress_nl_refresh(self):
        local_offer(self.movie, country="JP")

        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_movie_series_identity_collision_cannot_cross_contaminate(self):
        local_offer(self.movie, country="NL")

        self.assertFalse(is_stale(self.movie, country="NL"))
        self.assertTrue(is_stale(self.series_collision, country="NL"))

    def test_global_behavior_is_only_used_when_country_is_omitted(self):
        local_offer(self.movie, country="US")

        self.assertFalse(is_stale(self.movie))
        self.assertTrue(is_stale(self.movie, country="NL"))

    def test_upstream_refresh_boundary_deterministic_results(self):
        """Mock upstream refresh calls at actual client/service boundary with deterministic results."""
        client = MagicMock(spec=StreamingAvailabilityClient)
        source = StreamingAvailabilitySource(client=client)

        # 1. NL positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9201, offers=[StreamingOffer(country="NL", service_id="netflix", service_name="Netflix", offer_type="subscription")]
        )
        self.assertEqual(len(source.fetch(self.movie).offers), 1)

        # 2. US-only
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9201, offers=[StreamingOffer(country="US", service_id="hulu", service_name="Hulu", offer_type="subscription")]
        )
        self.assertEqual(source.fetch(self.movie).offers[0].country, "US")

        # 3. JP positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=9201, offers=[StreamingOffer(country="JP", service_id="prime", service_name="Prime", offer_type="subscription")]
        )
        self.assertEqual(source.fetch(self.movie).offers[0].country, "JP")

        # 4. No offer
        client.get_availability.return_value = StreamingResult(tmdb_id=9201, offers=[])
        self.assertEqual(source.fetch(self.movie).offers, [])

        # 5. TITLE_NOT_FOUND
        client.get_availability.side_effect = UpstreamTitleNotFound("not found")
        with self.assertRaises(UpstreamTitleNotFound):
            source.fetch(self.movie)

        # 6. Transport failure
        client.get_availability.side_effect = TimeoutError("transport failure")
        with self.assertRaises(TimeoutError):
            source.fetch(self.movie)

        # 7. Quota exhaustion
        client.get_availability.side_effect = RuntimeError("quota exhaustion")
        with self.assertRaises(RuntimeError):
            source.fetch(self.movie)

        # 8. Legacy unknown
        client.get_availability.side_effect = None
        client.get_availability.return_value = StreamingResult(tmdb_id=9201, offers=[])
        res = source.fetch(self.movie)
        self.assertIsNotNone(res)
