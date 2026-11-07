"""Attribution cardinality: which refresh requested which execution.

The bug this pins is not cosmetic. `RefreshRun` and `SyncRun` previously had no
link to `AvailabilityRefresh` at all, so attributing catalogue work meant
scraping worker logs and comparing clocks against the run's `started_at`. That
is unreliable precisely when it matters — several refreshes can be queued at
once, and one catalogue marker coalesces work requested by any number of them.

The relationship is therefore many-to-many, not a single `refresh_id`:

  * `request_catalogue_refresh` merges repeat requests into one pending marker,
    so two refreshes routinely share one marker and one `RefreshRun`;
  * one title-drain tick processes `RefreshRequest` rows from several batches
    and still writes exactly one `RefreshRun`.

A single nullable parent column would have had to pick one arbitrary owner —
either a false claim or a cross-attribution. These tests cover single parent,
multiple parents, the standalone case (no parent at all), and idempotency.
"""
from __future__ import annotations

import uuid

from django.test import TestCase

from availability.models import Source, SyncRun
from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    CatalogueRefreshAvailabilityRefresh,
    CatalogueRefreshRequest,
    Movie,
    Provider,
    RefreshRun,
    RefreshRunAvailabilityRefresh,
    SyncRunAvailabilityRefresh,
    WatchlistItem,
)
from tracker.services.refresh_attribution import (
    attribute_catalogue_marker,
    attribute_refresh_run,
    attribute_sync_runs,
    refreshes_for_marker,
    refreshes_for_refresh_run,
    refreshes_for_sync_run,
)
from tracker.services.refresh_requests import request_catalogue_refresh


class AttributionTestCase(TestCase):
    def make_refresh(self, *, batch=None) -> AvailabilityRefresh:
        return AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.QUEUED,
            batch=batch or uuid.uuid4(),
        )

    def make_sync_run(self, *, source_slug="justwatch", country=None):
        source, _ = Source.objects.get_or_create(
            slug=source_slug,
            defaults={"name": source_slug, "kind": "aggregator"},
        )
        provider, _ = Provider.objects.get_or_create(
            slug=f"{source_slug}-prov", defaults={"name": "P"}
        )
        return SyncRun.objects.create(
            source=source,
            provider=provider,
            mode="full",
            status="running",
            country=country,
        )

    def make_movie(self, tmdb_id=1, title="T"):
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=tmdb_id, defaults={"title": title}
        )
        return movie


class SingleRefreshTests(AttributionTestCase):
    """A. One refresh, one execution."""

    def test_one_refresh_links_to_one_refresh_run(self):
        refresh = self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")

        created = attribute_refresh_run(run, [refresh])

        self.assertEqual(created, 1)
        self.assertEqual(
            list(refresh.refresh_run_links.all()),
            [RefreshRunAvailabilityRefresh.objects.get(refresh_run=run,
                                                      availability_refresh=refresh)],
        )
        self.assertEqual(
            [r.pk for r in refreshes_for_refresh_run(run)], [refresh.pk]
        )


class ManyToManyCardinalityTests(AttributionTestCase):
    """B, C, D, G: the cardinality the single-parent design could not express."""

    def test_two_refreshes_share_one_refresh_run(self):
        a, b = self.make_refresh(), self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")

        attribute_refresh_run(run, [a, b])

        self.assertEqual(run.availability_refresh_links.count(), 2)
        self.assertEqual(
            sorted(r.pk for r in refreshes_for_refresh_run(run)),
            sorted([a.pk, b.pk]),
        )

    def test_two_refreshes_share_one_catalogue_marker(self):
        a, b = self.make_refresh(), self.make_refresh()
        marker = request_catalogue_refresh(["justwatch"], availability_refresh=a)
        # Second refresh asks for the same source while the marker is pending.
        marker_again = request_catalogue_refresh(
            ["justwatch"], availability_refresh=b
        )

        self.assertEqual(marker.pk, marker_again.pk)
        self.assertEqual(
            sorted(r.pk for r in refreshes_for_marker(marker)),
            sorted([a.pk, b.pk]),
        )

    def test_a_third_refresh_adds_without_replacing(self):
        a, b, c = self.make_refresh(), self.make_refresh(), self.make_refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=a)
        request_catalogue_refresh(["pbs"], availability_refresh=b)
        request_catalogue_refresh(["pbs"], availability_refresh=c)

        self.assertEqual(
            sorted(r.pk for r in refreshes_for_marker(marker)),
            sorted([a.pk, b.pk, c.pk]),
        )

    def test_coalesced_source_list_is_unchanged_by_attribution(self):
        """Adding an association must not alter how sources merge."""
        a, b = self.make_refresh(), self.make_refresh()
        marker = request_catalogue_refresh(["pbs", "tubi"], availability_refresh=a)
        marker = request_catalogue_refresh(["tubi", "justwatch"],
                                           availability_refresh=b)

        self.assertEqual(marker.sources, ["justwatch", "pbs", "tubi"])
        self.assertEqual(marker.refresh_links.count(), 2)

    def test_unrelated_refresh_does_not_receive_the_execution(self):
        """G. Cross-attribution must be impossible for a non-participant."""
        a, outsider = self.make_refresh(), self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")

        attribute_refresh_run(run, [a])

        self.assertNotIn(outsider.pk,
                         [r.pk for r in refreshes_for_refresh_run(run)])

    def test_several_sync_runs_share_the_same_refresh_set(self):
        """F. A partitioned walk emits many SyncRuns for one marker."""
        a, b = self.make_refresh(), self.make_refresh()
        runs = [self.make_sync_run() for _ in range(3)]

        attribute_sync_runs(runs, [a, b])

        for run in runs:
            self.assertEqual(
                sorted(r.pk for r in refreshes_for_sync_run(run)),
                sorted([a.pk, b.pk]),
            )


class StandaloneTests(AttributionTestCase):
    """E. Work no user refresh caused has no parent - and that is valid."""

    def test_standalone_refresh_run_has_no_links(self):
        run = RefreshRun.objects.create(service="justwatch")
        self.assertEqual(refreshes_for_refresh_run(run), [])

    def test_standalone_sync_run_has_no_links(self):
        run = self.make_sync_run()
        self.assertEqual(refreshes_for_sync_run(run), [])

    def test_marker_without_a_requester_stays_unattributed(self):
        marker = request_catalogue_refresh(["rai_play"])
        self.assertEqual(refreshes_for_marker(marker), [])

    def test_passing_none_is_ignored_rather_than_stored(self):
        refresh = self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")
        attribute_refresh_run(run, [refresh, None])
        self.assertEqual(run.availability_refresh_links.count(), 1)


class IdempotencyTests(AttributionTestCase):
    """H. Repeated propagation converges instead of duplicating."""

    def test_repeated_refresh_run_attribution_adds_one_link(self):
        refresh = self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")

        first = attribute_refresh_run(run, [refresh])
        second = attribute_refresh_run(run, [refresh])

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(run.availability_refresh_links.count(), 1)

    def test_duplicate_inputs_within_one_call_collapse(self):
        refresh = self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")

        created = attribute_refresh_run(run, [refresh, refresh, refresh.pk])

        self.assertEqual(created, 1)
        self.assertEqual(run.availability_refresh_links.count(), 1)

    def test_repeated_sync_run_attribution_is_stable(self):
        a, b = self.make_refresh(), self.make_refresh()
        runs = [self.make_sync_run(), self.make_sync_run()]

        attribute_sync_runs(runs, [a, b])
        created_again = attribute_sync_runs(runs, [a, b])

        self.assertEqual(created_again, 0)
        self.assertEqual(SyncRunAvailabilityRefresh.objects.count(), 4)

    def test_marker_association_is_idempotent(self):
        refresh = self.make_refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=refresh)

        created = attribute_catalogue_marker(marker, [refresh])

        self.assertEqual(created, 0)
        self.assertEqual(marker.refresh_links.count(), 1)


class ConcurrencyTests(AttributionTestCase):
    """K. Two refreshes queued before execution do not steal each other."""

    def test_each_batch_resolves_to_its_own_refresh(self):
        a, b = self.make_refresh(), self.make_refresh()
        self.make_movie(1)
        from tracker.models import RefreshRequest, RefreshRequestStatus

        RefreshRequest.objects.create(
            movie=self.make_movie(1), source="watchmode",
            status=RefreshRequestStatus.PENDING, batch=a.batch,
        )
        RefreshRequest.objects.create(
            movie=self.make_movie(2, "T2"), source="watchmode",
            status=RefreshRequestStatus.PENDING, batch=b.batch,
        )

        self.assertEqual(
            AvailabilityRefresh.objects.filter(batch=a.batch).count(), 1
        )
        self.assertEqual(
            AvailabilityRefresh.objects.filter(batch=b.batch).count(), 1
        )
        self.assertNotEqual(a.batch, b.batch)


class NoBehaviorChangeTests(AttributionTestCase):
    """L. Attribution is inert: it reads no runtime decision."""

    def test_no_availability_refresh_table_change_to_requests(self):
        """Creating links must not touch request rows or their statuses."""
        from tracker.models import RefreshRequest, RefreshRequestStatus

        refresh = self.make_refresh()
        movie = self.make_movie(1)
        request = RefreshRequest.objects.create(
            movie=movie, source="watchmode",
            status=RefreshRequestStatus.PENDING, batch=refresh.batch,
        )
        run = RefreshRun.objects.create(service="streaming_availability")
        before = RefreshRequest.objects.values("id", "status").order_by("id")

        attribute_refresh_run(run, [refresh])

        self.assertEqual(
            list(RefreshRequest.objects.values("id", "status").order_by("id")),
            list(before),
        )
        request.refresh_from_db()
        self.assertEqual(request.status, RefreshRequestStatus.PENDING)

    def test_link_tables_are_distinct_from_domain_tables(self):
        refresh = self.make_refresh()
        run = RefreshRun.objects.create(service="streaming_availability")
        attribute_refresh_run(run, [refresh])

        # The link exists...
        self.assertEqual(RefreshRunAvailabilityRefresh.objects.count(), 1)
        # ...and the executions themselves are untouched.
        self.assertEqual(RefreshRun.objects.count(), 1)
        self.assertIsNone(run.finished_at)

    def test_marker_link_does_not_change_marker_status_or_sources(self):
        refresh = self.make_refresh()
        marker = request_catalogue_refresh(["pbs"], availability_refresh=refresh)
        marker.refresh_from_db()

        self.assertEqual(marker.status, CatalogueRefreshRequest._meta.get_field(
            "status").default)
        self.assertEqual(marker.sources, ["pbs"])


class WatchlistEndpointTests(TestCase):
    """I, J: the explicit endpoint now persists a refresh; the other is unchanged."""

    def setUp(self):
        for tmdb_id in (900001, 900002):
            movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"T{tmdb_id}")
            WatchlistItem.objects.create(movie=movie, user_id=1)

    def test_explicit_endpoint_records_a_refresh_for_its_batch(self):
        from tracker.models import RefreshRequest

        response = self.client.post("/api/watchlist/refresh-explicit/", {},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 202)
        self.assertIn("refresh_id", response.data)

        refresh = AvailabilityRefresh.objects.get(pk=response.data["refresh_id"])
        self.assertEqual(refresh.run_type, AvailabilityRefreshType.WATCHLIST)
        # Every request the run queued carries its batch, so the run and its
        # work are joined by something other than a timestamp.
        batches = set(
            RefreshRequest.objects.filter(batch=refresh.batch)
            .values_list("batch", flat=True)
        )
        self.assertEqual(batches, {refresh.batch})

    def test_other_endpoint_still_records_exactly_one_refresh(self):
        before = AvailabilityRefresh.objects.count()
        response = self.client.post("/api/availability/refresh/", {},
                                    content_type="application/json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(AvailabilityRefresh.objects.count(), before + 1)
