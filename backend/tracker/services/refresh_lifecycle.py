"""The authoritative lifecycle of an availability refresh run.

A run's status is a fact about **persisted** state, never about an in-memory
counter. The worker says "I finished"; *this module* decides what that means,
by querying the request rows, the per-source rows and the title
classification. That single decision is what makes a worker crash survivable:

    worker starts   -> RUNNING
    requests execute -> each row persists its own outcome as it settles
    worker dies     -> the rows are still there, so a later process can
                       read the true state and finalise correctly

If the status were computed from the worker's own tally and written at the
end, a crash before that write would leave a run stuck in ``RUNNING``
forever, and a crash *after* draining half the queue would report progress
the database never saw. Both failure modes are silent, and both are exactly
what this module exists to remove.

The state machine
-----------------
::

    QUEUED ──▶ RUNNING ──┬──▶ COMPLETED
                         ├──▶ PARTIAL
                         └──▶ FAILED

with ``COMPLETED``/``PARTIAL``/``FAILED`` all terminal and ``finalise_run``
idempotent across them.

The three terminal states, defined so they cannot be confused:

``COMPLETED``
    Every scheduled request settled and no unresolved work remains. Every
    title is accounted for as available or unavailable — nothing is waiting
    on a budget, a queue, or an upstream that has not answered.

``PARTIAL``
    The run processed what it could, and some work was skipped, blocked, or
    failed at the *source* level. **This is the normal outcome for a
    metered run.** 24 requests blocked by a spent quota is a successful run
    that could not finish its backlog, not a broken one — reporting it as
    ``FAILED`` would train the reader to ignore ``FAILED``, which is the
    only status that should ever mean "something is actually wrong".

``FAILED``
    The run itself could not execute: a fatal error, or every scheduled
    source failed so there is no evidence of any work having happened. An
    individual source failing is *never* on its own a reason for this.

The distinction is the whole point: ``FAILED`` must stay rare and loud, or
it stops meaning anything.

Crash recovery
--------------
:meth:`AvailabilityRefreshLifecycle.stale_running_runs` finds runs left in
``RUNNING`` past a threshold, and :meth:`reconcile` finalises them from their
persisted state. A run that actually finished while nobody was watching
becomes ``COMPLETED``/``PARTIAL``; one that is genuinely mid-flight stays
``RUNNING`` and is not touched. That distinction is why reconciliation is
based on the *request rows* rather than on elapsed time alone.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from ..models import (
    AvailabilityRefresh,
    AvailabilityRefreshStatus,
    AvailabilityRefreshType,
    BlockedReason,
    RefreshRequest,
    RefreshRequestStatus,
)
from .availability_report import (
    NON_TERMINAL_SOURCE_STATUSES,
    TERMINAL_SOURCE_STATUSES,
    TitleAvailabilityState,
    build_run_summary,
    sync_run_progress,
)

logger = logging.getLogger(__name__)

#: A ``RUNNING`` run untouched for this long is a candidate for
#: reconciliation. Long enough that a slow run is never mistaken for a dead
#: one, short enough that a crashed run is noticed within an hour.
STALE_RUNNING_AFTER = timedelta(minutes=30)

ACTIVE_STATUSES = (
    AvailabilityRefreshStatus.QUEUED,
    AvailabilityRefreshStatus.RUNNING,
)

TERMINAL_STATUSES = (
    AvailabilityRefreshStatus.COMPLETED,
    AvailabilityRefreshStatus.PARTIAL,
    AvailabilityRefreshStatus.FAILED,
)


class AvailabilityRefreshLifecycle:
    """The only writer of ``AvailabilityRefresh.status``.

    Centralised so the transition rules live in one place. Scattering
    ``refresh.status = ...`` across the worker, the views and the report is
    how a run ends up ``COMPLETED`` because a GET handler decided it was
    finished, which is precisely the "inferred from request rows" design this
    replaces — except worse, because an inference can be wrong and a
    transition cannot.
    """

    # -- transitions ------------------------------------------------------

    @staticmethod
    def start(refresh: AvailabilityRefresh) -> AvailabilityRefresh:
        """QUEUED -> RUNNING, or RUNNING -> RUNNING on a resumed run.

        Idempotent, because a worker that restarts mid-run must re-assert
        ownership rather than being refused. Never moves a terminal run
        back to ``RUNNING``: a finished run is finished, and a restarted
        worker adopting it would make the record lie.
        """
        if refresh.status in TERMINAL_STATUSES:
            return refresh
        if refresh.status == AvailabilityRefreshStatus.RUNNING:
            return refresh
        refresh.status = AvailabilityRefreshStatus.RUNNING
        if refresh.started_at is None:
            refresh.started_at = timezone.now()
        refresh.save(update_fields=["status", "started_at"])
        logger.info(
            "availability_refresh_running refresh_id=%s batch=%s",
            refresh.pk, refresh.batch,
        )
        return refresh

    @staticmethod
    def fail(refresh: AvailabilityRefresh, error: str = "") -> AvailabilityRefresh:
        """Record a fatal, run-level failure.

        The only path to ``FAILED`` besides "every source failed". An
        exception inside one source's fetch must not come through here.
        """
        refresh.status = AvailabilityRefreshStatus.FAILED
        refresh.finished_at = timezone.now()
        if error:
            refresh.error = error[:2000]
        refresh.save(update_fields=["status", "finished_at", "error"])
        logger.warning(
            "availability_refresh_failed refresh_id=%s error=%s", refresh.pk, error,
        )
        return refresh

    @staticmethod
    def finalise(refresh: AvailabilityRefresh, movie_ids=None) -> AvailabilityRefresh:
        """Settle the run's status from persisted state, idempotently.

        Idempotent twice over: calling it again on a terminal run returns the
        same status without re-deriving anything that could differ, and
        calling it on a live run twice cannot move it backwards.

        The status is derived **only** from what is persisted — the request
        rows, the per-source rows, and the title classification. No in-memory
        counter from the worker is consulted, which is what makes the result
        identical whether it is computed by the worker that did the work, by a
        later process after a crash, or by a reader.
        """
        if refresh.status in TERMINAL_STATUSES:
            return refresh

        ids = _scope_movie_ids(refresh, movie_ids)

        # The settlement boundary. Anything this run can no longer execute is
        # settled *before* the status is derived, so the status is computed from
        # rows that are all terminal. Deriving it first and settling afterwards
        # is what let a run report `PARTIAL` with 553 requests still queued.
        settled = settle_unexecutable_requests(refresh)

        summary = build_run_summary(ids, batch=refresh.batch)
        sync_run_progress(refresh, ids, batch=refresh.batch)

        outstanding = _outstanding_request_count(refresh)
        blocked = summary["totals"]["unknown"] > 0
        source_rows = list(refresh.sources.all())
        every_source_failed = bool(source_rows) and all(
            r.status == "failed" for r in source_rows
        )

        # Hard invariant, enforced rather than assumed. A terminal run that
        # still owns outstanding requests or unsettled source rows is a bug in
        # whatever tried to finalise it, and the correct response is to raise:
        # recording a terminal status over a non-empty queue produces a report
        # that claims work finished which did not, and that is worse than a
        # failed run because it is believed.
        if outstanding:
            raise UnsettledWorkError(
                f"refresh {refresh.pk} has {outstanding} unsettled request(s); "
                f"settle them before finalising"
            )
        unsettled_sources = [
            r for r in source_rows if r.status in NON_TERMINAL_SOURCE_STATUSES
        ]
        if unsettled_sources:
            raise UnsettledWorkError(
                f"refresh {refresh.pk} has unsettled source row(s): "
                + ", ".join(f"{r.source}={r.status}" for r in unsettled_sources)
            )

        # From here on the run owns nothing outstanding, so the status describes
        # the *result* rather than the interruption:
        #
        #   FAILED    — every source failed outright, so the run produced no
        #               evidence at all. Reserved and rare, so that it still
        #               means something when it appears.
        #   PARTIAL   — the run settled all its work, but titles remain
        #               unresolved (blocked, failed, or never in any catalogue).
        #               A worker running out of budget lands here too, and that
        #               is now honest: the remaining rows are `blocked`, not
        #               quietly pending.
        #   COMPLETED — every applicable title resolved.
        if every_source_failed:
            status = AvailabilityRefreshStatus.FAILED
        elif blocked:
            status = AvailabilityRefreshStatus.PARTIAL
        else:
            status = AvailabilityRefreshStatus.COMPLETED

        refresh.status = status
        refresh.finished_at = timezone.now()
        refresh.save(update_fields=["status", "finished_at"])
        logger.info(
            "availability_refresh_finalised refresh_id=%s status=%s "
            "settled=%s unknown=%s",
            refresh.pk, status, settled or "{}", summary["totals"]["unknown"],
        )
        return refresh

    # -- recovery ---------------------------------------------------------

    @staticmethod
    def stale_running_runs(now=None, older_than=STALE_RUNNING_AFTER) -> list[AvailabilityRefresh]:
        """``RUNNING`` runs with no recent activity — the crash signature.

        Measured against ``updated_at`` rather than ``started_at`` because a
        long run legitimately updates many times: a run that is genuinely
        working is never stale, however long it has been going.

        **``updated_at`` is ``auto_now``, so it cannot be backdated through
        ``save()``** — Django overwrites it on every write, which is exactly
        what makes it right for this purpose (it really does track the last
        write) and also what makes it awkward to *test*. Tests must age the
        row with a queryset ``update()``, which bypasses ``auto_now``; a
        ``save(update_fields=...)`` silently stores "now" and the test then
        fails for a reason that looks like a logic bug in the staleness
        filter. Measured: backdating via ``save()`` left the delta at 0.0
        minutes.
        """
        now = now or timezone.now()
        return list(
            AvailabilityRefresh.objects.filter(
                status=AvailabilityRefreshStatus.RUNNING,
                updated_at__lt=now - older_than,
            ).order_by("created_at")
        )

    @staticmethod
    def reconcile(refresh: AvailabilityRefresh, movie_ids=None) -> AvailabilityRefresh:
        """Settle a run left behind by a dead worker, from persisted state.

        A run whose queue is genuinely empty finalises normally. A run with
        requests still pending is left ``RUNNING``: the work was interrupted
        mid-flight rather than finished, and marking it ``COMPLETED`` is the
        single worst outcome available here, because it would claim the
        refresh succeeded while silently dropping its backlog.
        """
        outstanding = _outstanding_request_count(refresh)
        if outstanding:
            logger.info(
                "availability_refresh_reconcile_deferred refresh_id=%s outstanding=%s",
                refresh.pk, outstanding,
            )
            return refresh
        return AvailabilityRefreshLifecycle.finalise(refresh, movie_ids)


# --- module-level convenience (the worker's call surface) ------------------


def claim_active_run(batch=None) -> AvailabilityRefresh | None:
    """The run a worker should adopt this tick, moved to ``RUNNING``.

    ``None`` when there is nothing to adopt, so the worker can tell "no run"
    from "a run I could not claim". A terminal run is never adopted.
    """
    qs = AvailabilityRefresh.objects.filter(status__in=ACTIVE_STATUSES)
    if batch is not None:
        qs = qs.filter(batch=batch)
    run = qs.order_by("-created_at").first()
    if run is None:
        return None
    return AvailabilityRefreshLifecycle.start(run)


def finalise_run(refresh: AvailabilityRefresh, movie_ids=None) -> AvailabilityRefresh:
    """Module-level alias for :meth:`AvailabilityRefreshLifecycle.finalise`."""
    return AvailabilityRefreshLifecycle.finalise(refresh, movie_ids)


def reconcile_stale_runs(now=None) -> list[AvailabilityRefresh]:
    """Reconcile every run a dead worker left ``RUNNING``."""
    settled = []
    for run in AvailabilityRefreshLifecycle.stale_running_runs(now=now):
        settled.append(AvailabilityRefreshLifecycle.reconcile(run))
    return settled


def _scope_movie_ids(refresh: AvailabilityRefresh, movie_ids=None) -> list[int]:
    """Which titles this run is about.

    Prefers the run's frozen ``title_filter``; falls back to the caller's
    list, and finally to the union of the batch's own request rows. The last
    fallback matters after a crash: the worker that enqueued the run is gone,
    so whatever it passed in memory no longer exists and the rows are the only
    surviving record of what the run covered.
    """
    if refresh.title_filter:
        return [int(m) for m in refresh.title_filter]
    if movie_ids:
        return [int(m) for m in movie_ids]
    if refresh.batch is not None:
        return sorted(
            RefreshRequest.objects.filter(batch=refresh.batch)
            .values_list("movie_id", flat=True)
            .distinct()
        )
    return []


class UnsettledWorkError(RuntimeError):
    """A run tried to go terminal while it still owned unsettled work.

    Raised instead of quietly recording a terminal status. The alternative —
    finalising anyway — produces a report that claims more than happened: a
    ``COMPLETED`` run whose queue is still full, or a ``PARTIAL`` whose reason
    is indistinguishable from "the worker gave up". Failing loudly costs one
    reconcile pass; recording a lie costs every future reader of the data.
    """


def settle_unexecutable_requests(refresh: AvailabilityRefresh) -> dict[str, int]:
    """Settle the requests this run can no longer execute, before finalising.

    The settlement boundary. Before this existed, the worker processed what it
    could, decided the run was finished, and left the rest ``pending`` — which
    is how a run reached a terminal status with 553 request rows still queued.
    ``PARTIAL`` then had two incompatible meanings: "some titles are genuinely
    unresolved" and "the worker ran out of budget mid-queue", and nothing in the
    data could tell them apart.

    A pending request is settled as ``blocked`` when it *cannot* run, with the
    reason recorded:

    - the source has no remaining daily budget. The request was legitimate
      when queued and becomes runnable at the next reset, so it is blocked
      rather than failed — nothing went wrong, we simply may not spend it yet;
    - the source ladder decided it is unnecessary, because a cheaper source
      resolved the title first.

    Neither is ``done``: ``done`` claims a source was consulted, and neither of
    these was. A request that *could* run is left alone, because leaving it
    pending is the correct signal that the run is not finished yet.

    Returns per-reason counts for logging and tests.
    """
    if refresh.batch is None:
        return {}

    settled: dict[str, int] = {}
    pending = RefreshRequest.objects.filter(
        batch=refresh.batch, status=RefreshRequestStatus.PENDING
    )

    # Budget is decided per source, not per request, so a spent source is
    # settled in one statement rather than row by row.
    exhausted = {
        slug
        for slug in pending.values_list("source", flat=True).distinct()
        if _is_out_of_budget(slug)
    }
    if exhausted:
        count = pending.filter(source__in=exhausted).update(
            status=RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            completed_at=timezone.now(),
        )
        settled[BlockedReason.QUOTA_EXHAUSTED] = count

    from . import source_policy
    from .refresh_requests import _metered_source_is_unnecessary

    if source_policy.consult_metered_only_when_unresolved():
        for request in pending.exclude(source__in=exhausted):
            source = request.source
            if not source or not source_policy.is_policy_metered(source):
                continue
            if not _metered_source_is_unnecessary(
                request.movie, source, request.priority, explicitly_named_source=True
            ):
                continue
            request.settle(
                RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.POLICY_NOT_NEEDED,
            )
            settled[BlockedReason.POLICY_NOT_NEEDED] = (
                settled.get(BlockedReason.POLICY_NOT_NEEDED, 0) + 1
            )

    return settled


def _is_out_of_budget(source_slug: str) -> bool:
    """Whether this source has no daily budget left today.

    Delegates to the same limiter the drain consults, so "out of budget" means
    one thing in the two places that act on it.

    ``remaining()`` returns ``None`` for a source that declares no quota, which
    means unlimited rather than exhausted — treating ``None`` as zero would
    block every request from every unmetered source.
    """
    from .rate_limit import rate_limiter_for

    limiter = rate_limiter_for(source_slug or "")
    if limiter is None or limiter.daily_limit is None:
        return False
    remaining = limiter.remaining()
    return remaining is not None and remaining <= 0


def _outstanding_request_count(refresh: AvailabilityRefresh) -> int:
    """Requests this run owns that have not settled.

    Scoped to the run's ``batch`` — the join key stamped at enqueue — so a
    pending request queued by some unrelated event cannot hold a finished run
    open. Requests queued before source fan-out carry the legacy ``""`` scope
    but the same batch, so they are counted too; the batch is what defines
    ownership, not the source spelling.
    """
    if refresh.batch is None:
        return 0
    return RefreshRequest.objects.filter(
        batch=refresh.batch, status=RefreshRequestStatus.PENDING
    ).count()
