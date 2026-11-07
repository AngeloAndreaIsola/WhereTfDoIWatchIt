"""P6b — refresh result as a pure reduction of settled evidence.

This is the final layer and the only one that changed:

    request state → source evidence → title classification → **result**

`derive_refresh_result` reads what the other three layers already persisted. It
does not classify titles, consult upstreams, choose sources, or move a request
row. Everything here therefore arranges persisted state and asserts which status
comes out — no call-order assertions, and no mocking of the classifier.

The precedence being pinned:

    FAILED     a genuine lifecycle/orchestration failure — reached through
               `fail()`, never derived from ordinary evidence
    PARTIAL    lifecycle completed, settled evidence cannot support a complete
               determination
    COMPLETED  every applicable title has sufficient evidence, *including*
               negative answers and policy skips that follow a real resolution

Two distinctions carry most of the weight and are easy to lose:

* **`done` is not `failed`.** A source that ran and found nothing did its job.
  Treating "no offers" as failure would make every unavailable title read as a
  broken refresh.
* **`blocked` is not automatically `PARTIAL`.** `policy_not_needed` and
  `superseded` record work another source or a newer request legitimately
  replaced. Only `quota_exhausted`/`source_disabled` leave a real gap.

Each terminal case asserts the P6a database invariant directly — no queued or
running requests, no queued or running source rows, `finished_at` set — because
the whole point of the boundary is that the result describes settled state.
"""
import uuid

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    ApiCallCounter,
    AvailabilityRefresh,
    AvailabilityRefreshSource,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    BlockedReason,
    Movie,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.availability_report import NON_TERMINAL_SOURCE_STATUSES
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS
from tracker.services.refresh_lifecycle import (
    AvailabilityRefreshLifecycle,
    UnsettledWorkError,
    derive_refresh_result,
    finalise_run,
)

from .test_refresh_lifecycle import add_request, make_movie, make_run, offer_for

SA = "streaming_availability"
WM = "watchmode"
YT = "youtube"
MI = "mediaset_infinity"


class ResultBase(TestCase):
    def setUp(self):
        self._saved_limits = dict(SOURCE_DAILY_LIMITS)
        SOURCE_DAILY_LIMITS[SA] = 100
        SOURCE_DAILY_LIMITS[WM] = 100
        ApiCallCounter.objects.all().delete()
        self.movie = make_movie(870_001, "P6b")

    def tearDown(self):
        SOURCE_DAILY_LIMITS.clear()
        SOURCE_DAILY_LIMITS.update(self._saved_limits)
        super().tearDown()

    def exhaust(self, source, limit=100):
        SOURCE_DAILY_LIMITS[source] = limit
        ApiCallCounter.objects.update_or_create(
            service=source, defaults={"count": limit}
        )

    def source_row(self, run, source, status, reason=""):
        return AvailabilityRefreshSource.objects.create(
            refresh=run, source=source, status=status, reason=reason
        )

    def assert_terminal_invariant(self, run):
        """The P6a invariant, asserted against the database.

        Everything is re-read from the DB rather than trusted from an
        in-memory instance: a row created with an enum member keeps that member
        on the Python object even though the column stores the lowercase value,
        so comparing the stale object would silently pass or fail for the wrong
        reason.
        """
        run.refresh_from_db()
        rows = list(
            RefreshRequest.objects.filter(batch=run.batch).values_list(
                "status", flat=True
            )
        )
        self.assertIn(
            run.status,
            [
                AvailabilityRefreshStatus.COMPLETED.value,
                AvailabilityRefreshStatus.PARTIAL.value,
                AvailabilityRefreshStatus.FAILED.value,
            ],
            f"{run.status} is not a terminal result",
        )
        self.assertIsNotNone(run.finished_at, "a terminal run must be finished_at set")
        self.assertEqual(
            [
                s
                for s in rows
                if s
                not in (
                    RefreshRequestStatus.DONE.value,
                    RefreshRequestStatus.FAILED.value,
                    RefreshRequestStatus.BLOCKED.value,
                )
            ],
            [],
            "terminal run owns queued/running requests",
        )
        self.assertEqual(
            [
                f"{r.source}={r.status}"
                for r in run.sources.all()
                if r.status in NON_TERMINAL_SOURCE_STATUSES
            ],
            [],
            "terminal run owns queued/running source rows",
        )

    def finalise(self, run, movie_ids=None):
        finalise_run(run, movie_ids or [self.movie.pk])
        run.refresh_from_db()
        return run.status


class CompletedResultTests(ResultBase):
    """1, 2, 5, 6 — sufficient evidence, including negative answers."""

    def test_all_applicable_work_succeeded_is_completed(self):
        """1. Fully successful refresh."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        for source in (WM, MI, YT, SA):
            add_request(self.movie, source, RefreshRequestStatus.DONE, batch)
            self.source_row(run, source, "completed")

        self.assertEqual(self.finalise(run), AvailabilityRefreshStatus.COMPLETED)
        self.assert_terminal_invariant(run)

    def test_valid_negative_answers_are_completed(self):
        """2/Example C. Sources ran and found nothing: successful work.

        "No offers anywhere" is a *determination*, so the refresh completed. The
        negative answer is what distinguishes this from a refresh that could not
        find out.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        for source in (WM, MI, YT):
            add_request(self.movie, source, RefreshRequestStatus.DONE, batch, outcome=RefreshRequestOutcome.NO_OFFER)
            self.source_row(run, source, "completed")

        status = self.finalise(run)
        self.assert_terminal_invariant(run)
        self.assertEqual(status, AvailabilityRefreshStatus.COMPLETED)
        # And it really is a negative determination, not a silent pass: every
        # source that ran returned `no_offer`, so the title was determined to
        # be unavailable rather than left unresolved.
        # Note what is *not* asserted: `run.unavailable_titles`. The global
        # classifier marks this title `unknown` because Streaming Availability
        # has no request row in this batch and so reads `not_checked` — the
        # report scans every registry source, not this run's scope. That is the
        # classifier's question ("what is true about this title everywhere"),
        # not this run's ("did I establish what I was responsible for"). Every
        # source this run *did* engage answered `no_offer`, so the run's
        # evidence is complete and the result is COMPLETED.
        self.assertEqual(run.available_titles, 0)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=batch, status=RefreshRequestStatus.DONE
            ).count(),
            3,
            "every engaged source produced a definitive negative answer",
        )

    def test_policy_skip_after_a_cheaper_source_resolved_is_completed(self):
        """5/Example A. collector→AVAILABLE, SA→blocked/policy_not_needed."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, MI)
        add_request(self.movie, MI, RefreshRequestStatus.DONE, batch)
        # A `blocked` row must carry its reason (DB check constraint), so it is
        # created settled rather than pending-then-patched.
        RefreshRequest.objects.create(
            movie=self.movie,
            source=SA,
            status=RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.POLICY_NOT_NEEDED,
            batch=batch,
            completed_at=timezone.now(),
        )
        self.source_row(run, MI, "completed")
        self.source_row(run, SA, "skipped")

        self.assertEqual(self.finalise(run), AvailabilityRefreshStatus.COMPLETED)
        self.assert_terminal_invariant(run)
        self.assertEqual(run.available_titles, 1)

    def test_a_not_run_source_with_no_applicable_work_is_completed(self):
        """6. A collector in the registry that held no work is not a gap.

        This is the case that would make a refresh partial merely for containing
        a source that never had anything to do.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        self.source_row(run, WM, "completed")
        # Present in the registry, but this refresh never asked it anything.
        self.source_row(run, YT, "not_run", reason="collector_not_run")

        self.assertEqual(self.finalise(run), AvailabilityRefreshStatus.COMPLETED)
        self.assert_terminal_invariant(run)


class PartialResultTests(ResultBase):
    """3, 4, 9 — settled, but the evidence cannot support a full answer."""

    def test_quota_blocked_work_is_partial_not_failed(self):
        """3/Example B. Quota exhaustion alone is never FAILED."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        self.exhaust(SA)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)
        self.source_row(run, WM, "completed")
        self.source_row(run, SA, "quota_exhausted", reason="quota_exhausted")

        status = self.finalise(run)
        self.assertEqual(status, AvailabilityRefreshStatus.PARTIAL)
        self.assertNotEqual(status, AvailabilityRefreshStatus.FAILED)
        self.assert_terminal_invariant(run)

    def test_a_failed_source_is_partial_not_failed(self):
        """4. A source-level failure contributes evidence toward PARTIAL."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, MI, RefreshRequestStatus.FAILED, batch)
        self.source_row(run, WM, "completed")
        self.source_row(run, MI, "failed")

        status = self.finalise(run)
        self.assertEqual(status, AvailabilityRefreshStatus.PARTIAL)
        self.assertNotEqual(status, AvailabilityRefreshStatus.FAILED)
        self.assert_terminal_invariant(run)

    def test_partial_requires_every_owned_row_to_be_terminal(self):
        """9. PARTIAL is only reachable after all owned work is settled.

        Guards the P6a/P6b boundary: the reducer must refuse while a queue
        exists, so `PARTIAL` can never quietly mean "the worker stopped early".
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)

        with self.assertRaises(UnsettledWorkError):
            derive_refresh_result(run)

        run.refresh_from_db()
        self.assertNotIn(
            run.status,
            [
                AvailabilityRefreshStatus.COMPLETED.value,
                AvailabilityRefreshStatus.PARTIAL.value,
                AvailabilityRefreshStatus.FAILED.value,
            ],
        )
        self.assertIsNone(run.finished_at)

    def test_an_engaged_source_that_failed_leaves_the_title_unresolved(self):
        """PARTIAL with no blocked rows at all: a transient failure is enough.

        Every row settled, none is blocked, and no quota was spent — yet the
        refresh is PARTIAL because the source it engaged could not answer. This
        is the case that proves PARTIAL is about the *determination*, not about
        counting blocked rows.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, MI, RefreshRequestStatus.FAILED, batch)
        self.source_row(run, WM, "completed")
        self.source_row(run, MI, "failed")

        status = self.finalise(run)
        self.assertEqual(status, AvailabilityRefreshStatus.PARTIAL)
        self.assert_terminal_invariant(run)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=batch, status=RefreshRequestStatus.BLOCKED
            ).count(),
            0,
            "this case must not depend on blocked work",
        )


class FailedResultTests(ResultBase):
    """7, 8 — a genuine run-level failure, not an evidence reading."""

    def test_a_genuine_lifecycle_failure_is_failed(self):
        """7/Example D. The tick raised: FAILED regardless of evidence.

        Every request settled successfully first, which is the point: FAILED
        records that the orchestration broke, not that the work was bad.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        self.source_row(run, WM, "completed")

        AvailabilityRefreshLifecycle.fail(run, error="worker tick raised")

        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.FAILED)
        self.assert_terminal_invariant(run)

    def test_a_failed_refresh_may_hold_done_failed_and_blocked_rows_together(self):
        """8. Settling work is not declaring it successful.

        A FAILED refresh still holds real successes, real failures, and blocked
        work. Those rows are the evidence; the run-level error is the status.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        self.exhaust(SA)
        self.exhaust(WM)
        second = make_movie(870_002, "P6b2")
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, MI, RefreshRequestStatus.FAILED, batch)
        for source in (WM, SA):
            add_request(second, source, RefreshRequestStatus.PENDING, batch)
        self.source_row(run, WM, "quota_exhausted", reason="quota_exhausted")
        self.source_row(run, SA, "quota_exhausted", reason="quota_exhausted")
        self.source_row(run, MI, "failed")

        AvailabilityRefreshLifecycle.fail(run, error="orchestration failed")

        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.FAILED)
        self.assert_terminal_invariant(run)

        # The mixture survives verbatim: no row was rewritten to make the
        # terminal status tidier.
        statuses = set(
            RefreshRequest.objects.filter(batch=batch).values_list(
                "status", flat=True
            )
        )
        self.assertIn(RefreshRequestStatus.DONE, statuses)
        self.assertIn(RefreshRequestStatus.FAILED, statuses)
        self.assertIn(RefreshRequestStatus.BLOCKED, statuses)
        self.assertNotIn(RefreshRequestStatus.PENDING, statuses)

    def test_evidence_alone_never_produces_failed(self):
        """The reducer cannot return FAILED. Only `fail()` can.

        Even the worst settled evidence — every applicable source failed and
        every title unresolved — reads PARTIAL. FAILED stays meaningful because
        nothing else can produce it.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        for source in (WM, MI, YT, SA):
            add_request(self.movie, source, RefreshRequestStatus.FAILED, batch)
            self.source_row(run, source, "failed")

        status = self.finalise(run)
        self.assertNotEqual(
            status,
            AvailabilityRefreshStatus.FAILED,
            "FAILED must not be derivable from ordinary evidence",
        )
        self.assertEqual(status, AvailabilityRefreshStatus.PARTIAL)
        self.assert_terminal_invariant(run)


class TerminalInvariantStillEnforcedTests(ResultBase):
    """11. P6a remains enforced after the P6b change."""

    def test_a_running_source_row_prevents_a_result(self):
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        self.source_row(run, WM, "running")

        with self.assertRaises(UnsettledWorkError) as ctx:
            derive_refresh_result(run)
        self.assertIn("source row", str(ctx.exception))

    def test_the_terminal_invariant_is_stated_for_all_three_results(self):
        """A matrix over the outcomes, each reached only through settled work.

        Every case here is fully terminal: all request rows and all source rows
        settled, and `finished_at` set. That is deliberate and is the whole
        point of the matrix. An earlier version built the `partial` case from a
        `pending` request, which is precisely the shape P6a forbids — the run
        still owned queued work, so `UnsettledWorkError` was raised and the test
        errored.

        PARTIAL is therefore built from a *settled* gap: the metered source is
        out of budget, so its request is `blocked`/`quota_exhausted` (a real
        reason, recorded) and its source row is `quota_exhausted`. Nothing is
        outstanding, so the run may legitimately terminate as PARTIAL.
        """
        cases = [
            # (requests, blocked requests, source rows, expected, needs_offer)
            # Request statuses use the enum's stored (lowercase) values.
            ([("WM", "done")], [],
             [("WM", "completed")], "completed", True),
            ([("WM", "done")], [("SA", BlockedReason.QUOTA_EXHAUSTED)],
             [("WM", "completed"), ("SA", "quota_exhausted")], "partial", True),
            ([("MI", "failed")], [],
             [("MI", "failed")], "partial", False),
        ]
        for index, (reqs, blocked, sources, expected, needs_offer) in enumerate(cases):
            with self.subTest(case=expected):
                # A fresh title per case: classification is per-title state, so
                # reusing one title would let an earlier case's offer decide a
                # later case's result.
                movie = make_movie(870_100 + index, f"Matrix{index}")
                if needs_offer:
                    offer_for(movie, "watchmode")
                batch = uuid.uuid4()
                run = make_run(batch=batch, movie_ids=[movie])
                for source, status in reqs:
                    add_request(movie, source, status, batch)
                for source, reason in blocked:
                    RefreshRequest.objects.create(
                        movie=movie,
                        source=source,
                        status=RefreshRequestStatus.BLOCKED,
                        blocked_reason=reason,
                        batch=batch,
                        completed_at=timezone.now(),
                    )
                for source, status in sources:
                    self.source_row(run, source, status)
                finalise_run(run, [movie.pk])
                run.refresh_from_db()
                self.assertEqual(run.status, expected)
                self.assert_terminal_invariant(run)
                run.delete()
                movie.delete()


class HistoricalRunsUntouchedTests(ResultBase):
    """10. Runs 5/6/8 are never reopened, re-derived, or mutated."""

    def test_a_terminal_run_is_not_re_derived(self):
        legacy = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=uuid.uuid4(),
            status=AvailabilityRefreshStatus.PARTIAL,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            title_filter=[self.movie.pk],
        )
        legacy_row = self.source_row(legacy, WM, "running")
        before = (legacy.status, legacy.finished_at)

        AvailabilityRefreshLifecycle.finalise(legacy)

        legacy.refresh_from_db()
        legacy_row.refresh_from_db()
        self.assertEqual((legacy.status, legacy.finished_at), before)
        self.assertEqual(legacy_row.status, "running")

    def test_a_legacy_terminal_run_with_unsettled_rows_still_cannot_be_derived(self):
        """P6a's guard applies to historical runs too.

        They are preserved *as they are*, not exempt from the invariant: the
        reducer refuses, so nothing can quietly rewrite them.
        """
        legacy = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=uuid.uuid4(),
            status=AvailabilityRefreshStatus.FAILED,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            title_filter=[self.movie.pk],
        )
        self.source_row(legacy, WM, "running")

        with self.assertRaises(UnsettledWorkError):
            derive_refresh_result(legacy)

        legacy.refresh_from_db()
        self.assertEqual(legacy.status, AvailabilityRefreshStatus.FAILED)