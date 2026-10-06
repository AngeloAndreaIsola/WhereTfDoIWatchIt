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

Partition identity is ``(country, provider, content_type, year_window)``
------------------------------------------------------------------------------
One cursor row, one partition map
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``SyncCursor`` is unique on ``(provider, source, country)`` and that schema stays
as it is. The walk also varies ``content_type`` and — since P8 — the release-year
window, so several partitions share one row:

    SyncCursor (provider=netflix, source=justwatch, country=NL)
        ├── MOVIE  2017-2026  → 300
        ├── MOVIE  2007-2016  → 100
        ├── SERIES 2017-2026  → 200
        └── …

The row's ``cursor`` column holds that whole map, not a single offset. Before
this, every partition wrote a bare offset onto the shared row, so a MOVIE walk
erased the SERIES walk's progress and both restarted from 0 forever.

The invariant this preserves:

    a catalogue walk may advance exactly one partition,
    but it must never erase progress belonging to another.

It is enforced by :func:`_read_cursor_map` (snapshot every entry before the
fetch) plus :func:`_write_cursor_map` (restore the snapshot, then apply only this
partition's new offset). The refresh service in between writes the source's
single-entry cursor verbatim, so restoring the snapshot afterwards is what keeps
the siblings intact. Partitions are therefore independently restartable and
idempotent.

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

import hashlib
import logging
from collections import namedtuple

from availability.domain.enums import ContentType
from availability.models import Country, Source
from availability.persistence.repositories import CursorRepository
from availability.services.refresh import AvailabilityRefreshService
from availability.sources import source_registry
from availability.sources.base import accepts_kwarg
from availability.sources.justwatch_providers import PACKAGE_SHORT_NAMES
from tracker.models import Provider
from tracker.services.refresh_attribution import attribute_sync_runs

# Reused verbatim from the generic catalogue scheduler: same lock, same key, so
# the two entry points cannot run concurrently for the same source.
from tracker.services.bulk_refresh import release_source_lock, try_source_lock

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
#: Another coordinator already holds the per-source lock. Distinct from
#: STATUS_FAILED (nothing failed) and from STATUS_SUCCEEDED (nothing was walked).
STATUS_LOCKED = "locked"
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


#: One unit of catalogue work: a country/provider pair narrowed by content
#: type and release-year window. Carries its own identity so it owns its cursor.
_Partition = namedtuple(
    "_Partition", "country provider content_type min_year max_year"
)


def year_windows(start_year, end_year, window_size):
    """Deterministic, gapless, non-overlapping year windows.

    Returns ``[(min_year, max_year), ...]`` — each tuple **ascending**, lowest
    year first — generated **descending** from ``start_year`` down to
    ``end_year``, so the most recent (most demanded) catalogue is walked first.
    A returned tuple is always ``low <= high``.

    Descending order is deliberate. The offsets are the scarce resource — each
    window gets its own bounded offset stream, so a walk that is interrupted
    after a few windows has still covered the newest titles rather than a
    random slice of the back catalogue.

    Boundary handling is explicit because this is where year partitioning usually
    goes wrong: every title in ``[start_year, end_year]`` falls in exactly one
    window. A window that would run past ``end_year`` is clamped to it, and no
    window is ever allowed to overlap its neighbour by a year.
    """
    if window_size is None or window_size <= 0:
        # Normalised ascending so every window this function returns obeys the
        # same low <= high contract as a windowed one.
        return [(min(start_year, end_year), max(start_year, end_year))]
    if start_year < end_year:
        # A descending walk from a lower start year has nothing to do. Returning
        # an empty plan is honest: silently swapping the bounds would plan
        # partitions that do not match what the caller asked for.
        return []
    windows = []
    current = start_year
    while current >= end_year:
        # Inclusive on both ends: a 10-year window covers ten years, and the
        # next window starts at the year *before* the current one's floor, so no
        # year is ever counted twice and none is skipped.
        low = max(end_year, current - window_size + 1)
        windows.append((low, current))
        current = low - 1
    return windows


def _partition_id(*, country, provider, content_type, min_year, max_year):
    """A stable identity for one catalogue partition.

    Every dimension that can change the titles a partition returns belongs in
    here, so two partitions never share a cursor. Deterministic and order
    independent, so the same partition always resumes from its own offset across
    processes and runs.
    """
    year = "-" if min_year is None and max_year is None else f"{min_year or ''}-{max_year or ''}"
    raw = f"{str(country).upper()}|{provider}|{content_type}|{year}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw}:{digest}"


#: Separator between the offset and the owning partition id inside one cursor
#: value. ``SyncCursor``'s unique key is ``(provider, source, country)`` and that
#: is left alone: a schema change is not needed to stop partitions colliding,
#: because the partition that owns an offset is recorded *in the cursor value*.
#: One row per (provider, source, country) then holds a small map of
#: partition_id -> offset, and a partition can only ever resume its own offset.
#: Separator between entries in the cursor map. The partition id itself
#: contains ``|`` in its human-readable prefix, so the map is written with an
#: unambiguous encoding instead: keys and values are percent-escaped, which keeps
#: ``a|b=1|c=2`` from parsing as three entries.
_CURSOR_SEP = "&"
_CURSOR_ESCAPE = {"&": "%26", "=": "%3D", "%": "%25"}

#: Guards against a malformed or hostile cursor value growing without bound.
_MAX_CURSOR_PARTITIONS = 64


def _escape_cursor(text):
    for char, code in _CURSOR_ESCAPE.items():
        text = str(text).replace(char, code)
    return text


def _unescape_cursor(text):
    for char, code in (("%25", "%"), ("%3D", "="), ("%26", "&")):
        text = text.replace(code, char)
    return text


def _encode_cursor(partitions):
    """Serialise ``{partition_id: offset}`` into one cursor string.

    Both halves are escaped, because a partition id contains ``=`` and ``|``
    itself and an unescaped separator would silently split one partition's entry
    into several.
    """
    items = sorted(partitions.items())[:_MAX_CURSOR_PARTITIONS]
    return _CURSOR_SEP.join(
        f"{_escape_cursor(key)}={_escape_cursor(value)}" for key, value in items
    )


def _decode_cursor(raw):
    """Parse a cursor string back into ``{partition_id: offset}``.

    A bare integer (the pre-P8 format) parses as an empty map rather than
    raising, so an existing row left over from before this change is treated as
    "no partition has started yet" instead of breaking the walk.
    """
    out = {}
    if not raw:
        return out
    text = str(raw)
    if text.isdigit():
        return out
    for chunk in text.split(_CURSOR_SEP):
        key, sep, value = chunk.partition("=")
        if sep:
            out[_unescape_cursor(key)] = _unescape_cursor(value)
    return out


def _cursor_for(cursors, provider, source, country, partition_id):
    """This partition's own offset, or ``""`` when it has not started.

    Scoped to *this* partition deliberately: a cursor row may also hold offsets
    belonging to the sibling partitions of the same provider/country, and none
    of them may be mistaken for this one's progress.
    """
    row = cursors.get(provider=provider, source=source, country=country)
    stored = _decode_cursor(row.cursor if row else "")
    return stored.get(partition_id, "")


def _read_cursor_map(cursors, provider, source, country):
    """Every partition offset currently stored on this cursor row."""
    row = cursors.get(provider=provider, source=source, country=country)
    return _decode_cursor(row.cursor if row else "")


def _write_cursor_map(cursors, provider, source, country, snapshot, *,
                      partition_id):
    """Write the partition map back, advancing exactly one entry.

    ``snapshot`` is the map as it stood before this partition's fetch. The
    source's own cursor — already persisted by the refresh service — supplies
    this partition's new offset; every sibling entry is taken from the snapshot
    and is never touched. That is the whole invariant:

        a catalogue walk may advance exactly one partition,
        but it must never erase progress belonging to another.
    """
    row = cursors.get(provider=provider, source=source, country=country)
    merged = dict(snapshot)

    incoming = _decode_cursor(row.cursor if row else "")
    if partition_id in incoming:
        merged[partition_id] = incoming[partition_id]
    elif row is not None and row.cursor and str(row.cursor).isdigit():
        # A legacy bare offset, or a source that was never told its partition id.
        merged[partition_id] = str(row.cursor)

    return cursors.set(provider, source, country, _encode_cursor(merged))


def refresh_justwatch_partitions(*, partitions=None,
                                max_partitions=DEFAULT_MAX_PARTITIONS,
                                content_type=ContentType.MOVIE,
                                source=None, refresh_service=None,
                                pages_per_partition=1,
                                year_start=None, year_end=None,
                                year_window=None, availability_refreshes=()):
    """Walk JustWatch catalogue partitions, resuming each from its cursor.

    ``max_partitions=None`` means "no further cap": the caller has already
    limited what it asked for (the management command filters first, then caps),
    and truncating an already-filtered list here would be a second, invisible
    limit.

    ``pages_per_partition`` bounds how many upstream pages one partition visit
    walks. ``year_start``/``year_end``/``year_window`` opt into year
    partitioning; without them the plan is exactly what it was before, so the
    default invocation walks the same partitions in the same order.

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

    # Concurrency
    # -----------
    # Snapshot/merge/write is only safe against one writer. The generic
    # catalogue scheduler already serialises per source with a Postgres session
    # advisory lock (see ``bulk_refresh._try_source_lock``); the same lock is
    # taken here so an overlapping external cron tick cannot interleave two
    # coordinators and drop one partition's progress. The lock is per source
    # slug, so different sources never block one another, and it is released in
    # a ``finally`` (and automatically if the connection dies).
    owns_lock = try_source_lock(source.slug)
    if not owns_lock:
        logger.info(
            "justwatch_partitions_locked source=%s "
            "another coordinator is already running; skipping",
            source.slug,
        )
        return {
            "status": STATUS_LOCKED,
            "attempted": 0, "refreshed": 0, "errors": 0, "skipped": 0,
            "partial": 0, "results": [], "plan": [], "partitions": [],
            "succeeded_partitions": [], "failed_partitions": [],
            "skipped_partitions": [], "locked": True,
        }
    try:
        return _run_partitions(
            partitions=partitions, max_partitions=max_partitions,
            content_type=content_type, source=source,
            refresh_service=refresh_service, pages_per_partition=pages_per_partition,
            year_start=year_start, year_end=year_end, year_window=year_window,
            availability_refreshes=availability_refreshes,
        )
    finally:
        release_source_lock(source.slug)


def _run_partitions(*, partitions, max_partitions, content_type, source,
                    refresh_service, pages_per_partition, year_start, year_end,
                    year_window, availability_refreshes=()):
    """The walk itself, under the caller's per-source lock."""
    service = refresh_service or AvailabilityRefreshService(source=source)
    cursors = CursorRepository()

    if partitions is None:
        base_plan = available_partitions(
            max_partitions=max_partitions
            if max_partitions is not None else DEFAULT_MAX_PARTITIONS
        )
    elif max_partitions is None:
        # Caller-supplied and already capped: use it verbatim.
        base_plan = [(str(country).upper(), str(slug)) for country, slug in partitions]
    else:
        # Caller-supplied with a cap: truncate, but never re-derive the plan.
        base_plan = [
            (str(country).upper(), str(slug))
            for country, slug in list(partitions)[:max_partitions]
        ]

    # Year windows multiply each (country, provider) pair into one partition per
    # window. Offsets are per-partition, so a window gets its own bounded stream
    # instead of competing with every other window for the same offsets — which
    # is what removes the offset ceiling as a hard coverage stop.
    windows = (
        year_windows(year_start, year_end, year_window)
        if year_start is not None and year_end is not None
        else [(None, None)]
    )
    plan = []
    for country_code, provider_slug in base_plan:
        for min_year, max_year in windows:
            plan.append(_Partition(
                country=country_code,
                provider=provider_slug,
                content_type=content_type,
                min_year=min_year,
                max_year=max_year,
            ))

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
    for partition in plan:
        country_code = partition.country
        provider_slug = partition.provider
        partition_id = _partition_id(
            country=country_code,
            provider=provider_slug,
            content_type=partition.content_type,
            min_year=partition.min_year,
            max_year=partition.max_year,
        )
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

        # Keyed by partition identity, so a MOVIE walk, a SERIES walk and each
        # year window each keep their own offset instead of clobbering one
        # shared row.
        cursor = _cursor_for(cursors, provider, source_row, country,
                             partition_id=partition_id)
        # Snapshot the *whole* map before the fetch. The service below writes
        # the source's single-entry cursor straight onto this row, which
        # overwrites every sibling partition's offset; the snapshot is what
        # makes it possible to put them back afterwards.
        had_cursor_row = cursors.get(
            provider=provider, source=source_row, country=country) is not None
        snapshot = _read_cursor_map(cursors, provider, source_row, country)
        if cursor == CURSOR_EXHAUSTED_MARKER:
            # Partition walked past the API's boundary last time. Start it over
            # now, so a partition is never permanently parked as "finished".
            logger.info("justwatch_partition_reset country=%s provider=%s",
                        country_code, provider_slug)
            cursor = ""

        result = {
            "country": country_code,
            "provider": provider_slug,
            "content_type": partition.content_type,
            "partition": partition_id,
            "min_year": partition.min_year,
            "max_year": partition.max_year,
            "discovered": 0,
            "created": 0,
            "updated": 0,
            "status": STATUS_SUCCEEDED,
            "cursor_in": cursor,
        }
        try:
            # ``pages`` is only forwarded to a service that declares it, so an
            # injected test double with the older signature keeps working.
            catalogue_kwargs = {
                "provider": provider_slug,
                "country": country_code,
                "cursor": {
                    "offset": _as_offset(cursor),
                    "content_type": partition.content_type,
                    "min_year": partition.min_year,
                    "max_year": partition.max_year,
                    "partition": partition_id,
                },
            }
            if accepts_kwarg(service.refresh_catalogue, "pages"):
                catalogue_kwargs["pages"] = pages_per_partition
            outcome = service.refresh_catalogue(**catalogue_kwargs)
            if availability_refreshes:
                # Every partition this walk emitted belongs to the same set of
                # refreshes that asked for the coalesced marker - which is why
                # this takes a set rather than one parent. Diagnostic only.
                attribute_sync_runs(
                    getattr(outcome, "sync_runs", ()) or (), availability_refreshes
                )
        except Exception as exc:  # noqa: BLE001 - one partition must not stop the rest
            logger.exception(
                "justwatch_partition_failed country=%s provider=%s error=%s",
                country_code, provider_slug, exc,
            )
            result["status"] = STATUS_FAILED
            result["error"] = f"{type(exc).__name__}: {exc}"
            # A failure must not invent progress, and must not disturb anything
            # either. The row is rewritten from the snapshot so a source that
            # wrote a partial cursor before failing cannot leak into the stored
            # map, and the failed partition keeps the offset it had — which is
            # what makes it retryable from exactly where it stopped.
            # A failure must not invent progress, and must not manufacture a
            # cursor row that never existed. If the source left a partial write
            # behind, the row is restored to the snapshot; otherwise any row it
            # created is removed. Either way the failed partition is retryable
            # from exactly the offset it had, and no sibling is disturbed.
            if had_cursor_row:
                _write_cursor_map(
                    cursors, provider, source_row, country,
                    snapshot, partition_id=partition_id,
                )
            else:
                cursors.delete(
                    provider=provider, source=source_row, country=country)
            results.append(result)
            continue

        result["discovered"] = outcome.discovered
        result["created"] = outcome.created
        result["updated"] = outcome.updated

        # Restore the siblings. The service persisted the source's cursor as
        # the row's whole value, so without this every partition other than
        # this one would silently restart from offset 0.
        _write_cursor_map(
            cursors, provider, source_row, country,
            snapshot, partition_id=partition_id,
        )
        # ``RefreshResult`` does not carry the paging cursor; the refresh service
        # persists it on the SyncCursor row it just wrote. An empty value there
        # means the source hit the API's depth boundary, i.e. this partition is
        # complete for now.
        # The refresh service wrote the *next* offset into the SyncCursor row.
        # Re-reading it here is per-partition, so a sibling partition's offset
        # is never reported as this one's progress.
        persisted = _cursor_for(cursors, provider, source_row, country,
                                partition_id=partition_id)
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
        # Unchanged shape: (country, provider) pairs, as before. P8 partitions
        # by year and content type too, but that detail is in ``partitions`` so
        # existing readers of ``plan`` keep working.
        "plan": [list((country, provider)) for country, provider, *_ in plan],
        "partitions": [
            {
                "country": country,
                "provider": provider,
                "content_type": str(content_type),
                "min_year": min_year,
                "max_year": max_year,
                "partition_id": _partition_id(
                    country=country, provider=provider,
                    content_type=content_type,
                    min_year=min_year, max_year=max_year,
                ),
            }
            for country, provider, content_type, min_year, max_year in plan
        ],
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