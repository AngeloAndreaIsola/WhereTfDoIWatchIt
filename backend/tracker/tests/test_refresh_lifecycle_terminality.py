"""P6a — a terminal refresh must own nothing that is still outstanding.

The defect these pin is measured, not hypothetical. A live 191-title watchlist
produced this:

    run 5: status=partial, finished_at written
      mediaset_infinity  7/191 completed, 184 queued
      youtube            6/191 completed, 185 queued
      watchmode          7/191 completed, 184 queued
      -> 553 request rows still queued under a terminal run

``PARTIAL`` was reachable two incompatible ways — "some titles are genuinely
unresolved" and "the worker ran out of budget mid-queue" — and nothing in the
data could distinguish them. A report that cannot distinguish those is not
evidence, which is the whole reason the ladder work was worth doing.

The invariant, stated once:

    terminal AvailabilityRefresh
        => no queued request rows
        => no running request rows
        => no unsettled source rows
        => finished_at IS NOT NULL

Tests assert against the **database**, not against a call, so a future
refactor cannot satisfy them by reordering internal calls.
"""
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    ApiCallCounter,
    BlockedReason,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import refresh_lifecycle as rl
from tracker.services import source_policy
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS

SA = "streaming_availability"
WM = "watchmode"


class RefreshLifecycleTerminalityBase(TestCase):
    def setUp(self):
        from tracker.models import ApiCallCounter, Movie

        ApiCallCounter.objects.all().delete()
        SOURCE_DAILY_LIMITS[SA] = 100
        self.movie = Movie.objects.create(tmdb_id=770_001, title="Terminal", year=2021)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)
        self.batch = uuid.uuid4()
        self.refresh = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movie.pk],
        )

    def exhaust(self, source=SA, limit=100):
        """Burn a source's daily budget for real, so the limiter says 0.

        Written as a direct UPDATE because ``call_date`` is ``auto_now_add``:
        it cannot be supplied to ``create()``, and ``update_or_create`` with it
        in ``defaults`` raises FieldError. The counter's day is today by
        construction, which is the day the limiter reads.
        """
        SOURCE_DAILY_LIMITS[source] = limit
        ApiCallCounter.objects.update_or_create(
            service=source, defaults={"count": limit}
        )

    def request(self, source=SA, status=RefreshRequestStatus.PENDING, movie=None):
        return RefreshRequest.objects.create(
            movie=movie or self.movie,
            source=source,
            status=status,
            batch=self.batch,
            completed_at=None if status == RefreshRequestStatus.PENDING else timezone.now(),
        )

    def more_titles(self, count):
        """`count` further titles, so bulk cases can create several requests.

        Necessary because the pending-dedup constraint allows only one live
        request per (movie, source) — which is correct product behaviour, so the
        "184 SA requests still queued" scenario is modelled as 184 *titles*, one
        request each, not 184 rows for one title.
        """
        return [
            Movie.objects.create(
                tmdb_id=770_100 + i, title=f"Queued {i}", year=2022
            )
            for i in range(count)
        ]

    def assert_invariant_holds(self, refresh):
        """The terminal invariant, as a reusable assertion."""
        refresh.refresh_from_db()
        self.assertIn(
            refresh.status,
            {
                AvailabilityRefreshStatus.COMPLETED,
                AvailabilityRefreshStatus.PARTIAL,
                AvailabilityRefreshStatus.FAILED,
            },
            f"expected a terminal status, got {refresh.status}",
        )
        self.assertIsNotNone(
            refresh.finished_at, "a terminal refresh must have finished_at"
        )
        # 1 & 2: no queued, no running.
        live = RefreshRequest.objects.filter(
            batch=refresh.batch,
            status__in=RefreshRequestStatus.values,
        ).exclude(status__in=["done", "failed", "blocked"])
        self.assertEqual(
            list(live.values_list("status", flat=True)),
            [],
            "a terminal refresh retained unsettled request rows",
        )
        # 3: no unsettled source rows. Checked against the non-terminal set
        # rather than the terminal one, for the reason documented on
        # NON_TERMINAL_SOURCE_STATUSES: an unknown-but-not-in-progress status
        # is not evidence that the run stopped early.
        from tracker.services.availability_report import NON_TERMINAL_SOURCE_STATUSES

        unsettled = [
            f"{r.source}={r.status}"
            for r in refresh.sources.all()
            if r.status in NON_TERMINAL_SOURCE_STATUSES
        ]
        self.assertEqual(unsettled, [], "unsettled source rows under a terminal run")


class TerminalRefreshHasNoOutstandingWorkTests(RefreshLifecycleTerminalityBase):
    def test_terminal_refresh_cannot_retain_queued_request_rows(self):
        """Test 1 — the headline invariant, on the request queue."""
        self.request(SA, RefreshRequestStatus.DONE)
        self.request(WM, RefreshRequestStatus.DONE)

        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)

        self.assert_invariant_holds(self.refresh)

    def test_every_terminal_refresh_has_finished_at(self):
        """Test 3 — the timestamp is part of the invariant, not a nicety."""
        self.request(SA, RefreshRequestStatus.DONE)

        self.assertIsNone(self.refresh.finished_at, "precondition: not yet finished")

        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)

        self.refresh.refresh_from_db()
        self.assertIsNotNone(self.refresh.finished_at)
        self.assertLessEqual(self.refresh.finished_at, timezone.now())


class QuotaSettlesRemainingWorkTests(RefreshLifecycleTerminalityBase):
    def test_quota_exhaustion_settles_remaining_work_as_blocked(self):
        """Test 2 — the 553-row case, which this is the regression for.

        Out of budget, three requests still queued. The worker cannot execute
        them, so it must settle them rather than leave them pending: `blocked`
        with `quota_exhausted`, which preserves the fact that the source was
        never consulted.
        """
        self.exhaust(SA)
        for movie in self.more_titles(3):
            self.request(SA, RefreshRequestStatus.PENDING, movie=movie)

        settled = rl.settle_unexecutable_requests(self.refresh)

        self.assertEqual(settled.get(BlockedReason.QUOTA_EXHAUSTED), 3)
        self.assertFalse(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.PENDING
            ).exists(),
            "out-of-budget requests must not stay pending",
        )
        for req in RefreshRequest.objects.filter(batch=self.batch):
            self.assertEqual(req.status, RefreshRequestStatus.BLOCKED)
            self.assertEqual(req.blocked_reason, BlockedReason.QUOTA_EXHAUSTED)
            self.assertIsNotNone(req.completed_at)

    def test_quota_settlement_then_finalise_yields_a_consistent_terminal_run(self):
        """Settlement followed by finalisation produces a terminal run whose
        rows all agree, which is the whole point of doing it in that order."""
        self.exhaust(SA)
        for movie in self.more_titles(3):
            self.request(SA, RefreshRequestStatus.PENDING, movie=movie)

        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)

        self.assert_invariant_holds(self.refresh)
        # The 553-row contradiction is now impossible: the rows are blocked,
        # not queued, and the run's status describes an outcome rather than an
        # interruption.
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.BLOCKED
            ).count(),
            3,
        )


class PartialCannotMeanUnfinishedQueueTests(RefreshLifecycleTerminalityBase):
    def test_finalising_with_unsettled_work_defers_rather_than_recording_partial(self):
        """Test 4 — reproduce the observed failure mode and make it impossible.

        A request that *could* run (its source has budget) is left pending, and
        finalisation is attempted anyway. The old code recorded `PARTIAL` here,
        which made "the worker stopped" indistinguishable from "titles are
        unresolved". It now *defers*: the run stays ``RUNNING`` so the next tick
        resumes it, rather than recording a misleading terminal status. A run
        budget limits work performed, not future eligibility.
        """
        # The source has budget, so this request is genuinely runnable and must
        # not be settled by the settlement pass.
        SOURCE_DAILY_LIMITS[SA] = 100
        self.request(SA, RefreshRequestStatus.PENDING)

        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)

        self.refresh.refresh_from_db()
        self.assertEqual(
            self.refresh.status,
            AvailabilityRefreshStatus.RUNNING,
            "a run with runnable work must stay RUNNING, not record a terminal status",
        )
        self.assertIsNone(self.refresh.finished_at)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.PENDING
            ).count(),
            1,
            "the deferred request must remain queued for the next tick",
        )

    def test_an_unrunnable_source_is_settled_so_finalise_can_succeed(self):
        """The counterpart: blocked-by-quota is settled, so the run is free to
        finalise. This is the legitimate shape of PARTIAL."""
        self.exhaust(SA)
        self.request(SA, RefreshRequestStatus.PENDING)

        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)

        self.assert_invariant_holds(self.refresh)


class PolicyBlockedIsNotUnknownTests(RefreshLifecycleTerminalityBase):
    def test_policy_gated_request_settles_as_policy_not_needed_without_calling_upstream(self):
        """The additional test — P6a must not undo the source-ladder work.

        A blocked-by-policy request must say `policy_not_needed`, must not
        consult the source, and — critically — must leave the *title* resolved.
        A title blocked on the metered source is available precisely because a
        cheaper source answered.
        """
        # Reuse the sibling module's offer helper rather than re-deriving the
        # Provider/Country/Source wiring here: one definition of "this title is
        # available on this source", so the two test modules cannot disagree
        # about what an active offer looks like.
        from tracker.tests.test_refresh_source_policy_drain import offer_for

        offer_for(self.movie, WM, available=True)

        self.request(WM, RefreshRequestStatus.DONE)
        self.request(SA, RefreshRequestStatus.PENDING)

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=True
        ):
            settled = rl.settle_unexecutable_requests(self.refresh)

        self.assertEqual(settled.get(BlockedReason.POLICY_NOT_NEEDED), 1)
        blocked = RefreshRequest.objects.get(batch=self.batch, source=SA)
        self.assertEqual(blocked.status, RefreshRequestStatus.BLOCKED)
        self.assertEqual(blocked.blocked_reason, BlockedReason.POLICY_NOT_NEEDED)

        # And the title is still available: the request lifecycle and the
        # title classifier are separate layers, and blocking a request must not
        # demote a resolved title to unknown.
        from tracker.services.availability_report import title_source_report

        entry = title_source_report([self.movie.pk], batch=self.batch)[self.movie.pk]
        self.assertEqual(
            entry["state"], "available", "a blocked request demoted a resolved title"
        )


class BlockedReasonVocabularyTests(RefreshLifecycleTerminalityBase):
    def test_a_blocked_row_must_carry_a_valid_reason(self):
        """The reason vocabulary is closed, so a bad value cannot persist."""
        import django.db.utils

        req = self.request(SA, RefreshRequestStatus.PENDING)
        with self.assertRaises(ValueError):
            req.settle(RefreshRequestStatus.BLOCKED, blocked_reason="because")

        with self.assertRaises(ValueError):
            req.settle(RefreshRequestStatus.BLOCKED)

    def test_blocked_reason_may_not_be_set_on_a_done_row(self):
        req = self.request(SA, RefreshRequestStatus.PENDING)
        with self.assertRaises(ValueError):
            req.settle(
                RefreshRequestStatus.DONE, blocked_reason=BlockedReason.QUOTA_EXHAUSTED
            )

    def test_a_done_row_clears_any_stale_blocked_reason(self):
        req = self.request(SA, RefreshRequestStatus.PENDING)
        req.settle(
            RefreshRequestStatus.BLOCKED, blocked_reason=BlockedReason.QUOTA_EXHAUSTED
        )
        req.refresh_from_db()
        self.assertEqual(req.blocked_reason, BlockedReason.QUOTA_EXHAUSTED)

        req.settle(RefreshRequestStatus.DONE)
        req.refresh_from_db()
        self.assertEqual(req.blocked_reason, "", "a settled row kept a stale reason")

    def test_the_database_rejects_a_blocked_row_without_a_reason(self):
        """The check constraint, not just the Python guard."""
        import django.db.utils

        with self.assertRaises(django.db.utils.IntegrityError):
            RefreshRequest.objects.create(
                movie=self.movie,
                source=SA,
                status=RefreshRequestStatus.BLOCKED,
                blocked_reason="",  # blocked but unexplained
                batch=self.batch,
            )