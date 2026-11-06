"""A2.2.3 — lifecycle country scope and negative-result provenance."""
from __future__ import annotations

import uuid

from django.test import TestCase
from django.utils import timezone

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


class CountryScopedLifecycleTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=992_301, title="Scoped lifecycle", year=2025)
        self.batch = uuid.uuid4()

    def request(self, *, country, source="justwatch", status=RefreshRequestStatus.DONE, outcome=""):
        return RefreshRequest.objects.create(
            movie=self.movie,
            source=source,
            country=country,
            batch=self.batch,
            status=status,
            outcome=outcome,
            completed_at=timezone.now() if status != RefreshRequestStatus.PENDING else None,
        )

    def run(self, country):
        return AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            country=country,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movie.pk],
        )

    def test_completed_nl_request_cannot_settle_jp_run(self):
        self.request(country="NL")
        jp_run = self.run("JP")

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
        nl_source = next(row for row in nl["sources"] if row["source"] == "justwatch")
        jp_source = next(row for row in jp["sources"] if row["source"] == "justwatch")

        self.assertEqual(nl_source["reason"], "title_not_found")
        self.assertNotEqual(jp_source["reason"], "title_not_found")

    def test_historical_unscoped_negative_does_not_establish_regional_unavailability(self):
        self.request(
            country="", outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )

        entry = title_source_report([self.movie.pk], batch=self.batch, country="NL")[self.movie.pk]

        self.assertEqual(entry["state"], TitleAvailabilityState.UNKNOWN)
        self.assertNotIn("justwatch", entry["resolved_sources"])

    def test_country_scoped_progress_counts_only_its_requests(self):
        self.request(country="NL", source="justwatch")
        self.request(country="JP", source="watchmode")
        run = self.run("NL")

        sync_run_progress(run, [self.movie.pk])

        run.refresh_from_db()
        self.assertEqual(run.total_titles, 1)
        source = run.sources.get(source="justwatch")
        self.assertEqual(source.total, 1)
        self.assertFalse(run.sources.filter(source="watchmode").exists())
