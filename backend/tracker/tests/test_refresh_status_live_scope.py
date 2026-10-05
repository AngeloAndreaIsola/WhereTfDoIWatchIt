"""Historical pending rows must not read as work in flight.

Runs 5 and 6 are recorded `partial` while still owning 476 pending request
rows. Those rows are genuine history: no source is out of budget, so they were
runnable when the runs were wrongly recorded terminal, and inventing an outcome
for them (`done`, or `blocked`/`superseded`) would falsify the record. They are
therefore left exactly as they are.

Leaving them alone is only half the job. The other half is that nothing reading
the data may treat them as current work, because if it does the user sees a
refresh in progress when no run is active. That was a live defect, not a
hypothetical one:

    GET /api/watchlist/refresh-status/?since=0
      -> pending: 382, rate_limited: true, batch: null

with zero non-terminal runs in the database. `rate_limited` was true only
because Streaming Availability was out of quota, which combined with 382
pending rows to read unmistakably as "a refresh is progressing, throttled by
quota".

These tests pin the distinction:

    active refresh                      -> pending rows are live work
    historical terminal refresh         -> pending rows are an anomaly, ignored
"""
import uuid
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    BlockedReason,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services import refresh_lifecycle as rl
from tracker.services import source_policy
from tracker.services.refresh_lifecycle import ACTIVE_STATUSES


class _WatchlistFixture(TestCase):
    """One watched title, so the endpoint's watchlist scope matches."""

    def setUp(self):
        self.movie = Movie.objects.create(tmdb_id=770_900, title="Historical", year=2020)
        WatchlistItem.objects.get_or_create(user_id=1, movie=self.movie)

    def make_run(self, status, movie_ids):
        run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            batch=uuid.uuid4(),
            status=status,
            started_at=timezone.now(),
            title_filter=list(movie_ids),
        )
        return run

    def pending_row(self, batch, movie=None):
        return RefreshRequest.objects.create(
            movie=movie or self.movie,
            source="watchmode",
            status=RefreshRequestStatus.PENDING,
            batch=batch,
        )

    def pending_rows(self, batch, count):
        """`count` pending rows, each on its own title.

        The pending-dedup constraint allows one live request per (title,
        source), which is correct product behaviour — so several queued rows
        have to belong to several titles, exactly as they do in production.

        Each extra title is added to the watchlist, because the endpoint counts
        the *watchlist's* outstanding work. Leaving them off the watchlist made
        the rows exist but correctly invisible, which looked like the endpoint
        under-counting when in fact the fixture did not match production.
        """
        movies = [
            Movie.objects.create(
                tmdb_id=770_800 + i, title=f"Queued {i}", year=2021
            )
            for i in range(count)
        ]
        for movie in movies:
            WatchlistItem.objects.get_or_create(user_id=1, movie=movie)
        return [self.pending_row(batch, movie=m) for m in movies]


class PendingCountReflectsLiveWorkTests(_WatchlistFixture):
    def _get(self, **params):
        query = "since=0"
        for key, value in params.items():
            query += f"&{key}={value}"
        return Client().get(f"/api/watchlist/refresh-status/?{query}")

    def test_pending_rows_from_a_terminal_run_are_not_counted_as_outstanding(self):
        """The regression: a terminal run's leftover rows are not live work."""
        run = self.make_run(AvailabilityRefreshStatus.PARTIAL, [self.movie.pk])
        self.pending_rows(run.batch, 3)

        response = self._get()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        # The rows are still there — history is not rewritten.
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=run.batch, status=RefreshRequestStatus.PENDING
            ).count(),
            3,
        )
        # But they are not outstanding work.
        self.assertEqual(
            body["pending"], 0, "historical rows reported as work in flight"
        )

    def test_pending_rows_owned_by_an_active_run_are_counted(self):
        """The counterpart, so the fix cannot be "ignore pending entirely"."""
        run = self.make_run(AvailabilityRefreshStatus.RUNNING, [self.movie.pk])
        self.pending_rows(run.batch, 3)

        body = self._get().json()

        self.assertEqual(body["pending"], 3, "live work was not counted")

    def test_a_terminal_run_with_pending_rows_is_what_the_invariant_forbids(self):
        """The state being filtered out is exactly what P6a makes unreachable.

        Asserted directly against the database rather than through the
        endpoint, so the endpoint's filtering cannot be what makes this pass.
        Run through the *current* lifecycle, the same attempt is refused.
        """
        run = self.make_run(AvailabilityRefreshStatus.RUNNING, [self.movie.pk])
        self.pending_row(run.batch)

        # Current lifecycle: refused, because the request is runnable.
        with patch.object(
            source_policy,
            "consult_metered_only_when_unresolved",
            return_value=False,
        ):
            with self.assertRaises(rl.UnsettledWorkError):
                rl.AvailabilityRefreshLifecycle.finalise(run)

        run.refresh_from_db()
        self.assertIsNone(run.finished_at)
        self.assertEqual(run.status, AvailabilityRefreshStatus.RUNNING)

        # And the only way to reach the historical shape is to write the status
        # directly, which is precisely why the endpoint cannot assume rows
        # behind a terminal run were settled legitimately.
        AvailabilityRefresh.objects.filter(pk=run.pk).update(
            status=AvailabilityRefreshStatus.PARTIAL, finished_at=run.created_at
        )
        self.assertEqual(
            RefreshRequest.objects.filter(
                batch=run.batch, status=RefreshRequestStatus.PENDING
            ).count(),
            1,
        )

    def test_an_unbatched_pending_request_is_still_counted(self):
        """A single-title request has no run; it is still live work.

        The counterpart that caught a real over-correction. Filtering `pending`
        to rows owned by an *active* run also dropped every `batch=NULL` row —
        and a detail-page refresh, an import or a cron-sweep request is queued
        with no batch at all. That reported zero pending for a watchlist with a
        genuinely queued title, which is the mirror image of the bug this file
        exists to fix.
        """
        RefreshRequest.objects.create(
            movie=self.movie, source="watchmode", status=RefreshRequestStatus.PENDING
        )

        body = self._get().json()

        self.assertEqual(body["pending"], 1, "a runless pending row was dropped")

    def test_blocked_rows_are_terminal_so_never_counted_as_pending(self):
        """A settled `blocked` row is not outstanding, in any run."""
        run = self.make_run(AvailabilityRefreshStatus.RUNNING, [self.movie.pk])
        req = self.pending_row(run.batch)
        req.settle(
            RefreshRequestStatus.BLOCKED, blocked_reason=BlockedReason.QUOTA_EXHAUSTED
        )

        body = self._get().json()

        self.assertEqual(body["pending"], 0)
        self.assertIsNone(body["batch"])

    def test_active_statuses_cover_exactly_the_non_terminal_runs(self):
        """Guards the filter's own premise.

        If a new non-terminal status were added without being listed here, the
        filter would silently under-count live work — the same class of drift
        as the terminal-status allowlist this work removed elsewhere.
        """
        self.assertEqual(
            set(ACTIVE_STATUSES),
            {
                AvailabilityRefreshStatus.QUEUED,
                AvailabilityRefreshStatus.RUNNING,
            },
        )
        self.assertFalse(
            set(ACTIVE_STATUSES) & set(rl.TERMINAL_STATUSES),
            "a status cannot be both active and terminal",
        )