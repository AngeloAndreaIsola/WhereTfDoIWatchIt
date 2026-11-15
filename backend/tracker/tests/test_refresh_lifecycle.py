"""Step 9 — the run lifecycle, derived from persisted state.

The contract:

    QUEUED -> RUNNING -> COMPLETED | PARTIAL | FAILED

with the three terminal states meaning:

    COMPLETED   every scheduled request settled, no unresolved work remains
    PARTIAL     the run processed what it could; some work was skipped,
                blocked, or failed at the *source* level
    FAILED      the run itself could not execute

and the property that makes it worth having at all:

    the status is computed by querying persisted rows, never from an
    in-memory counter the worker happened to be holding

**Frozen in this step and not touched here:** ``classify_title``,
``AvailabilityResolutionReason``, ``PolicySkipReason``, ``SOURCE_ROLES``,
``decide_sources_for_title``, the Step 8 enqueue behaviour, and the
counterfactual identity. These tests only arrange persisted state and assert
which status comes out.
"""
import datetime as dt
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from availability.models import ContentType, Offer
from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    BlockedReason,
    Movie,
    Provider,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import source_policy
from tracker.services.availability_report import TitleAvailabilityState
from tracker.services.rate_limit import SOURCE_DAILY_LIMITS
from tracker.services.refresh_lifecycle import (
    STALE_RUNNING_AFTER,
    AvailabilityRefreshLifecycle,
    claim_active_run,
    finalise_run,
    reconcile_stale_runs,
    settle_unexecutable_requests,
)

SA = "streaming_availability"
WM = "watchmode"


def make_movie(tmdb_id: int, title: str = "T") -> Movie:
    movie = Movie.objects.create(tmdb_id=tmdb_id, title=title, year=2020)
    WatchlistItem.objects.get_or_create(user_id=1, movie=movie)
    return movie


def offer_for(movie: Movie, source_slug: str) -> Offer:
    from availability.models import Country, Source

    provider, _ = Provider.objects.get_or_create(
        slug=f"{source_slug}-prov", defaults={"name": source_slug, "category": "stream"}
    )
    country, _ = Country.objects.get_or_create(code="IT", defaults={"name": "Italy"})
    source, _ = Source.objects.get_or_create(
        slug=source_slug, defaults={"name": source_slug}
    )
    return Offer.objects.create(
        tmdb_id=movie.tmdb_id,
        provider=provider,
        country=country,
        source=source,
        content_type=ContentType.MOVIE,
        offer_type="subscription",
        currently_available=True,
    )


def make_run(batch=None, movie_ids=None) -> AvailabilityRefresh:
    return AvailabilityRefresh.objects.create(
        run_type=AvailabilityRefreshType.WATCHLIST,
        status=AvailabilityRefreshStatus.QUEUED,
        batch=batch or uuid.uuid4(),
        title_filter=[m.pk for m in (movie_ids or [])],
        started_at=timezone.now(),
    )


def add_request(movie: Movie, source: str, status: str, batch, *, outcome=RefreshRequestOutcome.UNKNOWN) -> RefreshRequest:
    return RefreshRequest.objects.create(
        movie=movie, source=source, status=status, batch=batch,
        outcome=outcome,
        requested_at=timezone.now(),
        completed_at=timezone.now() if status != RefreshRequestStatus.PENDING else None,
    )


class TransitionTests(TestCase):
    def setUp(self):
        self.movie = make_movie(800, "Lifecycle")

    def test_a_new_run_starts_queued(self):
        run = make_run(movie_ids=[self.movie])
        self.assertEqual(run.status, AvailabilityRefreshStatus.QUEUED)
        self.assertTrue(run.is_active)

    def test_claiming_moves_queued_to_running(self):
        run = make_run(movie_ids=[self.movie])
        claimed = claim_active_run(batch=run.batch)
        self.assertEqual(claimed.status, AvailabilityRefreshStatus.RUNNING)

    def test_claiming_is_idempotent(self):
        """A worker restarting mid-run must re-assert ownership, not be
        refused — otherwise a restart would abandon the run."""
        run = make_run(movie_ids=[self.movie])
        claim_active_run(batch=run.batch)
        again = claim_active_run(batch=run.batch)
        self.assertEqual(again.status, AvailabilityRefreshStatus.RUNNING)

    def test_a_terminal_run_is_never_re_adopted(self):
        """A finished run is finished. Re-claiming it would make the record
        lie about work that already ended."""
        run = make_run(movie_ids=[self.movie])
        run.status = AvailabilityRefreshStatus.COMPLETED
        run.save(update_fields=["status"])
        claimed = claim_active_run(batch=run.batch)
        self.assertIsNone(claimed)

    def test_no_active_run_claims_nothing(self):
        self.assertIsNone(claim_active_run())

    def test_a_tick_failure_marks_the_run_failed(self):
        run = make_run(movie_ids=[self.movie])
        AvailabilityRefreshLifecycle.start(run)
        AvailabilityRefreshLifecycle.fail(run, error="boom")
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.FAILED)
        self.assertIn("boom", run.error)


class TerminalStatusTests(TestCase):
    """COMPLETED / PARTIAL / FAILED, and the boundary between them."""

    def setUp(self):
        self.movie = make_movie(801, "Terminal")

    def test_all_work_succeeded_and_nothing_unresolved_is_completed(self):
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        for source in ("mediaset_infinity", "youtube", SA):
            add_request(self.movie, source, RefreshRequestStatus.DONE, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.COMPLETED)
        self.assertIsNotNone(run.finished_at)

    def test_a_quota_blocked_source_makes_the_run_partial_not_failed(self):
        """The headline requirement: 24 blocked requests is a run that could
        not finish its backlog, not a broken one.

        Reporting it FAILED would train the reader to ignore FAILED, which is
        the only status that should mean something is actually wrong.
        """
        from tracker.models import ApiCallCounter

        ApiCallCounter.objects.create(
            service=SA, count=SOURCE_DAILY_LIMITS[SA], call_date=timezone.now().date()
        )
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        # A free source answered; the metered one is still queued and blocked.
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.PARTIAL)

    def test_a_single_failing_source_does_not_fail_the_run(self):
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, "mediaset_infinity", RefreshRequestStatus.FAILED, batch)
        add_request(self.movie, SA, RefreshRequestStatus.DONE, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertNotEqual(run.status, AvailabilityRefreshStatus.FAILED)

    def test_every_source_failing_is_never_completed(self):
        """No source produced an answer, so the run did no useful work.

        Whether that settles as FAILED or PARTIAL depends on whether titles are
        still unresolved, and both are honest readings — what must never
        happen is COMPLETED, which would claim the refresh succeeded while
        every source had failed. FAILED proper is reached through `fail()`
        (a run-level error) and is asserted in TransitionTests.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        for source in ("mediaset_infinity", "youtube", SA, WM):
            add_request(self.movie, source, RefreshRequestStatus.FAILED, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertNotEqual(run.status, AvailabilityRefreshStatus.COMPLETED)
        self.assertEqual(run.total_titles, 1)
        self.assertEqual(run.available_titles, 0)

    def test_outstanding_requests_can_no_longer_produce_a_terminal_run(self):
        """A pending request no longer means PARTIAL — it means *deferral*.

        This test previously asserted ``PARTIAL`` for a run holding one
        outstanding request. That single assertion encoded the defect P6a
        removes: ``PARTIAL`` was reachable both by "titles remain unresolved"
        and by "the worker ran out of budget mid-queue", so a report could not
        tell a legitimate partial result from an interrupted one.

        Now a runnable outstanding request is not a status at all: finalisation
        defers, leaving the run ``RUNNING`` so the next tick resumes it — a run
        budget limits work performed, not future eligibility. Settling the
        request first is what makes PARTIAL legitimate.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)

        # Ladder off for this half on purpose. With it on, settlement would
        # legitimately block the metered request (a free source answered), and
        # there would be nothing left to defer — which is the *other* half of
        # this test. Here the request must be genuinely runnable, so the run
        # cannot finalise and stays RUNNING.
        with patch.object(
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=False,
        ):
            finalise_run(run, [self.movie.pk])

        run.refresh_from_db()
        self.assertNotIn(
            run.status,
            {
                AvailabilityRefreshStatus.COMPLETED,
                AvailabilityRefreshStatus.PARTIAL,
                AvailabilityRefreshStatus.FAILED,
            },
            "finalisation must not record a terminal status over a live queue",
        )
        self.assertIsNone(run.finished_at)
        self.assertEqual(
            RefreshRequest.objects.get(
                movie=self.movie, source=SA, batch=batch
            ).status,
            RefreshRequestStatus.PENDING,
            "the runnable request must stay queued, not be settled away",
        )

        # And once it is settled, the run finalises honestly. The metered
        # source declines it here because the ladder is on and a free source
        # already answered the title — so the title is available *and* the run
        # is COMPLETED, which is the point: blocking a request does not demote
        # a resolved title.
        with patch.object(
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=True,
        ):
            settle_unexecutable_requests(run)
            finalise_run(run, [self.movie.pk])

        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.COMPLETED)
        self.assertIsNotNone(run.finished_at)
        blocked = RefreshRequest.objects.get(movie=self.movie, source=SA, batch=batch)
        self.assertEqual(blocked.status, RefreshRequestStatus.BLOCKED)
        self.assertEqual(blocked.blocked_reason, BlockedReason.POLICY_NOT_NEEDED)

    def test_a_pending_request_from_another_run_does_not_hold_this_one_open(self):
        """Ownership is the batch. An unrelated pending request must not keep
        a finished run PARTIAL forever."""
        batch = uuid.uuid4()
        other = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, other)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.COMPLETED)

    def test_a_legacy_empty_source_row_still_counts_as_outstanding(self):
        """Pre-fan-out rows carry ``source=""`` but the same batch, so they
        must be counted — the batch defines ownership, not the spelling.

        The consequence changed with P6a: being counted no longer means
        ``PARTIAL``, it means finalising is deferred. Ownership is what decides
        whether a row holds the run open, and a legacy empty-scope row is owned
        by the run, so it keeps it ``RUNNING``. Asserting the deferral rather
        than a status is the honest form of this test now — the old assertion
        (`PARTIAL`) was really asserting the very ambiguity P6a removes.
        """
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        add_request(self.movie, "", RefreshRequestStatus.PENDING, batch)

        finalise_run(run, [self.movie.pk])

        run.refresh_from_db()
        self.assertNotIn(
            run.status,
            {
                AvailabilityRefreshStatus.COMPLETED,
                AvailabilityRefreshStatus.PARTIAL,
                AvailabilityRefreshStatus.FAILED,
            },
            "finalisation must not record a terminal status over a live queue",
        )
        self.assertIsNone(run.finished_at)
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=batch, status=RefreshRequestStatus.PENDING
            ).count(),
            1,
        )


class PersistedStateTests(TestCase):
    """The status must come from the database, not from a worker's memory."""

    def setUp(self):
        self.movie = make_movie(802, "Persisted")

    def test_finalising_twice_is_safe(self):
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        first = finalise_run(run, [self.movie.pk])
        first_status = first.status
        first_finished = first.finished_at
        second = finalise_run(run, [self.movie.pk])
        self.assertEqual(second.status, first_status)
        self.assertEqual(second.finished_at, first_finished)

    def test_a_terminal_run_is_not_re_derived(self):
        """Once terminal, the status is history. Re-finalising must not
        rewrite it, or a run's outcome depends on when you last looked."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        AvailabilityRefreshLifecycle.finalise(run, [self.movie.pk])
        run.refresh_from_db()
        settled = run.status
        # New work appears for the same run after it was declared done.
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)
        AvailabilityRefreshLifecycle.finalise(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(run.status, settled)

    def test_the_outcome_survives_losing_the_in_memory_scope(self):
        """A worker that enqueued the run is gone after a crash; the batch's
        own rows are the only surviving record of what it covered."""
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        # No movie_ids passed and no title_filter: recovery must still work.
        AvailabilityRefreshLifecycle.finalise(run)
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.COMPLETED)
        self.assertEqual(run.total_titles, 1)

    def test_counters_are_persisted_not_computed_at_read_time(self):
        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(run.total_titles, 1)
        self.assertEqual(run.available_titles, 1)
        # And the per-source rows exist in the database, not just in a
        # response built on the fly.
        self.assertTrue(run.sources.exists())


class CrashRecoveryTests(TestCase):
    """A dead worker must not produce a silent COMPLETED."""

    def setUp(self):
        self.movie = make_movie(803, "Crash")

    def _aged_running_run(self, batch, age_minutes=47) -> AvailabilityRefresh:
        run = make_run(batch=batch, movie_ids=[self.movie])
        AvailabilityRefreshLifecycle.start(run)
        # Queryset update(), not save(): `updated_at` is auto_now and would be
        # overwritten, leaving the row at "now" and the staleness filter
        # legitimately (but confusingly) finding nothing.
        AvailabilityRefresh.objects.filter(pk=run.pk).update(
            updated_at=timezone.now() - dt.timedelta(minutes=age_minutes)
        )
        run.refresh_from_db()
        return run

    def test_a_freshly_updated_running_run_is_not_stale(self):
        run = self._aged_running_run(uuid.uuid4(), age_minutes=1)
        stale = AvailabilityRefreshLifecycle.stale_running_runs()
        self.assertNotIn(run, stale)

    def test_a_long_abandoned_run_is_stale(self):
        run = self._aged_running_run(uuid.uuid4(), age_minutes=47)
        self.assertIn(run, AvailabilityRefreshLifecycle.stale_running_runs())

    def test_a_crashed_run_that_had_finished_work_is_reconciled_to_completed(self):
        """The rows say the work is done, so the run's outcome is knowable
        without the worker that did it."""
        batch = uuid.uuid4()
        run = self._aged_running_run(batch)
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        reconcile_stale_runs()
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.COMPLETED)

    def test_a_crashed_run_with_outstanding_work_is_not_declared_complete(self):
        """The single worst outcome available: claiming success while
        silently dropping the backlog."""
        batch = uuid.uuid4()
        run = self._aged_running_run(batch)
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)
        reconcile_stale_runs()
        run.refresh_from_db()
        self.assertEqual(run.status, AvailabilityRefreshStatus.RUNNING)

    def test_a_crashed_run_that_blocked_on_quota_reconciles_to_partial(self):
        from tracker.models import ApiCallCounter

        ApiCallCounter.objects.create(
            service=SA, count=SOURCE_DAILY_LIMITS[SA], call_date=timezone.now().date()
        )
        batch = uuid.uuid4()
        run = self._aged_running_run(batch)
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        # The queued request is drained by nobody: it is a real, blocked
        # backlog, so the run reconciles to PARTIAL, not COMPLETED.
        add_request(self.movie, SA, RefreshRequestStatus.PENDING, batch)
        AvailabilityRefreshLifecycle.reconcile(run)
        run.refresh_from_db()
        self.assertIn(
            run.status,
            (
                AvailabilityRefreshStatus.RUNNING,
                AvailabilityRefreshStatus.PARTIAL,
            ),
        )
        self.assertNotEqual(run.status, AvailabilityRefreshStatus.COMPLETED)

    def test_reconciliation_never_downgrades_a_terminal_run(self):
        batch = uuid.uuid4()
        run = self._aged_running_run(batch)
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        reconcile_stale_runs()
        run.refresh_from_db()
        settled = run.status
        reconcile_stale_runs()
        run.refresh_from_db()
        self.assertEqual(run.status, settled)


class ReadPathTests(TestCase):
    """A reader must not make the transition itself."""

    def setUp(self):
        self.movie = make_movie(804, "ReadPath")

    def test_a_get_does_not_finalise_a_running_run(self):
        """The bug this design prevents: a reader inferring completion from
        request rows and writing the transition."""
        from tracker import views

        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        AvailabilityRefreshLifecycle.start(run)

        payload = views._serialize_refresh(run, [self.movie.pk])
        run.refresh_from_db()
        self.assertEqual(payload["status"], AvailabilityRefreshStatus.RUNNING)
        self.assertEqual(run.status, AvailabilityRefreshStatus.RUNNING)

    def test_a_terminal_run_reports_its_persisted_status_unchanged(self):
        from tracker import views

        batch = uuid.uuid4()
        run = make_run(batch=batch, movie_ids=[self.movie])
        offer_for(self.movie, WM)
        add_request(self.movie, WM, RefreshRequestStatus.DONE, batch)
        finalise_run(run, [self.movie.pk])
        run.refresh_from_db()
        settled = run.status
        payload = views._serialize_refresh(run, [self.movie.pk])
        self.assertEqual(payload["status"], settled)
        self.assertFalse(payload["is_active"])
