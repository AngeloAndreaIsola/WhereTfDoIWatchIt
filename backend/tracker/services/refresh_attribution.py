"""Attribution: link executions to the refreshes that requested them.

Read-only for every runtime decision. Nothing here is consulted when choosing
sources, counting quota, scoring offers or classifying a title - it exists so an
operator can answer "which refresh produced this execution?" from the database
instead of from worker logs.

The association is deliberately many-to-many because the queues coalesce:

  * ``request_catalogue_refresh`` merges repeat requests into one pending
    marker, so two refreshes can share a marker and therefore a ``RefreshRun``;
  * a title-drain tick writes one ``RefreshRun`` while processing
    ``RefreshRequest`` rows from any number of batches.

Every write is idempotent (``get_or_create`` against a unique constraint), so
re-running propagation - or a retry after a partial failure - converges instead
of duplicating links.
"""
from __future__ import annotations

from django.db import transaction

from tracker.models import (
    CatalogueRefreshAvailabilityRefresh,
    RefreshRunAvailabilityRefresh,
    SyncRunAvailabilityRefresh,
)

__all__ = [
    "attribute_refresh_run",
    "attribute_sync_runs",
    "attribute_catalogue_marker",
    "refreshes_for_marker",
    "refreshes_for_refresh_run",
    "refreshes_for_sync_run",
]


@transaction.atomic
def attribute_refresh_run(refresh_run, refreshes) -> int:
    """Link one ``RefreshRun`` to every refresh that requested its work.

    Returns the number of links newly created (0 on a repeat call).
    """
    created = 0
    for refresh in _distinct(refreshes):
        _, made = RefreshRunAvailabilityRefresh.objects.get_or_create(
            refresh_run=refresh_run, availability_refresh=refresh
        )
        created += int(made)
    return created


@transaction.atomic
def attribute_sync_runs(sync_runs, refreshes) -> int:
    """Link a group of ``SyncRun`` rows to the same set of refreshes.

    A partitioned catalogue walk emits one SyncRun per partition, so all of them
    belong to the refreshes that requested the marker.
    """
    targets = list(sync_runs)
    resolved = _distinct(refreshes)
    created = 0
    for sync_run in targets:
        for refresh in resolved:
            _, made = SyncRunAvailabilityRefresh.objects.get_or_create(
                sync_run=sync_run, availability_refresh=refresh
            )
            created += int(made)
    return created


@transaction.atomic
def attribute_catalogue_marker(marker, refreshes) -> int:
    """Record which refreshes asked for a coalesced marker.

    Called at enqueue time. A second request for the same source while the
    marker is still pending *adds* an association; it never replaces the first
    one, because the marker genuinely represents both.
    """
    created = 0
    for refresh in _distinct(refreshes):
        _, made = CatalogueRefreshAvailabilityRefresh.objects.get_or_create(
            marker=marker, availability_refresh=refresh
        )
        created += int(made)
    return created


def refreshes_for_marker(marker):
    """The refreshes a claimed catalogue marker represents."""
    return [
        link.availability_refresh
        for link in CatalogueRefreshAvailabilityRefresh.objects.filter(
            marker=marker
        ).select_related("availability_refresh")
    ]


def refreshes_for_refresh_run(refresh_run):
    return [
        link.availability_refresh
        for link in RefreshRunAvailabilityRefresh.objects.filter(
            refresh_run=refresh_run
        ).select_related("availability_refresh")
    ]


def refreshes_for_sync_run(sync_run):
    return [
        link.availability_refresh
        for link in SyncRunAvailabilityRefresh.objects.filter(
            sync_run=sync_run
        ).select_related("availability_refresh")
    ]


def _distinct(refreshes):
    """Deduplicate by pk while tolerating ``None`` and raw ids.

    Callers may hold model instances or pks (or a mix), and ``None`` must be
    silently dropped: standalone work - an operator command, a scheduled sync -
    has no parent refresh, and manufacturing one would be a false claim.
    """
    seen = set()
    out = []
    for item in refreshes or ():
        if item is None:
            continue
        pk = getattr(item, "pk", item)
        if pk in seen:
            continue
        seen.add(pk)
        out.append(item)
    return out
