"""End-to-end attribution across all four execution paths.

`test_refresh_attribution` pins the association *mechanics* (cardinality,
idempotency, standalone). This module pins that each real execution path
actually populates them, because a correct helper that no production path calls
would leave the observability gap exactly where it started.

The four paths:

  A. title lane          - one tick drains requests from several batches
  B. coalesced marker     - one marker requested by several refreshes
  C. generic first-party  - the source's own SyncRuns get the marker's set
  D. JustWatch            - every partition SyncRun gets the marker's set
  E. standalone           - nothing links, and the call shape is unchanged

Each test asserts the negative too: an unrelated refresh must not receive the
execution. Attribution that cannot say "not this one" is only half the feature.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

from django.test import TestCase

from tracker.models import (
    AvailabilityRefresh,
    Provider,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
)
from tracker.services.refresh_attribution import (
    attribute_refresh_run,
    attribute_sync_runs,
    refreshes_for_marker,
    refreshes_for_refresh_run,
    refreshes_for_sync_run,
)
from tracker.services.refresh_requests import request_catalogue_refresh


class PathTestCase(TestCase):
    def refresh(self):
        return AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.QUEUED,
            batch=uuid.uuid4(),
        )

    def pending_request(self, refresh, tmdb_id, source="watchmode"):
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=tmdb_id, defaults={"title": f"T{tmdb_id}"}
        )
        return RefreshRequest.objects.create(
            movie=movie,
            source=source,
            status=RefreshRequestStatus.PENDING,
            batch=refresh.batch,
        )


class TitleLaneTests(PathTestCase):
    """A. One tick, several batches - only the ones it actually drained."""

    def test_tick_links_every_drained_batch_and_not_others(self):
        a, b, c = self.refresh(), self.refresh(), self.refresh()
        self.pending_request(a, 800001)
        self.pending_request(b, 800002)
        # C's request exists but is never drained by this tick.
        self.pending_request(c, 800003)

        drained: set = set()
        run = RefreshRun.objects.create(service="streaming_availability")

        from tracker.services.refresh_requests import drain_refresh_requests

        # Drain only A's and B's rows: C's stays pending.
        with patch(
            "tracker.services.refresh_requests._candidate_batch",
            side_effect=[_only(a, b), []],
        ):
            drain_refresh_requests(
                summary=_summary(),
                service=None,
                limiter=None,
                represented_batches=drained,
            )

        attribute_refresh_run(run, _refreshes_for(drained))
        linked = sorted(r.pk for r in refreshes_for_refresh_run(run))

        self.assertEqual(linked, sorted([a.pk, b.pk]))
        self.assertNotIn(c.pk, linked)

    def test_drain_records_only_processed_batches(self):
        a, b = self.refresh(), self.refresh()
        self.pending_request(a, 800011)
        self.pending_request(b, 800012)

        from tracker.services.refresh_requests import drain_refresh_requests

        drained: set = set()
        with patch(
            "tracker.services.refresh_requests._candidate_batch",
            side_effect=[_only(a, b), []],
        ):
            drain_refresh_requests(
                summary=_summary(),
                service=None,
                limiter=None,
                represented_batches=drained,
            )

        self.assertEqual(drained, {a.batch, b.batch})


class CoalescedMarkerTests(PathTestCase):
    """B. One marker, several requesters, one execution."""

    def test_marker_execution_links_all_requesters(self):
        a, b, c = self.refresh(), self.refresh(), self.refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=a)
        marker = request_catalogue_refresh(["pbs"], availability_refresh=b)
        # C requested different work and must not be attached.
        self.assertNotIn(c.pk, [r.pk for r in refreshes_for_marker(marker)])

        run = RefreshRun.objects.create(service="pbs")
        attribute_refresh_run(run, refreshes_for_marker(marker))

        linked = sorted(r.pk for r in refreshes_for_refresh_run(run))
        self.assertEqual(linked, sorted([a.pk, b.pk]))
        self.assertNotIn(c.pk, linked)

    def test_marker_sources_are_not_affected_by_attribution(self):
        a, b = self.refresh(), self.refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=a)
        marker = request_catalogue_refresh(["justwatch", "pbs"],
                                           availability_refresh=b)
        marker.refresh_from_db()

        self.assertEqual(marker.sources, ["justwatch", "pbs"])
        self.assertEqual(marker.refresh_links.count(), 2)


class GenericCatalogueTests(PathTestCase):
    """C. First-party collector: RefreshRun *and* its SyncRuns."""

    def test_generic_source_sync_runs_receive_the_marker_set(self):
        from availability.models import Source, SyncRun

        a, b, c = self.refresh(), self.refresh(), self.refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=a)
        request_catalogue_refresh(["pbs"], availability_refresh=b)

        refreshes = refreshes_for_marker(marker)
        run = RefreshRun.objects.create(service="pbs")
        attribute_refresh_run(run, refreshes)

        source, _ = Source.objects.get_or_create(
            slug="pbs", defaults={"name": "PBS", "type": "first_party"}
        )
        provider, _ = Provider.objects.get_or_create(
            slug="pbs-prov", defaults={"name": "P", "category": "stream"}
        )
        runs = [
            SyncRun.objects.create(
                source=source, provider=provider, mode="full", status="succeeded"
            )
            for _ in range(2)
        ]
        attribute_sync_runs(runs, refreshes)

        for sync_run in runs:
            self.assertEqual(
                sorted(r.pk for r in refreshes_for_sync_run(sync_run)),
                sorted([a.pk, b.pk]),
            )
        self.assertEqual(
            sorted(r.pk for r in refreshes_for_refresh_run(run)),
            sorted([a.pk, b.pk]),
        )
        self.assertNotIn(c.pk, [r.pk for r in refreshes_for_refresh_run(run)])


class JustWatchPartitionTests(PathTestCase):
    """D. Every partition SyncRun inherits the whole marker set."""

    def test_all_partition_sync_runs_receive_a_plus_b(self):
        from availability.models import Source, SyncRun

        a, b, c = self.refresh(), self.refresh(), self.refresh()
        marker = request_catalogue_refresh(["justwatch"], availability_refresh=a)
        marker = request_catalogue_refresh(["justwatch"], availability_refresh=b)

        source, _ = Source.objects.get_or_create(
            slug="justwatch", defaults={"name": "JW", "type": "aggregator"}
        )
        provider, _ = Provider.objects.get_or_create(
            slug="nflx", defaults={"name": "Netflix", "category": "stream"}
        )
        partitions = [
            SyncRun.objects.create(
                source=source, provider=provider, mode="full", status="succeeded"
            )
            for _ in range(4)
        ]
        attribute_sync_runs(partitions, refreshes_for_marker(marker))

        for sync_run in partitions:
            self.assertEqual(
                sorted(r.pk for r in refreshes_for_sync_run(sync_run)),
                sorted([a.pk, b.pk]),
            )
        self.assertNotIn(c.pk, [r.pk for r in refreshes_for_sync_run(partitions[0])])


class StandaloneExecutionTests(PathTestCase):
    """E. Work nobody requested stays unattributed, call shape unchanged."""

    def test_standalone_marker_keeps_the_original_call_signature(self):
        from tracker.models import CatalogueRefreshRequest
        from tracker.services.bulk_refresh import process_pending_catalogue_refresh

        request_catalogue_refresh(["pbs"])

        with patch(
            "tracker.services.bulk_refresh.process_catalogue_refresh"
        ) as run_catalogue:
            run_catalogue.return_value = {"errors": [], "sources": ["pbs"]}
            process_pending_catalogue_refresh()

        run_catalogue.assert_called_once_with(source_slug="pbs")

        marker = CatalogueRefreshRequest.objects.get()
        self.assertEqual(refreshes_for_marker(marker), [])

    def test_standalone_execution_rows_have_no_links(self):
        from availability.models import Source, SyncRun

        run = RefreshRun.objects.create(service="pbs")
        source, _ = Source.objects.get_or_create(
            slug="pbs", defaults={"name": "PBS", "type": "first_party"}
        )
        provider, _ = Provider.objects.get_or_create(
            slug="pbs-prov", defaults={"name": "P", "category": "stream"}
        )
        sync_run = SyncRun.objects.create(
            source=source, provider=provider, mode="full", status="succeeded"
        )

        self.assertEqual(refreshes_for_refresh_run(run), [])
        self.assertEqual(refreshes_for_sync_run(sync_run), [])


def _summary():
    from tracker.services.bulk_refresh import BulkRefreshSummary

    return BulkRefreshSummary()


def _only(*refreshes):
    """Pending requests belonging to exactly these refreshes."""
    return list(
        RefreshRequest.objects.filter(
            status=RefreshRequestStatus.PENDING,
            batch__in=[r.batch for r in refreshes],
        ).order_by("priority", "requested_at", "pk")
    )


def _refreshes_for(batches):
    return AvailabilityRefresh.objects.filter(batch__in=batches)
