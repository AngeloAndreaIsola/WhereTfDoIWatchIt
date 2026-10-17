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
    AvailabilityResolutionReason,
    BlockedReason,
    RefreshRequest,
    RefreshRequestStatus,
)
from .availability_report import (
    NON_TERMINAL_SOURCE_STATUSES,
    TitleAvailabilityState,
    title_source_report,
    TERMINAL_SOURCE_STATUSES,
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

    def fail(refresh: AvailabilityRefresh, error: str = "") -> AvailabilityRefresh:
        """Record a fatal, run-level failure.

        The run-level failure path, and the only route to ``FAILED`` that means
        "the refresh itself broke". A single source's fetch failing must never
        arrive here.

        **Settles before it terminates.** The invariant P6a established applies
        to *every* terminal status, FAILED included:

            terminal refresh
                => zero queued/running requests
                => zero queued/running source rows
                => finished_at IS NOT NULL

        This function used to stamp ``FAILED`` unconditionally, which meant the
        worker's exception handler could produce precisely the state the
        invariant forbids. It happened in production: run 8's tick raised
        ``UnsettledWorkError`` because 563 requests were still queued, the
        handler caught it, called this, and recorded ``failed`` with 426 pending
        requests — a terminal run whose terminal status was earned by the very
        defect the status is supposed to report.

        So: settle what cannot execute, verify nothing is outstanding, then
        terminate. A failed refresh is full of settled rows — ``done``,
        ``failed``, ``blocked``/``quota_exhausted`` — and is still ``FAILED``,
        because settling work is not the same as declaring it successful. The
        run-level failure is what the status records.

        Refuses rather than records if work remains outstanding afterwards. That
        guards future callers who bypass the worker's own failure path: a
        misleading terminal record is worse than a loud failure, because it is
        believed.
        """
        settled = settle_unexecutable_requests(refresh)
        settled_sources = settle_unexecutable_source_rows(refresh)

        outstanding = _outstanding_request_count(refresh)
        if outstanding:
            raise UnsettledWorkError(
                f"cannot fail refresh {refresh.pk}: {outstanding} request(s) are "
                f"runnable and therefore must be executed or settled first"
            )
        unsettled_sources = [
            f"{r.source}={r.status}"
            for r in refresh.sources.all()
            if r.status in NON_TERMINAL_SOURCE_STATUSES
        ]
        if unsettled_sources:
            raise UnsettledWorkError(
                f"cannot fail refresh {refresh.pk}: unsettled source row(s): "
                + ", ".join(unsettled_sources)
            )

        refresh.status = AvailabilityRefreshStatus.FAILED
        refresh.finished_at = timezone.now()
        if error:
            refresh.error = error[:2000]
        refresh.save(update_fields=["status", "finished_at", "error"])
        logger.warning(
            "availability_refresh_failed refresh_id=%s error=%s settled=%s "
            "settled_sources=%s",
            refresh.pk, error, settled or "{}", settled_sources or "{}",
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

        # A run whose worker exhausted its *execution budget* still owns
        # runnable work: the drain leaves those requests PENDING precisely
        # because a run budget is not a source quota, so they were not settled
        # above. Deriving a terminal status now would raise on the invariant
        # (or, worse, record a verdict over a queue that is still full). Leave
        # the run ``RUNNING`` so the next tick resumes it — the same stance
        # ``reconcile`` already takes for a run interrupted with work in
        # flight. This is what makes a run budget limit *work performed*, not
        # future eligibility.
        outstanding = _outstanding_request_count(refresh)
        if outstanding:
            logger.info(
                "availability_refresh_finalise_deferred refresh_id=%s "
                "outstanding=%s (worker run budget, not source quota)",
                refresh.pk, outstanding,
            )
            return refresh

        summary = build_run_summary(ids, batch=refresh.batch)
        sync_run_progress(refresh, ids, batch=refresh.batch)

        # The result is a pure function of already-settled evidence, so it is
        # derived in one place rather than threaded through the worker.
        status = derive_refresh_result(refresh, summary)

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


def derive_refresh_result(
    refresh: AvailabilityRefresh,
    summary: dict | None = None,
) -> str:
    """Reduce settled evidence to a refresh-level result. The final layer.

    The layers above this one are unchanged and stay separate:

        request state → source evidence → title classification → **result**

    This function reads only what those layers already persisted. It does not
    classify titles, consult upstreams, or decide whether a source should run.

    Precedence, in order:

    ``FAILED``
        A genuine refresh-level lifecycle/orchestration failure. Reaching it
        means calling :meth:`AvailabilityRefreshLifecycle.fail` — the worker's
        tick raised, finalisation could not complete, the run could not be
        established. It is deliberately *not* derived from evidence, because a
        status that appears from ordinary evidence teaches readers to ignore it.
        This mirrors the precedence you specified: run-level failure wins.

    ``PARTIAL``
        The lifecycle completed, but the settled evidence cannot support a
        complete determination. Any of: a title still unresolved; a request
        blocked for a reason that leaves a gap (``quota_exhausted``, but *not*
        ``policy_not_needed``); or an applicable source that failed.

    ``COMPLETED``
        Every applicable title has sufficient evidence. That includes *negative*
        answers — a source that ran and found nothing is successful work, not a
        failure — and a ``policy_not_needed`` skip where a cheaper source already
        resolved the title. A ``not_run``/``skipped`` source row is likewise
        free: a collector that held no applicable work cannot be evidence of
        failure merely by existing.

    Raises :class:`UnsettledWorkError` if the run still owns queued or running
    work. The terminal invariant is enforced here rather than assumed, so a run
    can never be recorded terminal over a non-empty queue. `PARTIAL` is
    therefore only ever reachable *after* everything is settled.
    """
    outstanding = _outstanding_request_count(refresh)
    if outstanding:
        raise UnsettledWorkError(
            f"refresh {refresh.pk} has {outstanding} unsettled request(s); "
            f"settle them before deriving a result"
        )

    source_rows = list(refresh.sources.all())
    unsettled = [
        f"{r.source}={r.status}"
        for r in source_rows
        if r.status in NON_TERMINAL_SOURCE_STATUSES
    ]
    if unsettled:
        raise UnsettledWorkError(
            f"refresh {refresh.pk} has unsettled source row(s): "
            + ", ".join(unsettled)
        )

    ids = _scope_movie_ids(refresh, None)
    if summary is None:
        summary = build_run_summary(ids, batch=refresh.batch)

    if _has_unresolved_in_scope_title(ids, refresh):
        return AvailabilityRefreshStatus.PARTIAL

    # An applicable source that was tried and failed is incomplete evidence
    # even if every title happens to resolve elsewhere, because that source's
    # scope is unaccounted for. A source that never ran is not.
    if _has_applicable_source_failure(refresh):
        return AvailabilityRefreshStatus.PARTIAL

    # Blocked work is only a gap when it left something undetermined. A
    # `policy_not_needed` skip means a cheaper source already resolved the
    # title, which is exactly the ladder working.
    if _has_informative_blocked_work(refresh):
        return AvailabilityRefreshStatus.PARTIAL

    return AvailabilityRefreshStatus.COMPLETED


def _has_unresolved_in_scope_title(movie_ids, refresh: AvailabilityRefresh) -> bool:
    """Whether a title in this run's scope was left without a determination.

    Scope matters here, and it is the whole point. The report classifies a title
    against *every* source in the registry, including sources this refresh never
    asked about, so an untouched source reads ``not_checked`` and would hold the
    whole run at ``PARTIAL`` forever. Measured: three sources answering
    ``no_offer`` cleanly still reported ``unknown``, purely because
    Streaming Availability had no request row in that run.

    So this asks a narrower question — did any source **this run** was
    responsible for leave the title undetermined? A source outside the run's
    scope is not evidence about this run either way.

    Definitive answers count as determined, including negatives:
    ``resolved``, ``no_offer`` and ``title_not_found`` all mean the source
    answered. Only absence of information — ``source_unavailable`` (a transient
    failure), ``quota_exhausted``, ``not_checked`` from a source the run did
    engage, ``queued`` — leaves the title unresolved.
    """
    if not movie_ids:
        return False

    report = title_source_report(movie_ids, batch=refresh.batch)

    # What this refresh actually asked about. A source with no request row in
    # this batch was not this run's responsibility, whatever the global
    # report says about it.
    engaged = {
        (row["movie_id"], row["source"])
        for row in RefreshRequest.objects.filter(batch=refresh.batch).values(
            "movie_id", "source"
        )
    }

    for movie_id, entry in report.items():
        if entry["state"] == TitleAvailabilityState.AVAILABLE:
            # A positive answer settles the title regardless of what other
            # sources did or did not say.
            continue
        for src in entry["sources"]:
            if src["reason"] in _DEFINITIVE_NEGATIVE_OR_POSITIVE:
                continue
            if (movie_id, src["source"]) not in engaged:
                # Outside this run's scope: not this run's evidence gap.
                continue
            return True
    return False


# Per-source verdicts that mean the source *answered*. `resolved` is a positive
# answer; `no_offer` and `title_not_found` are definitive negatives. All three
# are completed work — a source that ran and found nothing has succeeded.
_DEFINITIVE_NEGATIVE_OR_POSITIVE = frozenset(
    {
        AvailabilityResolutionReason.RESOLVED,
        AvailabilityResolutionReason.NO_OFFER,
        AvailabilityResolutionReason.TITLE_NOT_FOUND,
    }
)


# Blocked reasons that mean "we may not run this yet", as opposed to the ladder
# correctly deciding the work was unnecessary. Mirrors BlockedReason but is
# stated here so the result layer reads without cross-referencing the request
# layer's vocabulary.
_UNINFORMATIVE_BLOCK_REASONS = frozenset(
    {
        "policy_not_needed",
        "superseded",
    }
)


def _has_informative_blocked_work(refresh: AvailabilityRefresh) -> bool:
    """Whether any settled row is blocked for a reason that leaves a gap.

    Only `blocked`/``quota_exhausted`` and ``source_disabled`` count: those mean
    a legitimate request could not be executed, so its evidence is missing.
    ``policy_not_needed`` and ``superseded`` do not — they record work another
    source or a newer request legitimately replaced.
    """
    return RefreshRequest.objects.filter(
        batch=refresh.batch, status=RefreshRequestStatus.BLOCKED
    ).exclude(blocked_reason__in=_UNINFORMATIVE_BLOCK_REASONS).exists()


def _has_applicable_source_failure(refresh: AvailabilityRefresh) -> bool:
    """Whether an applicable source failed, leaving its scope unaccounted for.

    A `failed` source row means it was tried and did not answer. A `skipped`,
    `not_applicable` or `not_run` row means it was never applicable, which is
    not a failure and must not make the run partial.
    """
    return any(row.status == "failed" for row in refresh.sources.all())


def settle_unexecutable_source_rows(refresh: AvailabilityRefresh) -> dict[str, int]:
    """Settle the source rows this run can no longer execute, before finalising.

    The source-level counterpart of :func:`settle_unexecutable_requests`.

    **Settlement is a lifecycle transition, so it only runs on a run that is
    still live.** A terminal refresh is a historical record: its source rows say
    what the run left behind at the moment it stopped, and rewriting them would
    falsify that record. Runs 5, 6 and 8 predate this function and still carry
    `running` source rows as evidence of the bug it fixes; that evidence is kept
    on purpose and no migration rewrites it. The guard below makes "historical
    runs are never repaired" a property of the code rather than a convention
    someone has to remember.

    **Each settled row records why it is no longer executable**, because that
    reason is what P6b's result calculation needs and cannot recover afterwards:

    - **out of daily budget** → ``quota_exhausted``. The source was applicable;
      the run simply may not spend it. Mirrors request-level
      ``blocked``/``quota_exhausted``.
    - **no work of this run belonged to it** → ``skipped``. ``running`` was a
      placeholder the fan-out opened and never needed.
    - **still runnable** → *left alone*, so ``fail()``/``finalise()`` refuse.
      Falsely settling it would manufacture terminality by discarding work that
      could still have executed, which is the failure this boundary exists to
      prevent.

    A genuine source failure is never reclassified: only ``queued``/``running``
    rows are considered, so a real ``failed``/``completed`` row keeps its own
    meaning.

    Scope is strictly this refresh's own rows; other runs are never touched.

    Returns per-source counts for logging and tests.
    """
    if refresh.status in TERMINAL_STATUSES:
        return {}

    rows = [
        row
        for row in refresh.sources.all()
        if row.status in NON_TERMINAL_SOURCE_STATUSES
    ]
    if not rows:
        return {}

    settled: dict[str, int] = {}

    # Budget is per source, so decide once per source rather than per row.
    exhausted = {row.source for row in rows if _is_out_of_budget(row.source)}

    for row in rows:
        if row.source in exhausted:
            target = "quota_exhausted"
            reason = AvailabilityResolutionReason.QUOTA_EXHAUSTED
        elif _source_row_had_no_work(refresh, row):
            target = "skipped"
            reason = AvailabilityResolutionReason.NOT_CHECKED
        else:
            # Genuinely runnable. Left as-is on purpose; the caller refuses.
            continue
        row.status = target
        row.reason = reason
        row.finished_at = row.finished_at or timezone.now()
        row.save(update_fields=["status", "reason", "finished_at"])
        key = f"{row.source}={target}"
        settled[key] = settled.get(key, 0) + 1

    return settled


def _source_row_had_no_work(refresh: AvailabilityRefresh, row) -> bool:
    """Whether no settled request in this run ever named this source.

    A source row can be ``running`` simply because the fan-out opened it before
    discovering it had nothing to ask. When the run's requests prove the source
    was never consulted for any title, ``skipped`` is the truthful terminal state
    and ``running`` was only ever a placeholder.
    """
    return not RefreshRequest.objects.filter(batch=refresh.batch, source=row.source).exists()


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
    #
    # This also covers a gap the drain cannot reach on its own. `_candidate_batch`
    # excludes a blocked source in SQL, so a source whose daily budget is spent
    # never has its requests offered to `_drain_one` at all — the drain's own
    # budget guard is unreachable for it. Settling here is what stops those rows
    # accumulating forever: measured on the live watchlist, the drain skipped
    # 490 budget-exhausted requests and every one of them stayed `pending`.
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
