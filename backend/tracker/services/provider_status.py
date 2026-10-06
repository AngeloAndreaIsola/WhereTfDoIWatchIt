"""Per-provider card status for the subscriptions page.

``GET /api/providers/`` feeds the square source cards of all three sections
(SVOD, BVOD, imported), so every operational field those cards render — last
run, available items, refresh target, refreshing state — must arrive in that
one response instead of one query (or HTTP request) per card. Everything here
is therefore computed for the whole provider list in a fixed set of aggregate
queries and handed to ``ProviderSerializer`` through the view context.

Serializers built without that context (nested imports, the v2 catalogue)
fall back to :func:`minimal_card_status`, which only costs registry lookups —
no database work — so those endpoints never pay for fields they don't use.

Refresh targets are registry-driven, never a slug list hardcoded in a view:

- ``catalogue`` — a registered availability source whose capabilities declare
  ``supports_catalogue_sync`` *and* name this provider; refreshing it
  enqueues the existing ``CatalogueRefreshRequest`` marker the
  ``refresh_worker`` drains through
  ``process_catalogue_refresh(source_slug=...)`` under the per-source
  advisory lock;
- ``scraper`` — the provider has a scraper class (or is a scraper-category
  row, which falls back to the generic URL adapter); refreshing it runs that
  provider's scraper only, through the same execution path
  ``/api/scrapers/run/`` uses;
- ``None`` — no collector exists for the provider (Netflix's offers only come
  from per-title streaming-availability checks, for example), so the card
  offers no refresh control instead of one that could never run.

``available_items`` follows the same ownership: the collector that refreshes
the provider owns the count. Catalogue sources report their canonical
``ProviderContent`` rows (falling back to active ``Offer`` rows for a source
that writes offers only), scrapers report their ``ScrapedContentMapping``
rows, and providers with no dedicated collector report the active canonical
offers the default title-targeted scope has written for them.

``last_run`` is the execution time of that same collector:

- catalogue: the newest ``RefreshRun(service=source)`` audit row — the row
  every catalogue refresh writes whether it succeeded or failed;
- scraper: ``Provider.last_scraped_at`` (what the scraper itself records);
- otherwise: the newest successful ``SyncRun`` the default scope wrote for
  *this provider* — per-provider evidence rather than a shared timestamp.

``refreshing`` is queue/worker state, never derived client-side: a provider
is refreshing while its source sits in a pending ``CatalogueRefreshRequest``
marker or while that source's newest ``RefreshRun`` has not finished yet
(a crashed run self-heals as soon as a later run finishes). Active/inactive
is a completely separate concept (the ``Subscription`` row) and is never
touched here.
"""
from __future__ import annotations

from datetime import datetime

from django.db.models import Count, Max

from availability.domain.enums import SyncStatus
from availability.models import Offer, ProviderContent, SyncRun
from availability.sources import DEFAULT_SOURCE_SLUG, source_registry

from ..models import (
    CatalogueRefreshRequest,
    CatalogueRefreshStatus,
    Provider,
    ProviderCategory,
    RefreshRun,
    ScrapedContentMapping,
)
from ..scrapers import resolve_scraper_class

CATALOGUE = "catalogue"
SCRAPER = "scraper"


def catalogue_source_map() -> dict[str, str]:
    """Map provider slug (lower-cased) -> catalogue source slug.

    Built from source capabilities only — the registry owns implementations,
    capabilities own scope, and no database row is consulted — so the view
    never hardcodes which provider belongs to which collector.
    """
    mapping: dict[str, str] = {}
    for source_slug in source_registry.slugs():
        capabilities = source_registry.get(source_slug).capabilities
        if not capabilities.supports_catalogue_sync:
            continue
        for provider_slug in capabilities.supported_providers or ():
            mapping[str(provider_slug).lower()] = source_slug
    return mapping


def refresh_target(
    provider: Provider, source_map: dict[str, str] | None = None
) -> tuple[str | None, str | None]:
    """Resolve ``(refresh_kind, source_slug)`` for one provider.

    Catalogue sources win over scrapers when a provider has both (RaiPlay and
    BBC iPlayer do): the catalogue source is the canonical, queue-backed
    collector. Returns ``(None, None)`` when no collector exists.
    """
    if source_map is None:
        source_map = catalogue_source_map()
    source_slug = source_map.get((provider.slug or "").lower())
    if source_slug:
        return CATALOGUE, source_slug
    if resolve_scraper_class(provider.slug) or provider.category == ProviderCategory.SCRAPER:
        return SCRAPER, None
    return None, None


def minimal_card_status(provider: Provider) -> dict:
    """Registry-only status for serializers without a batched context.

    Fields that need aggregate queries are reported empty rather than
    computed one query at a time: the endpoints that serialize a provider
    outside ``GET /api/providers/`` (imports, the v2 catalogue) never render
    these cards, and paying N aggregates there would reintroduce exactly the
    per-card cost this module exists to remove.
    """
    refresh_kind, source_slug = refresh_target(provider)
    return {
        "refresh_kind": refresh_kind,
        "source_slug": source_slug,
        "last_run": provider.last_scraped_at if refresh_kind == SCRAPER else None,
        "last_run_error": "",
        "available_items": 0,
        "refreshing": False,
    }


def _pending_catalogue_sources() -> set[str]:
    """Sources queued on the worker's single pending catalogue marker."""
    pending: set[str] = set()
    markers = CatalogueRefreshRequest.objects.filter(
        status=CatalogueRefreshStatus.PENDING
    ).values_list("sources", flat=True)
    for sources in markers:
        pending.update(sources or [])
    return pending


def _catalogue_run_status(source_slugs: set[str]) -> dict[str, dict]:
    """Per-source execution state from the ``RefreshRun`` audit rows.

    One bounded query over the sources' own rows, newest first:

    - ``running``: the *newest* row has not finished (a crashed row self-heals
      as soon as a later run finishes, unlike "any unfinished row");
    - ``last_run``: newest successfully finished row;
    - ``error``: error of the newest finished row, so a failed run is visible
      without hiding the last success that preceded it.
    """
    status = {
        slug: {"last_run": None, "running": False, "error": ""}
        for slug in source_slugs
    }
    if not source_slugs:
        return status

    # Reconciled runs say a worker died, not that the source failed.
    rows = (
        RefreshRun.objects.filter(
            service__in=source_slugs, abandoned_at__isnull=True
        )
        .order_by("-started_at")
        .values_list("service", "finished_at", "error")
    )
    seen_newest: set[str] = set()
    seen_finished: set[str] = set()
    for service, finished_at, error in rows:
        entry = status[service]
        if service not in seen_newest:
            seen_newest.add(service)
            entry["running"] = finished_at is None
        if finished_at is None:
            continue
        if service not in seen_finished:
            # First (i.e. newest) finished row: its error is the current one.
            seen_finished.add(service)
            entry["error"] = error
        if entry["last_run"] is None and not error:
            entry["last_run"] = finished_at
    return status


def _default_scope_last_runs(provider_ids: list[int]) -> dict[int, datetime]:
    """Newest successful default-scope sync per provider (aggregate)."""
    rows = (
        SyncRun.objects.filter(
            provider_id__in=provider_ids,
            source__slug=DEFAULT_SOURCE_SLUG,
            status=SyncStatus.SUCCEEDED,
            completed_at__isnull=False,
        )
        .values("provider_id")
        .annotate(last=Max("completed_at"))
    )
    return {row["provider_id"]: row["last"] for row in rows}


def _counts(queryset) -> dict[int, int]:
    """One grouped count keyed by provider id."""
    return {
        row["provider_id"]: row["n"]
        for row in queryset.values("provider_id").annotate(n=Count("id"))
    }


def provider_card_status(providers) -> dict[int, dict]:
    """Batched card status for every given provider.

    Fixed query count regardless of provider count: refresh targets come from
    the registry, run state from the ``RefreshRun`` audit rows, queued state
    from the pending marker, counts from three grouped aggregates — never one
    query per card.
    """
    providers = list(providers)
    if not providers:
        return {}

    source_map = catalogue_source_map()
    targets = {p.id: refresh_target(p, source_map) for p in providers}
    catalogue_sources = {
        source_slug
        for kind, source_slug in targets.values()
        if kind == CATALOGUE
    }
    provider_ids = [p.id for p in providers]

    run_status = _catalogue_run_status(catalogue_sources)
    pending_sources = _pending_catalogue_sources()

    plain_ids = [pid for pid, (kind, _src) in targets.items() if kind is None]
    default_last_runs = (
        _default_scope_last_runs(plain_ids) if plain_ids else {}
    )

    content_counts = _counts(
        ProviderContent.objects.filter(
            provider_id__in=provider_ids, currently_available=True
        )
    )
    offer_counts = _counts(
        Offer.objects.filter(provider_id__in=provider_ids, currently_available=True)
    )
    scraped_counts = _counts(
        ScrapedContentMapping.objects.filter(
            provider_id__in=provider_ids, is_available=True
        )
    )

    status: dict[int, dict] = {}
    for provider in providers:
        kind, source_slug = targets[provider.id]
        if kind == CATALOGUE:
            run = run_status.get(source_slug, {})
            status[provider.id] = {
                "refresh_kind": CATALOGUE,
                "source_slug": source_slug,
                "last_run": run.get("last_run"),
                "last_run_error": run.get("error", ""),
                "available_items": content_counts.get(provider.id, 0)
                or offer_counts.get(provider.id, 0),
                "refreshing": bool(run.get("running"))
                or source_slug in pending_sources,
            }
        elif kind == SCRAPER:
            status[provider.id] = {
                "refresh_kind": SCRAPER,
                "source_slug": None,
                "last_run": provider.last_scraped_at,
                "last_run_error": "",
                "available_items": scraped_counts.get(provider.id, 0),
                # Scraper runs are synchronous (the request waits for them),
                # so pending state is tracked by the caller that pressed the
                # button; there is no queue row to read here.
                "refreshing": False,
            }
        else:
            status[provider.id] = {
                "refresh_kind": None,
                "source_slug": None,
                "last_run": default_last_runs.get(provider.id),
                "last_run_error": "",
                "available_items": offer_counts.get(provider.id, 0),
                "refreshing": False,
            }
    return status
