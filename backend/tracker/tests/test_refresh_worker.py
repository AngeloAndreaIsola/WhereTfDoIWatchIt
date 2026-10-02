"""Refresh worker: the queue-only consumer for event-driven refresh requests.

Covers the missing piece of the Phase B architecture: detail views, imports
and watchlist events enqueue ``RefreshRequest`` rows without any network I/O;
``process_refresh_queue`` + ``manage.py refresh_worker`` drain that queue.

Contracts under test:

- queue-only: a worker tick settles pending requests but never runs the
  background stale sweep (the scheduled ``refresh_availability`` run owns it),
  so looping cannot burn the daily API budget on maintenance;
- same guards as every other refresh path: rate limiter (upfront and per
  request), per-source freshness (a fresh scope settles without spending
  budget), ``RefreshRun`` audit row;
- the detail payload reports ``refresh_pending`` so the frontend can show
  "Checking availability…" and poll instead of a premature empty state.

No test in this module makes live HTTP requests to TMDb or any availability
provider.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from tracker.models import (
    Movie,
    RefreshRequestStatus,
    RefreshRun,
)
from tracker.services.refresh_requests import (
    has_pending_request,
    process_refresh_queue,
    request_availability_refresh,
)
from tracker.tests.availability_factories import local_offer


class ProcessRefreshQueueTests(TestCase):
    """One worker tick: drain pending requests, nothing else."""

    def test_idle_queue_returns_none_without_audit_row(self):
        self.assertIsNone(process_refresh_queue(budget=5))
        self.assertEqual(RefreshRun.objects.count(), 0)

    def test_pending_request_is_refreshed_and_settled(self):
        movie = Movie.objects.create(tmdb_id=901, title="Queued", year=2020)
        request_availability_refresh(movie)  # never fetched -> pending, NEW
        service = MagicMock()

        summary = process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_called_once_with(movie)
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["requests_drained"], 1)
        self.assertEqual(summary["requests_pending"], 0)
        # Queue-only: no background sweep candidate selection happened.
        self.assertEqual(summary["attempted"], 0)

    def test_refresh_request_marked_done_and_audited(self):
        movie = Movie.objects.create(tmdb_id=902, title="Audited", year=2020)
        request_availability_refresh(movie)

        process_refresh_queue(budget=5, service=MagicMock())

        from tracker.models import RefreshRequest

        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.DONE
        )
        run = RefreshRun.objects.get()
        self.assertEqual(run.rows_refreshed, 1)
        self.assertIsNotNone(run.finished_at)

    def test_fresh_scope_settles_without_spending_budget(self):
        movie = Movie.objects.create(tmdb_id=903, title="Fresh", year=2020)
        local_offer(movie)
        request_availability_refresh(movie, if_stale=False)  # forced enqueue
        service = MagicMock()

        summary = process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_not_called()
        self.assertEqual(summary["skipped_cached"], 1)
        self.assertEqual(summary["requests_drained"], 1)

    def test_daily_rate_limit_leaves_queue_untouched(self):
        movie = Movie.objects.create(tmdb_id=904, title="Capped", year=2020)
        request_availability_refresh(movie)
        limiter = MagicMock()
        limiter.remaining.return_value = 0
        service = MagicMock()

        summary = process_refresh_queue(budget=5, service=service, limiter=limiter)

        service.refresh_content.assert_not_called()
        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["requests_pending"], 1)
        # Nothing was attempted, so nothing to audit.
        self.assertEqual(RefreshRun.objects.count(), 0)

        from tracker.models import RefreshRequest

        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.PENDING
        )


class HasPendingRequestTests(TestCase):
    def test_reports_pending_scope_for_a_title(self):
        movie = Movie.objects.create(tmdb_id=905, title="Wait", year=2020)
        self.assertFalse(has_pending_request(movie.tmdb_id))

        request_availability_refresh(movie)
        self.assertTrue(has_pending_request(movie.tmdb_id))

        process_refresh_queue(budget=5, service=MagicMock())
        self.assertFalse(has_pending_request(movie.tmdb_id))


class RecentAttemptCooldownTests(TestCase):
    """A completed attempt that found nothing must settle the read side too.

    Unavailable titles stay stale forever (absence is not evidence), so
    without the cooldown every detail view / frontend poll would open a new
    attempt — and every drain would spend an external API call.
    """

    def test_completed_attempt_blocks_requeue_while_window_is_open(self):
        movie = Movie.objects.create(tmdb_id=909, title="Nothing", year=2020)
        request_availability_refresh(movie)

        # Worker drained it, but the provider has nothing for this title.
        process_refresh_queue(budget=5, service=MagicMock())

        from tracker.services.refresh_requests import recently_attempted

        self.assertTrue(recently_attempted(movie))
        self.assertIsNone(request_availability_refresh(movie))

        # The detail response therefore reports nothing pending: the
        # frontend stops polling instead of spinning on a settled queue.
        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertFalse(resp.data["refresh_pending"])

    def test_forced_enqueue_bypasses_the_cooldown(self):
        movie = Movie.objects.create(tmdb_id=910, title="Forced", year=2020)
        request_availability_refresh(movie)
        process_refresh_queue(budget=5, service=MagicMock())

        forced = request_availability_refresh(movie, if_stale=False)
        self.assertIsNotNone(forced)


class RefreshWorkerCommandTests(TestCase):
    """``manage.py refresh_worker`` — the long-running loop, tick by tick."""

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    def test_once_runs_a_single_tick_and_exits(self, process):
        process.return_value = None

        call_command("refresh_worker", "--once", verbosity=0)

        process.assert_called_once()

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    def test_tick_failure_does_not_kill_the_worker(self, process):
        process.side_effect = RuntimeError("db connection lost")

        call_command("refresh_worker", "--once", verbosity=0)  # must not raise

        process.assert_called_once()


class PrimeDetailPendingFlagTests(TestCase):
    """The detail payload tells the frontend whether to keep polling."""

    def test_stale_title_reports_refresh_pending(self):
        from tracker.tests.test_event_driven_refresh import _backdate_offers

        movie = Movie.objects.create(tmdb_id=906, title="Stale", year=2020)
        local_offer(movie)
        _backdate_offers(movie)

        with patch(
            "availability.services.refresh.AvailabilityRefreshService.refresh_content"
        ):
            resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["refresh_pending"])

    def test_fresh_title_reports_nothing_pending(self):
        movie = Movie.objects.create(tmdb_id=907, title="Fresh", year=2020)
        local_offer(movie)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.data["refresh_pending"])

    def test_flag_clears_once_the_queue_is_drained(self):
        movie = Movie.objects.create(tmdb_id=908, title="Drained", year=2020)
        request_availability_refresh(movie)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertTrue(resp.data["refresh_pending"])

        # Drain with a service that writes canonical offers, like the real
        # worker: the queue settles and the now-fresh title is not re-queued
        # by the next detail view.
        service = MagicMock()
        service.refresh_content.side_effect = lambda m: local_offer(m)
        process_refresh_queue(budget=5, service=service)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertFalse(resp.data["refresh_pending"])

    def test_refresh_status_distinguishes_a_failed_refresh_from_a_done_one(self):
        """The frontend must be able to tell "refreshed" from "gave up"."""
        from django.utils import timezone

        from tracker.models import RefreshRequest

        movie = Movie.objects.create(tmdb_id=912, title="Queued", year=2020)
        request_availability_refresh(movie)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertTrue(resp.data["refresh_pending"])
        self.assertEqual(resp.data["refresh_status"], RefreshRequestStatus.PENDING)

        # The worker gave up after MAX_ATTEMPTS: no longer pending, but the
        # detail payload must not report it as a completed refresh.
        RefreshRequest.objects.update(
            status=RefreshRequestStatus.FAILED, completed_at=timezone.now()
        )
        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertFalse(resp.data["refresh_pending"])
        self.assertEqual(resp.data["refresh_status"], RefreshRequestStatus.FAILED)
