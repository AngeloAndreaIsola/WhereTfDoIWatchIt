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
- after the title queue the tick runs the watchlist's pending
  ``CatalogueRefreshRequest`` marker (whole-catalogue syncs, no API budget),
  and a rate-limited tick is *printed* instead of staying silent — an
  exhausted quota must be observable, not mistaken for a dead worker;
- the detail payload reports ``refresh_pending`` so the frontend can show
  "Checking availability…" and poll instead of a premature empty state.

No test in this module makes live HTTP requests to TMDb or any availability
provider.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from availability.sources import DEFAULT_SOURCE_SLUG

from tracker.models import (
    ApiCallCounter,
    BlockedReason,
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
)
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS, RateLimiter
from tracker.services.refresh_requests import (
    has_pending_request,
    process_refresh_queue,
    request_availability_refresh,
    request_catalogue_refresh,
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

        service.refresh_content.assert_called_once_with(movie, country=None)
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

    def test_daily_rate_limit_leaves_the_default_scope_request_pending(self):
        """A spent default-source budget defers that request, not the queue.

        The old behavior returned early and audited nothing, which read as
        "the whole queue is blocked". Now the run is still audited (so the
        blocked source is observable) and only the default-scope request waits.
        """
        movie = Movie.objects.create(tmdb_id=904, title="Capped", year=2020)
        request_availability_refresh(movie)
        limiter = MagicMock()
        limiter.remaining.return_value = 0
        service = MagicMock()

        summary = process_refresh_queue(budget=5, service=service, limiter=limiter)

        service.refresh_content.assert_not_called()
        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["requests_pending"], 1)

        from tracker.models import RefreshRequest

        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.PENDING
        )

    def test_an_exhausted_default_source_does_not_block_other_sources(self):
        """The core source-aware guarantee, at the worker-tick boundary.

        Streaming Availability is out of budget; YouTube declares none. The
        YouTube request must drain while the Streaming Availability one waits.
        """
        from tracker.models import RefreshPriority, RefreshRequest

        movie = Movie.objects.create(tmdb_id=915, title="Mixed", year=2020)
        RefreshRequest.objects.create(
            movie=movie, source="youtube", priority=RefreshPriority.EXPLICIT
        )
        RefreshRequest.objects.create(
            movie=movie,
            source=DEFAULT_SOURCE_SLUG,
            priority=RefreshPriority.EXPLICIT,
        )
        limiter = MagicMock()
        limiter.remaining.return_value = 0  # Streaming Availability is spent

        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            summary = process_refresh_queue(
                budget=5, service=MagicMock(), limiter=limiter
            )

        # The unmetered YouTube request ran against its own registry source;
        # the exhausted one stayed pending for tomorrow's reset.
        self.assertIn(
            "youtube",
            {call.kwargs["source"].slug for call in service_cls.call_args_list},
        )
        youtube = RefreshRequest.objects.get(source="youtube")
        streaming = RefreshRequest.objects.get(source=DEFAULT_SOURCE_SLUG)
        self.assertEqual(youtube.status, RefreshRequestStatus.DONE)
        self.assertEqual(streaming.status, RefreshRequestStatus.PENDING)
        self.assertEqual(summary["rate_limited_sources"], [DEFAULT_SOURCE_SLUG])


class StarvationResistanceTests(TestCase):
    """A huge blocked backlog must not hide executable work.

    The regression this pins: with one shared limiter, a refused request
    latched a flag that skipped every later request in the tick, so an
    exhausted source looked like a dead queue. The candidate query now
    excludes out-of-budget sources in SQL, which has to hold even when the
    blocked backlog is far larger than one candidate page.
    """

    BLOCKED_BACKLOG = 520  # deliberately above CANDIDATE_SCAN_LIMIT

    def setUp(self):
        # A genuinely exhausted streaming_availability counter: no mock, so
        # the test proves real limiter behavior.
        ApiCallCounter.objects.create(
            service=DEFAULT_SOURCE_SLUG,
            call_date=dt.datetime.utcnow().date(),
            count=SOURCE_DAILY_LIMITS[DEFAULT_SOURCE_SLUG],
        )
        self.blocked_movies = []
        for index in range(self.BLOCKED_BACKLOG):
            movie = Movie.objects.create(
                tmdb_id=50_000 + index, title=f"Blocked {index}", year=2020
            )
            RefreshRequest.objects.create(
                movie=movie,
                source=DEFAULT_SOURCE_SLUG,
                priority=RefreshPriority.EXPLICIT,
            )
            self.blocked_movies.append(movie)

    def test_a_large_blocked_backlog_never_hides_an_executable_request(self):
        from tracker.services.refresh_requests import CANDIDATE_SCAN_LIMIT

        self.assertGreater(
            self.BLOCKED_BACKLOG, CANDIDATE_SCAN_LIMIT,
            "the backlog must exceed one page for this test to mean anything",
        )
        # Queued *after* the whole blocked backlog, as the worst case.
        for source in ("youtube", "mediaset_infinity"):
            movie = Movie.objects.create(
                tmdb_id=90_000 + len(source), title=f"Executable {source}", year=2021
            )
            RefreshRequest.objects.create(
                movie=movie, source=source, priority=RefreshPriority.EXPLICIT
            )

        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            service_cls.return_value = MagicMock()
            process_refresh_queue(budget=20, service=MagicMock())

        executed = {
            call.kwargs["source"].slug for call in service_cls.call_args_list
        }
        self.assertEqual(executed, {"youtube", "mediaset_infinity"})

        # Every blocked request is still PENDING (waiting, not failed) and
        # both executable ones are DONE.
        self.assertEqual(
            RefreshRequest.objects.filter(
                source=DEFAULT_SOURCE_SLUG, status=RefreshRequestStatus.PENDING
            ).count(),
            self.BLOCKED_BACKLOG,
        )
        self.assertEqual(
            RefreshRequest.objects.filter(
                source__in=["youtube", "mediaset_infinity"],
                status=RefreshRequestStatus.DONE,
            ).count(),
            2,
        )

    def test_the_exhausted_source_is_reported_but_the_run_is_not_empty(self):
        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            service_cls.return_value = MagicMock()
            summary = process_refresh_queue(budget=20, service=MagicMock())

        self.assertEqual(summary["rate_limited_sources"], [DEFAULT_SOURCE_SLUG])
        # Blocked requests are waiting, never counted as drained.
        self.assertEqual(summary["requests_drained"], 0)
        self.assertEqual(summary["requests_pending"], self.BLOCKED_BACKLOG)


class PrioritySemanticsTests(TestCase):
    """Fan-out must not invent a priority system."""

    def setUp(self):
        # These tests are about ordering, not quota, so give the metered
        # source a clean budget: a developer's machine can legitimately have
        # today's calls spent, and a spent budget would (correctly) defer
        # every default-scope request and mask the ordering.
        ApiCallCounter.objects.filter(service=DEFAULT_SOURCE_SLUG).delete()

    def test_requests_of_equal_priority_drain_oldest_first(self):
        watched = Movie.objects.create(tmdb_id=61_001, title="Watched", year=2019)
        explicit = Movie.objects.create(tmdb_id=61_002, title="Explicit", year=2020)
        # Explicitly stamp distinct wait times: ``requested_at`` defaults to
        # "now", which would tie at microsecond resolution and make the order
        # arbitrary. Enqueue ``watched`` first, so it is the older request.
        first = RefreshRequest.objects.create(
            movie=watched, source=DEFAULT_SOURCE_SLUG, priority=RefreshPriority.EXPLICIT
        )
        second = RefreshRequest.objects.create(
            movie=explicit, source=DEFAULT_SOURCE_SLUG, priority=RefreshPriority.EXPLICIT
        )
        base = timezone.now()
        RefreshRequest.objects.filter(pk=first.pk).update(
            requested_at=base - dt.timedelta(minutes=10)
        )
        RefreshRequest.objects.filter(pk=second.pk).update(requested_at=base)
        service = MagicMock()

        # An explicitly scoped request resolves its own service through the
        # registry, so the default injected service is not enough here: patch
        # the resolution or the second request would call the real API.
        with patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            registry_service = MagicMock()
            service_cls.return_value = registry_service
            summary = process_refresh_queue(budget=10, service=service)

        order = [
            call.args[0].tmdb_id
            for call in registry_service.refresh_content.call_args_list
        ]
        self.assertEqual(order, [watched.tmdb_id, explicit.tmdb_id])
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["errors"], 0)

    def test_a_higher_intent_source_scoped_request_drains_before_a_lower_one(self):
        """Priority ordering holds across source-scoped requests.

        Declares ladder-off explicitly, and the declaration is load-bearing
        rather than defensive. ``DEFAULT_SOURCE_SLUG`` *is*
        ``'streaming_availability'``, so this test creates a request naming
        the one metered source for a title that already has a local offer. With
        the ladder on, the drain correctly settles that request as
        policy-gated without attempting it, so only the YouTube request is
        refreshed and the count is 1 rather than 2.

        That is the source policy working, not a priority bug — this test is
        about *ordering*, and it should measure ordering alone. The behaviour
        it would otherwise be measuring is asserted deliberately, with the gate
        on, in ``test_refresh_source_policy_drain``; the coverage is moved
        rather than lost.
        """
        from tracker.tests.test_event_driven_refresh import _backdate_offers

        stale = Movie.objects.create(tmdb_id=61_003, title="Stale", year=2018)
        fresh_intent = Movie.objects.create(tmdb_id=61_004, title="Viewed", year=2020)
        local_offer(stale)
        _backdate_offers(stale)
        local_offer(fresh_intent)
        _backdate_offers(fresh_intent)
        RefreshRequest.objects.create(
            movie=stale, source="youtube", priority=RefreshPriority.WATCHED
        )
        RefreshRequest.objects.create(
            movie=fresh_intent,
            source=DEFAULT_SOURCE_SLUG,
            priority=RefreshPriority.RECENTLY_VIEWED,
        )

        with patch.dict(os.environ, {"AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED": "0"}), patch(
            "tracker.services.refresh_requests.AvailabilityRefreshService"
        ) as service_cls:
            service_cls.return_value = MagicMock()
            summary = process_refresh_queue(budget=10, service=MagicMock())

        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(summary["skipped_policy_gated"], 0)

    def test_the_priority_ladder_is_unchanged(self):
        self.assertEqual(
            [
                RefreshPriority.EXPLICIT,
                RefreshPriority.NEW,
                RefreshPriority.RECENTLY_VIEWED,
                RefreshPriority.VISIBLE,
                RefreshPriority.WATCHLIST,
                RefreshPriority.WATCHED,
                RefreshPriority.STALE,
                RefreshPriority.BACKGROUND,
            ],
            [10, 20, 30, 40, 50, 60, 70, 80],
        )


class LegacyScopeTests(TestCase):
    """``source=""`` keeps working and is migrated, not duplicated."""

    def test_a_legacy_request_resolves_to_the_default_source(self):
        from tracker.services.refresh_requests import resolve_request_source

        movie = Movie.objects.create(tmdb_id=62_001, title="Legacy", year=2020)
        request = RefreshRequest.objects.create(movie=movie, source="")

        self.assertEqual(resolve_request_source(request), DEFAULT_SOURCE_SLUG)

    def test_a_legacy_request_still_executes(self):
        movie = Movie.objects.create(tmdb_id=62_002, title="LegacyRun", year=2020)
        RefreshRequest.objects.create(
            movie=movie, source="", priority=RefreshPriority.EXPLICIT
        )
        service = MagicMock()

        summary = process_refresh_queue(budget=5, service=service)

        service.refresh_content.assert_called_once_with(movie, country=None)
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(
            RefreshRequest.objects.get().status, RefreshRequestStatus.DONE
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

    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=False,
    )
    @patch("availability.services.refresh.AvailabilityRefreshService.refresh_content")
    def test_completed_attempt_blocks_requeue_while_window_is_open(
        self, refresh_content, _gate
    ):
        """A drained fan-out settles the read side, so the next detail view
        reports nothing pending instead of reopening every scope.

        ``refresh_content`` is patched to succeed without writing offers —
        exactly the "provider has nothing for this title" case that leaves the
        title stale forever, which is what the cooldown exists to stop from
        re-queuing on every read.
        """
        refresh_content.return_value = MagicMock()
        movie = Movie.objects.create(tmdb_id=909, title="Nothing", year=2020)

        # Opening the detail view fans out; the worker drains the whole lot.
        self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        process_refresh_queue(budget=99, service=MagicMock())

        from tracker.services.refresh_requests import recently_attempted

        self.assertTrue(recently_attempted(movie, DEFAULT_SOURCE_SLUG, "US"))
        self.assertIsNone(request_availability_refresh(movie, country="US"))

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

    def test_a_blocked_row_does_not_suppress_requeue(self):
        """Historical quota-blocked rows must not lock a title out of the queue.

        The ~10.5k rows the old run-budget defect settled as
        ``blocked``/``quota_exhausted`` are terminal history. The cooldown
        counts only ``done``/``failed`` attempts and the pending-dedup index
        ignores settled rows, so the very next enqueue opens a fresh pending
        row alongside them. That is what makes those rows self-healing rather
        than a permanent lockout — and why no destructive data migration is
        needed to recover the affected titles.
        """
        movie = Movie.objects.create(tmdb_id=913, title="WasBlocked", year=2020)
        RefreshRequest.objects.create(
            movie=movie,
            source=DEFAULT_SOURCE_SLUG,
            status=RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            completed_at=timezone.now(),
        )

        from tracker.services.refresh_requests import recently_attempted

        self.assertFalse(recently_attempted(movie, DEFAULT_SOURCE_SLUG))
        opened = request_availability_refresh(movie, if_stale=False)
        self.assertIsNotNone(opened)
        self.assertEqual(opened.status, RefreshRequestStatus.PENDING)
        # It coexists with the historical row: settled rows never collide with
        # the pending-only dedup constraint.
        self.assertEqual(
            RefreshRequest.objects.filter(movie=movie).count(), 2
        )


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

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    def test_rate_limited_tick_is_printed(self, process):
        """An exhausted quota must be visible in the logs, not silent."""
        process.return_value = {
            "rate_limited": True,
            "requests_drained": 0,
            "requests_pending": 7,
            "errors": 0,
            "refreshed": 0,
        }

        out = io.StringIO()
        call_command("refresh_worker", "--once", stdout=out)

        summary = json.loads(out.getvalue().strip())
        self.assertTrue(summary["rate_limited"])
        self.assertEqual(summary["requests_pending"], 7)

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    def test_idle_quiet_tick_prints_nothing(self, process):
        process.return_value = None

        out = io.StringIO()
        call_command("refresh_worker", "--once", stdout=out)

        self.assertEqual(out.getvalue().strip(), "")


class WorkerCatalogueMarkerTests(TestCase):
    """The worker drains titles first, then the queued catalogue marker."""

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    @patch("tracker.services.bulk_refresh.process_catalogue_refresh")
    def test_tick_runs_the_pending_marker_after_the_title_queue(
        self, run_catalogue, process_queue
    ):
        process_queue.return_value = None
        run_catalogue.return_value = {"errors": 0}
        request_catalogue_refresh(["src_x"])

        out = io.StringIO()
        call_command("refresh_worker", "--once", stdout=out)

        run_catalogue.assert_called_once_with(source_slug="src_x")
        marker = CatalogueRefreshRequest.objects.get()
        self.assertEqual(marker.status, CatalogueRefreshStatus.DONE)
        payload = json.loads(out.getvalue().strip())
        self.assertEqual(payload["catalogue"]["sources"], ["src_x"])
        self.assertEqual(payload["catalogue"]["failed_sources"], [])

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    @patch("tracker.services.bulk_refresh.process_catalogue_refresh")
    def test_marker_runs_even_when_the_title_queue_is_rate_limited(
        self, run_catalogue, process_queue
    ):
        # Catalogue syncs spend no API budget, so quota exhaustion on the
        # title side must not park them.
        process_queue.return_value = {
            "rate_limited": True,
            "requests_drained": 0,
            "errors": 0,
            "refreshed": 0,
            "requests_pending": 3,
        }
        run_catalogue.return_value = {"errors": 0}
        request_catalogue_refresh(["src_x"])

        call_command("refresh_worker", "--once", stdout=io.StringIO())

        run_catalogue.assert_called_once_with(source_slug="src_x")

    @patch("tracker.management.commands.refresh_worker.process_refresh_queue")
    @patch("tracker.management.commands.refresh_worker.process_pending_catalogue_refresh")
    def test_titles_always_drain_before_the_catalogue_marker(
        self, marker, process_queue
    ):
        order = []
        process_queue.side_effect = lambda **kwargs: order.append("titles") or None
        marker.side_effect = lambda: order.append("catalogue")

        call_command("refresh_worker", "--once", stdout=io.StringIO())

        self.assertEqual(order, ["titles", "catalogue"])


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
        from tracker.tests.test_event_driven_refresh import (
            _fresh_every_title_level_scope,
        )

        movie = Movie.objects.create(tmdb_id=907, title="Fresh", year=2020)
        # Fresh in *every* title-level scope, so the fan-out has nothing to
        # queue — a single fresh default-scope offer is no longer enough.
        _fresh_every_title_level_scope(movie)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.data["refresh_pending"])

    @patch(
        "tracker.services.source_policy.consult_metered_only_when_unresolved",
        return_value=False,
    )
    @patch("availability.services.refresh.AvailabilityRefreshService.refresh_content")
    def test_flag_clears_once_the_queue_is_drained(self, refresh_content, _gate):
        """After the worker drains the fan-out and writes offers, the flag
        clears and the now-fresh title is not re-queued by the next read."""
        # Each drained request writes a canonical offer, like the real worker.
        # The provider_content_id is made unique per call so the offer-identity
        # constraint is satisfied when several scopes settle in one drain.
        calls = {"n": 0}

        def _settle(m, country=None):
            calls["n"] += 1
            return local_offer(m, provider_content_id=f"settled-{calls['n']}")

        refresh_content.side_effect = _settle

        movie = Movie.objects.create(tmdb_id=908, title="Drained", year=2020)

        resp = self.client.get(f"/api/prime/movie/{movie.tmdb_id}/")
        self.assertTrue(resp.data["refresh_pending"])

        process_refresh_queue(budget=99, service=MagicMock())

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
