"""Bulk availability refresh services.

``process_watchlist_refresh`` (Phase 3): selects watchlist movies needing
availability refresh, respecting daily API quota and caller-provided budget
limit. Phase B extended it with an event-driven request drain
(``tracker.services.refresh_requests``): pending ``RefreshRequest`` rows —
created by watchlist adds, imports, and detail views — are processed first,
in priority order, through the exact same budget, rate limiter, freshness
policy and audit row as the background sweep.

``process_catalogue_refresh`` (Phases 5/6): refreshes the full catalogue of
catalogue-capable sources (CBC Gem first) with no budget selection at all —
one service call per source, each internally fetching its whole configured
catalogue. It is the cron-facing entry point: capability-driven source
discovery (registry + enabled ``Source`` rows), a per-source advisory lock
against overlapping runs, declared-country initialization, per-source failure
isolation, and a summary that distinguishes succeeded / failed / locked /
disabled sources.

Write path:

    bulk_refresh
        -> AvailabilityRefreshService (refresh_content / refresh_catalogue)
        -> AvailabilitySource interface (registry: streaming_availability,
           cbc_gem)
        -> OfferRepository (canonical availability.Offer rows)

Staleness and candidate ordering are evaluated against canonical
``availability.Offer.last_seen`` **per source**: every scope is measured
against its own declared window (``availability.freshness``), never a single
global timeout, so two sources for the same title go stale independently. The
legacy ``AvailabilityCache`` no longer owns availability persistence here. The
legacy tracker ``RefreshRun`` audit row is kept for run bookkeeping, while
per-group lifecycle details live on ``availability.SyncRun`` rows created by
the refresh service.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from django.conf import settings
from django.db import connection
from django.db.models import Max
from django.utils import timezone

from availability.domain.enums import ContentType
from availability.freshness import (
    TITLE_AGGREGATORS,
    freshness_for,
    source_configuration_snapshot,
    source_is_stale,
    source_last_seen,
    stale_sources,
)
from availability.models import Country, Offer, Source
from availability.services.refresh import (
    AvailabilityRefreshService,
    RefreshResult,
)
from availability.sources import (
    DEFAULT_SOURCE_SLUG,
    RaiPlayConfigurationError,
    RaiPlayHttpBudgetExceeded,
    RaiPlaySource,
    source_registry,
)
from availability.sources.base import (
    SourceFetchError,
    SourceQuotaExceeded,
    accepts_kwarg,
)

from ..models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRequestOutcome,
    RefreshRun,
    RefreshPriority,
    TitleAvailabilityCheck,
    WatchlistItem,
)
from .preferences import resolve_source_country
from .rate_limit import (
    RateLimitExceeded,
    RateLimiter,
    SourceQuotaCircuit,
    rate_limiter_for,
    source_budget,
)
from .refresh_attribution import (attribute_refresh_run, attribute_sync_runs,
                                  refreshes_for_marker)
from .streaming_api import UpstreamTitleNotFound
from .title_availability_cache import (
    departures_for_titles,
    eligible_aggregator_sources,
    record_title_attempt,
    record_title_success,
    title_check_is_due,
    title_departures,
)

logger = logging.getLogger(__name__)


def is_stale(
    movie: Movie,
    *,
    source: str = "",
    country: Optional[str] = None,
    configuration=None,
    departures=None,
    now=None,
) -> bool:
    """True when this title source is due to check its own scope.

    JustWatch and Watchmode use the title cache and confirmed-departure
    policy. Other sources use their declared observation window.

    ``source=""`` is the default title-targeted scope
    (``DEFAULT_SOURCE_SLUG``).  When ``country`` is explicit it is a hard
    evidence boundary, and the movie's content type is always included, so a
    US movie, NL movie, and same-id series cannot make one another fresh.
    ``country=None`` deliberately retains the legacy global read behaviour.
    """
    slug = source or DEFAULT_SOURCE_SLUG
    if slug in TITLE_AGGREGATORS and country:
        moment = now or timezone.now()
        country = country.upper()
        if configuration is None:
            configuration = source_configuration_snapshot([slug]).get(slug, {})
        check = TitleAvailabilityCheck.objects.filter(
            movie=movie, tmdb_id=movie.tmdb_id, content_type=movie.content_type,
            source=slug, country=country,
        ).first()
        if departures is None:
            departures = title_departures(movie.tmdb_id, movie.content_type, country, moment)
        return title_check_is_due(
            slug, configuration,
            source_last_seen(
                movie.tmdb_id, slug, country=country, content_type=movie.content_type,
                targeted_only=True,
            ),
            check, departures, moment,
        )
    return source_is_stale(
        movie.tmdb_id,
        source or DEFAULT_SOURCE_SLUG,
        country=country,
        content_type=movie.content_type,
        configuration=configuration,
        now=now,
    )


def title_level_source_slugs() -> list[str]:
    """Registered sources that answer one title with one fetch.

    The single definition of "title-level", capability-driven and never
    slug-driven:

        registered source
        AND supports_title_fetch

    ``supports_title_fetch`` is the correct discriminator because it is the
    *only* capability that says a source accepts a per-title request. It is
    deliberately **not** ``not supports_catalogue_sync``: that older test
    conflated two independent capabilities and so dropped any source that
    implements **both** a title-targeted fetch and a catalogue walk.
    JustWatch is exactly such a source — its ``fetch(content=...)`` resolves
    one title by name/year (``_fetch_title``) while ``fetch(content=None)``
    walks whole provider catalogues — so the old rule excluded it from every
    per-title path even though it can answer per title, leaving it able to
    contribute only through the slow, incomplete catalogue walk.

    Catalogue-only collectors (Prime Video, BBC iPlayer, CBC Gem, Tubi, Pluto
    TV, RaiPlay, PBS, ABC iview) leave ``supports_title_fetch`` at its default
    ``False`` — their ``fetch(content)`` raises — so their behaviour is
    unchanged. Every registered source that *does* implement a title fetch
    joins Refresh All automatically: a newly registered title-level collector
    fans out with no further change here.

    Unknown slugs are excluded by construction (they are not registered).
    """
    return [
        slug
        for slug in source_registry.slugs()
        if source_registry.get(slug).capabilities.supports_title_fetch
    ]


def refreshable_source_slugs() -> list[str]:
    """Sources the per-title sweep may refresh.

    Kept as the sweep's name for :func:`title_level_source_slugs`: a source
    that syncs its own catalogue is refreshed by ``process_catalogue_refresh``
    (a whole-catalogue fetch, no budget), while everything else needs a
    per-title fetch here. Explicit Refresh All fans out over the very same
    set, so the button and the background sweep can never disagree about
    which sources are per-title work.
    """
    return title_level_source_slugs()


def stale_refresh_scopes(
    tmdb_id,
    *,
    country: Optional[str] = None,
    content_type: Optional[str] = None,
    source_configurations=None,
    movie_id=None,
    departures=None,
    aggregators=None,
    now=None,
) -> list[str]:
    """Sources due for the sweep, evaluated independently in this title scope."""
    refreshable = set(refreshable_source_slugs())
    configurations = (
        source_configuration_snapshot(refreshable)
        if source_configurations is None else source_configurations
    )
    stale = set(stale_sources(
        tmdb_id, country=country, content_type=content_type,
        source_configurations=configurations, now=now,
    )) & refreshable
    if not country:
        return sorted(stale if aggregators is None else stale - TITLE_AGGREGATORS)

    country = country.upper()
    moment = now or timezone.now()
    stale.difference_update(TITLE_AGGREGATORS)
    if aggregators is None:
        aggregators = eligible_aggregator_sources(refreshable, country)
    checks = {
        check.source: check for check in TitleAvailabilityCheck.objects.filter(
            movie_id=movie_id, tmdb_id=tmdb_id, content_type=content_type,
            country=country, source__in=aggregators,
        )
    } if movie_id is not None else {}
    if departures is None:
        departures = title_departures(tmdb_id, content_type, country, moment)
    for slug in aggregators:
        observed = source_last_seen(
            tmdb_id, slug, country=country, content_type=content_type, targeted_only=True,
        )
        check = checks.get(slug)
        # A sweep revisits existing evidence/checks and confirmed departures.
        # Detail and manual requests own previously unseen ordinary scopes.
        if (observed or check or departures) and title_check_is_due(
            slug, configurations.get(slug, {}), observed, check, departures, moment,
        ):
            stale.add(slug)
    return sorted(stale)


def _latest_checks(tmdb_ids) -> dict:
    """Map tmdb_id -> newest default-scope ``Offer.last_seen`` for those ids.

    Ordering follows the scope the sweep refreshes: a title another source
    refreshed an hour ago is still worth ordering by *its own* age here.
    """
    rows = (
        Offer.objects.filter(
            tmdb_id__in=list(tmdb_ids), source__slug=DEFAULT_SOURCE_SLUG
        )
        .values("tmdb_id")
        .annotate(latest=Max("last_seen"))
    )
    return {row["tmdb_id"]: row["latest"] for row in rows}


def permanently_absent_scopes(tmdb_id, country: str | None = None) -> set[str]:
    """Compatibility reader using the shared market-aware supersession rules."""
    from .availability_report import _permanent_miss_sources

    movie_ids = list(Movie.objects.filter(tmdb_id=tmdb_id).values_list("id", flat=True))
    misses = _permanent_miss_sources(movie_ids, country=country)
    return {slug for slugs in misses.values() for slug in slugs}


def _is_spent(limiter) -> bool:
    """True when *limiter* has no daily or monthly capacity left.

    ``remaining()`` is the primary daily answer: ``None`` means the source
    declares no daily quota, which then falls through to the explicit
    ``is_exhausted()`` predicate so a monthly-only budget is still honoured.
    An injected test double that answers neither real number nor a real bool
    is never treated as spent — a mock's truthiness must not decide that a
    queue is blocked.
    """
    remaining = limiter.remaining()
    if isinstance(remaining, int):
        if remaining <= 0:
            return True
        monthly_remaining = getattr(limiter, "monthly_remaining", None)
        if callable(monthly_remaining):
            monthly = monthly_remaining()
            if isinstance(monthly, int) and monthly <= 0:
                return True
        return False
    is_exhausted = getattr(limiter, "is_exhausted", None)
    if callable(is_exhausted):
        return is_exhausted() is True
    return False


def _candidate_pairs(user_id: int, content_types) -> list[tuple[int, int]]:
    """(movie_id, tmdb_id) pairs to consider, watchlist first, no duplicates."""
    items = WatchlistItem.objects.filter(
        user_id=user_id,
        movie__content_type__in=list(content_types),
        movie__is_quarantined=False,
    ).select_related("movie")

    pairs: list[tuple[int, int]] = []
    seen: set[int] = set()
    for movie_id, tmdb_id in items.values_list("movie_id", "movie__tmdb_id"):
        if movie_id not in seen:
            seen.add(movie_id)
            pairs.append((movie_id, tmdb_id))

    if pairs:
        return pairs

    # Fallback to querying catalog movies and series directly
    for movie_id, tmdb_id in Movie.objects.visible().filter(
        content_type__in=list(content_types)
    ).values_list("id", "tmdb_id"):
        if movie_id not in seen:
            seen.add(movie_id)
            pairs.append((movie_id, tmdb_id))
    return pairs


def _order_candidates(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Never-checked first, then oldest canonical check first."""
    latest = _latest_checks(tmdb_id for _movie_id, tmdb_id in pairs)
    return sorted(
        pairs,
        key=lambda pair: (
            pair[1] in latest,
            latest.get(pair[1]) or timezone.now(),
        ),
    )


@dataclass
class BulkRefreshSummary:
    attempted: int = 0
    refreshed: int = 0
    skipped_cached: int = 0
    skipped_budget_exhausted: int = 0
    skipped_rate_limited: int = 0
    # Titles the upstream catalogue does not carry at all (a permanent 404).
    # Not an error and not a cache hit: the title is simply absent upstream, so
    # it is reported on its own counter instead of inflating ``errors`` and
    # making a healthy run look broken.
    skipped_upstream_missing: int = 0
    # Requests a metered source would have been asked, but a cheaper source had
    # already resolved the title by the time the row came up for execution.
    #
    # Not a cache hit and not an error: the work was unnecessary, not skipped
    # for speed, and no upstream call was made. It gets its own counter
    # because "how much scarce budget did the source hierarchy save?" is the
    # question the gate exists to answer, and it cannot be read off any other
    # field — a cached hit still counted a request this run would otherwise
    # have repeated.
    skipped_policy_gated: int = 0
    #: ``{blocked_reason: count}`` for this drain. Kept separate from
    #: ``skipped_policy_gated`` because that is a single counter for one
    #: reason, and the seven-day view will need to tell "declined by policy"
    #: apart from "out of quota" — two very different facts that a single
    #: "skipped" number hides. Not persisted on the run; recomputed from the
    #: request rows when it is needed.
    blocked_by_reason: dict = field(default_factory=dict)
    errors: int = 0
    budget: int = 0
    remaining_budget: int = 0
    # "Quota/rate limiting stopped the run" is no longer a single boolean's
    # job once each source carries its own budget: it stays True for the
    # legacy single-source callers, and the source-specific truth lives in
    # ``rate_limited_sources``.
    rate_limited: bool = False
    # Sources whose own daily/monthly budget is spent or whose terminal
    # upstream plan quota was observed, so their requests are waiting or
    # blocked (not failing). Empty means nothing is quota-blocked, which is the
    # normal case for every unmetered source.
    rate_limited_sources: list = field(default_factory=list)
    # Event-driven refresh requests (Phase B): how many pending
    # RefreshRequest rows were settled (done or failed) by this run, and how
    # many are still queued for a later run.
    requests_drained: int = 0
    requests_pending: int = 0

    def note_rate_limited_source(self, source: str) -> None:
        """Record that *source* is budget-blocked (deduplicated, sorted)."""
        if source and source not in self.rate_limited_sources:
            self.rate_limited_sources.append(source)
            self.rate_limited_sources.sort()
        self.rate_limited = True

    def to_dict(self) -> dict:
        return asdict(self)


def process_watchlist_refresh(
    budget: int = 90,
    user_id: int = 1,
    refresh_service: Optional[AvailabilityRefreshService] = None,
    limiter: Optional[RateLimiter] = None,
    content_types: tuple[str, ...] | list[str] = ("movie", "series"),
) -> dict:
    """Run the watchlist drain and stale sweep under the shared drain lock.

    The lock covers both phases: otherwise a queue worker could begin draining
    a newly queued request for a movie while this invocation is sweeping that
    same movie. On contention this invocation defers without creating a run,
    settling queue rows, or starting the sweep.
    """
    from .refresh_requests import title_drain_lock, _drain_lock_busy_summary

    with title_drain_lock() as acquired:
        if not acquired:
            return _drain_lock_busy_summary(budget)
        return _process_watchlist_refresh_locked(
            budget=budget,
            user_id=user_id,
            refresh_service=refresh_service,
            limiter=limiter,
            content_types=content_types,
        )


def _process_watchlist_refresh_locked(
    budget: int = 90,
    user_id: int = 1,
    refresh_service: Optional[AvailabilityRefreshService] = None,
    limiter: Optional[RateLimiter] = None,
    content_types: tuple[str, ...] | list[str] = ("movie", "series"),
) -> dict:
    """Batch refresh watchlist movie availability up to budget limit.

    Candidates selection & priority:
    1. Pending event-driven refresh requests (``tracker.RefreshRequest``),
       drained first in their deterministic priority order — the user-intent
       ladder (``RefreshPriority``: explicit action, never-fetched, recent
       view, watchlist, watched, stale, background) outranks background
       maintenance, with one bounded fairness slot so the oldest waiting
       request cannot be starved (see ``drain_refresh_requests``). Draining
       shares this run's budget, rate limiter and ``RefreshRun`` audit row,
       so event triggers can never bypass the daily API quota.
    2. Watchlist movies for user_id with no canonical availability yet.
    3. Watchlist movies with the oldest default-scope ``Offer.last_seen``.

    One unit of work = one ``AvailabilityRefreshService.refresh_content``
    call (one external API request + one transactional canonical write).

    Staleness is per source (``stale_refresh_scopes``): each source's
    observation of a title is evaluated against that source's own freshness
    window, only refreshable scopes are refreshed, and a fresh source for the
    same title never triggers — never suppresses — another scope's refresh.

    Summary fields:
    - attempted: number of sweep candidates considered
    - refreshed: number of titles successfully refreshed (requests + sweep)
    - skipped_cached: number skipped because existing cache was fresh
    - skipped_budget_exhausted: number deferred because the *run's execution
      budget* was spent. This is a scheduling deferral, not a source-quota
      fact: the work is left PENDING and is retried on the next tick. It is
      deliberately kept out of ``blocked_by_reason`` so a run budget can never
      be misreported as a source running dry.
    - skipped_rate_limited: number skipped because rate limit/quota prevented further refreshes
    - skipped_upstream_missing: number skipped because the upstream catalogue has no
      record of the title at all (a permanent 404) — not an error, and never retried
    - errors: number of unexpected per-title errors
    - budget: input budget for the run
    - remaining_budget: unused live-refresh budget after the run
    - rate_limited: boolean indicating whether quota/rate limiting stopped the run
    - rate_limited_sources: sources whose own daily/monthly budget is spent or
      whose terminal upstream plan quota was observed (their titles are
      *waiting*, not failed); empty when nothing is blocked
    - blocked_by_reason: settled ``BLOCKED`` rows by reason — the only home for
      *genuine* source-quota exhaustion (``BlockedReason.QUOTA_EXHAUSTED``),
      which is terminal and not retried aggressively
    - requests_drained: pending RefreshRequest rows settled (done/failed/blocked) this run
    - requests_pending: RefreshRequest rows still queued after the run — this
      includes run-budget deferrals, which are queued work, not settled work

    The five request outcomes the user-facing summary must not conflate:
    queued (``requests_pending``), attempted (``attempted``), completed
    (``refreshed``), genuinely quota-blocked
    (``blocked_by_reason[QUOTA_EXHAUSTED]``), and deferred by the worker's run
    budget (``skipped_budget_exhausted``).

    Quota is per source, so an exhausted budget on one source never stops the
    others: a stale Prime scope still refreshes while Streaming Availability
    is capped out. ``rate_limited`` therefore means "at least one source is
    blocked", never "the run stopped" — read ``rate_limited_sources`` for
    which.
    """
    service = refresh_service or AvailabilityRefreshService()
    limiter = limiter or rate_limiter_for(DEFAULT_SOURCE_SLUG)

    summary = BulkRefreshSummary(budget=budget, remaining_budget=budget)

    # A tick-scoped terminal-quota circuit. It is created here, shared by the
    # request drain and the stale sweep, and discarded when the tick returns.
    # Nothing is persisted: a future quota reset must never be masked by a
    # stale in-memory or database flag.
    quota_circuit = SourceQuotaCircuit()
    # One read-only snapshot for the queue drain and sweep. Configuration is
    # resolved once per tick, then passed through per-title decisions.
    source_configurations = source_configuration_snapshot()

    # Check upfront if the daily or monthly budget is already exhausted.
    if _is_spent(limiter):
        summary.note_rate_limited_source(DEFAULT_SOURCE_SLUG)

    run = RefreshRun.objects.create(
        service=DEFAULT_SOURCE_SLUG,
        started_at=timezone.now(),
    )

    # Phase B: drain event-driven refresh requests first (local intent
    # outranks the background sweep). Local import: refresh_requests imports
    # this module's freshness helpers, so the dependency stays one-way at
    # module load time. The returned set owns those titles for this run —
    # settled or still queued — so the sweep below neither re-attempts nor
    # double-counts them.
    from .refresh_requests import (
        _drain_refresh_requests_locked,
        _metered_source_is_unnecessary,
    )

    current_country = resolve_source_country()
    queue_owned = _drain_refresh_requests_locked(
        summary=summary,
        service=service,
        limiter=limiter,
        quota_circuit=quota_circuit,
        source_configurations=source_configurations,
        sweep_country=current_country or "",
    )

    pairs = _order_candidates(_candidate_pairs(user_id, content_types))
    summary.attempted = len(pairs)
    movies_by_id = {
        m.id: m for m in Movie.objects.visible().filter(id__in=[movie_id for movie_id, _ in pairs])
    }

    # Read after draining: an explicit answer in this tick can supersede an
    # older permanent miss. Scope the snapshot to this sweep's market and key
    # by Movie identity, using exactly the report/worker's history policy.
    from .availability_report import _permanent_miss_sources

    aggregators = eligible_aggregator_sources(refreshable_source_slugs(), current_country)
    moment = timezone.now()
    departures = departures_for_titles(
        {(movie.tmdb_id, movie.content_type) for movie in movies_by_id.values()}, current_country, moment,
    ) if aggregators else {}
    departure_movie_ids = {
        movie.pk for movie in movies_by_id.values()
        if (movie.tmdb_id, movie.content_type) in departures
    }
    pairs.sort(key=lambda pair: pair[0] not in departure_movie_ids)
    permanent_misses = _permanent_miss_sources(list(movies_by_id), country=current_country)

    for movie_id, tmdb_id in pairs:
        movie = movies_by_id.get(movie_id)
        if not movie or (tmdb_id, movie.content_type) in queue_owned:
            # The request drain owns this typed identity for this run.
            continue

        if summary.remaining_budget <= 0:
            summary.skipped_budget_exhausted += 1
            continue

        # Per-source staleness: each source's own window decides, so a fresh
        # source neither triggers nor suppresses another scope's refresh.
        #
        # Scopes the upstream is known not to carry (a permanent 404) are
        # dropped here: they write no Offer row, so they would otherwise read
        # as stale forever and cost one metered call per sweep run, every run.
        absent = permanent_misses.get(movie_id, set())
        stale_scopes = stale_refresh_scopes(
            tmdb_id,
            country=current_country,
            content_type=movie.content_type,
            movie_id=movie.pk,
            source_configurations=source_configurations,
            departures=departures.get((tmdb_id, movie.content_type), {}),
            aggregators=aggregators,
            now=moment,
        )
        if not stale_scopes:
            summary.skipped_cached += 1
            continue
        # Reused negatives are their own outcome, never a second cache skip.
        summary.skipped_upstream_missing += sum(slug in absent for slug in stale_scopes)
        scopes = [slug for slug in stale_scopes if slug not in absent]

        # The stale scopes we may refresh, with budget and rate capacity.
        #
        # Quota is per source, so one exhausted scope only defers *its own*
        # titles: the loop continues with the remaining scopes of this title
        # and the next titles, instead of the run-wide bail-out the single
        # shared limiter used to force here.
        # Give confirmed departures priority over ordinary scopes on this title.
        if (tmdb_id, movie.content_type) in departures:
            scopes.sort(key=lambda slug: slug not in aggregators)
        for slug in scopes:
            # The sweep has no queue row, but still spends the same metered
            # budget. Re-check the same policy immediately before execution.
            if _metered_source_is_unnecessary(
                movie, slug, RefreshPriority.BACKGROUND, country=current_country
            ):
                summary.skipped_policy_gated += 1
                continue
            if summary.remaining_budget <= 0:
                summary.skipped_budget_exhausted += 1
                continue
            if quota_circuit.is_tripped(slug):
                # A terminal upstream plan quota was already proven this tick.
                # Do not issue another request to this source; other scopes
                # for this title and every other source remain eligible.
                summary.note_rate_limited_source(slug)
                summary.skipped_rate_limited += 1
                continue
            scope_limiter = (
                limiter if slug == DEFAULT_SOURCE_SLUG
                else rate_limiter_for(slug)
            )
            if _is_spent(scope_limiter):
                summary.note_rate_limited_source(slug)
                summary.skipped_rate_limited += 1
                continue
            scope_service = (
                service
                if slug == DEFAULT_SOURCE_SLUG
                else AvailabilityRefreshService(source=source_registry.get(slug))
            )
            try:
                with source_budget(scope_limiter, source=slug):
                    # Acquisition owns the local quota ledger. The run limit
                    # separately bounds attempts, including upstream failures.
                    summary.remaining_budget -= 1
                    requested_at = timezone.now()
                    check = record_title_attempt(movie, slug, current_country)
                    # Same as the request-drain path: the selected market is
                    # explicit, never inferred.
                    scope_service.refresh_content(
                        movie, country=current_country
                    )
                record_title_success(check)
                summary.refreshed += 1
            except RateLimitExceeded:
                summary.note_rate_limited_source(slug)
                summary.skipped_rate_limited += 1
            except SourceQuotaExceeded as exc:
                # An upstream refusal, not our local limiter. Plan exhaustion
                # is terminal for the rest of this tick and costs no offer
                # write; a short rate window stays retryable and never trips
                # the terminal circuit.
                summary.note_rate_limited_source(slug)
                summary.skipped_rate_limited += 1
                if exc.terminal:
                    quota_circuit.trip(slug, exc.diagnostic())
                    logger.warning(
                        "refresh_sweep_quota_blocked tmdb_id=%s source=%s kind=%s "
                        "retry_after=%s quota=%s",
                        movie.tmdb_id, slug, exc.kind, exc.retry_after, exc.quota,
                    )
                else:
                    logger.warning(
                        "refresh_sweep_rate_limited tmdb_id=%s source=%s kind=%s "
                        "retry_after=%s quota=%s",
                        movie.tmdb_id, slug, exc.kind, exc.retry_after, exc.quota,
                    )
            except UpstreamTitleNotFound as exc:
                # The same scoped evidence as a queued definitive miss. The
                # shared history reader owns reuse and supersession policy;
                # transient failures never enter this evidence path.
                RefreshRequest.objects.create(
                    movie=movie,
                    source=slug,
                    country=current_country,
                    priority=RefreshPriority.BACKGROUND,
                    status=RefreshRequestStatus.DONE,
                    outcome=RefreshRequestOutcome.TITLE_NOT_FOUND,
                    attempts=1,
                    requested_at=requested_at,
                    completed_at=timezone.now(),
                )
                summary.skipped_upstream_missing += 1
                logger.info(
                    "Upstream has no record of tmdb_id=%s source=%s: %s",
                    movie.tmdb_id, slug, exc,
                )
            except Exception as exc:
                logger.exception(
                    "Unexpected error refreshing movie tmdb_id=%s source=%s: %s",
                    movie.tmdb_id, slug, exc,
                )
                summary.errors += 1

    run.rows_refreshed = summary.refreshed
    run.rows_skipped = (
        summary.skipped_cached
        + summary.skipped_budget_exhausted
        + summary.skipped_rate_limited
        + summary.skipped_policy_gated
        + summary.skipped_upstream_missing
    )
    run.rate_limited = summary.rate_limited
    run.finished_at = timezone.now()
    run.save()

    summary.requests_pending = RefreshRequest.objects.filter(
        status=RefreshRequestStatus.PENDING
    ).count()

    return summary.to_dict()


@dataclass
class CatalogueSourceSummary:
    """Outcome of one source inside a scheduled catalogue refresh.

    ``status`` is one of:

    - ``succeeded``: fetch + mapping + persistence completed (an empty
      catalogue is still a success);
    - ``failed``: fetch/mapping/persistence raised — recorded on the source's
      ``RefreshRun.error``, never disguised as a successful empty run;
    - ``locked``: another refresh for the same source holds the lock, so this
      run skipped cleanly without touching the database;
    - ``disabled``: the database ``Source`` row is disabled, so the source
      was never scheduled (the row is never deleted).
    """

    source: str
    status: str = "succeeded"
    discovered: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    unresolved: int = 0
    error: str = ""
    started_at: str = ""
    duration_seconds: float = 0.0
    reason: str = ""
    error_reason: str = ""
    requests_attempted: Optional[int] = None
    requests_limit: Optional[int] = None
    blocked_url: Optional[str] = None

    @property
    def attempted(self) -> bool:
        """True when a refresh was actually invoked for this source."""
        return self.status in ("succeeded", "failed")


@dataclass
class CatalogueRefreshSummary:
    attempted: int = 0
    refreshed: int = 0
    errors: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    discovered: int = 0
    unresolved: int = 0
    locked: int = 0
    disabled: int = 0
    duration_seconds: float = 0.0
    sources: list = field(default_factory=list)
    results: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _catalogue_lock_key(slug: str) -> int:
    """Deterministic signed 64-bit advisory-lock key for a source slug."""
    digest = hashlib.sha256(slug.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def try_source_lock(slug: str) -> bool:
    """Try to take the per-source refresh lock; True when this run owns it.

    Uses a Postgres session advisory lock keyed by the source slug, so two
    overlapping cron runs of the *same* source cannot refresh concurrently —
    the second one skips cleanly — while two *different* sources each take
    their own key and never block one another. The lock is released in a
    ``finally`` (and automatically if the connection dies), so a crashed run
    cannot leave the source locked. Backends without advisory locks (local
    sqlite) simply proceed; the production/dev stack is Postgres.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_catalogue_lock_key(slug)])
        row = cursor.fetchone()
    return bool(row and row[0])


def release_source_lock(slug: str) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_catalogue_lock_key(slug)])


#: Backwards-compatible private aliases. The public names are canonical; these
#: exist so older callers and tests keep working unchanged.
_try_source_lock = try_source_lock
_release_source_lock = release_source_lock


def _ensure_declared_countries(slug: str, capabilities) -> None:
    """Initialize the source's declared country scope before it is written to.

    The refresh service never creates countries itself: an offer whose
    ``Country`` row is missing used to be skipped silently (the Phase 5
    discovery that left CBC writes empty). A scheduled run therefore makes
    sure every capability-declared country exists first — derived from
    ``SourceCapabilities``, never from a hardcoded country list, and never
    for sources whose country scope is unrestricted (nothing to initialize).

    Rows that already exist are untouched: a *disabled* country keeps its
    documented semantics (offers skipped, refresh succeeds) and is only
    logged so the situation is visible in cron output.
    """
    for code in sorted(capabilities.supported_countries or ()):
        normalized = str(code).upper()
        country, created = Country.objects.get_or_create(
            code=normalized, defaults={"name": normalized}
        )
        if created:
            logger.warning(
                "catalogue_refresh_country_initialized source=%s country=%s",
                slug, normalized,
            )
        elif not country.enabled:
            logger.warning(
                "catalogue_refresh_country_disabled source=%s country=%s "
                "offers for it will be skipped",
                slug, normalized,
            )


def _catalogue_source_slugs(
    source_slug: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    """Discover scheduled sources: (slugs to refresh, disabled slugs).

    Discovery is capability-driven end to end — the registry owns the code
    implementations, the database ``Source`` row owns configuration/state,
    and the scheduler never learns any provider's name:

        registered sources -> supports_catalogue_sync -> enabled in the DB

    An explicit ``source_slug`` is a manual override (``--source``): it still
    validates against the registry/capabilities — an unknown slug raises
    ``UnknownSourceError`` and a non-catalogue slug raises ``ValueError``,
    both before anything is recorded — but it bypasses the enabled filter so
    an operator can refresh a deliberately disabled source on purpose.
    """
    if source_slug is not None:
        source = source_registry.get(source_slug)
        if not source.capabilities.supports_catalogue_sync:
            raise ValueError(
                f"Source {source_slug!r} does not support catalogue refresh"
            )
        if Source.objects.filter(slug=source_slug, enabled=False).exists():
            logger.warning(
                "catalogue_refresh_source_disabled source=%s reason=manual_override",
                source_slug,
            )
        return [source_slug], []

    candidates = [
        slug for slug in source_registry.slugs()
        if source_registry.get(slug).capabilities.supports_catalogue_sync
    ]
    disabled = set(
        Source.objects.filter(enabled=False).values_list("slug", flat=True)
    )
    return (
        [slug for slug in candidates if slug not in disabled],
        [slug for slug in candidates if slug in disabled],
    )


def last_successful_completions(slugs, now=None) -> dict:
    """When each catalogue source last *completed* a walk, keyed by slug.

    This is the canonical answer to "when did this source last finish?", and it
    is derived from the run audit rather than from a cursor field on purpose.

    A checkpoint row cannot hold completion for every source: several walks
    delete their ``SyncCursor`` row on completion (the ordinary non-cycling
    case — the row's absence *is* the "nothing left to resume" signal), so a
    timestamp on that row would be destroyed by the very event it records.
    ``RefreshRun`` outlives it, so completion is read from there.

    ``SyncCursor.last_successful_sync`` therefore keeps a narrower meaning: the
    last completed traversal *of a walk whose checkpoint row still exists*
    (cycling sources such as Tubi, and multi-partition walks). Use this function
    when the question is about the source as a whole.

    Filters, all of which matter:
      * ``error=""``            — a failed run is not a completion;
      * ``finished_at__isnull=False`` — an in-flight run has not completed;
      * ``abandoned_at__isnull=True`` — a reconciled run records a dead worker,
        not a finished walk, so it must never count as a successful one.

    One aggregate query for any number of slugs.
    """
    slugs = list(slugs)
    if not slugs:
        return {}
    return {
        row["service"]: row["last"]
        for row in RefreshRun.objects.filter(
            service__in=slugs,
            error="",
            finished_at__isnull=False,
            abandoned_at__isnull=True,
        )
        .values("service")
        .annotate(last=Max("finished_at"))
    }


def last_successful_completion(slug, now=None):
    """The single-source form of :func:`last_successful_completions`."""
    return last_successful_completions([slug], now=now).get(slug)


def stale_catalogue_sources(now=None) -> list[str]:
    """Catalogue-capable slugs whose last successful sync left its window.

    Evidence is the legacy ``RefreshRun(service=slug)`` audit row
    ``process_catalogue_refresh`` writes for every attempted source (``error``
    set when the attempt failed): the newest error-free ``finished_at`` per
    slug — a slug with no such row has never synced and is stale by
    definition. Each slug is measured against its own catalogue freshness
    window (``availability.freshness``), and
    disabled sources are excluded exactly like the bare catalogue run does.

    One aggregate query regardless of how many catalogue sources exist —
    the watchlist's bulk refresh calls it to decide whether to press the
    catalogue marker.
    """
    slugs, _disabled = _catalogue_source_slugs()
    last_success = last_successful_completions(slugs)
    source_configurations = source_configuration_snapshot(slugs)
    moment = now or timezone.now()
    return sorted(
        slug
        for slug in slugs
        if last_success.get(slug) is None
        or moment - last_success[slug] > freshness_for(
            slug, source_configurations.get(slug, {})
        )
    )


def _catalogue_priority_hints(source):
    """Tracked titles the source may use to prioritise discovery candidates.

    Only sources that declare ``supports_priority_hints`` receive hints, so
    every other collector keeps its exact previous call shape. The hints are
    plain values (title, year, content type); the source never reads the
    database and still resolves identity through its normal mapping path.
    """
    if not getattr(source.capabilities, "supports_priority_hints", False):
        return None
    return list(Movie.objects.values_list("title", "year", "content_type"))


def _default_catalogue_runner(
    slug, source, refresh_service, availability_refreshes=()
):
    """The generic catalogue fetch: one scope-less call, budget-free.

    Correct for the first-party collectors: each owns exactly one
    provider/country and reports its whole configured scope per fetch.

    Returns a :class:`~availability.services.refresh.RefreshResult` so the
    caller has one uniform contract regardless of which runner ran.
    """
    service = refresh_service or AvailabilityRefreshService(source=source)
    hints = _catalogue_priority_hints(source)
    if hints is not None and accepts_kwarg(service.refresh_catalogue, "priority"):
        return service.refresh_catalogue(priority=hints)
    return service.refresh_catalogue()


def _justwatch_catalogue_runner(
    slug, source, refresh_service, availability_refreshes=()
):
    """JustWatch's catalogue work goes through its P8 partition coordinator.

    JustWatch is an aggregator over many providers *and* many countries, so the
    generic single unscoped fetch cannot express it — and it refuses one, because
    country is an explicit application decision (never inferred from IP, VPN or
    locale). Driving it directly from here raised
    ``SourceFetchError: justwatch requires an explicit country code`` on every
    single attempt, i.e. 11/11 historical ``RefreshRun`` rows.

    So the worker delegates to :func:`refresh_justwatch_partitions`, the one
    place that owns JustWatch partition identity, cursors and locking:

        resolve_source_country()
            -> (country, provider, content type, year window) partitions
            -> bounded JustWatch catalogue walks
            -> cursor persistence
            -> ProviderContent / ContentMapping / Offer

    This adds no second JustWatch implementation: it is the same coordinator the
    ``refresh_justwatch_catalogue`` operator command drives, so the worker and
    the command can never disagree about what a JustWatch refresh is. The
    country is read from ``Preference.current_country`` through the single
    existing resolver.
    """
    # Imported lazily: ``justwatch_sync`` imports this module's lock helpers at
    # import time, so a module-level import here would be circular.
    from .justwatch_sync import (
        DEFAULT_MAX_PARTITIONS,
        STATUS_FAILED,
        available_partitions,
        refresh_justwatch_partitions,
    )

    country = resolve_source_country()
    if not country:
        # Loud, not a silent unscoped fetch: an aggregator asked about the
        # wrong market returns confidently wrong offers.
        raise SourceFetchError(
            "justwatch requires an explicit country code; none is selected in "
            "preferences. It is never inferred from IP, VPN or browser locale."
        )
    country = country.upper()

    # The user's selected market is the *only* market walked.
    #
    # Filtering the shared plan (rather than planning anything new here) is the
    # mechanism ``refresh_justwatch_catalogue --country XX`` already uses, so the
    # worker and the operator command cannot disagree about which partitions
    # exist. Order matters: filter FIRST, cap SECOND — capping first would
    # truncate to an alphabetically-earlier country and silently walk nothing.
    plan = [
        partition for partition in available_partitions(max_partitions=None)
        if partition[0] == country
    ][:DEFAULT_MAX_PARTITIONS]

    if not plan:
        # No enabled country x mapped provider pair for this market. Loud, because
        # an empty plan would otherwise be indistinguishable from "JustWatch
        # currently offers nothing here", which is a different and wrong claim.
        raise SourceFetchError(
            f"justwatch has no available partition for the selected country "
            f"{country!r}: it needs an enabled country x an enabled provider "
            f"that JustWatch exposes in that market."
        )

    logger.info(
        "justwatch_catalogue_runner country=%s partitions=%d providers=%s",
        country, len(plan), ",".join(sorted({p[1] for p in plan})),
    )

    # Explicit plan, executed verbatim by the coordinator.
    summary = refresh_justwatch_partitions(
        partitions=plan,
        max_partitions=None,
        content_type=ContentType.MOVIE,
        availability_refreshes=availability_refreshes,
    )
    if summary.get("locked"):
        return RefreshResult(
            discovered=0, processed=0, created=0, updated=0, skipped=0,
            unresolved=0,
        )
    results = summary.get("results") or []
    failed = [r for r in results if r.get("status") == STATUS_FAILED]
    if failed:
        return _raise_justwatch_failure(failed)
    return RefreshResult(
        discovered=sum(r.get("discovered", 0) for r in results),
        processed=sum(r.get("discovered", 0) for r in results),
        created=sum(r.get("created", 0) for r in results),
        updated=sum(r.get("updated", 0) for r in results),
        skipped=0,
        unresolved=0,
    )


def _raise_justwatch_failure(failed):
    """Surface a partial/failed JustWatch run as a real failure.

    The coordinator's PARTIAL status means some partitions failed; collapsing
    that into "succeeded" would make the ``RefreshRun`` audit row claim a
    complete observation the run never made.
    """
    errors = "; ".join(
        str(r.get("error")) for r in failed if r.get("error")
    ) or "one or more JustWatch partitions failed"
    raise SourceFetchError(f"JustWatch catalogue run failed: {errors}")


#: Per-source catalogue runner. A source absent from this map uses the generic
#: single-fetch path, so adding an aggregator that needs real planning registers
#: one entry here instead of teaching the generic worker about it.
CATALOGUE_RUNNERS = {
    "justwatch": _justwatch_catalogue_runner,
}


def _refresh_one_source(
    slug: str,
    source,
    refresh_service: Optional[AvailabilityRefreshService] = None,
    availability_refreshes=(),
    max_contents: Optional[int] = None,
    max_http_requests: Optional[int] = None,
) -> CatalogueSourceSummary:
    """Refresh one source's catalogue, isolated from every other source.

    Failure isolation: whatever this source raises (fetch, normalization,
    mapping, persistence) is recorded on its own ``RefreshRun`` and returned
    as ``failed`` — the caller keeps going with the remaining sources, and
    no offer is ever deactivated because a refresh failed.

    Which runner executes is a per-source choice
    (:data:`CATALOGUE_RUNNERS`), because "one catalogue = one fetch" is only
    true of the first-party collectors.
    """
    started_at = timezone.now()
    result = CatalogueSourceSummary(source=slug, started_at=started_at.isoformat())
    t0 = time.monotonic()

    if not _try_source_lock(slug):
        result.status = "locked"
        logger.warning(
            "catalogue_refresh_skipped source=%s reason=already_running "
            "started_at=%s",
            slug, result.started_at,
        )
        return result

    run = RefreshRun.objects.create(service=slug, started_at=started_at)
    # Diagnostic only. ``availability_refreshes`` is the set the claimed marker
    # represents (it coalesces, so there is no single parent), and is empty for
    # standalone work - an operator command or a scheduled sync.
    if availability_refreshes:
        attribute_refresh_run(run, availability_refreshes)
    try:
        if slug == "rai_play":
            # A fixture-only run injects a fake transport (a ``requester``) and
            # issues no live request, so it needs no budget. EVERY live path is
            # operational and must carry a finite HTTP budget: the registry
            # source, the worker marker, the provider refresh endpoint, and a
            # direct ``refresh_service=`` operator invocation alike. The
            # discriminator is the transport, never "does this look like a
            # test" — there is deliberately no refresh_service / mock-class
            # clause, so a live operator invocation can never slip past the
            # gate by simply passing its own service.
            requester = getattr(getattr(source, "client", None), "requester", None)
            is_fixture_run = requester is not None
            configured_requests = max_http_requests or getattr(settings, "RAIPLAY_MAX_HTTP_REQUESTS", None)
            configured_contents = max_contents or getattr(settings, "RAIPLAY_MAX_CONTENTS", None)
            # Explicit CLI/settings config wins; otherwise fall back to the
            # source's own constructor args, so a direct caller that bakes a
            # finite budget is honoured rather than refused as unconfigured.
            effective_requests = (
                configured_requests
                if configured_requests is not None
                else getattr(source, "max_http_requests", None)
            )
            effective_contents = (
                configured_contents
                if configured_contents is not None
                else getattr(source, "max_contents", None)
            )
            if not is_fixture_run and (
                effective_requests is None
                or effective_contents is None
                or int(effective_requests) < 1
                or int(effective_contents) < 1
            ):
                raise RaiPlayConfigurationError(
                    f"RaiPlay catalogue refresh requires finite configuration: "
                    f"RAIPLAY_MAX_HTTP_REQUESTS={effective_requests!r}, "
                    f"RAIPLAY_MAX_CONTENTS={effective_contents!r}"
                )
            if configured_requests is not None or configured_contents is not None:
                source = RaiPlaySource(
                    client=getattr(source, "client", None),
                    max_contents=int(effective_contents) if effective_contents is not None else None,
                    max_http_requests=int(effective_requests) if effective_requests is not None else None,
                )
        _ensure_declared_countries(slug, source.capabilities)
        logger.info(
            "catalogue_refresh_started source=%s providers=%s countries=%s "
            "started_at=%s",
            slug,
            ",".join(sorted(source.capabilities.supported_providers or ())) or "any",
            ",".join(sorted(source.capabilities.supported_countries or ())) or "any",
            result.started_at,
        )
        runner = CATALOGUE_RUNNERS.get(slug, _default_catalogue_runner)
        refresh_result = runner(
            slug, source, refresh_service, availability_refreshes
        )
        if availability_refreshes:
            # The first-party collectors' SyncRuns are produced inside the
            # service call above, so they are linked here from the result the
            # call already returned - no second execution, no re-derivation of
            # scope. JustWatch does the same per partition inside its
            # coordinator. Diagnostic only: nothing reads these links.
            attribute_sync_runs(
                getattr(refresh_result, "sync_runs", ()) or (),
                availability_refreshes,
            )
    except RaiPlayConfigurationError as exc:
        # No finite budget configured. This is an operator setup error, not a
        # source fault and not a budget that ran out mid-walk, so it gets its
        # own log event (a warning, not a traceback) and a stable structured
        # reason — the same terminal representation every failed catalogue
        # source already uses (status "failed", RefreshRun.error set,
        # finished_at stamped), mirroring the RaiPlayHttpBudgetExceeded handler.
        duration = round(time.monotonic() - t0, 3)
        logger.warning(
            "catalogue_refresh_not_configured source=%s "
            "max_http_requests=%r max_contents=%r duration_s=%s "
            "requests_attempted=0 requests_limit=%r",
            slug, max_http_requests, max_contents, duration, locals().get("effective_requests"),
        )
        result.status = "failed"
        result.reason = "not_configured"
        result.error = str(exc)
        result.duration_seconds = duration
        result.requests_attempted = 0
        result.requests_limit = locals().get("effective_requests")
        run.error = result.error
        run.finished_at = timezone.now()
        run.save()
        return result
    except RaiPlayHttpBudgetExceeded as exc:
        duration = round(time.monotonic() - t0, 3)
        logger.warning(
            "catalogue_refresh_budget_exhausted source=%s limit=%s attempted=%s "
            "blocked_url=%s stage=%s duration_s=%s",
            slug, exc.limit, exc.attempted, exc.blocked_url, exc.stage, duration,
        )
        result.status = "failed"
        result.reason = "budget_exhausted"
        result.error_reason = "budget_exhausted"
        result.requests_attempted = exc.attempted
        result.requests_limit = exc.limit
        result.blocked_url = exc.blocked_url
        result.error = (
            f"RaiPlayHttpBudgetExceeded: HTTP budget exhausted for {slug}: "
            f"limit={exc.limit}, attempted={exc.attempted}, stage={exc.stage!r}, "
            f"blocked_url={exc.blocked_url!r}"
        )
        result.duration_seconds = duration
        run.error = result.error
        run.finished_at = timezone.now()
        run.save()
        return result
    except Exception as exc:
        duration = round(time.monotonic() - t0, 3)
        logger.exception(
            "catalogue_refresh_failed source=%s duration_s=%s error=%s",
            slug, duration, exc,
        )
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        result.duration_seconds = duration
        if slug == "rai_play":
            budget = getattr(getattr(source, "client", None), "budget", None)
            if budget is not None:
                result.requests_attempted = getattr(budget, "attempted", None)
                result.requests_limit = getattr(budget, "limit", None)
        run.error = result.error
        run.finished_at = timezone.now()
        run.save()
        return result
    else:
        duration = round(time.monotonic() - t0, 3)
        result.status = "succeeded"
        result.discovered = refresh_result.discovered
        result.processed = refresh_result.processed
        result.created = refresh_result.created
        result.updated = refresh_result.updated
        result.skipped = refresh_result.skipped
        result.unresolved = refresh_result.unresolved
        result.duration_seconds = duration
        run.rows_refreshed = refresh_result.processed
        run.rows_skipped = refresh_result.skipped
        run.finished_at = timezone.now()
        run.save()
        # Surface the RaiPlay HTTP budget consumption that actually occurred,
        # from the same budget instance RaiPlayClient._http mutated. Non-RaiPlay
        # sources leave these as None (backward compatible).
        if slug == "rai_play":
            budget = getattr(getattr(source, "client", None), "budget", None)
            if budget is not None:
                result.requests_attempted = getattr(budget, "attempted", None)
                result.requests_limit = getattr(budget, "limit", None)
            else:
                result.requests_attempted = 0
                result.requests_limit = None
        logger.info(
            "catalogue_refresh_completed source=%s discovered=%s mapped=%s "
            "unresolved=%s offers=%s created=%s updated=%s skipped=%s "
            "duration_s=%s finished_at=%s requests_attempted=%r requests_limit=%r",
            slug, refresh_result.discovered,
            refresh_result.discovered - refresh_result.unresolved,
            refresh_result.unresolved,
            refresh_result.processed,
            refresh_result.created, refresh_result.updated,
            refresh_result.skipped,
            duration, run.finished_at.isoformat(),
            result.requests_attempted, result.requests_limit,
        )
        return result
    finally:
        # Always release, on success, failure, or an unexpected record-time
        # error, so a crashed run can never wedge the source's schedule.
        try:
            _release_source_lock(slug)
        except Exception:
            logger.exception("catalogue_refresh_unlock_failed source=%s", slug)


def process_catalogue_refresh(
    source_slug: Optional[str] = None,
    refresh_service: Optional[AvailabilityRefreshService] = None,
    availability_refreshes=(),
    max_contents: Optional[int] = None,
    max_http_requests: Optional[int] = None,
) -> dict:
    """Refresh whole catalogues for catalogue-capable sources.

    One unit of work = one ``AvailabilityRefreshService.refresh_catalogue``
    call: one source fetch (a source may internally walk many upstream pages)
    and one transactional canonical write. Unlike ``process_watchlist_refresh``
    there is no staleness selection and no API budget — a catalogue source
    reports its full configured scope in a single fetch (CBC Gem: one pass
    over its category indices), so budget accounting would be meaningless.

    Scope selection:
    - ``refresh_service`` (tests / callers with an injected source) runs alone;
    - ``source_slug`` restricts the run to one registered source — an unknown
      slug raises ``UnknownSourceError`` and a non-catalogue source raises
      ``ValueError`` *before* any run is recorded, because both are usage
      errors rather than per-source refresh failures;
    - default: every registered source whose capabilities claim
      ``supports_catalogue_sync`` **and** whose database ``Source`` row is not
      disabled (a missing row means "not configured yet" and counts as
      enabled, because ``Source.enabled`` defaults to True).

    Scheduling contract (Phase 6): each source is attempted under its own
    advisory lock (overlapping runs of the same source skip cleanly), its
    declared country scope is initialized first, and one failure never stops
    the remaining sources. Each attempted source gets a legacy
    ``RefreshRun(service=slug)`` audit row whose ``error`` field is set when
    the attempt failed; per-group lifecycle details stay on
    ``availability.SyncRun`` rows created by the refresh service (a failed
    fetch creates no SyncRun at all — nothing was written).
    """
    started_at = time.monotonic()
    if refresh_service is not None:
        slugs = [refresh_service.source.slug]
        sources = {refresh_service.source.slug: refresh_service.source}
        disabled: list[str] = []
    else:
        slugs, disabled = _catalogue_source_slugs(source_slug)
        sources = {slug: source_registry.get(slug) for slug in slugs}

    results: list[CatalogueSourceSummary] = []
    for slug in disabled:
        logger.warning("catalogue_refresh_skipped source=%s reason=disabled", slug)
        results.append(CatalogueSourceSummary(source=slug, status="disabled"))
    for slug in slugs:
        results.append(
            _refresh_one_source(
                slug,
                sources[slug],
                refresh_service,
                availability_refreshes,
                max_contents=max_contents,
                max_http_requests=max_http_requests,
            )
        )

    summary = CatalogueRefreshSummary(
        attempted=sum(1 for item in results if item.attempted),
        refreshed=sum(1 for item in results if item.status == "succeeded"),
        errors=sum(1 for item in results if item.status == "failed"),
        locked=sum(1 for item in results if item.status == "locked"),
        disabled=sum(1 for item in results if item.status == "disabled"),
        created=sum(item.created for item in results),
        updated=sum(item.updated for item in results),
        skipped=sum(item.skipped for item in results),
        discovered=sum(item.discovered for item in results),
        unresolved=sum(item.unresolved for item in results),
        duration_seconds=round(time.monotonic() - started_at, 3),
        sources=[item.source for item in results if item.status == "succeeded"],
        results=results,
    )
    logger.info(
        "catalogue_refresh_summary attempted=%s refreshed=%s errors=%s "
        "locked=%s disabled=%s discovered=%s offers=%s unresolved=%s "
        "duration_s=%s",
        summary.attempted, summary.refreshed, summary.errors,
        summary.locked, summary.disabled, summary.discovered,
        summary.created + summary.updated, summary.unresolved,
        summary.duration_seconds,
    )
    return summary.to_dict()


def process_pending_catalogue_refresh() -> Optional[dict]:
    """Run the queued catalogue-sync marker, if any (refresh worker tick).

    The counterpart of ``request_catalogue_refresh``: one pending marker =
    one pass over exactly the sources that read stale when the watchlist's
    "Refresh all" pressed it, each under its own advisory lock — so an
    overlapping scheduled cron simply skips that source as ``locked``, which
    is success by definition (someone else is syncing it right now). Only
    genuine per-source errors (or usage errors escaping
    ``process_catalogue_refresh``) fail the marker; it stays consumed either
    way — a later button press opens a fresh job.

    Returns the marker's JSON-able summary, or ``None`` when no marker is
    pending (the common idle tick). Catalogue syncs spend no API budget, so
    this runs regardless of the daily rate limit.
    """
    marker = (
        CatalogueRefreshRequest.objects.filter(
            status=CatalogueRefreshStatus.PENDING
        )
        .order_by("requested_at")
        .first()
    )
    if marker is None:
        return None

    # Read the marker's requesters at claim time. They were recorded when the
    # work was enqueued, so this is exact rather than inferred - and because the
    # marker coalesces, the set can legitimately hold several refreshes.
    marker_refreshes = refreshes_for_marker(marker)
    sources = list(marker.sources)
    failed: list[str] = []
    for slug in sources:
        try:
            if marker_refreshes:
                result = process_catalogue_refresh(
                    source_slug=slug,
                    availability_refreshes=marker_refreshes,
                )
            else:
                # Standalone work (operator command, scheduled staleness) keeps
                # the original call exactly: no parent, no extra keyword.
                result = process_catalogue_refresh(source_slug=slug)
            if result.get("errors"):
                failed.append(slug)
        except Exception:  # noqa: BLE001 - one broken source must not stop the rest
            logger.exception("catalogue_marker_source_failed source=%s", slug)
            failed.append(slug)

    marker.completed_at = timezone.now()
    marker.status = (
        CatalogueRefreshStatus.FAILED if failed else CatalogueRefreshStatus.DONE
    )
    marker.last_error = ", ".join(failed)
    marker.save(update_fields=["status", "completed_at", "last_error"])
    logger.info(
        "catalogue_marker_processed sources=%s failed=%s status=%s",
        ",".join(sources) or "none",
        ",".join(failed) or "none",
        marker.status,
    )
    return {
        "sources": sources,
        "failed_sources": failed,
        "status": marker.status,
    }
