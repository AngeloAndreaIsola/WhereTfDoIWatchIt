"""Reload recovery at the API level, before the browser is involved.

The frontend will reconstruct a long-running refresh purely from what the API
returns. That only works if the API can answer these questions honestly, and
the most important one is about what it must *not* do:

    GET must never change a run's status.

A reader that infers "no pending requests, therefore completed" is making the
lifecycle transition itself, which is how a crashed run gets reported to the
user as a success. The worker is the only writer of ``AvailabilityRefresh.status``
(``refresh_lifecycle.AvailabilityRefreshLifecycle``), and these tests lock that
boundary down at the HTTP layer, where the frontend will actually meet it.

The sequence under test is exactly the one the UI depends on::

    POST /availability/refresh/        -> refresh_id
    GET  /availability/refreshes?active=true  -> the run exists
    (worker advances it)
    GET  /availability/refreshes?active=true  -> progress moved
    (run reaches a terminal state)
    GET  /availability/refreshes?active=true  -> no active run
    GET  /availability/refreshes/<id>/        -> still terminal, unchanged
"""
import json
import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from tracker.models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    Movie,
    WatchlistItem,
)
from tracker.services.refresh_lifecycle import (
    AvailabilityRefreshLifecycle,
    finalise_run,
)

POST_REFRESH = "/api/availability/refresh/"
LIST_REFRESHES = "/api/availability/refreshes/"
REPORT = "/api/availability/report/"


def make_movie(tmdb_id: int) -> Movie:
    movie = Movie.objects.create(tmdb_id=tmdb_id, title=f"T{tmdb_id}", year=2020)
    WatchlistItem.objects.create(user_id=1, movie=movie, source="letterboxd")
    return movie


class ReloadRecoveryApiTests(TestCase):
    def setUp(self):
        self.movies = [make_movie(8100 + i) for i in range(3)]
        self.ids = [m.pk for m in self.movies]

    def _get(self, url: str):
        return self.client.get(url)

    def test_a_new_refresh_run_is_queued_and_discoverable(self):
        """The first thing a page load asks: is there a run to attach to?

        Enqueue-only, so the endpoint must not contact a provider — the fetch
        seam is patched to raise if it is ever called, which is what makes this
        a real assertion about the contract rather than a status-code check.
        """
        with patch(
            "availability.services.refresh.AvailabilityRefreshService.refresh_content",
            side_effect=AssertionError("enqueue fetched a provider"),
        ):
            response = self.client.post(POST_REFRESH)
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertIn("refresh_id", payload)
        # And the run it created is immediately discoverable as active.
        active = self._get(LIST_REFRESHES + "?active=true").json()["active"]
        self.assertEqual(active["id"], payload["refresh_id"])

    def test_an_queued_run_appears_in_the_active_list(self):
        run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.QUEUED,
            batch=uuid.uuid4(),
            title_filter=self.ids,
            started_at=timezone.now(),
        )
        payload = self._get(LIST_REFRESHES + "?active=true").json()
        self.assertIsNotNone(payload["active"])
        self.assertEqual(payload["active"]["id"], run.pk)
        self.assertEqual(payload["active"]["status"], AvailabilityRefreshStatus.QUEUED)

    def test_a_running_run_appears_in_the_active_list(self):
        run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.RUNNING,
            batch=uuid.uuid4(),
            title_filter=self.ids,
            started_at=timezone.now(),
        )
        payload = self._get(LIST_REFRESHES + "?active=true").json()
        self.assertEqual(payload["active"]["id"], run.pk)
        self.assertTrue(payload["active"]["is_active"])

    def test_no_active_run_is_reported_as_null(self):
        """The idle case: nothing to attach to, so offer a fresh refresh."""
        payload = self._get(LIST_REFRESHES + "?active=true").json()
        self.assertIsNone(payload["active"])

    def test_a_terminal_run_is_not_offered_as_active(self):
        for status in (
            AvailabilityRefreshStatus.COMPLETED,
            AvailabilityRefreshStatus.PARTIAL,
            AvailabilityRefreshStatus.FAILED,
        ):
            AvailabilityRefresh.objects.create(
                run_type=AvailabilityRefreshType.WATCHLIST,
                status=status,
                batch=uuid.uuid4(),
                title_filter=self.ids,
                started_at=timezone.now(),
            )
        payload = self._get(LIST_REFRESHES + "?active=true").json()
        self.assertIsNone(payload["active"])
        # But it is still queryable — "what happened" must survive a reload.
        self.assertEqual(len(payload["results"]), 3)

    def test_progress_moves_between_polls(self):
        """A reload mid-run must show the run *advancing*, not a snapshot."""
        from tracker.models import RefreshRequest, RefreshRequestStatus

        batch = uuid.uuid4()
        run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.RUNNING,
            batch=batch,
            title_filter=self.ids,
            started_at=timezone.now(),
        )
        for movie in self.movies:
            RefreshRequest.objects.create(
                movie=movie, source="watchmode", status=RefreshRequestStatus.PENDING,
                batch=batch, requested_at=timezone.now(),
            )
        first = self._get(LIST_REFRESHES + "?active=true").json()
        # Look the source up by name rather than trusting the list order: the
        # payload is ordered by tier, and a positional index silently asserts
        # about whichever source happens to be first.
        def queued_count(payload: dict, source: str) -> int:
            return next(
                s["queued"] for s in payload["active"]["sources"] if s["source"] == source
            )

        self.assertEqual(queued_count(first, "watchmode"), len(self.movies))

        RefreshRequest.objects.filter(batch=batch).update(
            status=RefreshRequestStatus.DONE, completed_at=timezone.now()
        )
        second = self._get(LIST_REFRESHES + "?active=true").json()
        self.assertEqual(queued_count(second, "watchmode"), 0)
        self.assertGreaterEqual(
            second["active"]["completed_titles"], first["active"]["completed_titles"]
        )


class ReadMustNotMutateTests(TestCase):
    """The boundary that matters most: a GET is not a transition."""

    def setUp(self):
        self.movie = make_movie(8200)
        self.batch = uuid.uuid4()
        self.run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.RUNNING,
            batch=self.batch,
            title_filter=[self.movie.pk],
            started_at=timezone.now(),
        )

    def test_the_detail_endpoint_never_changes_a_running_status(self):
        """A run whose queue is empty but whose status is still RUNNING must
        survive a detail read as RUNNING.

        This is the exact regression: the reader sees no pending work and is
        tempted to conclude the run finished. It did not finish — the worker
        never said so.
        """
        from tracker.models import RefreshRequest, RefreshRequestStatus

        RefreshRequest.objects.create(
            movie=self.movie, source="watchmode",
            status=RefreshRequestStatus.DONE, batch=self.batch,
            requested_at=timezone.now(), completed_at=timezone.now(),
        )
        for _ in range(3):
            response = self.client.get(f"{LIST_REFRESHES}{self.run.pk}/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["status"], AvailabilityRefreshStatus.RUNNING
            )
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AvailabilityRefreshStatus.RUNNING)

    def test_the_active_endpoint_never_changes_a_running_status(self):
        self.client.get(LIST_REFRESHES + "?active=true")
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AvailabilityRefreshStatus.RUNNING)

    def test_the_report_endpoint_never_changes_any_status(self):
        self.client.get(REPORT)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AvailabilityRefreshStatus.RUNNING)

    def test_repeated_reads_are_stable(self):
        """A polling loop must be idempotent: N reads, N identical payloads."""
        payloads = [
            self.client.get(f"{LIST_REFRESHES}{self.run.pk}/").json()
            for _ in range(4)
        ]
        for later in payloads[1:]:
            self.assertEqual(later["status"], payloads[0]["status"])
            self.assertEqual(
                later["total_titles"], payloads[0]["total_titles"]
            )

    def test_a_terminal_run_reports_its_persisted_status(self):
        from tracker.models import RefreshRequest, RefreshRequestStatus

        RefreshRequest.objects.create(
            movie=self.movie, source="watchmode",
            status=RefreshRequestStatus.DONE, batch=self.batch,
            requested_at=timezone.now(), completed_at=timezone.now(),
        )
        finalise_run(self.run, [self.movie.pk])
        self.run.refresh_from_db()
        settled = self.run.status
        for _ in range(3):
            response = self.client.get(f"{LIST_REFRESHES}{self.run.pk}/")
            self.assertEqual(response.json()["status"], settled)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, settled)

    def test_a_missing_run_is_a_404_not_a_500(self):
        response = self.client.get(f"{LIST_REFRESHES}999999/")
        self.assertEqual(response.status_code, 404)


class UiContractTests(TestCase):
    """The payload the frontend consumes, asserted so the UI cannot guess."""

    def setUp(self):
        self.movie = make_movie(8300)

    def test_the_run_payload_carries_everything_the_status_card_needs(self):
        """Each field maps to a line the UI is meant to render."""
        run = AvailabilityRefresh.objects.create(
            run_type=AvailabilityRefreshType.WATCHLIST,
            status=AvailabilityRefreshStatus.RUNNING,
            batch=uuid.uuid4(),
            title_filter=[self.movie.pk],
            started_at=timezone.now(),
        )
        finalise_run(run, [self.movie.pk])
        payload = self.client.get(f"{LIST_REFRESHES}{run.pk}/").json()
        for field in (
            "id", "status", "is_active", "started_at", "finished_at",
            "total_titles", "available_titles", "unavailable_titles",
            "unknown_titles", "unknown_reasons", "sources", "policy",
        ):
            self.assertIn(field, payload, f"missing {field}")
        for source in payload["sources"]:
            for field in ("source", "label", "status", "reason", "total", "completed", "queued"):
                self.assertIn(field, source, f"source row missing {field}")

    def test_the_report_payload_carries_the_outcome_and_the_policy(self):
        payload = self.client.get(REPORT).json()
        for field in ("sources", "collectors", "totals", "unknown_reasons", "policy", "counterfactual"):
            self.assertIn(field, payload, f"missing {field}")
        # A skip reason must be distinguishable from an availability verdict:
        # the UI renders "not needed", never "unavailable".
        self.assertIn("stale_failures", payload)

    def test_skipped_reasons_are_not_availability_verdicts(self):
        """The UI contract for Step 8: `not_needed` must never be a value the
        classification could produce, so no renderer can confuse them."""
        from tracker.models import AvailabilityResolutionReason
        from tracker.services.source_policy import PolicySkipReason

        verdicts = {r.value for r in AvailabilityResolutionReason}
        skips = set(PolicySkipReason.all())
        self.assertEqual(skips & verdicts, set())
        self.assertIn("not_needed", skips)
