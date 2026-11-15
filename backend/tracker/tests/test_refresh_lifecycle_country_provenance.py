"""A2.2.3 — lifecycle country scope and negative-result provenance."""
from __future__ import annotations

import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone

from availability.models import Country
from availability.sources.streaming_availability import StreamingAvailabilityClient, StreamingAvailabilitySource
from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    Movie,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
)
from tracker.services.availability_report import (
    TitleAvailabilityState,
    sync_run_progress,
    title_source_report,
)
from tracker.services.refresh_lifecycle import AvailabilityRefreshLifecycle
from tracker.services.streaming_api import StreamingResult, StreamingOffer, UpstreamTitleNotFound


class CountryScopedLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls._socket_patcher = patch("socket.socket", side_effect=RuntimeError("Network access forbidden in tests"))
        cls._socket_patcher.start()
        for code in ("US", "NL", "JP", "GB"):
            Country.objects.get_or_create(code=code, defaults={"name": code})

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        try:
            cls._socket_patcher.stop()
        except Exception:
            pass

    def setUp(self):
        self.movie, _ = Movie.objects.get_or_create(
            tmdb_id=992_301, defaults={"title": "Scoped lifecycle", "year": 2025}
        )
        self.batch = uuid.uuid4()

    def request(self, *, country, source="streaming_availability", status=RefreshRequestStatus.DONE, outcome=""):
        return RefreshRequest.objects.create(
            movie=self.movie,
            source=source,
            country=country,
            batch=self.batch,
            status=status,
            outcome=outcome,
            completed_at=timezone.now() if status != RefreshRequestStatus.PENDING else None,
        )

    def create_run(self, country):
        return AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            country=country,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movie.pk],
        )

    def test_completed_nl_request_cannot_settle_jp_run(self):
        self.request(country="NL", status=RefreshRequestStatus.PENDING)
        jp_run = self.create_run("JP")

        AvailabilityRefreshLifecycle.finalise(jp_run)

        jp_run.refresh_from_db()
        self.assertEqual(jp_run.status, AvailabilityRefreshStatus.RUNNING)
        self.assertEqual(jp_run.total_titles, 0)

    def test_known_country_negative_only_answers_that_market(self):
        self.request(
            country="NL", outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )

        nl = title_source_report([self.movie.pk], batch=self.batch, country="NL")[self.movie.pk]
        jp = title_source_report([self.movie.pk], batch=self.batch, country="JP")[self.movie.pk]
        nl_source = next(row for row in nl["sources"] if row["source"] == "streaming_availability")
        jp_source = next(row for row in jp["sources"] if row["source"] == "streaming_availability")

        self.assertEqual(nl_source["reason"], "title_not_found")
        self.assertNotEqual(jp_source["reason"], "title_not_found")

    def test_historical_unscoped_negative_does_not_establish_regional_unavailability(self):
        self.request(
            country="", outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )

        entry = title_source_report([self.movie.pk], batch=self.batch, country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], TitleAvailabilityState.UNKNOWN)
        self.assertNotIn("streaming_availability", entry["resolved_sources"])

    def test_country_scoped_progress_counts_only_its_requests(self):
        self.request(country="NL", source="streaming_availability")
        self.request(country="JP", source="youtube")
        run = self.create_run("NL")

        sync_run_progress(run, [self.movie.pk])

        run.refresh_from_db()
        self.assertEqual(run.total_titles, 1)
        source = run.sources.get(source="streaming_availability")
        self.assertEqual(source.total, 1)
        self.assertEqual(run.sources.get(source="youtube").total, 0)

    def test_upstream_client_deterministic_outcomes(self):
        """Mock upstream refresh calls at actual client/service boundary with deterministic results."""
        client = MagicMock(spec=StreamingAvailabilityClient)
        source = StreamingAvailabilitySource(client=client)

        # 1. NL positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=992_301, offers=[StreamingOffer(country="NL", service_id="netflix", service_name="Netflix", offer_type="subscription")]
        )
        self.assertEqual(len(source.fetch(self.movie).offers), 1)

        # 2. US-only
        client.get_availability.return_value = StreamingResult(
            tmdb_id=992_301, offers=[StreamingOffer(country="US", service_id="hulu", service_name="Hulu", offer_type="subscription")]
        )
        self.assertEqual(source.fetch(self.movie).offers[0].country, "US")

        # 3. JP positive
        client.get_availability.return_value = StreamingResult(
            tmdb_id=992_301, offers=[StreamingOffer(country="JP", service_id="prime", service_name="Prime", offer_type="subscription")]
        )
        self.assertEqual(source.fetch(self.movie).offers[0].country, "JP")

        # 4. No offer
        client.get_availability.return_value = StreamingResult(tmdb_id=992_301, offers=[])
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
        client.get_availability.return_value = StreamingResult(tmdb_id=992_301, offers=[])
        res = source.fetch(self.movie)
        self.assertIsNotNone(res)
