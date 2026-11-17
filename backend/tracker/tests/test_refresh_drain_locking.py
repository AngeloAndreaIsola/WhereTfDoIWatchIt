"""PostgreSQL regressions for the shared title-refresh drain lock."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, connection, connections, transaction
from django.test import TransactionTestCase

from tracker.models import (
    ApiCallCounter,
    ApiCallLedger,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
)
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
            source="streaming_availability",
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

    def test_lock_releases_while_the_orm_transaction_is_broken(self):
        with transaction.atomic():
            with title_drain_lock() as acquired:
                self.assertTrue(acquired)
                # Catch the SQL error inside atomic so Django marks the ORM
                # session broken at lock-release time. The lock's dedicated
                # session must still release it successfully.
                with self.assertRaises(DatabaseError):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1 / 0")

        with title_drain_lock() as acquired_after_rollback:
            self.assertTrue(acquired_after_rollback)

    def test_worker_and_watchlist_cannot_overlap_source_call_or_sweep(self):
        source_entered = threading.Event()
        release_source = threading.Event()
        source_calls = []
        errors = []
        outcomes = {}

        def fake_source_refresh(movie, **_kwargs):
            source_calls.append(movie.pk)
            source_entered.set()
            if not release_source.wait(timeout=10):
                raise AssertionError("test did not release the source call")
            return None

        fake_service = SimpleNamespace(refresh_content=fake_source_refresh)

        def run_queue_worker():
            connections.close_all()
            try:
                outcomes["worker"] = process_refresh_queue(
                    budget=5, service=fake_service
                )
            except BaseException as exc:  # surface thread failures in the test
                errors.append(exc)
            finally:
                connections.close_all()

        from tracker.services.bulk_refresh import process_watchlist_refresh

        def run_watchlist_refresh():
            connections.close_all()
            try:
                outcomes["watchlist"] = process_watchlist_refresh(
                    budget=5, user_id=987654
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        worker = threading.Thread(target=run_queue_worker, daemon=True)
        watchlist = threading.Thread(target=run_watchlist_refresh, daemon=True)
        with patch(
            "tracker.services.refresh_requests._service_for_scope",
            return_value=fake_service,
        ), patch(
            "tracker.services.refresh_requests._metered_source_is_unnecessary",
            return_value=False,
        ), patch(
            "tracker.services.bulk_refresh._candidate_pairs",
            side_effect=AssertionError(
                "watchlist sweep must not start when the queue lock is held"
            ),
        ):
            try:
                worker.start()
                self.assertTrue(
                    source_entered.wait(timeout=10),
                    "worker did not enter its source call",
                )
                watchlist.start()
                watchlist.join(timeout=10)
                self.assertFalse(
                    watchlist.is_alive(), "watchlist waited indefinitely"
                )
            finally:
                release_source.set()
                if worker.ident is not None:
                    worker.join(timeout=10)
                if watchlist.ident is not None:
                    watchlist.join(timeout=10)

        self.assertFalse(worker.is_alive(), "queue worker did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(source_calls, [self.movie.pk])
        self.assertTrue(outcomes["watchlist"]["rate_limited"])
        self.assertEqual(outcomes["watchlist"]["requests_pending"], 1)
        self.assertEqual(
            RefreshRequest.objects.get(pk=self.request.pk).status,
            RefreshRequestStatus.DONE,
            "the queue owner should settle its request after one source call",
        )
        self.assertEqual(
            ApiCallCounter.objects.get(service="streaming_availability").count, 1
        )
        self.assertEqual(
            ApiCallLedger.objects.filter(
                service="streaming_availability"
            ).values_list("count", flat=True).get(),
            1,
            "the contending watchlist path must not reserve a second quota unit",
        )

    def test_worker_defers_while_watchlist_sweep_holds_the_shared_lock(self):
        sweep_entered = threading.Event()
        release_sweep = threading.Event()
        errors = []
        outcomes = {}

        def held_sweep(**_kwargs):
            sweep_entered.set()
            if not release_sweep.wait(timeout=10):
                raise AssertionError("test did not release the watchlist sweep")
            return {"refreshed": 0}

        from tracker.services.bulk_refresh import process_watchlist_refresh

        def run_watchlist():
            connections.close_all()
            try:
                outcomes["watchlist"] = process_watchlist_refresh(
                    budget=5, user_id=987654
                )
            except BaseException as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        def run_worker():
            connections.close_all()
            try:
                outcomes["worker"] = process_refresh_queue(budget=5)
            except BaseException as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        watchlist = threading.Thread(target=run_watchlist, daemon=True)
        worker = threading.Thread(target=run_worker, daemon=True)
        with patch(
            "tracker.services.bulk_refresh._process_watchlist_refresh_locked",
            side_effect=held_sweep,
        ), patch(
            "tracker.services.refresh_requests._drain_refresh_requests_locked",
            side_effect=AssertionError("worker must not enter the held drain lock"),
        ):
            try:
                watchlist.start()
                self.assertTrue(
                    sweep_entered.wait(timeout=10),
                    "watchlist did not enter the protected sweep",
                )
                worker.start()
                worker.join(timeout=10)
                self.assertFalse(worker.is_alive(), "worker waited indefinitely")
            finally:
                release_sweep.set()
                if worker.ident is not None:
                    worker.join(timeout=10)
                if watchlist.ident is not None:
                    watchlist.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertTrue(outcomes["worker"]["rate_limited"])
        self.assertEqual(outcomes["worker"]["requests_pending"], 1)
        self.assertEqual(
            RefreshRequest.objects.get(pk=self.request.pk).status,
            RefreshRequestStatus.PENDING,
        )
