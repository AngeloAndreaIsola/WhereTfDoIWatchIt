"""Watchlist "Refresh all availability" — bulk explicit enqueue + progress.

Covers the two endpoints behind the watchlist page's bulk refresh action:

- ``POST /api/watchlist/refresh-explicit/`` enqueues every watchlisted title
  at ``EXPLICIT`` priority in one local pass — enqueue-only (no provider
  I/O), deduplicated against pending event rows, freshness bypassed — and
  reports how many titles are queued. Each run owns a ``batch`` uuid stamped
  onto every request it touches, and stale catalogue-capable sources get a
  ``CatalogueRefreshRequest`` marker alongside the title queue;
- ``GET /api/watchlist/refresh-status/?since=<epoch>[&batch=<uuid>]`` reports
  that run's progress: pending/refreshed/failed counts scoped to the
  watchlist's default source scope — to completions at or after the run's
  start (legacy), or to the run's own batch — plus the shared daily quota
  (``rate_limited``/``quota_*``/``resets_at``);
- ``refresh_states_for`` derives the read-side evidence (pending/checked/
  failed/never_checked) the report turns into 🔵/⚫/⚪ badges, and
  ``stale_catalogue_sources``/``process_pending_catalogue_refresh`` drive
  the marker the worker runs after draining titles.

The drain itself is the existing worker path (already covered in
``test_event_driven_refresh``); one end-to-end test here asserts a bulk
enqueue is drained by ``process_refresh_queue`` and then reads back through
the status endpoint.

No test in this module makes live HTTP requests to any availability
provider.
"""
from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from availability.freshness import freshness_for
from availability.models import Source
from availability.sources import source_registry
from availability.tests.source_stubs import StubSource
from tracker.models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from tracker.services.bulk_refresh import (
    process_pending_catalogue_refresh,
    stale_catalogue_sources,
)
from tracker.services.refresh_requests import (
    process_refresh_queue,
    refresh_states_for,
    request_availability_refresh,
    request_catalogue_refresh,
    request_watchlist_refresh,
)
from tracker.services.watchability import RefreshState
from tracker.tests.availability_factories import local_offer
from tracker.tests.test_refresh_provider_catalogue import catalogue_slugs

REFRESH_CONTENT = (
    "availability.services.refresh.AvailabilityRefreshService.refresh_content"
)
REGISTRY = "tracker.services.bulk_refresh.source_registry"
CATALOGUE_RUN = "tracker.services.bulk_refresh.process_catalogue_refresh"


def _watchlist_movie(tmdb_id, title="Title", **kwargs):
    movie = Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2020, **kwargs)
    WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
    return movie


class BulkEnqueueServiceTests(TestCase):
    """request_watchlist_refresh: one local pass, per-title contracts intact."""

    def test_queues_every_watchlisted_title_at_explicit_priority(self):
        movie_a = _watchlist_movie(101)
        _watchlist_movie(102, content_type="series")
        local_offer(movie_a)  # fresh: if_stale=False must still enqueue

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("enqueue fetched")
            result = request_watchlist_refresh(user_id=1)

        refresh_content.assert_not_called()
        self.assertEqual(result["queued"], 2)
        self.assertEqual(result["total_watchlist"], 2)
        uuid.UUID(result["batch"])
        self.assertEqual(RefreshRequest.objects.count(), 2)
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.status, RefreshRequestStatus.PENDING)
            self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_titles_outside_the_watchlist_are_untouched(self):
        Movie.objects.create(tmdb_id=999, title="Not watched", year=1999)

        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["total_watchlist"], 0)
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_repeated_bulk_calls_stay_deduplicated_and_upgrade(self):
        movie = _watchlist_movie(103)
        local_offer(movie)
        request_availability_refresh(movie, priority=RefreshPriority.WATCHLIST)

        first = request_watchlist_refresh(user_id=1)
        second = request_watchlist_refresh(user_id=1)

        self.assertEqual(first["queued"], 1)
        self.assertEqual(second["queued"], 1)
        self.assertEqual(second["total_watchlist"], 1)
        self.assertNotEqual(first["batch"], second["batch"])
        self.assertEqual(RefreshRequest.objects.count(), 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)
        # Last click wins: the pending row now belongs to the newest run.
        self.assertEqual(request.batch, uuid.UUID(second["batch"]))

    def test_empty_watchlist_reports_zero(self):
        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["total_watchlist"], 0)
        uuid.UUID(result["batch"])


class BulkEnqueueEndpointTests(TestCase):
    """POST /api/watchlist/refresh-explicit/ — enqueue-only, answers 202."""

    def _post(self):
        return self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

    def test_queues_all_titles_without_provider_io(self):
        _watchlist_movie(201)
        local_offer(_watchlist_movie(202))

        with patch(REFRESH_CONTENT) as refresh_content:
            refresh_content.side_effect = AssertionError("endpoint fetched")
            resp = self._post()

        refresh_content.assert_not_called()
        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["queued"], 2)
        self.assertEqual(resp.data["total_watchlist"], 2)
        self.assertIsInstance(resp.data["now"], float)
        self.assertEqual(
            RefreshRequest.objects.filter(
                status=RefreshRequestStatus.PENDING,
                priority=RefreshPriority.EXPLICIT,
            ).count(),
            2,
        )

    def test_endpoint_upgrades_a_lower_priority_pending_request(self):
        movie = _watchlist_movie(203)
        local_offer(movie)
        existing = request_availability_refresh(
            movie, priority=RefreshPriority.WATCHLIST, if_stale=False
        )
        self.assertIsNotNone(existing)
        self.assertEqual(existing.priority, RefreshPriority.WATCHLIST)

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(RefreshRequest.objects.count(), 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.pk, existing.pk)
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_empty_watchlist_returns_202_with_zero_counts(self):
        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["queued"], 0)
        self.assertEqual(resp.data["total_watchlist"], 0)

    def test_total_failure_surfaces_instead_of_looking_queued(self):
        _watchlist_movie(204)

        with patch(
            "tracker.views.request_watchlist_refresh",
            return_value={"queued": 0, "total_watchlist": 1},
        ):
            resp = self._post()

        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", resp.data)


class RefreshStatusEndpointTests(TestCase):
    """GET /api/watchlist/refresh-status/ — progress scoped to one run."""

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()

    def _get(self, since=None):
        value = self.since if since is None else since
        return self.client.get(
            "/api/watchlist/refresh-status/", {"since": value}
        )

    def test_counts_pending_refreshed_failed_only_for_this_run(self):
        pending_movie = _watchlist_movie(301)
        refreshed_movie = _watchlist_movie(302)
        finished_before_run = _watchlist_movie(303)
        failed_movie = _watchlist_movie(304)
        non_watchlist = Movie.objects.create(tmdb_id=305, title="Other", year=2001)

        now = timezone.now()
        RefreshRequest.objects.create(
            movie=pending_movie, status=RefreshRequestStatus.PENDING
        )
        RefreshRequest.objects.create(
            movie=refreshed_movie,
            status=RefreshRequestStatus.DONE,
            completed_at=now,
        )
        RefreshRequest.objects.create(
            movie=finished_before_run,
            status=RefreshRequestStatus.DONE,
            completed_at=now - dt.timedelta(hours=2),
        )
        RefreshRequest.objects.create(
            movie=failed_movie,
            status=RefreshRequestStatus.FAILED,
            completed_at=now,
        )
        RefreshRequest.objects.create(
            movie=non_watchlist,
            status=RefreshRequestStatus.DONE,
            completed_at=now,
        )

        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["refreshed"], 1)
        self.assertEqual(resp.data["failed"], 1)

    def test_other_source_scopes_are_not_counted(self):
        movie = _watchlist_movie(306)
        RefreshRequest.objects.create(
            movie=movie,
            source="youtube",
            status=RefreshRequestStatus.PENDING,
        )

        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 0)

    def test_missing_or_malformed_since_is_rejected(self):
        self.assertEqual(
            self.client.get("/api/watchlist/refresh-status/").status_code, 400
        )
        self.assertEqual(
            self.client.get(
                "/api/watchlist/refresh-status/", {"since": "not-a-timestamp"}
            ).status_code,
            400,
        )


class BulkDrainEndToEndTests(TestCase):
    """Bulk enqueue -> worker drain -> status endpoint reads the progress back."""

    def test_worker_drains_bulk_queue_and_status_reports_it(self):
        movie_a = _watchlist_movie(401)
        movie_b = _watchlist_movie(402, content_type="series")
        local_offer(movie_a)  # fresh: EXPLICIT must still be refreshed

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 202, resp.content)
        since = resp.data["now"]

        before = self.client.get(
            "/api/watchlist/refresh-status/", {"since": since}
        )
        self.assertEqual(before.data["pending"], 2)
        self.assertEqual(before.data["refreshed"], 0)

        service = MagicMock()
        summary = process_refresh_queue(budget=5, service=service)

        self.assertEqual(summary["refreshed"], 2)
        refreshed_movies = {call.args[0] for call in service.refresh_content.call_args_list}
        self.assertEqual(refreshed_movies, {movie_a, movie_b})

        after = self.client.get(
            "/api/watchlist/refresh-status/", {"since": since}
        )
        self.assertEqual(after.data["pending"], 0)
        self.assertEqual(after.data["refreshed"], 2)
        self.assertEqual(after.data["failed"], 0)


class BatchStampingTests(TestCase):
    """One uuid per run, stamped onto every request the run touches."""

    def test_service_returns_a_batch_id_and_stamps_its_rows(self):
        _watchlist_movie(411)
        _watchlist_movie(412)

        result = request_watchlist_refresh(user_id=1)

        batch = uuid.UUID(result["batch"])
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.batch, batch)

    def test_endpoint_returns_the_batch_it_stamped(self):
        _watchlist_movie(413)

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 202, resp.content)
        batch = uuid.UUID(resp.data["batch"])
        self.assertEqual(
            set(
                RefreshRequest.objects.values_list("batch", flat=True)
            ),
            {batch},
        )

    def test_a_row_opened_by_another_event_joins_the_run(self):
        movie = _watchlist_movie(414)
        local_offer(movie)
        request_availability_refresh(
            movie, priority=RefreshPriority.WATCHLIST, if_stale=False
        )
        self.assertEqual(RefreshRequest.objects.get().batch, None)

        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(
            RefreshRequest.objects.get().batch, uuid.UUID(result["batch"])
        )

    def test_batch_scoped_status_reads_this_runs_progress_end_to_end(self):
        _watchlist_movie(415)
        _watchlist_movie(416)

        resp = self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )
        batch = resp.data["batch"]

        before = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(before.data["pending"], 2)
        self.assertEqual(before.data["refreshed"], 0)

        process_refresh_queue(budget=5, service=MagicMock())

        after = self.client.get(
            "/api/watchlist/refresh-status/",
            {"since": resp.data["now"], "batch": batch},
        )
        self.assertEqual(after.data["pending"], 0)
        self.assertEqual(after.data["refreshed"], 2)


class RefreshStatusBatchTests(TestCase):
    """batch= scopes every count to the run's own stamped backlog."""

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()
        self.batch = uuid.uuid4()

    def _get(self, **extra):
        params = {"since": self.since, **extra}
        return self.client.get("/api/watchlist/refresh-status/", params)

    def test_pending_counts_only_the_batches_own_rows(self):
        mine = _watchlist_movie(421)
        theirs = _watchlist_movie(422)
        RefreshRequest.objects.create(
            movie=mine,
            status=RefreshRequestStatus.PENDING,
            batch=self.batch,
        )
        RefreshRequest.objects.create(
            movie=theirs,
            status=RefreshRequestStatus.PENDING,
            batch=uuid.uuid4(),
        )

        resp = self._get(batch=str(self.batch))

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["pending"], 1)
        self.assertEqual(resp.data["batch"], str(self.batch))

    def test_batch_scoping_ignores_the_since_cutoff(self):
        movie = _watchlist_movie(423)
        RefreshRequest.objects.create(
            movie=movie,
            status=RefreshRequestStatus.DONE,
            completed_at=timezone.now() - dt.timedelta(hours=2),
            batch=self.batch,
        )

        legacy = self._get()
        scoped = self._get(batch=str(self.batch))

        self.assertEqual(legacy.data["refreshed"], 0)
        self.assertEqual(scoped.data["refreshed"], 1)

    def test_invalid_batch_is_rejected(self):
        resp = self._get(batch="not-a-uuid")

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("batch", resp.data["error"])


class RefreshStatusQuotaTests(TestCase):
    """The status endpoint carries the shared daily quota for the UI."""

    def setUp(self):
        self.since = (timezone.now() - dt.timedelta(seconds=60)).timestamp()

    def _get(self):
        return self.client.get(
            "/api/watchlist/refresh-status/", {"since": self.since}
        )

    def test_quota_fields_are_reported(self):
        resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["quota_limit"], 100)
        # Fresh day, no calls recorded: quota untouched, not rate-limited.
        self.assertEqual(resp.data["quota_remaining"], 100)
        self.assertFalse(resp.data["rate_limited"])
        self.assertGreater(resp.data["resets_at"], timezone.now().timestamp())
        # Rate-limited is exactly "nothing left today".
        self.assertEqual(
            resp.data["rate_limited"], resp.data["quota_remaining"] <= 0
        )

    def test_exhausted_quota_reports_rate_limited(self):
        with patch("tracker.views.RateLimiter") as limiter_cls:
            limiter_cls.return_value.remaining.return_value = 0
            limiter_cls.return_value.daily_limit = 100
            resp = self._get()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["rate_limited"])
        self.assertEqual(resp.data["quota_remaining"], 0)


class StaleCatalogueSourcesTests(TestCase):
    """stale_catalogue_sources: evidence is the last error-free sync per slug."""

    STUB = "stub_catalogue"

    def _stale(self):
        with patch(REGISTRY) as registry:
            registry.slugs.return_value = [self.STUB]
            registry.get.return_value.capabilities.supports_catalogue_sync = True
            return stale_catalogue_sources()

    def test_a_source_that_never_synced_reads_stale(self):
        self.assertEqual(self._stale(), [self.STUB])

    def test_a_recent_successful_run_is_fresh(self):
        RefreshRun.objects.create(
            service=self.STUB, error="", finished_at=timezone.now()
        )

        self.assertEqual(self._stale(), [])

    def test_a_failed_run_is_not_fresh_evidence(self):
        RefreshRun.objects.create(
            service=self.STUB, error="boom", finished_at=timezone.now()
        )

        self.assertEqual(self._stale(), [self.STUB])

    def test_a_run_older_than_the_window_reads_stale(self):
        RefreshRun.objects.create(
            service=self.STUB,
            error="",
            finished_at=timezone.now()
            - freshness_for(self.STUB)
            - dt.timedelta(hours=1),
        )

        self.assertEqual(self._stale(), [self.STUB])

    def test_disabled_sources_are_skipped(self):
        Source.objects.create(slug=self.STUB, name="Stub", enabled=False)

        self.assertEqual(self._stale(), [])


class CatalogueMarkerEnqueueTests(TestCase):
    """The button's enqueue side queues the catalogue marker alongside titles."""

    def _post(self):
        return self.client.post(
            "/api/watchlist/refresh-explicit/",
            data={},
            content_type="application/json",
        )

    def test_never_synced_catalogue_sources_are_queued(self):
        expected = stale_catalogue_sources()
        self.assertTrue(expected)

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["catalogue_queued"], expected)
        marker = CatalogueRefreshRequest.objects.get(
            status=CatalogueRefreshStatus.PENDING
        )
        self.assertEqual(marker.sources, expected)

    def test_fresh_catalogue_sources_are_not_queued(self):
        now = timezone.now()
        for slug in catalogue_slugs():
            RefreshRun.objects.create(
                service=slug, error="", finished_at=now
            )

        resp = self._post()

        self.assertEqual(resp.status_code, 202, resp.content)
        self.assertEqual(resp.data["catalogue_queued"], [])
        self.assertEqual(CatalogueRefreshRequest.objects.count(), 0)

    def test_repeated_clicks_collapse_into_one_pending_marker(self):
        first = request_catalogue_refresh(["src_b"])
        second = request_catalogue_refresh(["src_a", "src_b"])

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            CatalogueRefreshRequest.objects.filter(
                status=CatalogueRefreshStatus.PENDING
            ).count(),
            1,
        )
        self.assertEqual(second.sources, ["src_a", "src_b"])


class CatalogueMarkerWorkerTests(TestCase):
    """process_pending_catalogue_refresh: one marker, one pass over its slugs."""

    def test_no_pending_marker_returns_none(self):
        self.assertIsNone(process_pending_catalogue_refresh())

    def test_marker_runs_every_source_then_completes(self):
        marker = request_catalogue_refresh(["src_b", "src_a"])

        with patch(CATALOGUE_RUN) as run:
            run.return_value = {"errors": 0}
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            {call.kwargs["source_slug"] for call in run.call_args_list},
            {"src_a", "src_b"},
        )
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.DONE)
        self.assertIsNotNone(marker.completed_at)
        self.assertEqual(result["status"], CatalogueRefreshStatus.DONE)
        self.assertEqual(result["failed_sources"], [])

        # Consumed: the next tick sees nothing to do.
        self.assertIsNone(process_pending_catalogue_refresh())

    def test_a_failing_source_marks_the_marker_failed_and_continues(self):
        marker = request_catalogue_refresh(["src_a", "src_b"])

        with patch(CATALOGUE_RUN) as run:
            run.side_effect = [{"errors": 1}, {"errors": 0}]
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.FAILED)
        self.assertEqual(marker.last_error, "src_a")
        self.assertEqual(result["failed_sources"], ["src_a"])

    def test_an_exception_is_isolated_to_its_source(self):
        marker = request_catalogue_refresh(["src_a", "src_b"])

        with patch(CATALOGUE_RUN) as run:
            run.side_effect = [RuntimeError("source exploded"), {"errors": 0}]
            result = process_pending_catalogue_refresh()

        self.assertEqual(run.call_count, 2)
        marker.refresh_from_db()
        self.assertEqual(marker.status, CatalogueRefreshStatus.FAILED)
        self.assertEqual(result["failed_sources"], ["src_a"])


class RefreshStateEvidenceTests(TestCase):
    """refresh_states_for: the read side's pending/checked/failed/never map."""

    def test_states_are_derived_from_request_history(self):
        never = _watchlist_movie(431)
        pending = _watchlist_movie(432)
        checked = _watchlist_movie(433)
        failed = _watchlist_movie(434)
        request_availability_refresh(pending)
        RefreshRequest.objects.create(
            movie=checked, status=RefreshRequestStatus.DONE
        )
        RefreshRequest.objects.create(
            movie=failed, status=RefreshRequestStatus.FAILED
        )

        states = refresh_states_for(
            {never.id, pending.id, checked.id, failed.id}
        )

        self.assertEqual(states[never.id], RefreshState.NEVER_CHECKED)
        self.assertEqual(states[pending.id], RefreshState.PENDING)
        self.assertEqual(states[checked.id], RefreshState.CHECKED)
        self.assertEqual(states[failed.id], RefreshState.FAILED)

    def test_pending_beats_completed_evidence_while_queued(self):
        movie = _watchlist_movie(435)
        RefreshRequest.objects.create(
            movie=movie, status=RefreshRequestStatus.DONE
        )
        request_availability_refresh(movie, if_stale=False)

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.PENDING)

    def test_other_source_scopes_are_ignored(self):
        movie = _watchlist_movie(436)
        RefreshRequest.objects.create(
            movie=movie,
            source="youtube",
            status=RefreshRequestStatus.DONE,
        )

        states = refresh_states_for({movie.id})

        self.assertEqual(states[movie.id], RefreshState.NEVER_CHECKED)

    def test_report_payload_carries_the_refresh_state(self):
        queued = _watchlist_movie(437)
        never = _watchlist_movie(438)
        request_availability_refresh(queued)

        resp = self.client.get("/api/watchlist/report/")

        self.assertEqual(resp.status_code, 200, resp.content)
        by_tmdb = {
            row["movie"]["tmdb_id"]: row["watchability"]
            for row in resp.data["report"]
        }
        self.assertEqual(
            by_tmdb[queued.tmdb_id]["status"], "checking"
        )
        self.assertEqual(
            by_tmdb[queued.tmdb_id]["refresh_state"], RefreshState.PENDING
        )
        self.assertEqual(
            by_tmdb[never.tmdb_id]["status"], "unknown"
        )
        self.assertEqual(
            by_tmdb[never.tmdb_id]["refresh_state"],
            RefreshState.NEVER_CHECKED,
        )
