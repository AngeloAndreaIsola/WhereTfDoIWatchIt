"""Watchlist "Refresh all availability" — bulk explicit enqueue + progress.

Covers the two endpoints behind the watchlist page's bulk refresh action:

- ``POST /api/watchlist/refresh-explicit/`` enqueues every watchlisted title
  at ``EXPLICIT`` priority in one local pass — enqueue-only (no provider
  I/O), deduplicated against pending event rows, freshness bypassed — and
  reports how many titles are queued;
- ``GET /api/watchlist/refresh-status/?since=<epoch>`` reports that run's
  progress: pending/refreshed/failed counts scoped to the watchlist's
  default source scope and to completions at or after the run's start.

The drain itself is the existing worker path (already covered in
``test_event_driven_refresh``); one end-to-end test here asserts a bulk
enqueue is drained by ``process_refresh_queue`` and then reads back through
the status endpoint.

No test in this module makes live HTTP requests to any availability
provider.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.refresh_requests import (
    process_refresh_queue,
    request_availability_refresh,
    request_watchlist_refresh,
)
from tracker.tests.availability_factories import local_offer

REFRESH_CONTENT = (
    "availability.services.refresh.AvailabilityRefreshService.refresh_content"
)


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
        self.assertEqual(result, {"queued": 2, "total_watchlist": 2})
        self.assertEqual(RefreshRequest.objects.count(), 2)
        for request in RefreshRequest.objects.all():
            self.assertEqual(request.status, RefreshRequestStatus.PENDING)
            self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_titles_outside_the_watchlist_are_untouched(self):
        Movie.objects.create(tmdb_id=999, title="Not watched", year=1999)

        result = request_watchlist_refresh(user_id=1)

        self.assertEqual(result, {"queued": 0, "total_watchlist": 0})
        self.assertEqual(RefreshRequest.objects.count(), 0)

    def test_repeated_bulk_calls_stay_deduplicated_and_upgrade(self):
        movie = _watchlist_movie(103)
        local_offer(movie)
        request_availability_refresh(movie, priority=RefreshPriority.WATCHLIST)

        first = request_watchlist_refresh(user_id=1)
        second = request_watchlist_refresh(user_id=1)

        self.assertEqual(first, {"queued": 1, "total_watchlist": 1})
        self.assertEqual(second, {"queued": 1, "total_watchlist": 1})
        self.assertEqual(RefreshRequest.objects.count(), 1)
        request = RefreshRequest.objects.get()
        self.assertEqual(request.priority, RefreshPriority.EXPLICIT)

    def test_empty_watchlist_reports_zero(self):
        self.assertEqual(
            request_watchlist_refresh(user_id=1),
            {"queued": 0, "total_watchlist": 0},
        )


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
