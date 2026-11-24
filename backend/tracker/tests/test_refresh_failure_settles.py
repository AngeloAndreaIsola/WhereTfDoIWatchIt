"""The terminal invariant applies to FAILED exactly as it does to COMPLETED/PARTIAL.

This closes a P6a gap found in production rather than in a test. Run 8's worker
tick raised ``UnsettledWorkError`` because 563 requests were still queued; the
tick handler caught the exception and called ``AvailabilityRefreshLifecycle.fail``,
which stamped ``FAILED`` unconditionally. The result was a terminal run holding
426 pending requests — the exact defect P6a exists to prevent, created by the
failure path itself.

    run 8 | failed | error: worker tick raised | 426 pending requests

Two causes, both now fixed:

1. `fail()` terminated without settling or verifying anything.
2. The drain's budget-exhausted paths *skipped* requests and left them queued,
   so the queue could never drain to zero and settlement never got to them
   (490 skipped on that tick).

Settled work is not successful work: a FAILED refresh is full of `done`,
`failed` and `blocked`/`quota_exhausted` rows and is still `FAILED`, because the
run-level failure is what the status records.
"""
from tracker.tests.availability_factories import reviewed_test_sources
import io
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
from tracker.services.refresh_lifecycle import UnsettledWorkError
from tracker.services.availability_report import NON_TERMINAL_SOURCE_STATUSES
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS
from tracker.management.commands import refresh_worker
from tracker.tests.test_refresh_source_policy_drain import offer_for

SA = "streaming_availability"
WM = "watchmode"


class FailureSettlesWorkBase(TestCase):
    def setUp(self):
        # `SOURCE_DAILY_LIMITS` is module-level mutable state, and
        # `has_configured_rate_limit()` reads it. Writing a limit for a source
        # and leaving it behind makes a *later* test conclude that source is
        # rate-limited — which is exactly how an earlier version of this file
        # broke `test_policy_metered_reads_roles_not_rate_limits`, which asserts
        # a policy-metered source has *no* configured rate limit. Save and
        # restore, as the sibling policy-drain tests do.
        reviewed_test_sources()
        self._saved_limits = dict(SOURCE_DAILY_LIMITS)
        SOURCE_DAILY_LIMITS[SA] = 100
        SOURCE_DAILY_LIMITS[WM] = 100
        ApiCallCounter.objects.all().delete()
        self.movies = [
            Movie.objects.create(tmdb_id=770_600 + i, title=f"T{i}", year=2021)
            for i in range(4)
        ]
        for movie in self.movies:
            WatchlistItem.objects.get_or_create(user_id=1, movie=movie)
        self.batch = uuid.uuid4()
        self.refresh = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[m.pk for m in self.movies],
        )

    def tearDown(self):
        SOURCE_DAILY_LIMITS.clear()
        SOURCE_DAILY_LIMITS.update(self._saved_limits)
        super().tearDown()

    def exhaust(self, source, limit=100):
        """Spend a source's real daily budget so the limiter reports zero."""
        SOURCE_DAILY_LIMITS[source] = limit
        ApiCallCounter.objects.update_or_create(
            service=source, defaults={"count": limit}
        )

    def extra_titles(self, count, start=100):
        """Fresh titles, so several sources can each hold a pending request.

        The pending-dedup constraint allows one live request per (title, source),
        so a two-source test needs two titles per source rather than reusing the
        base fixture's four.
        """
        made = [
            Movie.objects.create(tmdb_id=770_700 + start + i, title=f"Q{i}", year=2021)
            for i in range(count)
        ]
        for movie in made:
            WatchlistItem.objects.get_or_create(user_id=1, movie=movie)
        return made

    def queue(self, source, count, movies=None, status=RefreshRequestStatus.PENDING):
        pool = movies or self.movies
        return [
            RefreshRequest.objects.create(
                movie=pool[i], source=source, status=status, batch=self.batch,
                country="NL" if source == WM else "",
                completed_at=None
                if status == RefreshRequestStatus.PENDING
                else timezone.now(),
            )
            for i in range(count)
        ]

    def assert_terminal_invariant(self):
        """The invariant, asserted against the database for any terminal status."""
        self.refresh.refresh_from_db()
        self.assertIn(
            self.refresh.status,
            {
                AvailabilityRefreshStatus.COMPLETED,
                AvailabilityRefreshStatus.PARTIAL,
                AvailabilityRefreshStatus.FAILED,
            },
        )
        self.assertIsNotNone(self.refresh.finished_at)
        live = RefreshRequest.objects.filter(batch=self.batch).exclude(
            status__in=["done", "failed", "blocked"]
        )
        self.assertEqual(
            list(live.values_list("status", flat=True)), [], "queued rows under terminal run"
        )
        self.assertEqual(
            [
                f"{r.source}={r.status}"
                for r in self.refresh.sources.all()
                if r.status in NON_TERMINAL_SOURCE_STATUSES
            ],
            [],
            "unsettled source rows under terminal run",
        )


class FailMustSettleTests(FailureSettlesWorkBase):
    def test_worker_failure_with_budget_exhausted_work_settles_then_fails(self):
        """The run-8 scenario, fixed: budget spent, so settlement can act.

        Every queued request is unexecutable, so `fail()` settles them and the
        run terminates legitimately as FAILED with zero pending.
        """
        self.exhaust(SA)
        self.queue(SA, 3)

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="worker tick raised")

        self.assert_terminal_invariant()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        self.assertEqual(self.refresh.error, "worker tick raised")
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, blocked_reason=BlockedReason.QUOTA_EXHAUSTED
            ).count(),
            3,
            "unexecutable work must be settled, not abandoned",
        )

    def test_a_failed_refresh_holds_settled_rows_and_still_reads_failed(self):
        """Settling work is not declaring it successful."""
        # Every source spent: a runnable request would (correctly) refuse to be
        # called failed, so this fixture must make the whole queue unexecutable.
        self.exhaust(SA)
        self.exhaust(WM)
        done = self.queue(
            SA, 1, movies=self.movies[1:2], status=RefreshRequestStatus.DONE
        )
        self.queue(WM, 1, movies=self.movies[:1])
        self.queue(SA, 2, movies=self.movies[2:])

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="orchestration failed")

        self.assert_terminal_invariant()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        # A failed refresh keeps its settled mix: real work, a real done, and
        # blocked work that could not run.
        statuses = set(
            RefreshRequest.objects.filter(batch=self.batch).values_list(
                "status", flat=True
            )
        )
        self.assertIn(RefreshRequestStatus.DONE, statuses)
        self.assertIn(RefreshRequestStatus.BLOCKED, statuses)
        self.assertNotIn(RefreshRequestStatus.PENDING, statuses)
        self.assertTrue(done[0].pk)

    def test_fail_refuses_when_runnable_work_remains(self):
        """The defensive guard: work that *could* run must not be called failed.

        A run with executable backlog has not failed — it is unfinished. Calling
        it failed would claim the refresh resolved nothing when in fact it can
        still make progress, so the run is left RUNNING for reconciliation.
        """
        SOURCE_DAILY_LIMITS[SA] = 100  # budget available: runnable
        self.queue(SA, 2)

        with self.assertRaises(rl.UnsettledWorkError):
            rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="boom")

        self.refresh.refresh_from_db()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.RUNNING)
        self.assertIsNone(self.refresh.finished_at)

    def test_policy_blocked_work_is_settled_by_the_failure_path(self):
        """Ladder-declined work is settled even on the failure path."""
        # The ladder only declines a metered source when a *cheaper* source has
        # already resolved the title. Without a real offer the title is unknown,
        # the ladder correctly declines to gate, and the request stays runnable —
        # so the fixture has to supply the evidence the policy actually reads.
        for movie in self.movies:
            offer_for(movie, WM, available=True)
        self.queue(WM, 1, movies=self.movies[:1], status=RefreshRequestStatus.DONE)
        self.queue(SA, 2, movies=self.movies[1:])

        # Settlement uses the same policy predicate the drain uses, so the gate
        # must be declared on for this path to act.
        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=True
        ), patch.object(
            source_policy, "is_policy_metered", return_value=True
        ):
            rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="boom")

        self.assert_terminal_invariant()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, blocked_reason=BlockedReason.POLICY_NOT_NEEDED
            ).count(),
            2,
        )


class DrainBudgetExhaustionSettlesTests(FailureSettlesWorkBase):
    """Two guards, two outcomes — a run budget is not a source quota.

    A spent *source* daily quota settles the request as
    ``blocked``/``quota_exhausted``: terminal, and not retried aggressively
    until the reset. A spent *run* budget defers instead — the request stays
    ``PENDING`` for the next tick, because a run budget limits work performed,
    not future eligibility. The deferral is reported as
    ``skipped_budget_exhausted`` and is deliberately kept out of
    ``blocked_by_reason``, so the two can never be confused.
    """

    def _drain(self, budget):
        """Drive the real drain entrypoint, not a private helper.

        `process_refresh_queue` is what the worker calls, so these tests exercise
        the same guards and the same summary the production path uses. The
        upstream fetch is stubbed: the point is which rows get settled, not what
        an upstream returns.
        """
        from tracker.services import refresh_requests as rr

        with patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as fetch, patch.object(rr, "is_stale", return_value=True):
            summary = rr.process_refresh_queue(budget=budget)
        fetch.return_value = None
        return summary

    def test_run_budget_exhaustion_leaves_requests_pending(self):
        # The source's *daily* quota is deliberately left wide open: this test is
        # about the run's own execution budget, a different guard at a different
        # point in the sequence. A spent run budget means the worker has used up
        # *this tick's* work allowance — not that the source is dry — so the
        # requests stay PENDING and are retried on the next tick.
        SOURCE_DAILY_LIMITS[SA] = 100_000
        self.queue(SA, 2)

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            summary = self._drain(budget=0)

        self.assertEqual(summary["skipped_budget_exhausted"], 2)
        # A run budget is never recorded as a source-quota block.
        self.assertEqual(
            summary["blocked_by_reason"].get(BlockedReason.QUOTA_EXHAUSTED, 0), 0
        )
        self.assertEqual(summary["requests_drained"], 0)
        self.assertEqual(summary["refreshed"], 0, "an upstream call was made anyway")
        # The work is deferred, not settled: eligible for the very next tick.
        for req in RefreshRequest.objects.filter(batch=self.batch):
            self.assertEqual(req.status, RefreshRequestStatus.PENDING)
            self.assertEqual(req.blocked_reason, "")
            self.assertIsNone(req.completed_at)

    def test_a_budget_exhausted_free_collector_settles_as_blocked(self):
        """A free, non-metered source with a spent budget still settles."""
        SOURCE_DAILY_LIMITS[WM] = 2
        ApiCallCounter.objects.update_or_create(
            service=WM, defaults={"count": 2}
        )
        self.queue(WM, 2)

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            self._drain(budget=10)

        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            ).count(),
            2,
            "a free collector with a spent budget must settle as blocked",
        )

    def test_a_blocked_source_settles_through_the_settlement_boundary(self):
        """A source excluded from the candidate query in SQL still settles.

        The drain cannot settle a source whose daily budget is spent, because
        `_candidate_batch` filters blocked sources out *before* the drain ever
        sees the rows. That anti-starvation exclusion is correct and stays — it
        is what stops a large metered backlog from hiding executable work. But
        it means such rows are settled by the settlement boundary instead, not
        by the drain, and this pins that division of labour.

        The run budget here is ample (``budget=10``) so the *only* reason SA's
        rows go unreached is the SQL exclusion — the point of the test — and not
        a run-budget deferral, which by design leaves rows PENDING.
        """
        self.exhaust(SA)
        SOURCE_DAILY_LIMITS[WM] = 100_000
        extra = self.extra_titles(4)
        self.queue(SA, 2, movies=extra[:2])
        self.queue(WM, 2, movies=extra[2:])

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            self._drain(budget=10)

            # The drain settles what it could reach...
            self.assertEqual(
                RefreshRequest.objects.filter(
                    batch=self.batch, source=WM, status=RefreshRequestStatus.DONE
                ).count(),
                2,
            )
            # ...and deliberately does not reach the SQL-excluded source.
            self.assertEqual(
                RefreshRequest.objects.filter(
                    batch=self.batch, source=SA, status=RefreshRequestStatus.PENDING
                ).count(),
                2,
            )

            # The settlement boundary is what closes the gap.
            settled = rl.settle_unexecutable_requests(self.refresh)
            self.assertEqual(settled.get(BlockedReason.QUOTA_EXHAUSTED), 2)

        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.PENDING
            ).count(),
            0,
            "no decided request may remain queued",
        )

    def test_the_run8_shape_now_settles_instead_of_terminating_with_pending(self):
        """End-to-end reproduction of the production incident.

        Run 8: a worker tick raised `UnsettledWorkError` over 563 queued
        requests, the handler called `fail()`, and the run was recorded `failed`
        holding 426 pending rows. Same inputs here must now produce a failed run
        holding *zero* pending rows.
        """
        self.exhaust(SA)
        extra = self.extra_titles(3)
        self.queue(SA, 3, movies=extra)

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            self._drain(budget=0)

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="worker tick raised")

        self.assert_terminal_invariant()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            ).count(),
            3,
        )


class RunBudgetDeferralTests(FailureSettlesWorkBase):
    """A spent run budget defers work across ticks; it never fakes exhaustion.

    The live defect this pins: a worker with ``--budget 10`` and a 194-title
    watchlist settled ~181 requests *per source* as ``blocked/quota_exhausted``
    in a single tick — four of those sources declare no quota at all — and 22
    titles read as ``not_applicable`` unknowns purely because the worker never
    reached them. A run budget limits work performed, not future eligibility,
    so the unreached requests must stay ``PENDING`` and be retried next tick.
    """

    def _tick(self, budget):
        """Drive the real worker entrypoint, stubbing only the upstream fetch."""
        from tracker.services import refresh_requests as rr

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ), patch.object(
            rr.AvailabilityRefreshService, "refresh_content"
        ) as fetch, patch.object(rr, "is_stale", return_value=True):
            fetch.return_value = None
            return rr.process_refresh_queue(budget=budget)

    def test_a_spent_run_budget_defers_and_the_next_tick_completes_it(self):
        SOURCE_DAILY_LIMITS[SA] = 100_000
        first, second = self.movies[0], self.movies[1]
        self.queue(SA, 2, movies=[first, second])

        first_tick = self._tick(budget=1)
        self.assertEqual(first_tick["skipped_budget_exhausted"], 1)
        self.assertEqual(first_tick["requests_pending"], 1)

        # The run still owns runnable work, so finalisation defers: RUNNING.
        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)
        self.refresh.refresh_from_db()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.RUNNING)
        self.assertIsNone(self.refresh.finished_at)
        self.assertEqual(
            RefreshRequest.objects.get(movie=second).status,
            RefreshRequestStatus.PENDING,
            "the deferred request must stay eligible for the next tick",
        )

        # The next tick picks the deferred request up and finishes the run.
        second_tick = self._tick(budget=10)
        self.assertEqual(second_tick["refreshed"], 1)
        rl.AvailabilityRefreshLifecycle.finalise(self.refresh)
        self.assert_terminal_invariant()
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.DONE
            ).count(),
            2,
        )

    def test_deferred_work_is_never_recorded_as_quota_exhausted(self):
        SOURCE_DAILY_LIMITS[SA] = 100_000
        self.queue(SA, 2)

        summary = self._tick(budget=0)

        self.assertEqual(summary["skipped_budget_exhausted"], 2)
        self.assertEqual(
            summary["blocked_by_reason"].get(BlockedReason.QUOTA_EXHAUSTED, 0), 0
        )
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch,
                status=RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            ).count(),
            0,
            "a run budget must never be recorded as source-quota exhaustion",
        )

    def test_repeated_ticks_do_not_duplicate_requests(self):
        SOURCE_DAILY_LIMITS[SA] = 100_000
        movie = self.movies[0]
        self.queue(SA, 1, movies=[movie])

        for _ in range(3):
            self._tick(budget=0)
        self.assertEqual(
            RefreshRequest.objects.filter(batch=self.batch, movie=movie).count(),
            1,
            "a deferred request must be retried in place, never re-queued",
        )

        self._tick(budget=10)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, movie=movie, status=RefreshRequestStatus.DONE
            ).count(),
            1,
        )

    def test_a_deferred_title_does_not_read_as_not_applicable(self):
        """A partial batch is an *absence of information*, not a negative answer.

        With run-budget rows previously settled ``blocked``, the title classified
        as ``not_applicable`` — a scheduling fact misreported as a verdict.
        Deferred, it is honestly ``queued``.
        """
        from tracker.services.availability_report import (
            AvailabilityResolutionReason,
            title_source_report,
        )

        SOURCE_DAILY_LIMITS[SA] = 100_000
        movie = self.movies[0]
        self.queue(SA, 1, movies=[movie])

        self._tick(budget=0)

        entry = title_source_report([movie.pk], batch=self.batch)[movie.pk]
        self.assertEqual(entry["state"], "unknown")
        self.assertEqual(entry["reason"], AvailabilityResolutionReason.QUEUED)
        self.assertNotEqual(
            entry["reason"], AvailabilityResolutionReason.NOT_APPLICABLE
        )


class TerminalInvariantAppliesToFailedTests(FailureSettlesWorkBase):
    def test_no_terminal_status_may_hold_queued_rows(self):
        """The invariant, asserted uniformly across all three terminal statuses.

        Whichever status the aggregate logic picks, a terminal run owns nothing
        outstanding. This is the P6a/P6b boundary stated as a database fact.
        """
        self.exhaust(SA)
        self.queue(SA, 2)

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="run-level failure")
        self.assert_terminal_invariant()

        # And the same holds for a finalised run, so the guarantee is not
        # specific to the failure path.
        other_batch = uuid.uuid4()
        other = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=other_batch,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movies[0].pk],
        )
        RefreshRequest.objects.create(
            movie=self.movies[0],
            source=SA,
            status=RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            batch=other_batch,
            completed_at=timezone.now(),
        )
        rl.AvailabilityRefreshLifecycle.finalise(other)
        other.refresh_from_db()
        self.assertIsNotNone(other.finished_at)
        self.assertEqual(
            RefreshRequest.objects.filter(batch=other_batch)
            .exclude(status__in=["done", "failed", "blocked"])
            .count(),
            0,
        )

    def test_the_worker_handler_does_not_kill_the_worker_on_refusal(self):
        """The handler must swallow the invariant's exception.

        That handler exists so one bad tick cannot kill the worker; letting
        `UnsettledWorkError` escape it would reintroduce exactly that. The run is
        left RUNNING, which is the truthful state for a run with executable
        backlog.
        """
        SOURCE_DAILY_LIMITS[SA] = 100
        self.queue(SA, 2)  # runnable -> fail() refuses

        # Reproduce the handler's own body. The tick raises; the handler calls
        # fail(); fail() refuses. The handler must return normally — this code
        # exists so one bad tick cannot kill the worker, and an escaping
        # UnsettledWorkError would reintroduce exactly that failure mode.
        with patch.object(
            refresh_worker, "claim_active_run", return_value=self.refresh
        ), patch.object(
            refresh_worker,
            "process_refresh_queue",
            side_effect=RuntimeError("simulated tick failure"),
        ):
            cmd = refresh_worker.Command()
            cmd.stdout = io.StringIO()
            try:
                cmd.handle(interval=1, budget=10, once=True)
            except UnsettledWorkError:  # pragma: no cover - the regression
                self.fail("the invariant exception escaped the worker's handler")

        self.refresh.refresh_from_db()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.RUNNING)
        self.assertIsNone(self.refresh.finished_at)