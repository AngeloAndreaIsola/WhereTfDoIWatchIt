"""Source-first availability reporting: which source produced what, and why not.

The read side of the refresh architecture. Everything here is derived
**read-only** from state the pipeline already persists (``RefreshRequest``,
``Offer``, ``Source``/``SyncRun``, ``ApiCallCounter``) — it performs no
network I/O and writes nothing, so it can be called from a GET, from a
management command, and from a test alike.

Two things live here, and they are deliberately the *same* computation so a
run summary and a per-title breakdown can never disagree:

1. :func:`title_source_report` — for one title, the verdict for every
   applicable source: resolved, or unresolved *with a reason*.
2. :func:`summarise_run` / :func:`unknown_breakdown` — for a set of titles,
   the source-first totals ("collectors 312/312, JustWatch 843/1120,
   Streaming Availability 0/764 — quota exhausted") and the
   ``{reason: count}`` map that answers "why are these 28 films unknown?".

Why a reason vocabulary at all
------------------------------
The system used to reduce a title's state to a single ``unknown``, which
collapsed "nobody has looked yet", "the one metered source is out of
budget", "the upstream catalogue has never heard of this film" and "we
checked and there is genuinely nothing" into one indistinguishable word.
Those demand different responses — one is a bug, one is a budget decision,
one is permanent and unfixable, and one is a real answer that should
render as "not available". A title is *unknown* only when no applicable
source resolved it; it is *unavailable* when a source was asked, answered,
and offered nothing.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict

from django.db.models import Count, Q
from django.utils import timezone

from availability.models import Offer, ProviderContent, Source, SyncRun
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry

from ..models import (
    AvailabilityResolutionReason,
    AvailabilityRefresh,
    AvailabilityRefreshSource,
    Movie,
    RefreshRequest,
    RefreshRequestStatus,
    RefreshRun,
)
from .rate_limit import rate_limiter_for

logger = logging.getLogger(__name__)


def _role_value(slug: str) -> str:
    """The source's declared role as a plain string ("" when undeclared)."""
    from .source_policy import role_of

    role = role_of(slug)
    return role.value if role is not None else ""

#: Reasons that mean "we asked and the answer was no" — i.e. the title is
#: genuinely unavailable, as opposed to unknown. A title whose only evidence
#: is in this set is NOT unknown.
ABSENCE_REASONS = frozenset(
    {
        AvailabilityResolutionReason.NO_OFFER,
        AvailabilityResolutionReason.TITLE_NOT_FOUND,
    }
)

#: Reasons that mean "we never got to ask" — the source is blocked, skipped,
#: or out of budget. Any title left in this state is unknown, and stays
#: unknown no matter how many *other* sources are also silent.
INFORMATION_GAP_REASONS = frozenset(
    {
        AvailabilityResolutionReason.NOT_CHECKED,
        AvailabilityResolutionReason.QUEUED,
        AvailabilityResolutionReason.QUOTA_EXHAUSTED,
        AvailabilityResolutionReason.SOURCE_UNAVAILABLE,
        AvailabilityResolutionReason.COLLECTOR_NOT_RUN,
        AvailabilityResolutionReason.NOT_SUPPORTED,
        AvailabilityResolutionReason.NOT_APPLICABLE,
        AvailabilityResolutionReason.UNKNOWN_REASON,
    }
)

#: Stable precedence for picking *one* headline reason per title.
#:
#: A title can be blocked for several reasons at once (out of budget *and*
#: carrying a legacy 404), and the breakdown must pick one so the counts sum
#: to the number of unknown titles. The order is therefore
#: **most-actionable-first, not most-definitive-first**, which is a
#: deliberate inversion of the obvious choice:
#:
#: - a *permanent* miss ranks first: it is a fact, never changing, and it is
#:   the only reason in this list that is not worth retrying;
#: - a **transient failure outranks a quota block**, because a quota block
#:   resolves itself at midnight while a failure needs a human. Collapsing
#:   the two — which the first version of this precedence did — hides every
#:   real fault behind a number that always reads as "just wait", which is
#:   precisely how 2 genuinely broken titles came to be reported as 26
#:   innocent ones;
#: - ``never checked`` ranks last: the least informative.
REASON_PRECEDENCE: tuple[str, ...] = (
    AvailabilityResolutionReason.TITLE_NOT_FOUND,
    AvailabilityResolutionReason.SOURCE_UNAVAILABLE,
    AvailabilityResolutionReason.QUOTA_EXHAUSTED,
    AvailabilityResolutionReason.QUEUED,
    AvailabilityResolutionReason.NOT_SUPPORTED,
    AvailabilityResolutionReason.NOT_APPLICABLE,
    AvailabilityResolutionReason.COLLECTOR_NOT_RUN,
    AvailabilityResolutionReason.NOT_CHECKED,
    AvailabilityResolutionReason.NO_OFFER,
    AvailabilityResolutionReason.UNKNOWN_REASON,
)

#: The three states a title's availability can be in. Spelled out as a class
#: rather than bare strings because the classification below is the most
#: load-bearing logic in the feature, and a typo in a string literal would
#: otherwise be a silent behaviour change.
class TitleAvailabilityState:
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    ALL = (AVAILABLE, UNAVAILABLE, UNKNOWN)

#: Human-facing source names. Presentational only — the slug stays the
#: identity, so an unregistered source falls back to its slug rather than
#: disappearing from a report.
SOURCE_LABELS: dict[str, str] = {
    "streaming_availability": "Streaming Availability",
    "justwatch": "JustWatch",
    "watchmode": "Watchmode",
    "youtube": "YouTube",
    "mediaset_infinity": "Mediaset Infinity",
    "bbc_iplayer": "BBC iPlayer",
    "cbc_gem": "CBC Gem",
    "abc_iview": "ABC iview",
    "prime_video": "Prime Video",
    "pluto_tv": "Pluto TV",
    "rai_play": "RaiPlay",
    "tubi": "Tubi",
    "pbs": "PBS",
}


def source_label(slug: str) -> str:
    return SOURCE_LABELS.get(slug, slug)


def source_tier(slug: str) -> int:
    """Display tier for a source: 0 collectors, 1 broad, 2 metered.

    A *presentation* ordering derived from the declared role
    (:mod:`tracker.services.source_policy`), never from capabilities and never
    from offer volume. It exists so the report can sort rows, and it is
    deliberately not the scheduling decision — that is
    ``source_policy.allowed_sources_for_title``.
    """
    from .source_policy import AvailabilityRole, role_of

    role = role_of(slug)
    if role is AvailabilityRole.COLLECTOR:
        return 0
    if role is AvailabilityRole.BROAD_AGGREGATOR:
        return 1
    if role is AvailabilityRole.METERED_AGGREGATOR:
        return 2
    return 3


def is_metered(slug: str) -> bool:
    """Whether the source declares a daily budget (see ``rate_limit``)."""
    return rate_limiter_for(slug).daily_limit is not None


def source_quota(slug: str) -> dict | None:
    """Remaining budget for a metered source, or ``None`` when unmetered."""
    limiter = rate_limiter_for(slug)
    remaining = limiter.remaining()
    if remaining is None:
        return None
    return {
        "remaining": remaining,
        "limit": limiter.daily_limit,
        "exhausted": remaining <= 0,
    }


def _title_level_slugs() -> list[str]:
    """Registered, enabled, title-level sources, in tier order."""
    from .bulk_refresh import title_level_source_slugs

    enabled = set(
        Source.objects.filter(enabled=False).values_list("slug", flat=True)
    )
    slugs = [s for s in title_level_source_slugs() if s not in enabled]
    return sorted(slugs, key=lambda s: (source_tier(s), s))


def _catalogue_slugs() -> list[str]:
    """Registered, enabled, catalogue-capable slugs, in tier order.

    Derives the list straight from the registry rather than importing
    ``bulk_refresh``'s private ``_catalogue_source_slugs``, which also
    returns the *disabled* slugs and exists to plan an actual refresh (it
    raises for a non-catalogue slug) — neither is what a read-only report
    wants.
    """
    disabled = set(
        Source.objects.filter(enabled=False).values_list("slug", flat=True)
    )
    return sorted(
        (
            slug
            for slug in source_registry.slugs()
            if source_registry.get(slug).capabilities.supports_catalogue_sync
            and slug not in disabled
        ),
        key=lambda s: (source_tier(s), s),
    )


def _terminal_status_evidence(movie_ids: list[int], batch=None) -> dict[int, dict[str, str]]:
    """``{movie_id: {source: terminal status}}`` from the newest row per pair.

    A title can accumulate many request rows per source over its life
    (re-stamped pending rows, retries, permanent-failure markers), so the
    *newest* row per (title, source) is the current evidence. Reading the
    oldest instead is how a title that has since been checked still reports
    as failed.

    ``batch`` restricts the read to one run's stamped rows. It is not an
    optimisation: without it, a report of the *current* run is decided by
    historical rows, and the per-source totals become the sum of every check
    the title has ever had (measured: 987 Mediaset and 985 YouTube rows for
    a 191-title watchlist, against 191 titles).
    """
    newest: dict[int, dict[str, tuple[str, object]]] = defaultdict(dict)
    qs = RefreshRequest.objects.filter(movie_id__in=movie_ids)
    if batch is not None:
        qs = qs.filter(batch=batch)
    for movie_id, src, status, requested_at, pk in qs.values_list(
        "movie_id", "source", "status", "requested_at", "id"
    ).iterator():
        key = src or DEFAULT_SOURCE_SLUG
        prev = newest[movie_id].get(key)
        candidate = (status, (requested_at, pk))
        if prev is None or candidate[1] > prev[1]:
            newest[movie_id][key] = candidate
    return {
        mid: {src: value[0] for src, value in per.items()}
        for mid, per in newest.items()
    }


def _permanent_miss_sources(movie_ids: list[int]) -> dict[int, set[str]]:
    """``{movie_id: {source}}`` where a title is a *permanent* upstream 404.

    ``UpstreamTitleNotFound`` is the one terminal error that must never be
    retried — the upstream catalogue has no record of the film, so the
    honest reason is ``title_not_found`` and no amount of quota or patience
    changes it. Recognising the exception by its name (rather than any 404)
    is what keeps a transient network 404 from being written off forever.
    """
    out: dict[int, set[str]] = defaultdict(set)
    for movie_id, src, err in RefreshRequest.objects.filter(
        movie_id__in=movie_ids, status=RefreshRequestStatus.FAILED
    ).values_list("movie_id", "source", "last_error"):
        if "UpstreamTitleNotFound" not in (err or ""):
            continue
        out[movie_id].add(src or DEFAULT_SOURCE_SLUG)
    return out


def _movie_tmdb_map(movie_ids: list[int]) -> dict[int, int]:
    """``{movie_id: tmdb_id}`` — the join the canonical store requires.

    ``Offer`` keys on ``tmdb_id``, while the queue and the watchlist key on
    ``movie_id``. Every offer-side read in this module goes through here so
    that mismatch is stated exactly once.
    """
    return dict(Movie.objects.filter(id__in=movie_ids).values_list("id", "tmdb_id"))


def _active_offers_by_source(movie_ids: list[int]) -> tuple[dict[int, set[str]], set[int]]:
    """``({movie_id: {sources with an ACTIVE offer}}, {movie_id})``.

    Filtered on ``currently_available=True``, unlike
    :func:`_offers_by_source` (which reports raw presence for diagnostics).
    The distinction is load-bearing and was a real bug: an offer that has
    since been withdrawn — a film that left a service — still exists as a row,
    so an unfiltered read keeps reporting a title as ``available`` from a
    source that has demonstrably said it is not there. Worse, it made the
    headline state depend on which read was consulted: source evidence saw
    the stale row while provider evidence (already filtered) did not, so one
    title could be simultaneously "resolved by SA" and "available on nobody".

    Both sides of the classification now read this one filtered function, so
    they cannot disagree.
    """
    by_tmdb: dict[int, list[int]] = defaultdict(list)
    for movie_id, tmdb_id in _movie_tmdb_map(movie_ids).items():
        if tmdb_id:
            by_tmdb[tmdb_id].append(movie_id)

    per_movie: dict[int, set[str]] = defaultdict(set)
    active: set[int] = set()
    for tmdb_id, slug in Offer.objects.filter(
        tmdb_id__in=set(by_tmdb), currently_available=True
    ).values_list("tmdb_id", "source__slug").iterator():
        for movie_id in by_tmdb.get(tmdb_id, ()):
            per_movie[movie_id].add(slug)
            active.add(movie_id)
    return per_movie, active


def _offers_by_source(movie_ids: list[int]) -> tuple[dict[int, set[str]], set[int]]:
    """``({movie_id: {sources with ANY offer row}}, {movie_id with any offer})``.

    Unfiltered — includes withdrawn offers. Use this for diagnostics ("which
    sources have ever spoken about this title?") and
    :func:`_active_offers_by_source` for anything that decides a state.
    """
    by_tmdb: dict[int, list[int]] = defaultdict(list)
    for movie_id, tmdb_id in _movie_tmdb_map(movie_ids).items():
        if tmdb_id:
            by_tmdb[tmdb_id].append(movie_id)

    per_movie: dict[int, set[str]] = defaultdict(set)
    any_offer: set[int] = set()
    for tmdb_id, slug in Offer.objects.filter(
        tmdb_id__in=set(by_tmdb)
    ).values_list("tmdb_id", "source__slug").iterator():
        for movie_id in by_tmdb.get(tmdb_id, ()):
            per_movie[movie_id].add(slug)
            any_offer.add(movie_id)
    return per_movie, any_offer


def _offered_providers(movie_ids: list[int]) -> dict[int, set[str]]:
    """``{movie_id: {provider slugs}}`` — the "available on X" answer."""
    by_tmdb: dict[int, list[int]] = defaultdict(list)
    for movie_id, tmdb_id in _movie_tmdb_map(movie_ids).items():
        if tmdb_id:
            by_tmdb[tmdb_id].append(movie_id)

    out: dict[int, set[str]] = defaultdict(set)
    for tmdb_id, provider in Offer.objects.filter(
        tmdb_id__in=set(by_tmdb), currently_available=True
    ).values_list("tmdb_id", "provider__slug").iterator():
        for movie_id in by_tmdb.get(tmdb_id, ()):
            out[movie_id].add(provider)
    return out


def _last_checked_per_source(movie_ids: list[int]) -> dict[tuple[int, str], object]:
    """``{(movie_id, source): newest Offer.last_seen}``.

    The "checked 14:32" stamp in the per-source breakdown. Derived from
    ``Offer.last_seen`` because that is the only observation of "this source
    looked at this title" that survives a request row being re-stamped.
    """
    by_tmdb: dict[int, list[int]] = defaultdict(list)
    for movie_id, tmdb_id in _movie_tmdb_map(movie_ids).items():
        if tmdb_id:
            by_tmdb[tmdb_id].append(movie_id)

    newest: dict[tuple[int, str], object] = {}
    for tmdb_id, slug, seen in Offer.objects.filter(
        tmdb_id__in=set(by_tmdb)
    ).values_list("tmdb_id", "source__slug", "last_seen").iterator():
        for movie_id in by_tmdb.get(tmdb_id, ()):
            key = (movie_id, slug)
            prev = newest.get(key)
            if prev is None or (seen is not None and (prev is None or seen > prev)):
                newest[key] = seen
    return newest


def classify_source(
    *,
    produced_active_offer: bool,
    permanent_miss: bool,
    request_status: str | None,
    quota_exhausted: bool,
) -> tuple[str, str]:
    """One source's verdict for one title: ``(status, reason)``.

    Isolated as a pure function because it is the unit the central invariant
    is tested through, and because "what does one source's evidence mean?"
    must be answerable without a database.

    The order of the branches **is** the specification, and the order
    matters:

    1. an active offer resolves the source, whatever else is true;
    2. a permanent upstream miss is a *definitive negative answer from that
       source* — it is not an outage, and it is not the title being
       unavailable overall;
    3. a ``DONE`` row with no offer is the other definitive negative;
    4. ``FAILED`` that is **not** a permanent miss is a transient failure —
       the source tried and could not answer, so it has produced no
       information;
    5. ``PENDING`` is still outstanding — and if the source is out of budget
       the reason is specifically the quota, because that is a different
       remedy (wait, or spend) from "the worker has not got to it";
    6. a source with no request at all, and one that is blocked by budget it
       has not yet been asked through, are both "no information yet".

    Note that (2) and (3) are the only branches that produce an *absence*
    answer, and every other branch produces *absence of information*. The
    caller combines them.
    """
    if produced_active_offer:
        return "completed", AvailabilityResolutionReason.RESOLVED
    if permanent_miss:
        return "completed", AvailabilityResolutionReason.TITLE_NOT_FOUND
    if request_status == RefreshRequestStatus.DONE:
        return "completed", AvailabilityResolutionReason.NO_OFFER
    if request_status == RefreshRequestStatus.FAILED:
        return "failed", AvailabilityResolutionReason.SOURCE_UNAVAILABLE
    if request_status == RefreshRequestStatus.PENDING:
        if quota_exhausted:
            return "queued", AvailabilityResolutionReason.QUOTA_EXHAUSTED
        return "queued", AvailabilityResolutionReason.QUEUED
    if quota_exhausted:
        return "quota_exhausted", AvailabilityResolutionReason.QUOTA_EXHAUSTED
    return "not_checked", AvailabilityResolutionReason.NOT_CHECKED


def classify_title(source_rows: list[dict]) -> tuple[str, str]:
    """A title's state from its per-source verdicts: ``(state, reason)``.

    **This is the central invariant.** Stated as three rules that are
    evaluated in this order, and the order is the whole point:

        AVAILABLE
            if ANY applicable source has a currently-active offer

        UNAVAILABLE
            only if EVERY applicable source has definitively answered
            "not available"

        UNKNOWN
            if AT LEAST ONE applicable source is still pending, failed
            transiently, quota-blocked, or not yet run

    The rule that was missing before, and the one that produced the
    wrong answer on 28 real titles: **an unanswered question is not evidence
    of absence.** A source that is quota-blocked has not said "no" — it has
    not spoken. So its silence must never contribute to ``unavailable``; it
    can only ever contribute to ``unknown``.

    Worked examples, each one pinned by a test:

    ==================================  =========
    evidence                            state
    ==================================  =========
    BBC collector available,
      Streaming API quota exhausted      available
    JustWatch no offer,
      Watchmode no offer,
      Streaming API quota exhausted      unknown
    JustWatch no offer,
      Watchmode no offer,
      Streaming API no offer             unavailable
    ==================================  =========

    The ``permanent miss`` case is deliberately *not* allowed to make a title
    unavailable on its own: a single source reporting "no such title upstream"
    says nothing about the other sources, so it lands in the middle row's
    family (``unknown``) unless every source has also answered.
    """
    if not source_rows:
        return TitleAvailabilityState.UNKNOWN, AvailabilityResolutionReason.UNKNOWN_REASON

    resolved = [
        r for r in source_rows
        if r["reason"] == AvailabilityResolutionReason.RESOLVED
    ]
    if resolved:
        return TitleAvailabilityState.AVAILABLE, ""

    # No source produced an offer. Now: has every applicable source
    # *answered*? Anything that has not answered keeps the title unknown.
    unanswered = [r for r in source_rows if r["reason"] in INFORMATION_GAP_REASONS]
    answered_negative = [r for r in source_rows if r["reason"] in ABSENCE_REASONS]

    present = {r["reason"] for r in source_rows if r["reason"]}
    if unanswered:
        state = TitleAvailabilityState.UNKNOWN
    elif answered_negative:
        state = TitleAvailabilityState.UNAVAILABLE
    else:
        state = TitleAvailabilityState.UNKNOWN

    reason = ""
    if state == TitleAvailabilityState.UNKNOWN:
        for candidate in REASON_PRECEDENCE:
            if candidate in present:
                reason = candidate
                break
        if not reason:
            reason = AvailabilityResolutionReason.UNKNOWN_REASON
    elif answered_negative:
        reason = AvailabilityResolutionReason.NO_OFFER
    return state, reason


def title_source_report(movie_ids, batch=None) -> dict[int, dict]:
    """Per-title, per-source verdicts — the honest answer to "why unknown?".

    Returns ``{movie_id: {
        "state": "available" | "unavailable" | "unknown",
        "available_on": [provider_slug, ...],
        "sources": [{source, label, tier, status, reason, available, checked_at}],
        "reason": <headline reason or "">,
        "resolved_sources": [...], "unresolved_sources": [...],
    }}``

    ``batch`` scopes the *request* evidence to one run. It is essential and
    not merely an optimisation: without it the report reads every request row
    the title has ever had, so a 191-title watchlist reports ~987 checks per
    source and a title's verdict is decided by a stale row from a previous run
    rather than by the run being reported on.
    """
    movie_ids = list(movie_ids)
    if not movie_ids:
        return {}

    title_slugs = _title_level_slugs()
    evidence = _terminal_status_evidence(movie_ids, batch=batch)
    permanent = _permanent_miss_sources(movie_ids)
    offers_by_source, active_offer_movies = _active_offers_by_source(movie_ids)
    providers = _offered_providers(movie_ids)
    last_checked = _last_checked_per_source(movie_ids)
    blocked = {s: (source_quota(s) or {}) for s in title_slugs}
    exhausted = {s for s, q in blocked.items() if q.get("exhausted")}

    report: dict[int, dict] = {}
    for movie_id in movie_ids:
        per_src = evidence.get(movie_id, {})
        offer_sources = offers_by_source.get(movie_id, set())
        # A source that *produced an offer* has answered, whatever kind of
        # source it is. Catalogue-driven collectors and aggregators cannot be
        # asked a per-title question, but when one of them writes an offer
        # for this title it is a definitive positive answer — and the report
        # must both honour it and show who gave it.
        #
        # Omitting these is what made 2 watchlist titles with a live
        # JustWatch/collector offer report as `unknown` instead of
        # `available`: the classification only walked the title-level
        # sources, so an answer from outside that set was invisible.
        out_of_band = sorted(offer_sources - set(title_slugs))

        src_rows = []
        for slug in title_slugs:
            status_out, reason = classify_source(
                produced_active_offer=slug in offer_sources,
                permanent_miss=slug in permanent.get(movie_id, set()),
                request_status=per_src.get(slug),
                quota_exhausted=slug in exhausted,
            )
            src_rows.append(
                {
                    "source": slug,
                    "label": source_label(slug),
                    "tier": source_tier(slug),
                    "role": _role_value(slug),
                    "kind": "title",
                    "status": status_out,
                    "reason": reason,
                    "available": reason == AvailabilityResolutionReason.RESOLVED,
                    "checked_at": last_checked.get((movie_id, slug)),
                }
            )
        for slug in out_of_band:
            src_rows.append(
                {
                    "source": slug,
                    "label": source_label(slug),
                    "tier": source_tier(slug),
                    "role": _role_value(slug),
                    # A catalogue/aggregated source is not a per-title check
                    # even when it has answered this title; the distinction is
                    # what stops a reader assuming it can be re-queried.
                    "kind": "catalogue" if source_tier(slug) == 2 else "aggregated",
                    "status": "completed",
                    "reason": AvailabilityResolutionReason.RESOLVED,
                    "available": True,
                    "checked_at": last_checked.get((movie_id, slug)),
                }
            )

        state, reason = classify_title(src_rows)
        resolved_rows = [
            r for r in src_rows
            if r["reason"] == AvailabilityResolutionReason.RESOLVED
        ]
        gap_rows = [r for r in src_rows if r["reason"] in INFORMATION_GAP_REASONS]

        report[movie_id] = {
            "state": state,
            "available_on": sorted(providers.get(movie_id, set())),
            "sources": src_rows,
            "reason": reason,
            "resolved_sources": [r["source"] for r in resolved_rows],
            "unresolved_sources": [r["source"] for r in gap_rows],
        }
    return report


def unknown_breakdown(movie_ids) -> dict[str, int]:
    """``{reason: count}`` for the titles that are genuinely unknown.

    This is the query that answers "why are these films still unknown?"
    without opening a single movie page.
    """
    report = title_source_report(movie_ids)
    counts: Counter = Counter()
    for entry in report.values():
        if entry["state"] == "unknown":
            counts[entry["reason"]] += 1
    return dict(counts.most_common())


def source_progress(movie_ids, batch=None) -> list[dict]:
    """Per-source roll-up: how many checks each source owes, owes, is blocked on.

    The "source-first" report from the plan. Each entry answers three
    questions a user has during a refresh: how much work this source has,
    how much is done, and *why* it is not moving.
    """
    movie_ids = list(movie_ids)
    if not movie_ids:
        return []
    title_slugs = _title_level_slugs()
    scope = RefreshRequest.objects.filter(movie_id__in=movie_ids)
    if batch is not None:
        scope = scope.filter(batch=batch)

    rows: list[dict] = []
    for slug in title_slugs:
        src_scope = scope.filter(source=slug)
        counts = {
            r["status"]: r["n"]
            for r in src_scope.values("status").annotate(n=Count("id"))
        }
        total = sum(counts.values())
        done = counts.get(RefreshRequestStatus.DONE, 0)
        failed = counts.get(RefreshRequestStatus.FAILED, 0)
        pending = counts.get(RefreshRequestStatus.PENDING, 0)
        quota = source_quota(slug)
        produced = Offer.objects.filter(
            tmdb_id__in=Movie.objects.filter(id__in=movie_ids).values("tmdb_id"),
            source__slug=slug,
        ).count()

        if total == 0:
            status = "not_applicable"
            reason = AvailabilityResolutionReason.NOT_APPLICABLE
        elif pending and quota and quota["exhausted"]:
            status = "quota_exhausted"
            reason = AvailabilityResolutionReason.QUOTA_EXHAUSTED
        elif pending:
            status = "running" if done or failed else "queued"
            reason = AvailabilityResolutionReason.QUEUED
        elif failed and not done:
            status = "failed"
            reason = AvailabilityResolutionReason.SOURCE_UNAVAILABLE
        else:
            status = "completed"
            reason = ""

        rows.append(
            {
                "source": slug,
                "label": source_label(slug),
                "tier": source_tier(slug),
                "role": _role_value(slug),
                "kind": "title",
                "status": status,
                "reason": reason,
                "total": total,
                "completed": done + failed,
                "successful": done,
                "failed": failed,
                "unknown": 0,
                "queued": pending,
                "offers": produced,
                "quota": quota,
            }
        )
    return rows


def catalogue_progress() -> list[dict]:
    """Per-collector roll-up for whole-catalogue sources.

    Reported separately from the title-level sources on purpose: a
    catalogue source cannot answer a question about one film, so
    presenting it as a per-title progress bar would be a lie. What it
    *can* honestly report is when it last synced and how big its catalogue
    is.

    **"Has it ever run?" is read from ``RefreshRun``, not ``SyncRun``.**
    That was a real bug and it cut both ways: reading ``SyncRun`` reported
    ``abc_iview``, ``pbs`` and ``tubi`` as ``collector_not_run`` (they
    *attempted* and failed, and write no ``SyncRun`` on failure), while
    simultaneously reporting JustWatch as healthy when its most recent
    attempt had failed outright. ``RefreshRun`` is written by
    ``process_catalogue_refresh`` for *every* attempt with ``error`` set on
    failure, so "attempted and failed" and "never attempted" become
    distinguishable — which is the entire difference between a collector you
    should retry and one you should stop asking about.
    """
    rows: list[dict] = []
    for slug in _catalogue_slugs():
        source = Source.objects.filter(slug=slug).first()
        last_run = (
            RefreshRun.objects.filter(service=slug)
            .order_by("-started_at")
            .first()
        )
        last_success = (
            RefreshRun.objects.filter(service=slug, error="")
            .exclude(finished_at=None)
            .order_by("-finished_at")
            .first()
        )
        contents = ProviderContent.objects.filter(source__slug=slug).count()

        if last_run is None:
            status, reason = "queued", AvailabilityResolutionReason.COLLECTOR_NOT_RUN
        elif last_run.error:
            status, reason = "failed", AvailabilityResolutionReason.SOURCE_UNAVAILABLE
        else:
            status, reason = "completed", ""

        rows.append(
            {
                "source": slug,
                "label": source_label(slug),
                "tier": source_tier(slug),
                "role": _role_value(slug),
                "kind": "catalogue",
                "status": status,
                "reason": reason,
                "total": contents,
                "completed": contents if status == "completed" else 0,
                "successful": contents if status == "completed" else 0,
                "failed": 0,
                "unknown": 0,
                "queued": 0,
                "offers": 0,
                "last_attempt": last_run.started_at if last_run else None,
                "last_success": last_success.finished_at if last_success else None,
                "error": last_run.error if last_run else "",
                "enabled": source is None or source.enabled,
            }
        )
    return rows


def build_run_summary(movie_ids, batch=None) -> dict:
    """The whole availability picture: per-source progress plus title outcomes.

    Shape::

        {
          "sources": [...],          # title-level, role-ordered
          "collectors": [...],       # catalogue-driven, reported separately
          "totals": {"titles": 191, "available": 163, "unavailable": 0,
                     "unknown": 28, "checks": 764},
          "unknown_reasons": {"quota_exhausted": 22, "title_not_found": 4,
                              "source_unavailable": 2},
        }

    The title classification and the per-source progress are computed from
    the **same** ``batch``, so the "191 titles" on the summary and the
    "1,274 checks" beside it are describing one run rather than two
    different populations.

    ``stale_failures`` is a diagnostic, not a headline: titles that carry an
    *older* failed request for a source whose newest row is pending. The
    headline reason is the current blocker (the pending row is newer, so the
    quota really is what the title is waiting on), but the older failure is
    real information — on the live watchlist that is the difference between
    "24 titles are simply waiting for midnight" and "2 of them have a 404
    behind them that will not fix itself". Reporting only the headline
    hides exactly the faults the report exists to surface.
    """
    movie_ids = list(movie_ids)
    sources = source_progress(movie_ids, batch=batch)
    collectors = catalogue_progress()
    report = title_source_report(movie_ids, batch=batch)
    states = Counter(entry["state"] for entry in report.values())
    return {
        "sources": sources,
        "collectors": collectors,
        "totals": {
            "titles": len(movie_ids),
            "available": states.get(TitleAvailabilityState.AVAILABLE, 0),
            "unavailable": states.get(TitleAvailabilityState.UNAVAILABLE, 0),
            "unknown": states.get(TitleAvailabilityState.UNKNOWN, 0),
            "checks": sum(s["total"] for s in sources),
        },
        "unknown_reasons": {
            reason: count
            for reason, count in Counter(
                e["reason"] for e in report.values()
                if e["state"] == TitleAvailabilityState.UNKNOWN
            ).most_common()
        },
        "stale_failures": stale_failure_breakdown(movie_ids),
        # Internal: consumed by the policy counterfactual so it does not
        # re-derive a per-title classification with its own queries.
        "_states": {mid: e["state"] for mid, e in report.items()},
        "_resolved_sources": {
            mid: {r["source"] for r in e["sources"] if r["available"]}
            for mid, e in report.items()
        },
    }


def stale_failure_breakdown(movie_ids) -> dict[str, int]:
    """Unknown titles carrying an older failure, split by that failure's kind.

    ``{"permanent": n, "transient": n}`` — how many unknown titles have a
    title-not-found or a retryable error behind their current blocker. A
    permanent miss is the one that matters: it means waiting will not help.
    """
    newest = _terminal_status_evidence(movie_ids)
    unknown_ids = [
        mid for mid, e in title_source_report(movie_ids).items()
        if e["state"] == TitleAvailabilityState.UNKNOWN
    ]
    if not unknown_ids:
        return {"permanent": 0, "transient": 0}
    counts = {"permanent": 0, "transient": 0}
    for movie_id, source, err in RefreshRequest.objects.filter(
        movie_id__in=unknown_ids, status=RefreshRequestStatus.FAILED
    ).values_list("movie_id", "source", "last_error"):
        current = newest.get(movie_id, {}).get(source or DEFAULT_SOURCE_SLUG)
        if current == RefreshRequestStatus.FAILED:
            continue  # already the headline; not stale
        key = (
            "permanent" if "UpstreamTitleNotFound" in (err or "")
            else "transient"
        )
        counts[key] += 1
    return counts


# --- persisted run bookkeeping -------------------------------------------


def record_run(
    run_type: str,
    *,
    batch=None,
    movie_ids=None,
    source_policy=None,
    status: str = "queued",
) -> AvailabilityRefresh:
    """Create (or adopt) the run row for a refresh and freeze its scope."""
    refresh, created = AvailabilityRefresh.objects.get_or_create(
        batch=batch,
        run_type=run_type,
        defaults={"status": status, "started_at": timezone.now()},
    ) if batch is not None else (
        AvailabilityRefresh.objects.create(
            run_type=run_type, status=status, started_at=timezone.now()
        ),
        True,
    )
    if source_policy is not None:
        refresh.source_policy = list(source_policy)
        refresh.save(update_fields=["source_policy"])
    if movie_ids is not None:
        refresh.title_filter = [int(m) for m in movie_ids]
        refresh.save(update_fields=["title_filter"])
    if not created and refresh.status == "queued":
        refresh.status = status
        refresh.save(update_fields=["status"])
    return refresh


def sync_run_progress(refresh: AvailabilityRefresh, movie_ids, batch=None) -> AvailabilityRefresh:
    """Recompute a run's per-source rows and title counters from live state.

    Idempotent and read-only with respect to the queue: it never enqueues,
    drains, or settles anything, so it is safe to call on every poll. That
    property is what lets the frontend recover a run after a reload by
    simply asking for it again.
    """
    summary = build_run_summary(movie_ids, batch=batch if batch is not None else refresh.batch)
    rows = summary["sources"] + summary["collectors"]

    for row in rows:
        source_row, _ = AvailabilityRefreshSource.objects.update_or_create(
            refresh=refresh,
            source=row["source"],
            defaults={
                "kind": row["kind"],
                "status": row["status"],
                "total": row["total"],
                "completed": row["completed"],
                "successful": row["successful"],
                "failed": row["failed"],
                "unknown": row["unknown"],
                "queued": row["queued"],
                "tier": row["tier"],
                "reason": row.get("reason", ""),
                "error": row.get("error", ""),
                "finished_at": timezone.now() if row["status"] in (
                    "completed", "failed", "skipped", "quota_exhausted",
                ) else None,
            },
        )
        if source_row.started_at is None and row["completed"]:
            source_row.started_at = timezone.now()
            source_row.save(update_fields=["started_at"])

    totals = summary["totals"]
    refresh.total_titles = totals["titles"]
    refresh.available_titles = totals["available"]
    refresh.unavailable_titles = totals["unavailable"]
    refresh.unknown_titles = totals["unknown"]
    refresh.unknown_reasons = summary["unknown_reasons"]
    refresh.completed_titles = totals["available"] + totals["unavailable"]
    refresh.save(
        update_fields=[
            "total_titles",
            "available_titles",
            "unavailable_titles",
            "unknown_titles",
            "unknown_reasons",
            "completed_titles",
        ]
    )
    return refresh


# NOTE: run-status transitions live in ``tracker.services.refresh_lifecycle``.
# They used to live here and were moved because a status must be derived from
# persisted state by exactly one writer; keeping a second implementation here
# is how a run ends up COMPLETED because a reader inferred it was finished.


def active_refresh() -> AvailabilityRefresh | None:
    """The run a page reload should resume showing, or ``None``."""
    return (
        AvailabilityRefresh.objects.filter(
            status__in=[
                AvailabilityRefreshStatus.QUEUED,
                AvailabilityRefreshStatus.RUNNING,
            ]
        )
        .order_by("-created_at")
        .first()
    )
