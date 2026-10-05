"""Explicit partition coordinator for JustWatch catalogue synchronization.

Why a coordinator instead of the generic catalogue scheduler
-----------------------------------------------------------
``process_catalogue_refresh`` calls ``refresh_catalogue()`` with no scope, which
is right for the first-party collectors: each one owns exactly one
provider/country and reports its whole configured scope per fetch. JustWatch is
different — it is an aggregator over *many* providers and *many* countries, and
it refuses an unscoped call (country is an explicit application decision, never
inferred). Left to the generic scheduler it would either raise forever or, worse,
be "fixed" by hard-coding one country.

So JustWatch catalogue work is **partitioned** and walked explicitly:

    NL  -> netflix, prime, disney, ...
    IT  -> netflix, prime, raiplay, ...
    JP  -> netflix, ...

Explicit partition lists
-----------------------
:func:`refresh_justwatch_partitions` accepts an explicit ``partitions`` list and
executes exactly that list. This matters for operator commands: ``--country NL
--provider netflix`` must run *that* partition even when Netflix is nowhere near
the head of the global plan. The list is never re-filtered or rebuilt here — the
caller owns what it asked for, and ``max_partitions`` only truncates it.

Aggregate status: SUCCEEDED / PARTIAL / FAILED
----------------------------------------------
A run over several partitions has a meaningful aggregate state, and collapsing
it to "succeeded if anything worked" would be actively misleading downstream
("is this a complete observation?" is exactly the question the PARTIAL status
exists to answer):

============================  ============
partition outcomes            aggregate
============================  ============
all succeeded                 SUCCEEDED
some succeeded, some failed   PARTIAL
all failed                    FAILED
none runnable (empty plan)    SUCCEEDED
============================  ============

The aggregate lives on the returned summary (``status``) rather than on a
synthetic parent ``SyncRun``: each partition already owns a real ``SyncRun``
written by the refresh service with its own accurate per-partition status, and
inventing a parent row would duplicate audit information the architecture
already stores. The summary is the single place that speaks for the whole run.

Safety invariant
----------------
Nothing here deactivates anything, and nothing here can: JustWatch is
permanently non-authoritative (``supports_authoritative_snapshots = False``), so
the refresh service never calls ``deactivate_unseen`` for it. A failed or partial
partition therefore leaves every existing offer untouched, and a successful
JustWatch partition cannot remove a first-party collector's evidence.

Incremental progress
--------------------
Each partition's paging state lives on the existing ``SyncCursor`` row
(``(provider, source, country)`` is exactly its unique key). A partition that
walks past the API's measured depth boundary is marked
:data:`CURSOR_EXHAUSTED_MARKER` so a later run starts it over rather than
leaving it permanently parked as "finished" — and, crucially, only a partition
that actually completed advances its cursor, so a failed partition stays
retryable from where it was.
"""
from __future__ import annotations

import logging

from availability.domain.enums import ContentType
from availability.models import Country, Source
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import source_registry
from availability.sources.justwatch_providers import PACKAGE_SHORT_NAMES
from tracker.models import Provider

logger = logging.getLogger(__name__)

#: Cap on partitions walked in one invocation when the caller does not choose a
#: limit. Keeps a single cron tick bounded: a partition is several JustWatch
#: requests, and the unofficial endpoint is fragile enough that an unbounded
#: walk is how one bad run turns into a multi-hour stall.
DEFAULT_MAX_PARTITIONS = 6

#: Paging state written when a partition walked past the API's depth boundary.
CURSOR_EXHAUSTED_MARKER = "done"

#: Aggregate outcome values, named after ``availability.domain.enums.SyncStatus``
#: so a scheduler can map them onto the persisted vocabulary directly.
STATUS_SUCCEEDED = "succeeded"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def available_partitions(*, max_partitions=DEFAULT_MAX_PARTITIONS):
    """Build the default partition plan: ``[(country_code, provider_slug), ...]``.

    Only combinations that can actually produce data are planned — a provider
    JustWatch does not expose in a market has no short name there, so the pair is
    skipped rather than requested and found empty (the ``packages`` filter
    selects *titles*, so an unmapped pair would silently return a
    cross-provider page).

    Ordering is country-first then provider, both alphabetical, so the plan is
    deterministic and a truncated run is a prefix of the full plan rather than an
    arbitrary subset that changes between ticks.
    """
    countries = sorted(
        Country.objects.filter(enabled=True).values_list("code", flat=True)
    )
    mapped_providers = {
        slug for slug in PACKAGE_SHORT_NAMES
        if Provider.objects.filter(slug=slug, is_enabled=True).exists()
    }

    plan: list[tuple[str, str]] = []
    for country in countries:
        for provider_slug in sorted(mapped_providers):
            if country not in PACKAGE_SHORT_NAMES.get(provider_slug, {}):
                continue
            plan.append((country, provider_slug))
    return plan[:max_partitions]


def _cursor_for(cursors, provider, source, country):
    row = cursors.get(provider=provider, source=source, country=country)
    return (row.cursor if row else "") or ""


def refresh_justwatch_partitions(*, partitions=None,
                                max_partitions=DEFAULT_MAX_PARTITIONS,
                                content_type=ContentType.MOVIE,
                                source=None, refresh_service=None):
    """Walk JustWatch catalogue partitions, resuming each from its cursor.

    ``max_partitions=None`` means "no further cap": the caller has already
    limited what it asked for (the management command filters first, then caps),
    and truncating an already-filtered list here would be a second, invisible
    limit.

    ``partitions=None`` builds the default global plan
    (:func:`available_partitions`). When ``partitions`` is given, **exactly**
    those ``(country_code, provider_slug)`` pairs are executed — the list is not
    rebuilt or re-filtered, because an operator asking for one specific
    partition must get it even when it sits past ``max_partitions`` of the global
    ordering. ``max_partitions`` truncates whatever plan is in effect.

    The returned summary carries per-partition outcomes plus an aggregate
    ``status`` of ``succeeded`` / ``partial`` / ``failed``; see the module
    docstring for the mapping.
    """
    source = source or source_registry.get("justwatch")
    service = refresh_service or AvailabilityRefreshService(source=source)
    cursors = CursorRepository()

    if partitions is None:
        plan = available_partitions(
            max_partitions=max_partitions
            if max_partitions is not None else DEFAULT_MAX_PARTITIONS
        )
    elif max_partitions is None:
        # Caller-supplied and already capped: use it verbatim.
        plan = [(str(country).upper(), str(slug)) for country, slug in partitions]
    else:
        # Caller-supplied with a cap: truncate, but never re-derive the plan.
        plan = [
            (str(country).upper(), str(slug))
            for country, slug in list(partitions)[:max_partitions]
        ]

    if not plan:
        logger.info("justwatch_partitions_empty reason=no_walkable_partition")
        return {
            "status": STATUS_SUCCEEDED,
            "attempted": 0,
            "refreshed": 0,
            "errors": 0,
            "partial": 0,
            "skipped": 0,
            "succeeded_partitions": [],
            "failed_partitions": [],
            "skipped_partitions": [],
            "results": [],
            "plan": [],
        }

    source_row, _ = _source_row(source)
    results = []
    for country_code, provider_slug in plan:
        provider = Provider.objects.filter(
            slug=provider_slug, is_enabled=True).first()
        country = Country.objects.filter(code=country_code, enabled=True).first()
        if provider is None or country is None:
            # Not runnable right now (provider disabled, country disabled or
            # unknown). Counted as skipped, never as a failure: nothing was
            # attempted, so nothing can be wrong.
            logger.info(
                "justwatch_partition_skipped country=%s provider=%s "
                "reason=disabled_or_unknown", country_code, provider_slug,
            )
            results.append({
                "country": country_code,
                "provider": provider_slug,
                "discovered": 0,
                "created": 0,
                "updated": 0,
                "status": "skipped",
            })
            continue

        cursor = _cursor_for(cursors, provider, source_row, country)
        if cursor == CURSOR_EXHAUSTED_MARKER:
            # Partition walked past the API's boundary last time. Start it over
            # now, so a partition is never permanently parked as "finished".
            logger.info("justwatch_partition_reset country=%s provider=%s",
                        country_code, provider_slug)
            cursor = ""

        result = {
            "country": country_code,
            "provider": provider_slug,
            "discovered": 0,
            "created": 0,
            "updated": 0,
            "status": STATUS_SUCCEEDED,
            "cursor_in": cursor,
        }
        try:
            outcome = service.refresh_catalogue(
                provider=provider_slug,
                country=country_code,
                cursor={"offset": _as_offset(cursor),
                        "content_type": content_type},
            )
        except Exception as exc:  # noqa: BLE001 - one partition must not stop the rest
            logger.exception(
                "justwatch_partition_failed country=%s provider=%s error=%s",
                country_code, provider_slug, exc,
            )
            result["status"] = STATUS_FAILED
            result["error"] = f"{type(exc).__name__}: {exc}"
            # The cursor is deliberately left untouched: a failed partition must
            # stay retryable from exactly where it stopped.
            results.append(result)
            continue

        result["discovered"] = outcome.discovered
        result["created"] = outcome.created
        result["updated"] = outcome.updated
        # ``RefreshResult`` does not carry the paging cursor; the refresh service
        # persists it on the SyncCursor row it just wrote. An empty value there
        # means the source hit the API's depth boundary, i.e. this partition is
        # complete for now.
        persisted = _cursor_for(cursors, provider, source_row, country)
        result["cursor_out"] = persisted or CURSOR_EXHAUSTED_MARKER
        results.append(result)

    succeeded = [r for r in results if r["status"] == STATUS_SUCCEEDED]
    failed = [r for r in results if r["status"] == STATUS_FAILED]
    skipped = [r for r in results if r["status"] == "skipped"]

    if succeeded:
        # Anything usable came out: a mixed run is PARTIAL, never SUCCESS.
        status = STATUS_PARTIAL if failed else STATUS_SUCCEEDED
    elif failed:
        # FAILED only when something was actually attempted; a run of pure
        # skips attempted nothing, so it is not a failure.
        status = STATUS_FAILED if not skipped else STATUS_PARTIAL
    else:
        status = STATUS_SUCCEEDED

    summary = {
        "status": status,
        "attempted": len(succeeded) + len(failed),
        "refreshed": len(succeeded),
        "errors": len(failed),
        "partial": 1 if status == STATUS_PARTIAL else 0,
        "skipped": len(skipped),
        "succeeded_partitions": [f"{r['country']}/{r['provider']}" for r in succeeded],
        "failed_partitions": [f"{r['country']}/{r['provider']}" for r in failed],
        "skipped_partitions": [f"{r['country']}/{r['provider']}" for r in skipped],
        "results": results,
        "plan": [list(p) for p in plan],
    }
    logger.info(
        "justwatch_partitions_summary status=%s attempted=%s refreshed=%s "
        "errors=%s skipped=%s",
        summary["status"], summary["attempted"], summary["refreshed"],
        summary["errors"], summary["skipped"],
    )
    return summary


def _source_row(source):
    row, _ = Source.objects.get_or_create(
        slug=source.slug,
        defaults={"name": source.name, "type": source.source_type},
    )
    return row, True


def _as_offset(cursor):
    try:
        return max(0, int(cursor))
    except (TypeError, ValueError):
        return 0