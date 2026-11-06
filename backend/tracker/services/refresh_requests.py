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

from django.db import IntegrityError, connection
from django.db.models import Q
from django.utils import timezone

from availability.models import Offer
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry
from availability.sources.base import SourceQuotaExceeded

from ..models import (
    AvailabilityRefresh,
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    BlockedReason,
    RefreshPriority,
    RefreshRequest,
    RefreshRequestOutcome,
    RefreshRequestStatus,
    RefreshRun,
    WatchlistItem,
)
from .bulk_refresh import (
    BulkRefreshSummary,
    _is_spent,
    is_stale,
    title_level_source_slugs,
)
from .preferences import resolve_source_country
from .refresh_attribution import (attribute_catalogue_marker,
                                  attribute_refresh_run)
from .rate_limit import (
    RateLimitExceeded,
    RateLimiter,
    SourceQuotaCircuit,
    rate_limiter_for,
    source_budget,
)
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


def recently_attempted(movie, source: str = "", country: str = "") -> bool:
    """True when this scope already had a completed attempt within the window.

    Guards the enqueue side against churn: a drain that found nothing leaves
    the title stale, and this read-side check is what stops the next detail
    view (or frontend poll) from immediately opening another attempt for the
    very same observation window.

    The scope is **resolved** before matching, so the legacy ``""`` scope and
    its explicit ``DEFAULT_SOURCE_SLUG`` count as one — exactly as
    :func:`is_stale` and :func:`resolve_request_source` already treat them.
    Without that, a drain of a legacy ``""`` row would be invisible to the
    explicit-slug fan-out, which would immediately reopen the very scope the
    cooldown exists to leave alone.
    """
    scope = source or DEFAULT_SOURCE_SLUG
    scopes = {scope, ""} if scope == DEFAULT_SOURCE_SLUG else {scope}
    return RefreshRequest.objects.filter(
        movie=movie,
        source__in=scopes,
        country=(country or "").strip().upper(),
        status__in=[RefreshRequestStatus.DONE, RefreshRequestStatus.FAILED],
        completed_at__gte=timezone.now() - RECENT_ATTEMPT_WINDOW,
    ).exists()


def request_availability_refresh(
    movie,
    priority: int = RefreshPriority.STALE,
    *,
    source: str = "",
    country: str | None = None,
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
    # A missing request-specific country inherits the selected user market;
    # an explicit empty value remains unknown evidence rather than guessing.
    country = (
        resolve_source_country() if country is None else str(country).strip().upper()
    ) or ""
    try:
        if if_stale and not is_stale(movie, source=source, country=country or None):
            return None
        if if_stale and recently_attempted(movie, source, country):
            # Already fetched (or failed) moments ago for this scope — the
            # title may legitimately still be stale because the provider has
            # nothing for it; do not open a new attempt on every read.
            return None

        effective_priority = priority
        if not has_canonical_offers(movie):
            effective_priority = min(priority, RefreshPriority.NEW)

        # Empty source is a legacy alias for the default source. Let the
        # partial unique constraint arbitrate concurrent adoption safely.
        if source == DEFAULT_SOURCE_SLUG:
            legacy_qs = RefreshRequest.objects.filter(
                movie=movie,
                source="",
                country__in=[country, ""],
                status=RefreshRequestStatus.PENDING,
            )
            if legacy_qs.exists():
                try:
                    adopted = legacy_qs.update(source=DEFAULT_SOURCE_SLUG, country=country)
                except IntegrityError:
                    legacy_qs.delete()
                    adopted = 0
                if adopted:
                    RefreshRequest.objects.filter(
                        movie=movie,
                        source=DEFAULT_SOURCE_SLUG,
                        country=country,
                        status=RefreshRequestStatus.PENDING,
                        priority__gt=effective_priority,
                    ).update(priority=effective_priority)
                    if batch is not None:
                        RefreshRequest.objects.filter(
                            movie=movie,
                            source=DEFAULT_SOURCE_SLUG,
                            country=country,
                            status=RefreshRequestStatus.PENDING,
                        ).update(batch=batch)
                    return RefreshRequest.objects.get(
                        movie=movie,
                        source=DEFAULT_SOURCE_SLUG,
                        country=country,
                        status=RefreshRequestStatus.PENDING,
                    )

        try:
            request, created = RefreshRequest.objects.get_or_create(
                movie=movie,
                source=source,
                country=country,
                status=RefreshRequestStatus.PENDING,
                defaults={"priority": effective_priority, "batch": batch},
            )
        except IntegrityError:
            # Concurrent event won the partial unique constraint; the row we
            # want already exists — fall through to the upgrade check below.
            request, created = RefreshRequest.objects.get(
                movie=movie, source=source, country=country, status=RefreshRequestStatus.PENDING
            ), False

        if not created:
            # A conditional database update cannot overwrite a concurrent
            # priority upgrade with the stale value this caller read.
            RefreshRequest.objects.filter(
                pk=request.pk,
                status=RefreshRequestStatus.PENDING,
                priority__gt=effective_priority,
            ).update(priority=effective_priority)
            if batch is not None:
                # Last bulk click owns progress without touching priority.
                RefreshRequest.objects.filter(
                    pk=request.pk, status=RefreshRequestStatus.PENDING
                ).update(batch=batch)
            request.refresh_from_db()
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


def request_title_refresh(
    movie,
    priority: int = RefreshPriority.STALE,
    *,
    if_stale: bool = True,
    sources: Optional[list[str]] = None,
    batch: uuid.UUID | None = None,
    country: str | None = None,
) -> dict:
    """Fan a **single** title out to every eligible title-level source.

    The single-title counterpart of :func:`request_watchlist_refresh`, and the
    answer to "why is an opened title still unknown?": one default-scope check
    cannot report a market only an aggregator like JustWatch covers, so opening
    a title must ask every source that can answer a per-title question — not
    just Streaming Availability.

    **Eligibility comes from the capability model**, never a hard-coded list:
    ``sources`` defaults to :func:`title_level_source_slugs` (every source
    whose ``capabilities.supports_title_fetch`` is true). Catalogue-only
    collectors such as Prime Video are therefore structurally excluded, so
    this can never trigger a catalogue crawl — every request it writes is a
    title-scoped :func:`request_availability_refresh`.

    Everything else stays the per-title contract: idempotent dedup per
    ``(movie, source scope)``, priority upgrade, the never-fetched bump, the
    staleness/cooldown guards (``if_stale``), the metered-source policy
    (:func:`~tracker.services.source_policy.decide_sources_for_title`, applied
    *before* a row exists), and swallow-on-failure. Country scope and rate
    limits are applied later, at drain time, exactly as for the bulk path.

    Returns ``{"sources": <allowed slugs>, "queued": <rows touched>,
    "skipped": {source: reason}}``.
    """
    country = (
        resolve_source_country() if country is None else str(country).strip().upper()
    ) or ""
    base_sources = (
        list(sources) if sources is not None else title_level_source_slugs()
    )

    from .availability_report import title_source_report
    from .source_policy import decide_sources_for_title

    report_entry = title_source_report([movie.pk], country=country).get(movie.pk, {})
    ladder_switch = priority != RefreshPriority.EXPLICIT
    decision = decide_sources_for_title(
        report_entry, applicable_slugs=base_sources, ladder_switch=ladder_switch
    )
    allowed = list(decision["allowed_sources"])

    queued = 0
    for source in allowed:
        row = request_availability_refresh(
            movie,
            priority=priority,
            if_stale=if_stale,
            source=source,
            batch=batch,
            country=country,
        )
        if row is not None:
            queued += 1
    return {
        "sources": allowed,
        "queued": queued,
        "skipped": decision["skipped_sources"],
    }


def request_watchlist_refresh(
    user_id: int = 1,
    *,
    priority: int = RefreshPriority.EXPLICIT,
    if_stale: bool = False,
    sources: Optional[list[str]] = None,
    country: str | None = None,
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
    country = (
        resolve_source_country() if country is None else str(country).strip().upper()
    ) or ""
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

    report = title_source_report(movie_ids, country=country) if movie_ids else {}

    # `applicable` is the union across titles (which sources this run touches
    # at all); `skipped_by_source` records per-source skip counts so the
    # response can say "not needed for 163 titles" rather than a bare total.
    applicable: list[str] = []
    skipped_counts: dict[str, int] = {}
    skipped_by_source: dict[str, int] = {}

    for _movie_id, movie in Movie.objects.in_bulk(movie_ids).items():
        decision = decide_sources_for_title(
            report.get(movie.pk, {}), applicable_slugs=base_sources,
            ladder_switch=priority != RefreshPriority.EXPLICIT,
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
                country=country,
            )

    # `queued` counts what this batch actually owns. Counting the union of
    # sources across titles is wrong once titles legitimately differ — with
    # gating on, a resolved title contributes no metered row at all, so a
    # cross-title source count would overstate the run's real size.
    queued = RefreshRequest.objects.filter(
        movie_id__in=movie_ids,
        source__in=applicable,
        country=country,
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


def request_catalogue_refresh(
    sources, *, availability_refresh=None
) -> CatalogueRefreshRequest:
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

    ``availability_refresh`` records who asked. Because the marker coalesces,
    that is a set rather than one owner: a second refresh requesting the same
    source while the marker is still pending *adds* a link and the marker then
    legitimately represents both. Omitting it (an operator command, scheduled
    staleness) leaves the marker unattributed, which is the honest outcome.
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
    if availability_refresh is not None:
        attribute_catalogue_marker(marker, [availability_refresh])
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


def _finish(
    request: RefreshRequest,
    status: RefreshRequestStatus,
    *,
    outcome: str = RefreshRequestOutcome.UNKNOWN,
) -> None:
    request.status = status
    request.completed_at = timezone.now()
    request.last_error = ""
    request.outcome = outcome
    request.save(update_fields=["status", "completed_at", "last_error", "outcome"])


def _successful_outcome(request: RefreshRequest, source: str) -> str:
    qs = Offer.objects.filter(
        tmdb_id=request.movie.tmdb_id,
        source__slug=source,
        currently_available=True,
    )
    if request.country:
        qs = qs.filter(country__code=request.country)
    return (
        RefreshRequestOutcome.OFFERS_FOUND
        if qs.exists()
        else RefreshRequestOutcome.NO_OFFER
    )


def _metered_source_is_unnecessary(
    movie, source: str, priority, explicitly_named_source: bool = True, country: str | None = None
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

    entry = title_source_report([movie.pk], country=country).get(movie.pk)
    if not entry:
        # No classification available: never block a request on missing data.
        return False
    return source_policy.decide_sources_for_title(entry).get(
        "skipped_sources", {}
    ).get(source) == source_policy.PolicySkipReason.NOT_NEEDED.value


def _has_reusable_permanent_negative(request: RefreshRequest) -> bool:
    """Use a 404 only in its observed market; unknown is never global."""
    source = resolve_request_source(request)
    rows = RefreshRequest.objects.filter(movie=request.movie).filter(
        Q(
            status=RefreshRequestStatus.DONE,
            outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
        )
        | Q(
            status=RefreshRequestStatus.FAILED,
            last_error__startswith="UpstreamTitleNotFound",
        )
    ).only("source", "country")
    rows = [row for row in rows if resolve_request_source(row) == source]
    # Country-bound title lookups have country-bound negatives. A blank country
    # is unknown evidence, not a universal negative; only an exact explicit
    # country match may suppress another queued request.
    return bool(request.country) and any(row.country == request.country for row in rows)


def _drain_one(
    request: RefreshRequest,
    *,
    summary,
    service: AvailabilityRefreshService,
    limiter,
    rate_limit_hit: bool,
    limiter_overrides: dict[str, RateLimiter] | None = None,
    quota_circuit: SourceQuotaCircuit | None = None,
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
    - local daily/monthly budget exhausted -> stays ``pending`` for the next run.
      A local rate-limited request has **not** failed: it is waiting for its
      source's quota to reset, and it is the only reason that source appears
      in ``summary.rate_limited_sources``;
    - upstream terminal plan quota -> ``blocked``/``quota_exhausted`` with the
      captured diagnostic, and the tick-scoped circuit suppresses further
      requests to that source this tick. A retryable upstream rate window stays
      ``pending`` and does not trip the terminal circuit.

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
        movie, source, request.priority, explicitly_named_source=bool(request.source), country=str(request.country) or None
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
        and not is_stale(
            movie, source=request.source, country=request.country or None
        )
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
        and _has_reusable_permanent_negative(request)
    ):
        request.attempts += 1
        # The source already supplied a definitive negative answer.  This is
        # not a new failed transport attempt; preserving it as a successful
        # cached observation keeps error metrics and retry policy truthful.
        request.last_error = ""
        request.status = RefreshRequestStatus.DONE
        request.outcome = RefreshRequestOutcome.TITLE_NOT_FOUND
        request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "outcome", "completed_at"]
        )
        summary.skipped_upstream_missing += 1
        summary.requests_drained += 1
        return rate_limit_hit

    # A terminal upstream plan quota already observed this tick. The source
    # cannot succeed again before its plan resets, so settle this request as
    # the same blocked/quota_exhausted fact the first refusal recorded, without
    # issuing another request. The captured diagnostic is copied so the row is
    # still auditable; no credential ever reaches it. Other sources are not
    # affected, and a fresh tick gets one new probe (the circuit is in-memory
    # and tick-scoped, never a permanent disable).
    if quota_circuit is not None and quota_circuit.is_tripped(source):
        diagnostic = quota_circuit.diagnostic(source)
        if diagnostic:
            request.settle(
                RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
                error=diagnostic,
            )
        else:
            request.settle(
                RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
            )
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        summary.blocked_by_reason[BlockedReason.QUOTA_EXHAUSTED] = (
            summary.blocked_by_reason.get(BlockedReason.QUOTA_EXHAUSTED, 0) + 1
        )
        summary.requests_drained += 1
        return rate_limit_hit

    # This source's own budget. A blocked source defers only its own
    # requests; the caller keeps scanning for sources that can still run.
    if _is_spent(scope_limiter):
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        # A *source's* own daily quota is genuinely spent, so this request is
        # settled as `blocked`/`quota_exhausted`: terminal, and not retried
        # aggressively until the reset. Deliberately different from the
        # run-budget branch below, which *defers* (leaves PENDING) — a run
        # budget limits work performed, not future eligibility. Without
        # settling here, a source whose own daily budget is spent accumulated a
        # pending backlog that no tick could ever clear.
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
        # The *run's* execution budget is spent — not the source's quota. This
        # request is therefore left PENDING, never settled: it is perfectly
        # runnable and becomes the very next tick's work. The two conditions
        # are deliberately different facts with different consequences —
        # `quota_exhausted` is terminal and must not be retried aggressively,
        # whereas a run-budget deferral is retried on the next tick — so a run
        # budget must never be recorded as a source-quota block.
        #
        # Conflating them was a real defect. A worker with `--budget 10` and a
        # 194-title watchlist settled ~181 requests per source as
        # `blocked/quota_exhausted` inside a single tick, even though four of
        # the five sources declare no quota at all and the fifth still had
        # 93/100 calls remaining. 22 titles consequently read as
        # `not_applicable` unknowns purely because the worker never reached
        # them — an absence of *information* misreported as a scheduling fact.
        #
        # Only the counter moves here. `requests_drained` deliberately does
        # not: that counts settled rows, and a deferred row is not settled.
        summary.skipped_budget_exhausted += 1
        return rate_limit_hit

    try:
        with source_budget(scope_limiter, source=source):
            # The user's selected market travels with the request. Sources that
            # answer per country (JustWatch) cannot be called without it, and
            # ones that answer globally (Streaming Availability) ignore it.
            _service_for_scope(request.source, service).refresh_content(
                movie, country=request.country or resolve_source_country()
            )
    except RateLimitExceeded:
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        return source == DEFAULT_SOURCE_SLUG or rate_limit_hit
    except SourceQuotaExceeded as exc:
        # An *upstream* quota refusal, classified by the source itself. This is
        # deliberately not the same branch as ``RateLimitExceeded`` above
        # (which is our own local daily guard) and not the generic failure
        # branch below (which retries a transient fault):
        #
        # - a spent plan is terminal until the plan resets. Settle it as
        #   ``blocked``/``quota_exhausted`` so it cannot masquerade as a
        #   failed source after MAX_ATTEMPTS.
        # - a short rate window is retryable. Leave the row ``pending`` and do
        #   not consume an attempt: a burst of 429s is not a broken title.
        #
        # The diagnostic carries the body, status, Retry-After and quota
        # headers, so a monthly plan exhaustion can be told apart from a
        # one-minute rate limit after the fact.
        summary.note_rate_limited_source(source)
        summary.skipped_rate_limited += 1
        diagnostic = exc.diagnostic()
        if exc.terminal:
            if quota_circuit is not None:
                quota_circuit.trip(source, diagnostic)
            request.settle(
                RefreshRequestStatus.BLOCKED,
                blocked_reason=BlockedReason.QUOTA_EXHAUSTED,
                error=diagnostic,
            )
            summary.blocked_by_reason[BlockedReason.QUOTA_EXHAUSTED] = (
                summary.blocked_by_reason.get(BlockedReason.QUOTA_EXHAUSTED, 0) + 1
            )
            summary.requests_drained += 1
            logger.warning(
                "refresh_request_quota_blocked tmdb_id=%s source=%s kind=%s "
                "retry_after=%s quota=%s",
                movie.tmdb_id, source, exc.kind, exc.retry_after, exc.quota,
            )
        else:
            request.last_error = diagnostic
            request.save(update_fields=["last_error"])
            logger.warning(
                "refresh_request_rate_limited tmdb_id=%s source=%s kind=%s "
                "retry_after=%s quota=%s",
                movie.tmdb_id, source, exc.kind, exc.retry_after, exc.quota,
            )
        return rate_limit_hit
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
        request.last_error = ""
        request.status = RefreshRequestStatus.DONE
        request.outcome = RefreshRequestOutcome.TITLE_NOT_FOUND
        request.completed_at = timezone.now()
        request.save(
            update_fields=["attempts", "last_error", "status", "outcome", "completed_at"]
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
    _finish(
        request,
        RefreshRequestStatus.DONE,
        outcome=_successful_outcome(request, source),
    )
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
    represented_batches=None,
    quota_circuit: SourceQuotaCircuit | None = None,
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
    quota_circuit = quota_circuit or SourceQuotaCircuit()
    rate_limit_hit = summary.rate_limited
    # Batches actually *processed* by this tick. Attribution must reflect the
    # requests this execution really drained - not every batch that happened to
    # be pending - so the set is recorded as each request is settled rather than
    # read from "the active refresh" (several may be queued at once).
    #
    # ``represented_batches`` is an optional out-parameter (a set the caller
    # supplies) rather than a changed return type: existing callers unpack the
    # ``excluded`` result positionally, and diagnostic plumbing should not
    # silently re-shape every one of them.
    drained_batches: set = represented_batches if represented_batches is not None else set()

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
    blocked: set[str] = set(quota_circuit.tripped_sources())
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
            # Recorded before the call: a request that fails or defers is still
            # work this execution handled, and its refresh asked for it.
            if request.batch is not None:
                drained_batches.add(request.batch)
            source = resolve_request_source(request)
            rate_limit_hit = _drain_one(
                request,
                summary=summary,
                service=service,
                limiter=limiter,
                rate_limit_hit=rate_limit_hit,
                limiter_overrides=limiters,
                quota_circuit=quota_circuit,
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


#: Advisory-lock key serialising the title-queue drain. Fixed constant (not
#: hashed from a slug) because it guards the queue as a whole, not one source.
_TITLE_DRAIN_LOCK_KEY = 0x7A9E_D41C


def try_title_drain_lock() -> bool:
    """Take the advisory lock that serialises title-queue drains.

    ``RefreshRequest`` is deliberately a four-state model (``PENDING`` plus three
    terminal outcomes) with no ``RUNNING``, so a row cannot be "claimed" by
    writing a status: that would need a migration and would change what every
    consumer of request semantics reads. The whole drain is therefore serialised
    with a Postgres session advisory lock — the same mechanism already used to
    keep one catalogue walk per source
    (:func:`tracker.services.bulk_refresh.try_source_lock`).

    Two title workers never interleave: one takes the lock and drains, the other
    skips the tick cleanly and picks up whatever is still pending next tick.
    Strictly more conservative than per-row claiming, and needs no schema change.

    Degrades to "always acquired" on backends without advisory locks, so local
    sqlite testing is unaffected — exactly like the catalogue lock.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s)", [_TITLE_DRAIN_LOCK_KEY]
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def release_title_drain_lock() -> None:
    """Release the drain lock. Safe to call even if it was never taken."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_unlock(%s)", [_TITLE_DRAIN_LOCK_KEY]
        )


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
    # One terminal-quota circuit per worker tick. It suppresses only the source
    # that proved plan exhaustion, and only for this drain.
    quota_circuit = SourceQuotaCircuit()

    # The default limiter is the run's own injected/quota-bearing guard: when
    # it is spent up front we still drain other sources, so this no longer
    # returns early — it only seeds ``rate_limited`` for the summary.
    if _is_spent(limiter):
        summary.note_rate_limited_source(DEFAULT_SOURCE_SLUG)

    # Serialise the drain itself. Two title workers must never interleave: the
    # queue is read-then-settle with no per-row claim, so without this both would
    # see the same candidates and call the same sources for the same titles.
    if not try_title_drain_lock():
        # Another title worker owns the queue this tick. Report it as
        # rate-limited rather than inventing a new status: the work is neither
        # failed nor done, it simply has an owner, and the caller's existing
        # rate-limit branch already treats that as "back off and retry".
        summary.note_rate_limited_source(DEFAULT_SOURCE_SLUG)
        summary.skipped_rate_limited += 1
        summary.rate_limited = True
        summary.requests_pending = pending.count()
        return summary.to_dict()

    # Batches this tick actually drains. One tick may span several batches
    # (queued refreshes accumulate), and the resulting RefreshRun legitimately
    # belongs to all of them - so attribution is a set, never a single parent.
    drained_batches: set = set()
    try:
        run = RefreshRun.objects.create(
            service=DEFAULT_SOURCE_SLUG,
            started_at=timezone.now(),
        )
        drain_refresh_requests(
            summary=summary,
            service=service,
            limiter=limiter,
            represented_batches=drained_batches,
            quota_circuit=quota_circuit,
        )
    finally:
        release_title_drain_lock()
    # Diagnostic only: read by nothing that decides behaviour. A batch with no
    # AvailabilityRefresh row (requests queued directly, or by an older
    # version) simply produces no link.
    if drained_batches:
        attribute_refresh_run(
            run,
            AvailabilityRefresh.objects.filter(batch__in=drained_batches),
        )
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
