"""Source-row settlement: the second half of the terminal invariant.

`settle_unexecutable_requests` settles requests. Nothing settled *source* rows,
so the source half of the P6a invariant was checked but never satisfied — the
`unsettled source row` guards inside `fail()` and `finalise()` could not fire
and were effectively dead code. Measured on the live database, every terminal
run (5, 6 and 8) ended holding three `running` source rows: `mediaset_infinity`,
`youtube`, `watchmode`.

These tests pin the new boundary, and pin it *narrowly*: settlement answers
"why is this source no longer executable in this run?", not "make everything
terminal". A row that could still run must survive settlement so that `fail()`
refuses, because manufacturing terminality by discarding runnable work is the
precise failure this whole mechanism exists to prevent.

Historical runs are deliberately untouched. Runs 5/6/8 keep their legacy
`running` source rows as evidence of the old bug; the invariant is enforced for
current lifecycle transitions only. `test_historical_terminal_runs_are_left_alone`
asserts the new code does not reach back and repair them.
"""
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    ApiCallCounter,
    AvailabilityRefresh,
    AvailabilityRefreshSource,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    AvailabilityResolutionReason,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import source_policy
from tracker.services import refresh_lifecycle as rl
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS

SA = "streaming_availability"
WM = "watchmode"
YT = "youtube"


class SourceSettlementBase(TestCase):
    def setUp(self):
        self._saved_limits = dict(SOURCE_DAILY_LIMITS)
        SOURCE_DAILY_LIMITS[SA] = 100
        SOURCE_DAILY_LIMITS[WM] = 100
        SOURCE_DAILY_LIMITS[YT] = 100
        ApiCallCounter.objects.all().delete()
        self.movie = Movie.objects.create(tmdb_id=881_001, title="T", year=2021)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)
        self.batch = uuid.uuid4()
        self.refresh = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movie.pk],
        )

    def tearDown(self):
        SOURCE_DAILY_LIMITS.clear()
        SOURCE_DAILY_LIMITS.update(self._saved_limits)
        super().tearDown()

    def exhaust(self, source, limit=100):
        SOURCE_DAILY_LIMITS[source] = limit
        ApiCallCounter.objects.update_or_create(
            service=source, defaults={"count": limit}
        )

    def source_row(self, source, status="running", **kw):
        return AvailabilityRefreshSource.objects.create(
            refresh=self.refresh, source=source, status=status, **kw
        )

    def assert_no_unsettled_sources(self):
        bad = [
            f"{r.source}={r.status}"
            for r in self.refresh.sources.all()
            if r.status in rl.NON_TERMINAL_SOURCE_STATUSES
        ]
        self.assertEqual(bad, [], "terminal run left source rows in flight")


class SourceSettlementTests(SourceSettlementBase):
    def test_a_budget_exhausted_source_row_settles_as_quota_exhausted(self):
        """Applicable but unspendable → quota, with the reason recorded."""
        self.exhaust(SA)
        row = self.source_row(SA)
        self.source_row(WM, status="running")  # budget available, has work

        settled = rl.settle_unexecutable_source_rows(self.refresh)

        row.refresh_from_db()
        self.assertEqual(row.status, "quota_exhausted")
        self.assertEqual(row.reason, AvailabilityResolutionReason.QUOTA_EXHAUSTED)
        self.assertIsNotNone(row.finished_at)
        # WM has budget but no request in this run named it, so `running` was
        # only a placeholder and `skipped` is the truthful terminal state.
        self.assertEqual(settled, {f"{SA}=quota_exhausted": 1, f"{WM}=skipped": 1})
        self.assertEqual(
            self.refresh.sources.get(source=WM).reason,
            AvailabilityResolutionReason.NOT_CHECKED,
        )

    def test_a_source_with_no_work_of_this_run_settles_as_skipped(self):
        """No request in this run named it, so `running` was a placeholder."""
        RefreshRequest.objects.create(
            movie=self.movie,
            source=WM,
            status=RefreshRequestStatus.DONE,
            batch=self.batch,
            completed_at=timezone.now(),
        )
        idle = self.source_row(YT)

        rl.settle_unexecutable_source_rows(self.refresh)

        idle.refresh_from_db()
        self.assertEqual(idle.status, "skipped")
        self.assertEqual(idle.reason, AvailabilityResolutionReason.NOT_CHECKED)

    def test_a_genuinely_runnable_source_row_is_never_falsely_settled(self):
        """It has budget *and* work in this run, so it must survive.

        Settling it would let `fail()` reach terminality by discarding work that
        could still have executed.
        """
        # The request must already be settled, or the *request* guard fires
        # first and masks the source guard this test is about.
        RefreshRequest.objects.create(
            movie=self.movie,
            source=WM,
            status=RefreshRequestStatus.DONE,
            batch=self.batch,
            completed_at=timezone.now(),
        )
        runnable = self.source_row(WM)

        settled = rl.settle_unexecutable_source_rows(self.refresh)

        runnable.refresh_from_db()
        self.assertEqual(
            runnable.status, "running", "a runnable source row must survive settlement"
        )
        self.assertEqual(settled, {})

        # And the refusal is what protects the invariant.
        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            with self.assertRaises(rl.UnsettledWorkError) as ctx:
                rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="boom")
        self.assertIn("source row", str(ctx.exception))

    def test_a_real_source_failure_is_never_reclassified_as_quota(self):
        """A genuine upstream failure keeps its own meaning and reason."""
        self.exhaust(SA)
        failed = self.source_row(SA, status="failed")
        self.source_row(WM, status="running")

        rl.settle_unexecutable_source_rows(self.refresh)

        failed.refresh_from_db()
        self.assertEqual(
            failed.status, "failed", "a real failure must not become a quota result"
        )

    def test_settlement_is_scoped_to_this_refresh_only(self):
        """Another run's source rows are never read or written."""
        self.exhaust(SA)
        mine = self.source_row(SA)

        other_batch = uuid.uuid4()
        other = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=other_batch,
            status=AvailabilityRefreshStatus.RUNNING,
            started_at=timezone.now(),
            title_filter=[self.movie.pk],
        )
        theirs = AvailabilityRefreshSource.objects.create(
            refresh=other, source=SA, status="running"
        )

        rl.settle_unexecutable_source_rows(self.refresh)

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.status, "quota_exhausted")
        self.assertEqual(
            theirs.status, "running", "settlement leaked into another run"
        )


class FailedRefreshSettlesSourceRowsTests(SourceSettlementBase):
    def test_a_failed_refresh_becomes_terminal_only_after_settling_sources(self):
        """The run-8 shape, on a fresh run.

        Run 8 reached `failed` holding three `running` source rows. The same
        shape here must terminate with zero unsettled source rows — settled by
        the boundary, not by repairing history.
        """
        self.exhaust(SA)
        self.source_row(SA)          # out of budget -> quota_exhausted
        self.source_row(YT)          # no work in this run -> skipped
        RefreshRequest.objects.create(
            movie=self.movie, source=SA, batch=self.batch
        )  # settled as blocked/quota_exhausted

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="worker tick raised")

        self.refresh.refresh_from_db()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        self.assertIsNotNone(self.refresh.finished_at)
        self.assert_no_unsettled_sources()

        statuses = dict(
            self.refresh.sources.values_list("source", "status")
        )
        self.assertEqual(statuses[SA], "quota_exhausted")
        self.assertEqual(statuses[YT], "skipped")

    def test_failed_still_means_run_level_failure_not_source_exhaustion(self):
        """Source exhaustion alone never produces FAILED.

        This is the distinction P6b will rely on: quota exhaustion is evidence,
        while `FAILED` records that the orchestration itself broke.
        """
        self.exhaust(SA)
        self.source_row(SA)
        RefreshRequest.objects.create(movie=self.movie, source=SA, batch=self.batch)

        with patch.object(
            source_policy, "consult_metered_only_when_unresolved", return_value=False
        ):
            rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="")

        self.refresh.refresh_from_db()
        self.assertEqual(self.refresh.status, AvailabilityRefreshStatus.FAILED)
        self.assertEqual(
            self.refresh.error, "", "no run-level error means no invented failure reason"
        )
        # The evidence is still visible as exhaustion, not as a failure.
        self.assertEqual(
            self.refresh.sources.get(source=SA).status, "quota_exhausted"
        )
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=self.batch, status=RefreshRequestStatus.BLOCKED
            ).count(),
            1,
        )

    def test_the_full_invariant_holds_for_a_failed_refresh(self):
        """Both halves: no outstanding requests *and* no unsettled sources."""
        self.exhaust(SA)
        self.exhaust(WM)
        self.source_row(SA)
        self.source_row(WM)
        for source in (SA, WM):
            RefreshRequest.objects.create(
                movie=self.movie, source=source, batch=self.batch
            )

        rl.AvailabilityRefreshLifecycle.fail(self.refresh, error="tick raised")

        self.assert_no_unsettled_sources()
        self.assertEqual(
            RefreshRequest.objects.filter(batch=self.batch)
            .exclude(status__in=["done", "failed", "blocked"])
            .count(),
            0,
        )
        self.assertIsNotNone(
            AvailabilityRefresh.objects.get(pk=self.refresh.pk).finished_at
        )


class HistoricalRunsUntouchedTests(TestCase):
    """Historical terminal runs keep their legacy rows, by design.

    Runs 5/6/8 predate source-row settlement and are evidence of the old bug.
    The new code enforces the invariant for current lifecycle transitions only;
    it must not reach back and repair them, because that would rewrite the record
    of what happened. There is deliberately no migration to make historical data
    conform.
    """

    def setUp(self):
        self.batch = uuid.uuid4()
        self.legacy = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=self.batch,
            status=AvailabilityRefreshStatus.FAILED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            error="worker tick raised",
        )
        self.legacy_row = AvailabilityRefreshSource.objects.create(
            refresh=self.legacy, source=WM, status="running"
        )

    def test_settlement_refuses_a_terminal_run_outright(self):
        """The guard that protects history, asserted directly.

        Run this against a terminal run holding a `running` source row and a
        spent budget — the exact live shape of runs 5/6/8. Settlement returns
        nothing and writes nothing, so the historical row survives. This is what
        makes "we do not repair historical runs" enforced rather than merely
        intended: an out-of-budget source cannot be settled on a closed run even
        if some future caller asks.
        """
        SOURCE_DAILY_LIMITS[WM] = 0
        try:
            settled = rl.settle_unexecutable_source_rows(self.legacy)
        finally:
            SOURCE_DAILY_LIMITS.pop(WM, None)

        self.assertEqual(settled, {}, "a terminal run must not be settled")
        self.legacy_row.refresh_from_db()
        self.assertEqual(
            self.legacy_row.status,
            "running",
            "historical evidence must not be rewritten by new code",
        )
        self.assertFalse(
            self.legacy_row.reason, "no reason was invented for a legacy row"
        )

    def test_the_failure_path_cannot_reopen_a_terminal_run(self):
        """`fail()` must not resurrect a closed run, even with a clean queue.

        With no outstanding requests, the only remaining unsettled state is the
        legacy `running` source row — which settlement correctly refuses to touch
        because the run is already terminal. So the source guard refuses, the run
        stays closed, and the historical row is never "tidied up" to let a
        re-fail through. That guard is the reason this run could not have been
        silently rewritten even if someone tried.
        """
        with self.assertRaises(rl.UnsettledWorkError) as ctx:
            rl.AvailabilityRefreshLifecycle.fail(self.legacy, error="re-run attempt")
        self.assertIn("source row", str(ctx.exception))

        self.legacy.refresh_from_db()
        self.assertEqual(self.legacy.status, AvailabilityRefreshStatus.FAILED)
        self.assertEqual(
            AvailabilityRefreshSource.objects.get(pk=self.legacy_row.pk).status,
            "running",
            "the legacy row is still evidence, even after another fail() call",
        )