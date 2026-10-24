"""Provider-scoped scraper execution, shared by the scraper endpoints.

One provider, one scraper: the registered class when the slug has one,
otherwise the generic URL adapter (how importer-created sources without a
dedicated adapter still run). Both ``POST /api/scrapers/run/`` and the
per-source refresh endpoint ``POST /api/providers/<slug>/refresh/`` execute
scrapers through this module, so a "refresh this source" press never
duplicates the execution logic and always leaves the same operational fields
behind: ``Provider.last_scraped_at`` plus one ``ScrapedContentMapping`` row
per item, bridged onto the canonical catalogue with
``ingest_scraped_mapping``.

Running a scraper here is *synchronous by design* — it is the established
behaviour of ``/api/scrapers/run/`` and there is no scraper queue in the
system. Callers track their own pending state while the request is in
flight; availability sources (catalogue collectors) go through the queued
``CatalogueRefreshRequest`` path instead.
"""
from __future__ import annotations

from django.utils import timezone

from ..models import Provider
from ..scrapers import resolve_scraper_class
from ..scrapers.adapters import GenericURLAdapter
from .scraped_mapping import persist_scraped_item


def run_provider_scraper(provider: Provider, query: str | None = None) -> list:
    """Run *provider*'s scraper, persist its results, return scraped items.

    ``query`` narrows the run when the caller has one (the import flow passes
    the URL-derived title); ``None`` runs the scraper's default full pass.
    """
    scraper_cls = resolve_scraper_class(provider.slug)
    if scraper_cls:
        scraper = scraper_cls()
    else:
        scraper = GenericURLAdapter(
            provider_slug=provider.slug, domain=provider.domain or "example.com"
        )

    items = scraper.run(query=query)

    provider.last_scraped_at = timezone.now()
    provider.save()

    for item in items:
        persist_scraped_item(provider=provider, item=item)

    return items
