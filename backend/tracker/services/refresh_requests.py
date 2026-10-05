"""Event-driven availability refresh requests (Phase B).

The event side of the canonical refresh architecture:

    user/system event
        -> request_availability_refresh(...)    # local INSERT, idempotent
        -> tracker.RefreshRequest (pending, prioritized, deduplicated)
        -> drain_refresh_requests(...)          # called by process_watchlist_refresh
        -> AvailabilityRefreshService           # budgeted, rate-limited
        -> canonical Offer / ProviderContent / ContentMapping rows
        -> local PostgreSQL read path

Contracts:

- **Requesting a refresh never performs network I/O.** It is a local
  database operation executed inside the originating transaction, so it
  commits or rolls back with the user action and the HTTP response never
  waits for an external provider. Actual refresh work happens later, in the
  existing refresh run (cron or ``POST /api/watchlist/refresh/``).
- **Requests are idempotent.** At most one pending request exists per
  (movie, source scope), enforced by a partial unique constraint; repeated
  events collapse into the same row and may only raise its priority.
- **Source is an execution dimension, not a priority.** A request names the
  source it must be checked against (``availability.Source.slug``, resolved
  through :func:`resolve_request_source`), and the drain looks that source's
  collector *and* that source's own rate limiter up from the registry. The
  two dimensions stay orthogonal: Refresh All fans one title out over every
  registered title-level source while every request keeps the *same*
  ``EXPLICIT`` priority, and a source that has spent its daily budget defers
  only its own requests instead of blocking the queue.
- **Draining reuses the existing refresh infrastructure**: the same
  ``RateLimiter``/daily budget, the same per-source freshness policy
  (``availability.freshness``, evaluated for the request's own source scope —
  a fresh pending request settles without spending budget, while an
  ``EXPLICIT`` one — the manual "Refresh availability" action — forces the
  new check even when the current data still reads fresh), the same
  ``RefreshRun`` audit row, and the same ``AvailabilityRefreshService`` as
  the scheduled stale sweep — so event triggers can never bypass rate limits
  or duplicate freshness/reconciliation logic.
- **Failures never propagate to the originating user action.** A failed
  drain records ``attempts``/``last_error`` on the request row and retries at
  most ``MAX_ATTEMPTS`` times before being marked ``failed`` permanently.
- **Provider vs Source stays intact**: a request's ``source`` field scopes
  *where the information comes from* (an ``availability.Source.slug``, empty
  for the default title-targeted refresh). It never selects *where the user
  watches* — providers continue to contribute through every registered
  source that reports them.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Optional

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from availability.models import Offer
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry

from ..models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    BlockedReason,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from .bulk_refresh import (
    BulkRefreshSummary,
    _is_spent,
    is_stale,
    permanently_absent_scopes,
    title_level_source_slugs,
)
from .preferences import resolve_source_country
from .rate_limit import RateLimitExceeded, RateLimiter, rate_limiter_for
from .streaming_api import UpstreamTitleNotFound
from .watchability import RefreshState

logger = logging.getLogger(__name__)

# A request is retried at most this many times before it is marked failed
# and left alone (a later event may legitimately open a fresh request).
MAX_ATTEMPTS = 3

# How many *executable* pending requests one drain reads as candidates, in
# priority order. Bounded on purpose: Refresh All fans out one request per
# (title, source), so a watchlist run can queue hundreds of rows, and an
# unbounded read would pull the entire backlog into memory on every worker
# tick (every 5s).
#
# This limit is a memory/scan guard, **not** a fairness boundary: the candidate
# query already excludes the sources that are out of budget (see
# ``drain_refresh_requests``), so an exhausted source can never fill the
# window and hide executable work behind it. That exclusion — not the size of
# this constant — is what guarantees a rate-limited source cannot starve the
# queue; the constant only bounds how much of the table one tick materializes.
CANDIDATE_SCAN_LIMIT = 500

# How many bounded pages one drain may read. The page size already bounds
# memory; this bounds total work per tick, so even a pathological queue cannot
# hold a worker tick open. With the worker's per-tick budget (10 by default)
# this is never the binding constraint — the budget is spent long before.
MAX_SCAN_PAGES = 4

# After a completed attempt (done or failed), the same (movie, source) scope
# is left alone for this long even when the title still reads as stale. A
# completed refresh that found nothing is *not* "fresh" (absence is not
# evidence), so without this window every detail view or frontend poll of an
# unavailable title would enqueue — and the worker would spend — a brand-new
# external fetch on each read.
RECENT_ATTEMPT_WINDOW = dt.timedelta(minutes=10)


def has_canonical_offers(movie) -> bool:
    """True when the canonical path has ever observed this title."""
    return Offer.objects.filter(tmdb_id=movie.tmdb_id).exists()


def recently_attempted(movie, source: str = "") -> bool:
    """True when this scope already had a completed attempt within the window.

    Guards the enqueue side against churn: a drain that found nothing leaves
    the title stale, and this read-side check is what stops the next detail
    view (or frontend poll) from immediately opening another attempt for the
    very same observation window.
    """
    return RefreshRequest.objects.filter(
        movie=movie,
        source=source,
        status__in=[RefreshRequestStatus.DONE, RefreshRequestStatus.FAILED],
        completed_at__gte=timezone.now() - RECENT_ATTEMPT_WINDOW,
    ).exists()


def request_availability_refresh(
    movie,
    priority: int = RefreshPriority.STALE,
    *,
    source: str = "",
    if_stale: bool = True,
    batch: uuid.UUID | None = None,
) -> RefreshRequest | None:
    """Request an asynchronous canonical availability refresh for *movie*.

    Idempotent and local-only: creates at most one pending
    ``RefreshRequest`` per (movie, source scope), upgrading its priority if a
    later event cares more. Titles the canonical path has never seen are
    always bumped to ``RefreshPriority.NEW``.

    ``if_stale`` (default) skips titles whose canonical offers *for the
    requested source scope* are fresh (that scope's own freshness window, see
    ``availability.freshness``) and titles whose same scope completed an
    attempt within ``RECENT_ATTEMPT_WINDOW``, so repeated events (detail
    views, frontend polls, re-adds) do not churn the queue — and one source's
    fresh observation never makes another source's stale scope look fresh.

    ``batch`` stamps the request (or a pending row it collapses into) with
    the id of the bulk run that asked — the watchlist's "Refresh all" uses
    it so its progress endpoint counts exactly the rows this run owns, not
    every pending request in the system. Single-title events pass nothing
    and leave any existing stamp untouched.

    Never raises: an enqueue failure is logged and swallowed so it cannot
    break the user action that triggered it.
    """
    try:
        if if_stale and not is_stale(movie, source=source):
            return None
        if if_stale and recently_attempted(movie, source):
            # Already fetched (or failed) moments ago for this scope — the
            # title may legitimately still be stale because the provider has
            # nothing for it; do not open a new attempt on every read.
            return None

        effective_priority = priority
        if not has_canonical_offers(movie):
            effective_priority = min(priority, RefreshPriority.NEW)

        # Adopt a pending legacy default-scope row instead of queueing beside
        # it. ``""`` and ``streaming_availability`` resolve to the same source
        # (``resolve_request_source``), so creating a second pending row would
        # make the worker execute the same scope twice — spending the quota
        # twice for one logical check and double-counting it in the batch.
        # Re-stamping the legacy row to its explicit slug is what makes the
        # migration happen naturally on the next explicit refresh, without a
        # data backfill.
        legacy_scope = ""
        if source == DEFAULT_SOURCE_SLUG:
            pending_scopes = RefreshRequest.objects.filter(
                movie=movie, status=RefreshRequestStatus.PENDING
            ).values_list("source", flat=True)
            if legacy_scope in pending_scopes and DEFAULT_SOURCE_SLUG not in pending_scopes:
                legacy = RefreshRequest.objects.get(
                    movie=movie, source=legacy_scope,
                    status=RefreshRequestStatus.PENDING,
                )
                legacy.source = DEFAULT_SOURCE_SLUG
                legacy.priority = min(legacy.priority, effective_priority)
                if batch is not None:
                    legacy.batch = batch
                legacy.save(update_fields=["source", "priority", "batch"])
                return legacy

        try:
            request, created = RefreshRequest.objects.get_or_create(
                movie=movie,
                source=source,
                status=RefreshRequestStatus.PENDING,
                defaults={"priority": effective_priority, "batch": batch},
            )
        except IntegrityError:
            # Concurrent event won the partial unique constraint; the row we
            # want already exists — fall through to the upgrade check below.
            request, created = RefreshRequest.objects.get(
                movie=movie, source=source, status=RefreshRequestStatus.PENDING
            ), False

        if not created:
            update_fields: list[str] = []
            if effective_priority < request.priority:
                request.priority = effective_priority
                update_fields.append("priority")
            if batch is not None and request.batch != batch:
                # Re-stamp: this pending row now belongs to the newest bulk
                # run (last click wins — its progress loop is the live one).
                request.batch = batch
                update_fields.append("batch")
            if update_fields:
                request.save(update_fields=update_fields)
        return request
    except Exception:  # noqa: BLE001 - enqueue must never break the caller
        logger.exception(
            "refresh_request_enqueue_failed tmdb_id=%s priority=%s",
            getattr(movie, "tmdb_id", None),
            priority,
        )
        return None


def refresh_states_for(movie_ids) -> dict[int, str]:
    """Derive each title's default-scope refresh state in one batched query.

    Returns ``{movie_id: RefreshState}`` — the queue-side evidence the read
    path needs to tell ⚪ Unknown (never fetched) from ⚫ Not available
    (checked, nothing found), and to show 🔵 while a title waits:

    - ``PENDING``      — a request is queued (checked/failed distinctions
      below only matter once the queue lets go);
    - ``CHECKED``      — at least one request completed successfully: the
      canonical store's contents (including "no offers") are a real answer;
    - ``FAILED``       — attempts exist but none ever succeeded;
    - ``NEVER_CHECKED`` — no request at all.

    ``DONE``/``FAILED`` rows are this evidence: they must outlive the queue
    (do not prune them) or titles revert to "unknown".

    **Every source scope counts, not just the legacy ``source=""`` one.**
    Refresh All fans a title out to one request per title-level source, so a
    title that has been checked is normally carried by an *explicit* slug row
    (``streaming_availability``, ``youtube``, ``mediaset_infinity``) and never
    by a ``""`` row at all. Filtering on the literal ``""`` therefore reported
    ``never_checked`` for almost every real title, so a title that had actually
    been fetched still rendered ⚪ Unknown. This mirrors what the refresh-status
    endpoint already does: it aggregates all scopes with no ``source`` filter
    (``views.watchlist_refresh_status``) precisely because a run owns one row
    per (title, source).
    """
    movie_ids = list(movie_ids)
    per_movie: dict[int, set[str]] = {}
    for movie_id, state in RefreshRequest.objects.filter(
        movie_id__in=movie_ids,
    ).values_list("movie_id", "status"):
        per_movie.setdefault(movie_id, set()).add(state)

    states: dict[int, str] = {}
    for movie_id in movie_ids:
        statuses = per_movie.get(movie_id, set())
        if RefreshRequestStatus.PENDING in statuses:
            states[movie_id] = RefreshState.PENDING
        elif RefreshRequestStatus.DONE in statuses:
            states[movie_id] = RefreshState.CHECKED
        elif RefreshRequestStatus.FAILED in statuses:
            states[movie_id] = RefreshState.FAILED
        else:
            states[movie_id] = RefreshState.NEVER_CHECKED
    return states


def request_watchlist_refresh(
    user_id: int = 1,
    *,
    priority: int = RefreshPriority.EXPLICIT,
    if_stale: bool = False,
    sources: Optional[list[str]] = None,
) -> dict:
    """Queue an availability refresh for every title on a user's watchlist.

    Bulk counterpart of the detail-page refresh button and the enqueue side
    of the watchlist's "Refresh all availability" action. One local pass over
    the watchlist calls the regular ``request_availability_refresh`` per
    (title, source), so deduplication, priority upgrades, the never-fetched
    bump and the swallow-on-failure contract stay exactly the per-title ones —
    repeated clicks and titles already queued by an event collapse into the
    same rows instead of multiplying them. No network I/O happens here; the
    queue is drained later by the existing ``refresh_worker`` under the
    per-source rate limits and the shared run budget.

    **Source fan-out.** One title becomes one request *per applicable
    registered title-level source* (``sources`` defaults to
    :func:`~tracker.services.bulk_refresh.title_level_source_slugs`), so a
    watchlist of N titles over S title-level sources queues N x S requests
    instead of N requests that all funneled into Streaming Availability and
    inherited its 100/day cap. Every request is explicitly source-scoped:
    the empty ``source=""`` scope stays reserved for single-title events and
    for requests written before this change (the drain still resolves them,
    see :func:`resolve_request_source`).

    ``sources=None`` means "force every applicable title-level source" —
    the explicit semantics the button wants. Passing ``sources`` explicitly
    (or ``if_stale=True``) selects only the stale ones, which is the
    background/selective path. Catalogue-capable sources are never in either
    list: they have no title-targeted fetch, so they keep going through
    ``request_catalogue_refresh`` / ``process_catalogue_refresh``.

    ``if_stale=False`` by default (mirroring the single-title explicit
    endpoint): a deliberate "refresh everything" must check every title even
    while its current offers still read fresh.

    Every touched request is stamped with a freshly generated ``batch`` id
    (created *and* reused pending rows — the latter so a row opened by an
    earlier event becomes part of this run), and that id is returned: the
    progress endpoint scopes its counts to it, so "pending" always means
    *this* run's backlog rather than every pending request in the system.

    Returns ``{"queued": <rows this batch now owns>,
    "total_watchlist": <titles covered>, "sources": <slugs fanned out over>,
    "checks_per_title": <title-level sources>, "batch": <uuid str>,
    "skipped": {source: reason}, "skipped_counts": {source: n}}``.

    **The source policy is applied here, before the request row is created.**
    Not after: a source skipped after enqueueing has already cost a queue row,
    a worker tick, a storage write and a place in the batch's denominator,
    and the progress bar would then have to render a request that will never
    run. So each title is classified from its current evidence and only the
    sources the policy allows get a row.

    With the gate off (the default) every applicable title-level source is
    enqueued exactly as before — the change is additive, and turning the gate
    off restores the previous scheduling byte for byte.
    """
    batch = uuid.uuid4()
    base_sources = list(sources) if sources is not None else title_level_source_slugs()
    movie_ids = list(
        WatchlistItem.objects.filter(user_id=user_id)
        .values_list("movie_id", flat=True)
        .distinct()
    )

    # One classification pass for the whole watchlist, so the decision is a
    # single batched read rather than a per-title query.
    from .availability_report import title_source_report
    from .source_policy import decide_sources_for_title

    report = title_source_report(movie_ids) if movie_ids else {}

    # `applicable` is the union across titles (which sources this run touches
    # at all); `skipped_by_source` records per-source skip counts so the
    # response can say "not needed for 163 titles" rather than a bare total.
    applicable: list[str] = []
    skipped_counts: dict[str, int] = {}
    skipped_by_source: dict[str, int] = {}

    for _movie_id, movie in Movie.objects.in_bulk(movie_ids).items():
        decision = decide_sources_for_title(
            report.get(movie.pk, {}), applicable_slugs=base_sources
        )
        allowed = list(decision["allowed_sources"])
        for source, reason in decision["skipped_sources"].items():
            skipped_counts[reason] = skipped_counts.get(reason, 0) + 1
            skipped_by_source[source] = skipped_by_source.get(source, 0) + 1
        for source in allowed:
            if source not in applicable:
                applicable.append(source)
            request_availability_refresh(
                movie,
                priority=priority,
                if_stale=if_stale,
                source=source,
                batch=batch,
            )

    # `queued` counts what this batch actually owns. Counting the union of
    # sources across titles is wrong once titles legitimately differ — with
    # gating on, a resolved title contributes no metered row at all, so a
    # cross-title source count would overstate the run's real size.
    queued = RefreshRequest.objects.filter(
        movie_id__in=movie_ids,
        source__in=applicable,
        status=RefreshRequestStatus.PENDING,
        batch=batch,
    ).count()
    return {
        "queued": queued,
        "total_watchlist": len(movie_ids),
        "sources": applicable,
        "checks_per_title": len(applicable),
        "batch": str(batch),
        # Per-source skip reasons, plus the count of titles each applies to,
        # so the UI can render "not needed for 163 titles" instead of a
        # number that cannot be acted on.
        "skipped": {
            source: reason
            for source, reason in decide_sources_for_title(
                {"state": "available"}, applicable_slugs=base_sources
            )["skipped_sources"].items()
        },
        "skipped_counts": skipped_counts,
        "skipped_titles_per_source": skipped_by_source,
    }


def request_import_refresh(
    movie,
    priority: int = RefreshPriority.WATCHLIST,
    *,
    entering: bool = False,
) -> RefreshRequest | None:
    """Refresh-request rule for imported titles.

    Only requests refresh where it is useful:

    - ``entering`` — the title just entered the catalogue (new content row or
      new watchlist item), or
    - the title has **no canonical availability data at all** (never fetched).

    Existing titles that merely happen to be stale are left to the scheduled
    sweep, and fresh titles are skipped by the ``if_stale`` guard inside
    ``request_availability_refresh`` — so re-importing a 500-row backup never
    floods the queue with uncontrolled requests.
    """
    if not entering and has_canonical_offers(movie):
        return None
    return request_availability_refresh(movie, priority=priority, if_stale=True)


def request_catalogue_refresh(sources) -> CatalogueRefreshRequest:
    """Queue the worker's catalogue-sync job (enqueue side, local-only).

    Counterpart of the per-title queue for sources that sync their *whole*
    catalogue: the watchlist's "Refresh all" calls this with the slugs that
    currently read stale (``bulk_refresh.stale_catalogue_sources``), and the
    refresh worker runs them via
    ``bulk_refresh.process_pending_catalogue_refresh`` after draining its
    title queue — catalogue syncs spend no API budget.

    Idempotent: repeated clicks collapse into the one pending marker (partial
    unique constraint), merging newly stale slugs into it instead of
    stacking jobs.
    """
    wanted = sorted(set(sources))
    marker, created = CatalogueRefreshRequest.objects.get_or_create(
        status=CatalogueRefreshStatus.PENDING,
        defaults={"sources": wanted},
    )
    if not created:
        merged = sorted(set(marker.sources) | set(wanted))
        if merged != marker.sources:
            marker.sources = merged
            marker.save(update_fields=["sources"])
    return marker


def resolve_request_source(request) -> str:
    """The source slug a request must be executed against.

    Every freshly queued request carries an explicit slug (Refresh All fans
    out per source), so this is normally just ``request.source``. The empty
    ``source=""`` scope is the legacy/default one and resolves to
    ``DEFAULT_SOURCE_SLUG`` — the same behavior ``is_stale`` already had,
    which keeps requests written before this change working unchanged
    during migration instead of needing a data backfill.

    One function owns this mapping so the drain, the limiter lookup and the
    collector lookup can never disagree about which source a request is for.
    """
    return request.source or DEFAULT_SOURCE_SLUG


def _service_for_scope(source: str, default_service: AvailabilityRefreshService):
    """Resolve the refresh service for a request's source scope.

    Registry-driven: the slug goes through ``source_registry``, never an
    ``if source == ...`` branch, so a newly registered collector needs no
    change here.
    """
    if not source:
        return default_service
    return AvailabilityRefreshService(source=source_registry.get(source))


def _limiter_for_scope(
    source: str,
    default_limiter: RateLimiter | None,
    overrides: dict[str, RateLimiter] | None = None,
) -> RateLimiter:
    """The limiter that governs *source*.

    An injected override wins (tests, and the worker passing its own default
    limiter); otherwise the source's declared budget decides, via
    :func:`rate_limiter_for`. Only sources that declare a quota are ever
    metered, so an unmetered collector can never be reported as blocked.
    """
    if overrides and source in overrides:
        return overrides[source]
    if default_limiter is not None and source == DEFAULT_SOURCE_SLUG:
        return default_limiter
    return rate_limiter_for(source)


def _finish(request: RefreshRequest, status: RefreshRequestStatus) -> None:
    request.status = status
    request.completed_at = timezone.now()
    request.last_error = ""
    request.save(update_fields=["status", "completed_at", "last_error"])


def _metered_source_is_unnecessary(
    movie, source: str, priority, explicitly_named_source: bool = True
) -> bool:
    """Whether this metered request should never have been spent.

    ``explicitly_named_source`` is the *raw* ``request.source`` field, passed
    separately from the resolved ``source`` slug. They differ, and the
    difference is load-bearing: ``resolve_request_source`` maps an empty
    field onto ``DEFAULT_SOURCE_SLUG``, which is ``'streaming_availability'``
    — the metered slug itself. So a generic refresh request resolves to the
    metered aggregator while having named no source at all, and gating on the
    resolved slug alone would treat it as metered work. Callers pass the raw
    field so this predicate can tell "explicitly asked for SA" from "asked for
    availability in general".

    The execution-time half of the source hierarchy, and the only place the
    invariant is actually enforced. True means: *a non-metered source has
    already resolved this title, so this scarce request has nothing left to
    find.*

    Three deliberate refusals to gate:

    - a **non-metered** source — free to run, and running it is what produces
      the evidence the metered source would otherwise pay for;
    - an ``EXPLICIT`` request — the user's manual "Refresh availability" is a
      deliberate "ask me anyway", matching the existing freshness contract
      (see :func:`_drain_one`), so the gate must not silently override a
      direct instruction;
    - when the policy is off, which is the default. This is what keeps
      switching the gate on a measured, reversible decision rather than a
      side effect of deploying the module.

    Resolution is read through the *report's* classification rather than by
    asking "does this title have any offer", because only the report knows
    which offers are currently active and which sources have definitively
    answered. A withdrawn offer is not an answer and must not gate.
    """
    from . import source_policy

    if priority == RefreshPriority.EXPLICIT:
        return False
    if not source_policy.consult_metered_only_when_unresolved():
        return False
    # Gate only on a row that *explicitly names* its source. `DEFAULT_SOURCE_SLUG`
    # is `'streaming_availability'` — identical to the metered slug — so
    # `resolve_request_source` maps an empty `source` onto it, and an earlier
    # version of this guard compared against `DEFAULT_SOURCE_SLUG` and
    # therefore excluded genuine explicit SA requests too, disabling the gate
    # for the one source it exists to protect.
    #
    # The distinction is emptiness, never slug equality: an unscoped request
    # (`source=""`) has no metered identity to enforce, and an explicit SA
    # request does.
    if not explicitly_named_source:
        return False
    if not source_policy.is_policy_metered(source):
        return False

    from .availability_report import title_source_report

    entry = title_source_report([movie.pk]).get(movie.pk)
    if not entry:
        # No classification available: never block a request on missing data.
        return False
    return source_policy.decide_sources_for_title(entry).get(
        "skipped_sources", {}
    ).get(source) == source_policy.PolicySkipReason.NOT_NEEDED.value


def _drain_one(
    request: RefreshRequest,
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
    rate_limit_hit: bool,
    limiter_overrides: dict[str, RateLimiter] | None = None,
) -> bool:
    """Process one pending request under the run's guards.

    The very same guards as the background stale sweep, in order:
    freshness first — evaluated for *this request's source scope* on that
    source's own window, so a title that became fresh meanwhile is settled as
    ``done`` without spending budget (no external call) while a scope that is
    still stale for its own source still refreshes — then rate-limit capacity
    for **this request's own source**, then the remaining run budget.

    The one guard an ``EXPLICIT`` request does not take: the freshness
    short-circuit. Every other priority settles fresh scopes without
    spending budget, but ``EXPLICIT`` is the manual "Refresh availability"
    action — a deliberate *"check now"* that must perform a new availability
    check even while the current observation still reads as fresh (the
    enqueue side mirrors this with ``if_stale=False``). Rate limiting and
    the run budget are never bypassed.

    Outcomes:
    - refreshed / already fresh -> ``done``;
    - refresh raised -> ``attempts`` + ``last_error`` recorded, retried on a
      later run up to ``MAX_ATTEMPTS`` times, then ``failed`` permanently;
    - budget or rate limit exhausted -> stays ``pending`` for the next run.
      A rate-limited request has **not** failed: it is waiting for its
      source's quota to reset, and it is the only reason that source appears
      in ``summary.rate_limited_sources``.

    ``rate_limit_hit`` refers to the *default* source's limiter only (the
    caller's injected limiter): it may short-circuit default-scope requests,
    but never one belonging to another source.

    Returns the (possibly updated) default-source ``rate_limit_hit`` flag:
    once that limiter refuses, every later default-scope request of this run
    skips its acquire.
    """
    movie = request.movie
    source = resolve_request_source(request)
    scope_limiter = _limiter_for_scope(source, limiter, limiter_overrides)

    # Source policy, re-checked at EXECUTION time, not just at enqueue.
    #
    # The enqueue gate (:func:`request_watchlist_refresh`) refuses to create a
    # metered request for an already-resolved title, but a queued row can
    # outlive that decision: a cheap source's request may land *after* this
    # row was written, turning an unresolved title into a resolved one, and
    # the stale-sweep path enqueues requests that never passed through the
    # policy at all. Enforcing only at enqueue would therefore still spend the
    # scarce budget on answers nobody needs — the exact failure the hierarchy
    # exists to prevent.
    #
    # The gate is checked here, immediately before the upstream call, so a row
    # that became unnecessary while queued settles without spending a single
    # request. Non-metered sources are never gated.
    #
    # Settled as ``blocked``/``policy_not_needed``, not ``done``: the source was
    # never consulted, and ``done`` asserts that it was. This was previously
    # recorded as ``done``, which made the ladder's savings indistinguishable
    # from real upstream answers in the run's own accounting.
    #
    # Note this says nothing about the *title*. A title blocked here may well
    # be available — that is the entire point of the ladder — and the
    # title-level classifier reads evidence, not request statuses, so it still
    # reports such a title as resolved.
    if _metered_source_is_unnecessary(
        movie, source, request.priority, explicitly_named_source=bool(request.source)
    ):
        request.settle(
            RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.POLICY_NOT_NEEDED,
        )
        summary.skipped_policy_gated += 1
        summary.blocked_by_reason[BlockedReason.POLICY_NOT_NEEDED] = (
            summary.blocked_by_reason.get(BlockedReason.POLICY_NOT_NEEDED, 0) + 1
        )
        summary.requests_drained += 1
        return rate_limit_hit

    # Freshness is free: settle fresh scopes without touching the budget —
    # unless the user explicitly asked for a new check right now.
    if (
        request.priority != RefreshPriority.EXPLICIT
        and not is_stale(movie, source=request.source)
    ):
        _finish(request, RefreshRequestStatus.DONE)
        summary.skipped_cached += 1
        summary.requests_drained += 1
        return rate_limit_hit

    # A scope the upstream is known not to carry costs nothing to skip: the
    # title answered 404, so re-checking it can only re-earn the same 404 while
    # spending metered budget. Settled, never retried. An EXPLICIT request
    # still falls through — the user's manual "check now" may be probing
    # whether the catalogue has since picked the title up.
    if (
        request.priority != RefreshPriority.EXPLICIT
        and resolve_request_source(request) in permanently_absent_scopes(movie.tmdb_id)
    ):
        request.attempts += 1
        request.last_error = (
            f"UpstreamTitleNotFound: {request.last_error or 'title absent upstream'}"
        )
        request.status = RefreshRequestStatus.FAILED
        request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "completed_at"]
        )
        summary.skipped_upstream_missing += 1
        summary.requests_drained += 1
        return rate_limit_hit

    # This source's own budget. A blocked source defers only its own
    # requests; the caller keeps scanning for sources that can still run.
    if _is_spent(scope_limiter):
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        # Settled for the same reason as the run-budget branch below: the
        # worker has decided this cannot execute now, so it must not stay
        # queued. Without this a source whose own daily budget is spent
        # accumulated a pending backlog that no tick could ever clear.
        request.settle(
            RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
        )
        summary.blocked_by_reason[BlockedReason.QUOTA_EXHAUSTED] = (
            summary.blocked_by_reason.get(BlockedReason.QUOTA_EXHAUSTED, 0) + 1
        )
        summary.requests_drained += 1
        return rate_limit_hit

    if source == DEFAULT_SOURCE_SLUG and rate_limit_hit:
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        request.settle(
            RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
        )
        summary.blocked_by_reason[BlockedReason.QUOTA_EXHAUSTED] = (
            summary.blocked_by_reason.get(BlockedReason.QUOTA_EXHAUSTED, 0) + 1
        )
        summary.requests_drained += 1
        return rate_limit_hit

    if summary.remaining_budget <= 0:
        # Settled as `blocked`, not skipped-and-left-queued.
        #
        # Leaving it pending is the pre-P6a defect wearing a different hat: the
        # worker has decided this request cannot execute right now, and queued
        # means "the worker intends to execute this", which is now false. On the
        # live 191-title watchlist this path skipped 490 requests and left them
        # all queued, so the run could not finalise — the invariant raised,
        # the worker caught it and recorded `failed`, and the run ended terminal
        # with 426 pending requests. Exactly the state P6a exists to prevent.
        #
        # Note this is independent of `is_policy_metered`. That predicate answers
        # "should this source take part in the metered ladder"; a budget answers
        # "can this request execute now". A free collector has a budget too, and
        # when it is spent the request is legitimately `quota_exhausted` — it
        # becomes runnable at the next reset.
        request.settle(
            RefreshRequestStatus.BLOCKED,
            blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
        )
        summary.skipped_budget_exhausted += 1
        summary.blocked_by_reason[BlockedReason.QUOTA_EXHAUSTED] = (
            summary.blocked_by_reason.get(BlockedReason.QUOTA_EXHAUSTED, 0) + 1
        )
        summary.requests_drained += 1
        return rate_limit_hit

    try:
        scope_limiter.acquire()
        # The user's selected market travels with the request. Sources that
        # answer per country (JustWatch) cannot be called without it, and ones
        # that answer globally (Streaming Availability) ignore it.
        _service_for_scope(request.source, service).refresh_content(
            movie, country=resolve_source_country()
        )
    except RateLimitExceeded:
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        return source == DEFAULT_SOURCE_SLUG or rate_limit_hit
    except UpstreamTitleNotFound as exc:
        # The upstream catalogue has no such title: a permanent answer, not a
        # fault. Fail on the first attempt — retrying can only re-earn the same
        # 404 while spending metered budget, which is exactly the waste this
        # exception exists to prevent. Marked ``failed`` (never left pending),
        # so the title reads ⚪ "last check failed" instead of 🔵 forever.
        #
        # Counted as ``skipped_upstream_missing``, not ``errors``: the run did
        # not break, so a healthy run must not look broken in its own summary.
        # The failed row is also the durable evidence that stops the background
        # sweep re-fetching this title forever (see
        # ``bulk_refresh.permanently_absent_scopes``).
        summary.skipped_upstream_missing += 1
        request.attempts += 1
        request.last_error = f"{type(exc).__name__}: {exc}"
        request.status = RefreshRequestStatus.FAILED
        request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "completed_at"]
        )
        summary.requests_drained += 1
        logger.warning(
            "refresh_request_permanent_failure tmdb_id=%s source=%s error=%s",
            movie.tmdb_id, source, request.last_error,
        )
        return rate_limit_hit
    except Exception as exc:  # noqa: BLE001 - one bad title must not stop the run
        summary.errors += 1
        request.attempts += 1
        request.last_error = f"{type(exc).__name__}: {exc}"
        if request.attempts >= MAX_ATTEMPTS:
            request.status = RefreshRequestStatus.FAILED
            request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "completed_at"]
        )
        summary.requests_drained += 1
        logger.error(
            "refresh_request_failed tmdb_id=%s source=%s attempts=%s error=%s",
            movie.tmdb_id, source, request.attempts, request.last_error,
            exc_info=True,
        )
        return rate_limit_hit
    _finish(request, RefreshRequestStatus.DONE)
    summary.refreshed += 1
    summary.remaining_budget -= 1
    summary.requests_drained += 1
    return rate_limit_hit


def _candidate_batch(
    blocked: set[str],
    default_source: str,
    cursor: tuple | None = None,
) -> list[RefreshRequest]:
    """Next page of pending requests in priority order, minus blocked sources.

    **Keyset pagination.** ``cursor`` is the ``(priority, requested_at, pk)``
    tuple of the last row inspected; the next page starts strictly after it
    ("greater than" on that lexicographic triple). A positional ``OFFSET``
    would not do: the drain settles rows as it goes, so every settlement
    shifts the remaining rows and a fixed offset would silently re-read or
    skip rows. The keyset triple is stable under those removals, which is what
    lets a tick page safely while it mutates the very set it reads.

    ``pk`` is the final tiebreaker because ``priority``/``requested_at`` are
    not unique — without it, rows sharing a timestamp have no defined order and
    a page boundary could repeat or drop one.

    **The exclusion is the anti-starvation mechanism, and it lives in SQL on
    purpose.** A blocked source's requests are filtered out *before* the page
    window is applied, so however large the Streaming Availability backlog
    becomes it can never occupy the candidate page and hide an executable
    request behind it. The page size is therefore only a memory guard, not the
    thing that prevents starvation.

    ``source=""`` (legacy default-scope rows) is excluded together with
    ``DEFAULT_SOURCE_SLUG``: both resolve to the same source via
    :func:`resolve_request_source`, so they must be dropped as one unit or the
    filter would let one spelling through and defeat itself.

    Bounded by :data:`CANDIDATE_SCAN_LIMIT`; the caller pages at most
    :data:`MAX_SCAN_PAGES` times, so no tick reads the whole table.
    """
    blocked_slugs = set(blocked)
    if default_source in blocked_slugs:
        blocked_slugs.add("")  # legacy spelling of the same scope

    queryset = RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING)
    if blocked_slugs:
        queryset = queryset.exclude(source__in=blocked_slugs)
    if cursor is not None:
        priority, requested_at, pk = cursor
        queryset = queryset.filter(
            Q(priority__gt=priority)
            | Q(priority=priority, requested_at__gt=requested_at)
            | Q(priority=priority, requested_at=requested_at, pk__gt=pk)
        )
    return list(
        queryset.select_related("movie")
        .order_by("priority", "requested_at", "pk")[:CANDIDATE_SCAN_LIMIT]
    )


def drain_refresh_requests(
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
) -> set[int]:
    """Process pending refresh requests in priority order, budget permitting.

    Called by ``process_watchlist_refresh`` before its background stale
    sweep. Every request goes through the very same guards as the sweep
    (see ``_drain_one``), and priority never bypasses the rate limiter or
    the daily budget. Freshness is evaluated per request: each scope is
    checked against its own source's window, so the drain settles what is
    fresh for *that* scope and refreshes what is not.

    **Fairness (bounded, one slot):** when the run has budget to spare
    (``remaining_budget >= 2``), one unit is held back while the strict
    priority pass runs and is then spent — last, and only by — the
    *longest waiting* pending request, but only when that request is not
    already in the top priority tier present. Without this, a steady stream
    of high-priority events (detail views, watchlist adds) could starve the
    low-priority backlog forever, since the queue is re-read fresh every
    run. The reserved unit goes through the same freshness and rate-limit
    guards as everything else; if the reserved request settles without
    spending it (already fresh, refused, or failed), the unit simply stays
    with the run. With ``budget < 2`` nothing is reserved: plain priority
    wins outright.

    **No starvation by blocked sources.** A source whose own daily budget is
    spent defers only *its own* requests: they stay ``pending`` and the scan
    moves on. Candidates are read in priority order with the blocked sources
    excluded in SQL (:func:`_candidate_batch`), so an arbitrarily large
    exhausted backlog can never fill the candidate window and hide an
    executable request behind it — that exclusion is what makes the bounded
    scan safe, rather than the scan being large enough by luck. Priority is
    fully respected among *executable* requests, and a tick reads at most a
    few bounded pages, never the whole table.

    Returns every ``tmdb_id`` the request queue owns this run — settled
    (``done``/``failed``) **and** still-pending requests — so the caller's
    stale sweep neither re-attempts nor double-counts those titles (a pending
    request left for the next run is the queue's responsibility, not the
    sweep's).
    """
    excluded: set[int] = set()
    rate_limit_hit = summary.rate_limited

    # One limiter per registered source, built once per tick: resolving (and
    # for metered sources, querying) a source's budget per request would make
    # a large fan-out pay the same query hundreds of times. The caller's
    # injected limiter overrides the default source's, as before.
    limiters = {
        source: rate_limiter_for(source) for source in source_registry.slugs()
    }
    if limiter is not None:
        limiters[DEFAULT_SOURCE_SLUG] = limiter

    # Sources discovered to be out of budget during this tick. Seeded with the
    # default source when the caller handed us an already-spent limiter, so
    # the very first candidate query already excludes it.
    blocked: set[str] = set()
    if _is_spent(limiters[DEFAULT_SOURCE_SLUG]):
        blocked.add(DEFAULT_SOURCE_SLUG)

    # A blocked source only ever *defers* work, so the tick ends once the
    # budget is spent or nothing executable is left. MAX_SCAN_PAGES keeps the
    # per-tick work bounded even if a source keeps refusing mid-scan.
    cursor = None
    scanned = 0
    for _page in range(MAX_SCAN_PAGES):
        pending = _candidate_batch(blocked, DEFAULT_SOURCE_SLUG, cursor=cursor)
        if not pending:
            break
        scanned += len(pending)
        # Resume strictly after the last row of this page on the next call.
        cursor = (pending[-1].priority, pending[-1].requested_at, pending[-1].pk)

        # Fairness slot: the globally oldest request in this page, when it sits
        # below the top tier, gets one budget unit held back for the reserved
        # pass (see the fairness contract above).
        oldest = min(pending, key=lambda row: (row.requested_at, row.pk))
        reserved = None
        if summary.remaining_budget >= 2 and oldest.priority > pending[0].priority:
            reserved = oldest
            summary.remaining_budget -= 1

        strict = (
            pending if reserved is None
            else [r for r in pending if r.pk != reserved.pk]
        )

        def _drain(request):
            nonlocal rate_limit_hit
            source = resolve_request_source(request)
            rate_limit_hit = _drain_one(
                request,
                summary=summary,
                service=service,
                limiter=limiter,
                rate_limit_hit=rate_limit_hit,
                limiter_overrides=limiters,
            )
            # A source that just refused stops offering candidates on the next
            # page; the ones already in hand are drained by name below.
            if source in summary.rate_limited_sources:
                blocked.add(source)

        for request in strict:
            excluded.add(request.movie.tmdb_id)
            _drain(request)

        if reserved is not None:
            # Release the held-back unit for the reserved pass: the strict
            # pass spent from everything except this unit, which now becomes
            # spendable again — only by this one request, and only under the
            # same guards.
            summary.remaining_budget += 1
            excluded.add(reserved.movie.tmdb_id)
            _drain(reserved)

        if summary.remaining_budget <= 0:
            break
        if len(pending) < CANDIDATE_SCAN_LIMIT:
            break  # queue exhausted (or fully blocked): no further page
        # Otherwise page again from this page's last row (keyset cursor), so
        # the next page starts strictly after everything already examined.

    logger.debug(
        "refresh_queue_drain_scanned=%s blocked_sources=%s "
        "rate_limited_sources=%s remaining_budget=%s",
        scanned,
        sorted(blocked),
        summary.rate_limited_sources,
        summary.remaining_budget,
    )
    return excluded


def process_refresh_queue(
    *,
    budget: int = 10,
    service: AvailabilityRefreshService | None = None,
    limiter: RateLimiter | None = None,
) -> dict | None:
    """Drain the pending ``RefreshRequest`` queue exactly once (worker tick).

    The queue-only counterpart to ``process_watchlist_refresh``: it settles
    pending event-driven requests in priority order under the same
    ``RateLimiter``/daily quota, the same per-source freshness policy and the
    same ``RefreshRun`` audit row — but it never runs the background stale
    sweep. The refresh worker loops on this function, so a tick must not be
    able to spend the daily API budget on maintenance the scheduled
    ``refresh_availability`` run already owns.

    Returns the run summary dict, or ``None`` when the queue is empty (the
    common idle tick: no ``RefreshRun`` row, no work). A tick is never
    short-circuited by one source being out of budget: only when the caller's
    *default* limiter is already spent does it return early with
    ``rate_limited=True``, and even then a batch may be recorded so the
    blocked sources are reported. Requests belonging to other sources are
    still executable, so they are drained as usual.
    """
    pending = RefreshRequest.objects.filter(status=RefreshRequestStatus.PENDING)
    if not pending.exists():
        return None

    service = service or AvailabilityRefreshService()
    limiter = limiter or rate_limiter_for(DEFAULT_SOURCE_SLUG)

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)

    # The default limiter is the run's own injected/quota-bearing guard: when
    # it is spent up front we still drain other sources, so this no longer
    # returns early — it only seeds ``rate_limited`` for the summary.
    if _is_spent(limiter):
        summary.note_rate_limited_source(DEFAULT_SOURCE_SLUG)

    run = RefreshRun.objects.create(
        service=DEFAULT_SOURCE_SLUG,
        started_at=timezone.now(),
    )
    drain_refresh_requests(summary=summary, service=service, limiter=limiter)
    run.rows_refreshed = summary.refreshed
    run.rows_skipped = (
        summary.skipped_cached
        + summary.skipped_budget_exhausted
        + summary.skipped_rate_limited
    )
    run.rate_limited = summary.rate_limited
    run.finished_at = timezone.now()
    run.save()

    summary.requests_pending = pending.count()
    return summary.to_dict()


def has_pending_request(tmdb_id: int) -> bool:
    """True when any source scope for this title still waits in the queue."""
    return RefreshRequest.objects.filter(
        movie__tmdb_id=tmdb_id, status=RefreshRequestStatus.PENDING
    ).exists()
