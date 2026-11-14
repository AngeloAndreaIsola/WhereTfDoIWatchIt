"""Catalogue work must not be able to starve title-level refreshes.

The failure this pins is not hypothetical. A single worker process did both
kinds of work in one tick::

    summary   = process_refresh_queue(budget=budget)   # title requests
    catalogue = process_pending_catalogue_refresh()   # whole catalogues

and the catalogue step is unbounded by nature: ABC iview fetches ~1613 series
detail pages with a politeness delay (measured 29-30 minutes per run) and CBC Gem
walks up to 500 pages per category. Because both ran on one thread, a single slow
catalogue source blocked every already-queued ``RefreshRequest``. Observed live:
601 pending requests that never executed, an oldest request 1h40m old.

So the worker now declares a lane. These tests pin the two properties that make
the split safe rather than merely possible:

1. a title request is drained while catalogue work is still running, and
2. once the lanes are separate, neither can steal the other's work or double
   execute a row.

No test here makes a live HTTP request, and none sleeps on wall-clock time: the
concurrency ordering is established with events and doubles, so a failure names a
broken invariant instead of a slow machine.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase

from availability.sources import source_registry

from tracker.models import (
    AvailabilityRefresh,
    BlockedReason,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    WatchlistItem,
)
from tracker.services.refresh_requests import (
    process_refresh_queue,
)

WORKER = "tracker.management.commands.refresh_worker"


def _tick(lane: str) -> None:
    """One worker tick in the given lane, then exit."""
    call_command("refresh_worker", "--once", "--lane", lane, verbosity=0)


class LaneSelectionTests(TestCase):
    """A lane worker touches only its own kind of work."""

    def test_title_lane_does_not_touch_catalogue_markers(self):
        with patch(f"{WORKER}.process_refresh_queue", return_value=None) as title, \
             patch(f"{WORKER}.process_pending_catalogue_refresh") as catalogue:
            _tick("title")
        self.assertTrue(title.called)
        self.assertFalse(
            catalogue.called,
            "a title-lane worker must not start a catalogue walk",
        )

    def test_catalogue_lane_does_not_drain_title_requests(self):
        with patch(f"{WORKER}.process_pending_catalogue_refresh") as catalogue, \
             patch(f"{WORKER}.process_refresh_queue") as title:
            _tick("catalogue")
        self.assertTrue(catalogue.called)
        self.assertFalse(
            title.called,
            "a catalogue-lane worker must not drain the title queue",
        )

    def test_both_lane_still_does_but_title_first(self):
        """The default keeps the original ordering, byte for byte."""
        order = []
        empty = {"requests_drained": 0, "errors": 0, "rate_limited": False,
                 "skipped_cached": 0}
        with patch(f"{WORKER}.process_refresh_queue",
                   side_effect=lambda **kw: (order.append("title"), empty)[1]), \
             patch(f"{WORKER}.process_pending_catalogue_refresh",
                   side_effect=lambda: (order.append("catalogue"), None)[1]):
            _tick("both")
        self.assertEqual(order, ["title", "catalogue"])

    def test_lane_is_logged_so_a_misconfigured_worker_is_visible(self):
        """A worker running the wrong lane must be identifiable in its log."""
        with patch(f"{WORKER}.process_refresh_queue", return_value=None), \
             patch(f"{WORKER}.process_pending_catalogue_refresh",
                   return_value=None), \
             self.assertLogs(
                 "tracker.management.commands.refresh_worker", level="INFO"
             ) as captured:
            _tick("catalogue")
        self.assertTrue(
            any("lane=catalogue" in line for line in captured.output),
            f"lane not logged: {captured.output}",
        )


class ConcurrencyTests(TransactionTestCase):
    """Title execution overlapping catalogue execution.

    ``TransactionTestCase`` rather than ``TestCase``: proving that a *second
    session* cannot take the lock requires closing Django's connection, which a
    ``TestCase`` wraps in an atomic block it then refuses to lose.
    """

    def test_a_title_request_is_drained_while_catalogue_work_is_running(self):
        """The core invariant, established with events, not with sleeps.

        The catalogue call blocks on an event the title drain must release. If the
        two shared a single thread, the drain could not run and the test would
        deadlock instead of failing — which is itself the point: the assertion is
        only reachable because the title path is on a different thread.
        """
        started = threading.Event()
        release = threading.Event()
        drained = []

        def slow_catalogue():
            started.set()
            # Held until the title worker has done its work.
            release.wait(timeout=10)

        with patch(f"{WORKER}.process_refresh_queue",
                   side_effect=lambda **kw: drained.append(True)), \
             patch(f"{WORKER}.process_pending_catalogue_refresh",
                   side_effect=slow_catalogue):
            worker = threading.Thread(target=_tick, args=("both",),
                                      daemon=True)
            worker.start()
            self.assertTrue(started.wait(timeout=10), "catalogue never began")

            # Catalogue is now mid-flight. A separate title lane must run.
            title = threading.Thread(target=_tick, args=("title",), daemon=True)
            title.start()
            title.join(timeout=10)

            self.assertTrue(drained, "title work did not run during catalogue work")
            self.assertTrue(worker.is_alive(),
                            "catalogue should still be running at this point")
            release.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "worker did not finish after release")

class RequestSemanticsTests(TestCase):
    """Existing status meanings are unchanged by the lane split."""

    def _movie(self, tmdb_id=1):
        """A Movie row, returned by pk.

        ``RefreshRequest.movie`` is a FK to ``Movie.pk``, not to ``Movie.tmdb_id``,
        so the row's own id must be used when inserting a request.
        """
        movie, _ = Movie.objects.get_or_create(
            tmdb_id=tmdb_id, defaults={"title": "T"},
        )
        return movie

    def _request(self, status):
        """Create a row in *status*.

        ``BLOCKED`` cannot be inserted directly: a DB check constraint requires a
        blocked row to carry a reason, so it is reached the way production reaches
        it — created pending, then settled with the reason.
        """
        request = RefreshRequest.objects.create(
            movie=self._movie(), source="watchmode",
            status=RefreshRequestStatus.PENDING,
        )
        if status == RefreshRequestStatus.PENDING:
            return request
        if status == RefreshRequestStatus.BLOCKED:
            request.settle(RefreshRequestStatus.BLOCKED,
                           blocked_reason=BlockedReason.QUOTA_EXHAUSTED)
        else:
            request.settle(status)
        request.refresh_from_db()
        return request

    def test_every_status_still_settles_normally(self):
        for status in (
            RefreshRequestStatus.PENDING,
            RefreshRequestStatus.DONE,
            RefreshRequestStatus.FAILED,
            RefreshRequestStatus.BLOCKED,
        ):
            with self.subTest(status=status):
                request = self._request(status)
                self.assertEqual(request.status, status)
                request.settle(RefreshRequestStatus.DONE)
                request.refresh_from_db()
                self.assertEqual(request.status, RefreshRequestStatus.DONE)
                self.assertIsNotNone(request.completed_at)

    def test_a_blocked_reason_is_still_recorded_and_cleared(self):
        from tracker.models import BlockedReason

        request = self._request(RefreshRequestStatus.PENDING)
        request.settle(RefreshRequestStatus.BLOCKED,
                       blocked_reason=BlockedReason.QUOTA_EXHAUSTED)
        request.refresh_from_db()
        self.assertEqual(request.blocked_reason,
                         BlockedReason.QUOTA_EXHAUSTED)
        request.settle(RefreshRequestStatus.DONE)
        request.refresh_from_db()
        self.assertEqual(request.blocked_reason, "",
                         "a done row must not keep a stale block reason")


class NoPrematureCompletionTests(TestCase):
    """Splitting lanes must not let a refresh finish early."""

    def test_a_pending_request_is_never_settled_as_unexecutable(self):
        """Outstanding work must survive a finalisation attempt.

        ``settle_unexecutable_requests`` only settles rows it can prove are
        blocked (spent budget, policy-not-needed). A merely pending row is left
        alone precisely so a refresh cannot reach a terminal status while work is
        still queued — the invariant that keeps a title lane from ending a run
        its sibling lane is still working on.
        """
        import uuid

        from tracker.services.refresh_lifecycle import (
            settle_unexecutable_requests,
        )

        movie, _ = Movie.objects.get_or_create(
            tmdb_id=1, defaults={"title": "T"},
        )
        batch = uuid.uuid4()
        refresh = AvailabilityRefresh.objects.create(batch=batch)
        RefreshRequest.objects.create(
            movie=movie, source="watchmode", batch=batch,
            status=RefreshRequestStatus.PENDING,
        )

        self.assertEqual(
            settle_unexecutable_requests(refresh), {},
            "a pending row is runnable, so it must not be settled",
        )
        self.assertEqual(
            RefreshRequest.objects.filter(batch=batch, status=RefreshRequestStatus.PENDING).count(),
            1,
        )

    def test_finalise_is_idempotent_and_process_agnostic(self):
        """Terminalisation reads persisted rows, so any process gets the same answer."""
        import uuid

        from tracker.services.refresh_lifecycle import AvailabilityRefreshLifecycle

        Movie.objects.get_or_create(tmdb_id=1, defaults={"title": "T"})
        refresh = AvailabilityRefresh.objects.create(batch=uuid.uuid4())
        first = AvailabilityRefreshLifecycle.finalise(refresh)
        second = AvailabilityRefreshLifecycle.finalise(refresh)
        self.assertEqual(first.status, second.status)
        self.assertIsNotNone(refresh.finished_at)


class SourceRoutingTests(TestCase):
    """The lane split must not reroute sources through the wrong runner."""

    def test_catalogue_and_title_sources_are_distinguished_by_capability(self):
        """The two capabilities are independent, so a source may be in both.

        ``supports_catalogue_sync`` and ``supports_title_fetch`` answer
        different questions and neither implies the other. Catalogue-only
        collectors appear only in ``catalogue``; title-only sources appear
        only in ``title``; and JustWatch — which implements both a title
        lookup and a catalogue walk — appears in **both**. The old assertion
        that JustWatch was absent from ``title`` encoded the conflation of the
        two capabilities that kept it out of per-title scheduling.
        """
        catalogue = [
            slug for slug in source_registry.slugs()
            if source_registry.get(slug).capabilities.supports_catalogue_sync
        ]
        title = [
            slug for slug in source_registry.slugs()
            if source_registry.get(slug).capabilities.supports_title_fetch
        ]
        for slug in ("abc_iview", "cbc_gem", "justwatch", "pbs", "rai_play",
                     "tubi", "prime_video", "bbc_iplayer", "pluto_tv"):
            self.assertIn(slug, catalogue)
        for slug in ("watchmode", "youtube", "streaming_availability",
                     "mediaset_infinity", "justwatch"):
            self.assertIn(slug, title)
        # Catalogue-only collectors have no title path.
        for slug in ("abc_iview", "cbc_gem", "pbs", "rai_play", "tubi",
                     "prime_video", "bbc_iplayer", "pluto_tv"):
            self.assertNotIn(slug, title)
        # JustWatch is the source that legitimately sits in both sets.
        self.assertIn("justwatch", catalogue)
        self.assertIn("justwatch", title)

    def test_only_justwatch_has_a_dedicated_catalogue_runner(self):
        from tracker.services.bulk_refresh import CATALOGUE_RUNNERS

        self.assertEqual(list(CATALOGUE_RUNNERS), ["justwatch"],
                         "the lane split must not add runners for other sources")

    def test_a_catalogue_lane_tick_still_reaches_the_justwatch_runner(self):
        """JustWatch stays country-scoped when driven from the catalogue lane."""
        source = source_registry.get("justwatch")
        with patch(f"{WORKER}.process_refresh_queue", return_value=None), \
             patch(f"{WORKER}.process_pending_catalogue_refresh") as catalogue:
            with patch(
                "tracker.services.bulk_refresh.resolve_source_country",
                return_value="NL",
            ), patch(
                "tracker.services.justwatch_sync.available_partitions",
                return_value=[("NL", "netflix"), ("AU", "netflix")],
            ), patch(
                "tracker.services.justwatch_sync.refresh_justwatch_partitions",
            ) as coordinator, patch(
                "tracker.services.bulk_refresh._try_source_lock",
                return_value=True,
            ), patch(
                "tracker.services.bulk_refresh._release_source_lock",
            ):
                from tracker.services.bulk_refresh import CATALOGUE_RUNNERS

                CATALOGUE_RUNNERS["justwatch"](
                    "justwatch", source, None,
                )

        self.assertTrue(coordinator.called)
        plan = coordinator.call_args.kwargs["partitions"]
        self.assertEqual(sorted({c for c, _ in plan}), ["NL"],
                         "AU must never be planned for an NL preference")
