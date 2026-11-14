"""PostgreSQL regressions for the shared title-refresh drain lock."""
from __future__ import annotations

import threading
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.test import TransactionTestCase

from tracker.models import Movie, RefreshRequest, RefreshRequestStatus
from tracker.services.refresh_requests import (
    _TITLE_DRAIN_LOCK_KEY,
    process_refresh_queue,
    title_drain_lock,
)


@skipUnless(
    connection.vendor == "postgresql",
    "session advisory lock regressions require PostgreSQL",
)
class RefreshDrainLockPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.movie, _ = Movie.objects.get_or_create(
            tmdb_id=998201,
            defaults={"title": "Drain Lock Fixture", "year": 2020},
        )
        self.request = RefreshRequest.objects.create(
            movie=self.movie,
            source="justwatch",
            status=RefreshRequestStatus.PENDING,
        )

    def test_lock_is_nonblocking_and_owned_by_a_separate_session(self):
        with title_drain_lock() as acquired:
            self.assertTrue(acquired)
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                application_pid = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT pid FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = %s AND granted",
                    [_TITLE_DRAIN_LOCK_KEY],
                )
                lock_pids = [row[0] for row in cursor.fetchall()]

            self.assertEqual(len(lock_pids), 1)
            self.assertNotEqual(
                lock_pids[0], application_pid,
                "the lock must not depend on the ORM transaction connection",
            )
            with title_drain_lock() as second_acquired:
                self.assertFalse(second_acquired)

        with title_drain_lock() as acquired_after_release:
            self.assertTrue(acquired_after_release)

    def test_lock_is_released_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with title_drain_lock() as acquired:
                self.assertTrue(acquired)
                raise RuntimeError("fixture failure")

        with title_drain_lock() as acquired_after_exception:
            self.assertTrue(acquired_after_exception)

    def test_worker_and_watchlist_cannot_overlap_the_drain_or_sweep(self):
        drain_entered = threading.Event()
        release_drain = threading.Event()
        calls = []
        errors = []
        outcomes = {}

        def held_drain(**_kwargs):
            calls.append("queue")
            drain_entered.set()
            if not release_drain.wait(timeout=10):
                raise AssertionError("test did not release the queue drain")
            return {self.movie.tmdb_id}

        def run_queue_worker():
            close = connections.close_all
            close()
            try:
                outcomes["worker"] = process_refresh_queue(budget=5)
            except BaseException as exc:  # surface thread failures in the test
                errors.append(exc)
            finally:
                close()

        def run_watchlist_refresh():
            connections.close_all()
            try:
                outcomes["watchlist"] = process_watchlist_refresh_for_test()
            except BaseException as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        # Import in the main test context so the patched public service path is
        # the same one used by the API and refresh_availability command.
        from tracker.services.bulk_refresh import process_watchlist_refresh

        def process_watchlist_refresh_for_test():
            return process_watchlist_refresh(budget=5, user_id=987654)

        with patch(
            "tracker.services.refresh_requests._drain_refresh_requests_locked",
            side_effect=held_drain,
        ), patch(
            "tracker.services.bulk_refresh._candidate_pairs",
            side_effect=AssertionError(
                "watchlist sweep must not start when the queue lock is held"
            ),
        ):
            worker = threading.Thread(target=run_queue_worker, daemon=True)
            worker.start()
            self.assertTrue(drain_entered.wait(timeout=10), "worker did not enter drain")

            watchlist = threading.Thread(target=run_watchlist_refresh, daemon=True)
            watchlist.start()
            watchlist.join(timeout=10)
            self.assertFalse(watchlist.is_alive(), "watchlist waited indefinitely")

            release_drain.set()
            worker.join(timeout=10)

        self.assertFalse(worker.is_alive(), "queue worker did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(calls, ["queue"])
        self.assertTrue(outcomes["watchlist"]["rate_limited"])
        self.assertEqual(outcomes["watchlist"]["requests_pending"], 1)
        self.assertEqual(
            RefreshRequest.objects.get(pk=self.request.pk).status,
            RefreshRequestStatus.PENDING,
            "the contending watchlist refresh must leave queued work untouched",
        )
