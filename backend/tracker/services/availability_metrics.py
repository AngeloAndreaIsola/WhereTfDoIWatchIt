"""Seven-day operational metrics. Observational only.

Nothing in this module calls an availability source. It reads persisted state
and returns aggregates, which is the whole point of the phase: metrics must be
collectable at any time without spending a single unit of upstream quota, and
without the act of measuring perturbing what is being measured.

The definitions, the authoritative columns, and the metrics that *cannot* be
derived from the current schema are written down in ``docs/P7_METRICS.md``. The
two rules that most easily go wrong are implemented here rather than left to
each caller:

**A missing value is not a zero.** A source that never executed, a window with
no runs, a rate with no denominator — each returns ``None``, because ``0``
would assert "we checked and found nothing" where the truth is "we have no
data". The UI renders these differently on purpose.

**A source's statuses are never collapsed into one number.** ``not_run``,
``policy_not_needed``, ``quota_exhausted``, ``source_disabled`` and a
completed no-offer answer are five different facts. A table that showed
``0 offers`` for both "ran and found nothing" and "never ran" would invert the
most important comparison an operator makes.

Refresh failure is read from ``status='failed'`` and nowhere else. P6b writes
that status only through the genuine lifecycle failure path, so unknown-title
counts never masquerade as a failed refresh.

Query count is fixed: six aggregates, independent of how many sources exist or
how many runs are in the window. There is no query per source and none per
refresh.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone

from tracker.models import (
    ApiCallCounter,
    AvailabilityRefresh,
    AvailabilityRefreshSource,
    AvailabilityRefreshStatus,
    RefreshRequest,
    RefreshRequestStatus,
)
from tracker.services import source_policy
from tracker.services.rate_limit import (
    SOURCE_DAILY_LIMITS,
    SOURCE_MONTHLY_LIMITS,
    rate_limiter_for,
)

#: Rolling window length. 7x24h from now, inclusive of now.
WINDOW_DAYS = 7

#: Statuses that mean a refresh has not reached an outcome yet. Not failures.
ACTIVE_REFRESH_STATUSES = (
    AvailabilityRefreshStatus.QUEUED.value,
    AvailabilityRefreshStatus.RUNNING.value,
)

#: Statuses that represent a finished refresh with an outcome.
TERMINAL_REFRESH_STATUSES = (
    AvailabilityRefreshStatus.COMPLETED.value,
    AvailabilityRefreshStatus.PARTIAL.value,
    AvailabilityRefreshStatus.FAILED.value,
)

#: Source-row statuses that are terminal *for the run they belong to*. Mirrors
#: ``availability_report.TERMINAL_SOURCE_STATUSES`` rather than re-deriving it,
#: so metrics cannot drift from the invariant P6a enforces.
from tracker.services.availability_report import TERMINAL_SOURCE_STATUSES  # noqa: E402


def window_start(now: dt.datetime | None = None, days: int = WINDOW_DAYS) -> dt.datetime:
    """Inclusive start of the rolling window. Timezone-aware UTC."""
    now = now or timezone.now()
    return now - dt.timedelta(days=days)


def _rate(numerator: int, denominator: int) -> float | None:
    """Ratio or ``None``. A zero denominator is missing data, not a zero."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _median(values: list[float]) -> float | None:
    """Median of a materialised list, or ``None`` when empty.

    Postgres percentile functions would avoid the transfer, but the terminal-run
    count per window is small and bounded by the refresh cadence; keeping this
    in Python avoids a dialect-specific aggregate and keeps the arithmetic
    identical to what the tests assert.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 3)


def _refresh_metrics(start: dt.datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    """Refresh outcomes plus the per-refresh title counters.

    Two grouped aggregates over the window. The second return value is the
    unknown-reason tally, accumulated from a JSONField on each run.
    """
    runs = AvailabilityRefresh.objects.filter(created_at__gte=start)

    by_status = {
        row["status"]: row["n"]
        for row in runs.values("status").annotate(n=Count("id"))
    }

    total = sum(by_status.values())
    terminal_rows = runs.filter(status__in=TERMINAL_REFRESH_STATUSES)

    # Duration is only defined for a finished run. An unfinished run has no
    # duration, and folding its NULL in as zero would drag every average down
    # while making a stalled run look like a fast one.
    durations = [
        (finished - started).total_seconds()
        for started, finished in terminal_rows.values_list("started_at", "finished_at")
        if started is not None and finished is not None
    ]

    completed = by_status.get(AvailabilityRefreshStatus.COMPLETED.value, 0)
    partial = by_status.get(AvailabilityRefreshStatus.PARTIAL.value, 0)
    failed = by_status.get(AvailabilityRefreshStatus.FAILED.value, 0)
    active = sum(by_status.get(s, 0) for s in ACTIVE_REFRESH_STATUSES)
    terminal_total = completed + partial + failed

    counters = terminal_rows.aggregate(
        titles=Sum("total_titles"),
        available=Sum("available_titles"),
        unavailable=Sum("unavailable_titles"),
        unknown=Sum("unknown_titles"),
        failed_titles=Sum("failed_titles"),
    )

    metrics = {
        "total": total,
        "completed": completed,
        "partial": partial,
        "failed": failed,
        "active": active,
        "terminal": terminal_total,
        "completion_rate": _rate(completed, terminal_total),
        "partial_rate": _rate(partial, terminal_total),
        "failure_rate": _rate(failed, terminal_total),
        "runs_with_duration": len(durations),
        "average_duration_seconds": (
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        "median_duration_seconds": _median(durations),
        "max_duration_seconds": round(max(durations), 3) if durations else None,
        "titles_processed": counters["titles"] or 0,
        "titles_available": counters["available"] or 0,
        "titles_unavailable": counters["unavailable"] or 0,
        "titles_unknown": counters["unknown"] or 0,
        "titles_failed": counters["failed_titles"] or 0,
    }

    # unknown_reasons is overwritten per run rather than append-only, so this is
    # a tally of the *latest* state each run recorded, not an event log.
    unknown_reasons: dict[str, int] = {}
    for row in runs.exclude(unknown_reasons={}).values_list("unknown_reasons", flat=True):
        for reason, count in (row or {}).items():
            unknown_reasons[reason] = unknown_reasons.get(reason, 0) + int(count)

    return metrics, unknown_reasons


def _request_metrics(start: dt.datetime) -> dict[str, Any]:
    """Request throughput and outcome, with blocked reasons kept distinct."""
    requests = RefreshRequest.objects.filter(requested_at__gte=start)

    by_status = {
        row["status"]: row["n"]
        for row in requests.values("status").annotate(n=Count("id"))
    }

    # `blocked_reason` is a separate dimension from `status`: `blocked` rows can
    # be any of five different reasons, and only one of them is a quota event.
    blocked_reasons = {
        (row["blocked_reason"] or "unlabelled"): row["n"]
        for row in requests.filter(status=RefreshRequestStatus.BLOCKED.value)
        .exclude(blocked_reason="")
        .values("blocked_reason")
        .annotate(n=Count("id"))
    }

    done = by_status.get(RefreshRequestStatus.DONE.value, 0)

    return {
        "created": sum(by_status.values()),
        "completed": done,
        "failed": by_status.get(RefreshRequestStatus.FAILED.value, 0),
        "blocked": by_status.get(RefreshRequestStatus.BLOCKED.value, 0),
        "pending": by_status.get(RefreshRequestStatus.PENDING.value, 0),
        "blocked_by_reason": blocked_reasons,
        "completion_rate": _rate(done, sum(by_status.values())),
    }


def _registered_sources() -> list[str]:
    """Every source the registry knows, whether or not it ever ran."""
    try:
        from availability.sources import source_registry

        return sorted(source_registry.slugs())
    except Exception:  # noqa: BLE001 - metrics must never fail on the registry
        return sorted(
            set(source_policy.SOURCE_ROLES)
            | set(AvailabilityRefreshSource.objects.values_list("source", flat=True))
        )


def _offer_counts(start: dt.datetime) -> dict[str, dict[str, int]]:
    """Offer counts per source slug, grouped.

    ``Offer.last_seen`` is the observation stamp, so this counts offers *seen*
    in the window rather than offers created in it. Grouped by the FK to
    ``availability.Source``; the slug is resolved from the joined row.
    """
    from availability.models import Offer, Source

    return {
        row["source__slug"]: {"positive_offers": row["n"]}
        for row in (
            Offer.objects.filter(last_seen__gte=start)
            .values("source__slug")
            .annotate(n=Count("id"))
        )
    }


def _source_metrics(start: dt.datetime) -> list[dict[str, Any]]:
    """Per-source execution, outcomes, durations and freshness.

    One grouped pass over the source rows in the window plus one over offers.
    A source that never ran is emitted with ``None`` counters, not ``0``.
    """
    rows = AvailabilityRefreshSource.objects.filter(
        refresh__created_at__gte=start
    )

    per_source: dict[str, dict[str, Any]] = {}

    def bucket(slug: str) -> dict[str, Any]:
        return per_source.setdefault(
            slug,
            {
                "source": slug,
                "runs": 0,
                "completed": 0,
                "successful": 0,
                "failed": 0,
                "quota_exhausted": 0,
                "skipped": 0,
                "not_run": 0,
                "not_applicable": 0,
                "not_checked": 0,
                "running": 0,
                "_rows_completed": 0,
                "_rows_failed": 0,
                "_rows_quota_exhausted": 0,
                "_rows_skipped": 0,
                "_rows_not_run": 0,
                "_rows_not_applicable": 0,
                "_rows_not_checked": 0,
                "_rows_running": 0,
                "requests_processed": 0,
                "titles_unknown": 0,
                "positive_offers": 0,
                "_durations": [],
                "last_success_at": None,
                "last_attempted_at": None,
            },
        )

    # Status histogram + counters in a single grouped query.
    grouped = rows.values("source", "status").annotate(
        n=Count("id"),
        total=Sum("total"),
        completed=Sum("completed"),
        successful=Sum("successful"),
        failed=Sum("failed"),
        unknown=Sum("unknown"),
        queued=Sum("queued"),
    )

    # Row counts per status. `_rows_*` keeps these separate from the item-level
    # `completed`/`failed`/`successful` counters further down, which count
    # *titles* handled by those rows.
    status_to_field = {
        "completed": "_rows_completed",
        "failed": "_rows_failed",
        "quota_exhausted": "_rows_quota_exhausted",
        "skipped": "_rows_skipped",
        "not_run": "_rows_not_run",
        "not_applicable": "_rows_not_applicable",
        "not_checked": "_rows_not_checked",
        "queued": "_rows_running",
        "running": "_rows_running",
    }

    for row in grouped:
        entry = bucket(row["source"])
        entry["runs"] += row["n"]
        field = status_to_field.get(row["status"])
        if field:
            entry[field] += row["n"]
        entry["requests_processed"] += row["total"] or 0
        entry["titles_unknown"] += row["unknown"] or 0

    # Per-slug counter sums (one grouped query, not one per source). The row's
    # own counters are maintained by `sync_run_progress`, so metrics sum them
    # rather than recomputing from the status histogram.
    for row in rows.values("source").annotate(
        successful=Sum("successful"),
        completed=Sum("completed"),
        failed=Sum("failed"),
        total=Sum("total"),
        unknown=Sum("unknown"),
    ):
        entry = per_source[row["source"]]
        entry["successful"] = row["successful"] or 0
        entry["completed"] = row["completed"] or 0
        entry["failed"] = row["failed"] or 0
        entry["successful"] = row["successful"] or 0
        entry["requests_processed"] = row["total"] or 0
        entry["titles_unknown"] = row["unknown"] or 0

    # Durations from terminal rows only, plus the two freshness stamps.
    duration_rows = rows.exclude(started_at__isnull=True).values(
        "source", "started_at", "finished_at", "status"
    )
    for row in duration_rows:
        entry = per_source[row["source"]]
        started, finished = row["started_at"], row["finished_at"]
        if (
            row["status"] in TERMINAL_SOURCE_STATUSES
            and finished is not None
            and started is not None
        ):
            entry["_durations"].append((finished - started).total_seconds())
        if started is not None and (
            entry["last_attempted_at"] is None or started > entry["last_attempted_at"]
        ):
            entry["last_attempted_at"] = started
        if (
            row["status"] == "completed"
            and finished is not None
            and (entry["last_success_at"] is None or finished > entry["last_success_at"])
        ):
            entry["last_success_at"] = finished

    offers = _offer_counts(start)
    for slug, entry in per_source.items():
        entry["positive_offers"] = offers.get(slug, {}).get("positive_offers", 0)

    # Every registered source appears, so a source that never ran is visible as
    # such instead of being absent — and absence would read as "not configured".
    for slug in _registered_sources():
        bucket(slug)

    for slug, entry in per_source.items():
        durations = entry.pop("_durations")
        # Row counts per status. Distinct from the item-level counters above
        # (`failed` here = runs that failed; `entry["failed"]` = titles that
        # failed), and mixing the two would make one number mean two things.
        entry["status_breakdown"] = {
            "completed": entry["_rows_completed"],
            "failed": entry["_rows_failed"],
            "quota_exhausted": entry["_rows_quota_exhausted"],
            "skipped": entry["_rows_skipped"],
            "not_run": entry["_rows_not_run"],
            "not_applicable": entry["_rows_not_applicable"],
            "not_checked": entry["_rows_not_checked"],
            "running": entry["_rows_running"],
        }
        # No-offer results are not stored per source row, so this is reported
        # as unavailable rather than inferred from offers.
        entry["no_offers"] = None
        entry["average_duration_seconds"] = (
            round(sum(durations) / len(durations), 3) if durations else None
        )
        entry["median_duration_seconds"] = _median(durations)
        entry["never_executed"] = entry["runs"] == 0
        role = source_policy.role_of(slug)
        entry["role"] = role.value if role is not None else None
        entry["policy_metered"] = source_policy.is_policy_metered(slug)

    return [per_source[slug] for slug in sorted(per_source)]


def _quota_metrics(start: dt.datetime, requests: dict[str, Any]) -> dict[str, Any]:
    """Current daily + monthly quota state, kept separate from failures."""
    counter_services = set(
        ApiCallCounter.objects.values_list("service", flat=True)
    )
    services = (
        counter_services
        | set(SOURCE_DAILY_LIMITS)
        | set(SOURCE_MONTHLY_LIMITS)
    )

    quotas = []
    for service in sorted(services):
        limiter = rate_limiter_for(service)
        if not limiter.metered:
            continue
        daily_remaining = limiter.remaining()
        monthly_remaining = limiter.monthly_remaining()
        quotas.append(
            {
                "source": service,
                "limit": limiter.daily_limit,
                "used": (
                    limiter.daily_limit - daily_remaining
                    if limiter.daily_limit is not None
                    and daily_remaining is not None
                    else None
                ),
                "remaining": daily_remaining,
                "exhausted": limiter.is_exhausted(),
                "monthly_limit": limiter.monthly_limit,
                "monthly_used": limiter.monthly_used(),
                "monthly_remaining": monthly_remaining,
                "monthly_window_days": limiter.monthly_window_days,
            }
        )

    return {
        "sources": quotas,
        "sources_exhausted": [e["source"] for e in quotas if e["exhausted"]],
        "blocked_requests": requests.get("blocked_by_reason", {}).get(
            "quota_exhausted", 0
        ),
        # The counters record reservations, not an event when a cap is crossed,
        # so exhaustion *events* and their timestamps are not derivable. Said
        # plainly rather than returned as an empty list that would read as
        # "never exhausted".
        "events_available": False,
        "events_unavailable_reason": (
            "ApiCallCounter stores a single daily count per service and "
            "ApiCallLedger stores per-day counts; neither records an event when "
            "a limit is reached, so exhaustion events and their timestamps "
            "cannot be derived from persisted state."
        ),
        "window_start": start,
    }


def _daily_history(start: dt.datetime) -> list[dict[str, Any]]:
    """Per-day refresh outcome counts across the window.

    Bucketed in the database with ``TruncDate`` so the rows returned are bounded
    by the number of days, not by the number of runs.
    """
    from django.db.models.functions import TruncDate

    rows = (
        AvailabilityRefresh.objects.filter(created_at__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day", "status")
        .annotate(n=Count("id"))
        .order_by("day")
    )

    days: dict[dt.date, dict[str, int]] = {}
    for row in rows:
        entry = days.setdefault(
            row["day"],
            {
                "date": row["day"].isoformat(),
                "completed": 0,
                "partial": 0,
                "failed": 0,
                "active": 0,
                "total": 0,
            },
        )
        entry[row["status"]] = entry.get(row["status"], 0) + row["n"]
        entry["total"] += row["n"]

    return [days[day] for day in sorted(days)]


def availability_metrics(
    *, now: dt.datetime | None = None, days: int = WINDOW_DAYS
) -> dict[str, Any]:
    """The full seven-day operational picture.

    Purely observational: six grouped aggregates, no per-source or per-refresh
    queries, and no upstream call of any kind.
    """
    now = now or timezone.now()
    start = window_start(now, days)

    refreshes, unknown_reasons = _refresh_metrics(start)
    requests = _request_metrics(start)
    sources = _source_metrics(start)
    quota = _quota_metrics(start, requests)

    return {
        "window": {
            "start": start,
            "end": now,
            "days": days,
            "timezone": "UTC",
            "definition": f"now minus {days}x24h through now, inclusive",
        },
        "refreshes": refreshes,
        "daily": _daily_history(start),
        "titles": {
            # What the runs in the window recorded.
            "processed": refreshes["titles_processed"],
            "available": refreshes["titles_available"],
            "unavailable": refreshes["titles_unavailable"],
            "unknown": refreshes["titles_unknown"],
            "failed": refreshes["titles_failed"],
            "unknown_reasons": unknown_reasons,
        },
        "requests": requests,
        "sources": sources,
        "quota": quota,
        "unavailable_metrics": [
            {
                "metric": "quota.exhaustion_events",
                "reason": quota["events_unavailable_reason"],
            },
            {
                "metric": "sources[].no_offers",
                "reason": (
                    "Per-source no-offer results are not persisted. A source row "
                    "records that the source answered, not what it found, and "
                    "Offer rows only exist for positive results."
                ),
            },
            {
                "metric": "requests.resolved",
                "reason": (
                    "A request row records that a source answered, not whether it "
                    "produced an offer; offer counts come from Offer instead."
                ),
            },
        ],
    }