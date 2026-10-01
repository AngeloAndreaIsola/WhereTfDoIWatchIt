"""Reconciled read path: query-service API shape, cost, and read hygiene.

The raw ``get_for_*`` boundary stays exactly as it was; the reconciled
``get_reconciled_*`` methods are what the HTTP path consumes. Both must read
from local rows only, in a fixed number of queries, and neither may write.
"""
from contextlib import ExitStack
from unittest.mock import patch

from django.test import TestCase

from availability.domain.enums import ContentType, OfferType, SyncStatus
from availability.services.query import AvailabilityQueryService

from .reconciliation_fixtures import ReconciliationFixtureMixin


class ReconciledQueryServiceTests(ReconciliationFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.service = AvailabilityQueryService()

    def test_movie_content_and_convenience_methods_agree(self):
        self.offer(provider_content_id="a")
        self.offer(provider_content_id="b", source=self.source_b)

        by_content = self.service.get_reconciled_for_content(550, ContentType.MOVIE, now=self.now)
        by_movie = self.service.get_reconciled_for_movie(550, now=self.now)

        self.assertEqual(len(by_content.offers), 1)
        self.assertEqual(by_content, by_movie)
        self.assertEqual(by_content.offers[0].sources, ("recon-source-a", "recon-source-b"))

    def test_series_query_reconciles_series_and_season_rows_for_one_season(self):
        self.offer(
            content_type=ContentType.SERIES, season_number=1, episode_number=1,
            provider_content_id="s1e1",
        )
        self.offer(
            content_type=ContentType.SEASON, season_number=1,
            provider_content_id="s1", offer_type=OfferType.FREE,
        )
        self.offer(
            content_type=ContentType.SERIES, season_number=2, episode_number=1,
            provider_content_id="s2e1",
        )

        reconciled = self.service.get_reconciled_for_series(550, 1, now=self.now)
        raw = list(self.service.get_for_series(550, 1))

        self.assertEqual(
            {(offer.content_type, offer.season_number) for offer in reconciled.offers},
            {(ContentType.SERIES, 1), (ContentType.SEASON, 1)},
        )
        self.assertEqual(len(raw), len(reconciled.offers))

    def test_raw_boundary_still_returns_raw_rows_with_their_own_filters(self):
        live = self.offer(provider_content_id="live")
        gone = self.offer(provider_content_id="gone", currently_available=False)

        raw = list(self.service.get_for_content(550, ContentType.MOVIE))
        reconciled = self.service.get_reconciled_for_content(550, ContentType.MOVIE, now=self.now)

        # Raw is the observation boundary: deactivated rows are hidden there,
        # and reconciliation is where they become visible again.
        self.assertEqual([row.pk for row in raw], [live.pk])
        self.assertEqual(
            [obs.offer_id for obs in reconciled.offers[0].observations],
            sorted([live.pk, gone.pk]),
        )
        self.assertEqual(len(reconciled.offers[0].observations), 2)

    def test_raw_and_reconciled_reads_never_write(self):
        self.offer(provider_content_id="a")
        self.offer(provider_content_id="b", source=self.source_b)
        before = {
            row.pk: (row.last_seen, row.currently_available, row.quality)
            for row in self.service.get_for_content(550, ContentType.MOVIE)
        }

        self.service.get_for_content(550, ContentType.MOVIE)
        self.service.get_reconciled_for_content(550, ContentType.MOVIE, now=self.now)

        after = {
            row.pk: (row.last_seen, row.currently_available, row.quality)
            for row in self.service.get_for_content(550, ContentType.MOVIE)
        }
        self.assertEqual(before, after)

    def test_reconcile_reads_in_a_fixed_number_of_queries(self):
        for index in range(5):
            self.offer(provider_content_id=f"pc-{index}")

        # Observations, then one bulk lookup each for mappings, cross-content
        # claims and sync runs: no per-offer query, whatever the row count.
        with self.assertNumQueries(4):
            result = self.service.get_reconciled_for_content(
                550, ContentType.MOVIE, country="us", now=self.now,
            )
        self.assertEqual(result.diagnostics.observations, 5)
        self.assertEqual(len(result.offers), 1)

        with self.assertNumQueries(4):
            again = self.service.get_reconciled_for_content(
                550, ContentType.MOVIE, country="us", now=self.now,
            )
        self.assertEqual(again, result)

    def test_reconciliation_reaches_no_network_and_no_refresh(self):
        self.offer(provider_content_id="pc")
        seams = [
            "requests.get",
            "requests.post",
            "requests.sessions.Session.request",
            "tracker.services.streaming_api.StreamingAvailabilityClient.get_availability",
            "tracker.services.cache.AvailabilityCache.get_or_refresh",
            "availability.services.refresh.AvailabilityRefreshService.refresh_content",
            "availability.services.refresh.AvailabilityRefreshService.refresh_catalogue",
        ]
        with ExitStack() as stack:
            mocks = []
            for seam in seams:
                mock = stack.enter_context(patch(seam))
                mock.side_effect = AssertionError(f"{seam} called by a reconciled read")
                mocks.append(mock)
            result = self.service.get_reconciled_for_content(
                550, ContentType.MOVIE, now=self.now,
            )
            self.service.reconciliation.audit(now=self.now)
        self.assertEqual(len(result.offers), 1)
        for mock in mocks:
            mock.assert_not_called()

    def test_reconciled_reads_tolerate_a_failed_latest_sync(self):
        self.offer(provider_content_id="pc")
        self.sync_run(status=SyncStatus.FAILED, country=self.us)

        result = self.service.get_reconciled_for_content(550, ContentType.MOVIE, now=self.now)

        self.assertEqual(len(result.offers), 1)
        self.assertEqual(
            result.offers[0].observations[0].sync_status, SyncStatus.FAILED,
        )
        self.assertEqual(result.diagnostics.failed_syncs, 1)
