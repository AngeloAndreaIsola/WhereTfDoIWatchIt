"""PostgreSQL maintenance barrier shared by availability refresh writers.

Why this exists
---------------

The historical remediation command is a short, destructive transaction over
``ContentMapping``, ``Offer``, ``OfferEvidence`` and ``OfferPrice``.  Checking
``pg_locks`` once before starting proves only that *no refresh was running at
that instant*: a worker or cron job can start immediately afterwards and
re-create a mapping or offer that the remediation just deleted.

This module adds one database-wide write barrier with two sides:

* every participating canonical writer takes the barrier in **shared** mode
  around its write transaction (:func:`writer_barrier`): availability
  refreshes, the legacy scraper ingestion/deletion bridge and provider
  deletion.  Shared holders coexist, so normal writers are unaffected by each
  other;
* the remediation takes the same key in **exclusive** mode
  (:func:`acquire_maintenance_barrier`).  PostgreSQL grants it only when no
  shared holder exists, and while it is held a new refresh's
  ``pg_try_advisory_lock_shared`` fails, so the refresh refuses to write.

The lock is a *session* advisory lock.  It is deliberately acquired outside any
``transaction.atomic()`` block: otherwise PostgreSQL would release it at commit
and the post-commit validation window would be unprotected.  Callers must
release it explicitly (the context manager does), or the connection closing
does so.

:func:`writer_barrier` is re-entrant per thread.  A protected writer may call
another protected writer (the provider-delete endpoint cascades into the
``ScrapedContentMapping`` deletion mirror), and PostgreSQL session advisory
locks are counted, not idempotent: blindly nesting would need a matching second
unlock and would leave the lock held if the inner unwind ever failed.  The
outermost acquisition owns the single database lock; nested blocks only
increment an in-process depth counter.

What this does **not** prove
----------------------------

Advisory locks only constrain processes that take them.  A worker container
running older code without this module does not participate, so deploying the
same image to backend, worker, catalogue-worker and every cron entry point is an
operator precondition, verified in ``docs/REMEDIATE_MAPPING_AUDIT.md``.  The
``BARRIER_SENTINEL`` string exists so that deployment can be checked with
``grep`` in each container.

The same limit applies in-process: Django-admin edits/deletes of canonical
models, ``manage.py shell`` writes, direct ORM writes in data migrations and the
explicit orphan-purge commands do **not** acquire this barrier.  They must not
run while maintenance holds the exclusive lock.  The barrier protects only the
writers listed above.

On a backend without advisory locks (SQLite in the isolated test settings)
there is no database-level proof of exclusion.  :func:`acquire_maintenance_barrier`
returns an explicitly *unverified* token, and the remediation refuses to mutate
unless the test-only setting
``AVAILABILITY_REMEDIATION_ALLOW_UNVERIFIED_BARRIER`` is enabled.  That setting
must never be enabled in production.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Iterator

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)

#: Per-thread nesting depth of :func:`writer_barrier`.  Django database
#: connections are thread-local, so the advisory lock this tracks is too.
_barrier_state = threading.local()

#: Source marker used by the runbook to prove each container runs this code.
BARRIER_SENTINEL = "AVAILABILITY_MAINTENANCE_BARRIER_V1"

#: Fixed signed 64-bit advisory-lock key.  It is intentionally unrelated to the
#: per-source keys (SHA-256 derived) and the title-drain key (``0x7A9ED41C``).
#: Never renumber this: two deployments with different keys would not exclude
#: each other.
WRITER_BARRIER_KEY = 0x5A177EADBA221E01


class MaintenanceInProgress(RuntimeError):
    """Another maintenance operation or an older writer blocks the barrier."""


class BarrierUnavailable(RuntimeError):
    """The backend cannot provide a database-level write barrier."""


class MaintenanceBarrier:
    """Handle for an acquired exclusive maintenance lock.

    ``verified`` is true only when PostgreSQL granted the exclusive advisory
    lock and no other session held an advisory lock.  ``held`` tracks the
    in-process handle so a released token cannot be passed to the mutation.
    """

    def __init__(self, *, verified: bool, held: bool, detail: str = ""):
        self.verified = verified
        self.held = held
        self.detail = detail
        self._released = not held

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.held = False
        if not self.verified:
            return
        try:
            _unlock(exclusive=True)
        except DatabaseError:
            # A broken connection releases session locks when it closes; do not
            # mask the caller's original error with an unlock failure.
            logger.exception("availability_write_barrier_unlock_failed")


def advisory_locks_supported() -> bool:
    """True when the active connection is PostgreSQL."""
    return connection.vendor == "postgresql"


def _try_lock(*, exclusive: bool) -> bool:
    function = (
        "pg_try_advisory_lock" if exclusive else "pg_try_advisory_lock_shared"
    )
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}(%s)", [WRITER_BARRIER_KEY])
        row = cursor.fetchone()
    return bool(row and row[0])


def _unlock(*, exclusive: bool) -> None:
    function = "pg_advisory_unlock" if exclusive else "pg_advisory_unlock_shared"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}(%s)", [WRITER_BARRIER_KEY])


def other_advisory_locks() -> int:
    """Advisory locks held by *other* sessions in this database.

    Used only after taking the exclusive barrier, as a fail-closed check for a
    legacy writer that does not participate in the barrier (for example a
    container running pre-barrier code).  It is not the safety mechanism
    itself: new :func:`writer_barrier` holders cannot acquire while the
    exclusive lock is held.
    """
    if not advisory_locks_supported():
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
              FROM pg_locks
             WHERE locktype = 'advisory'
               AND pid <> pg_backend_pid()
               AND database = (
                   SELECT oid FROM pg_database WHERE datname = current_database()
               )
            """
        )
        return cursor.fetchone()[0]


def acquire_maintenance_barrier() -> MaintenanceBarrier:
    """Take the exclusive barrier, or raise without mutating anything.

    Fails when a refresh writer holds the shared barrier, when any other
    session holds an advisory lock (a conservative guard against pre-barrier
    writer code), or when the backend cannot provide advisory locks.
    """
    if not advisory_locks_supported():
        return MaintenanceBarrier(
            verified=False,
            held=False,
            detail="backend does not support PostgreSQL advisory locks",
        )
    if not _try_lock(exclusive=True):
        raise MaintenanceInProgress(
            "another availability writer or maintenance session holds the "
            "write barrier; pause all refresh workers and cron entries, then retry"
        )
    barrier = MaintenanceBarrier(verified=True, held=True)
    try:
        others = other_advisory_locks()
    except DatabaseError:
        barrier.release()
        raise
    if others:
        barrier.release()
        raise MaintenanceInProgress(
            f"{others} other advisory lock(s) are held in this database; a "
            "pre-barrier writer may still race. Restart every worker and cron "
            "entry point on this image before retrying"
        )
    return barrier


@contextmanager
def writer_barrier(operation: str = "availability refresh") -> Iterator[None]:
    """Hold the shared barrier for one canonical availability write.

    Raises :class:`MaintenanceInProgress` instead of waiting, so a refresh tick
    skips cleanly while remediation owns the exclusive lock.  Non-PostgreSQL
    backends (SQLite tests) proceed without a database lock.

    Re-entrant: when the current thread already holds the shared barrier, a
    nested call reuses that acquisition and the outermost block releases it.
    This is what lets provider deletion hold the barrier through its whole
    transaction while the ``post_delete`` mirror deletes the legacy projection.
    """
    if not advisory_locks_supported():
        yield
        return
    depth = getattr(_barrier_state, "depth", 0)
    if depth:
        _barrier_state.depth = depth + 1
        try:
            yield
        finally:
            _barrier_state.depth -= 1
        return
    if not _try_lock(exclusive=False):
        raise MaintenanceInProgress(
            f"{operation} refused: availability maintenance holds the write barrier"
        )
    _barrier_state.depth = 1
    try:
        yield
    finally:
        _barrier_state.depth = 0
        try:
            _unlock(exclusive=False)
        except DatabaseError:
            logger.exception("availability_write_barrier_shared_unlock_failed")
