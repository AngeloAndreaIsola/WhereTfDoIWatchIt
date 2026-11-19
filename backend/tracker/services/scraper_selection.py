"""The single policy boundary for legacy scraper execution.

Registration exposes parsers only; execution requires a separate policy check.  A missing registration is
*not* permission to run :class:`GenericURLAdapter`: a provider with a canonical
catalogue collector must use that collector, and a named legacy scraper that is
retired must fail explicitly.  Generic crawling remains available only to
genuinely imported ``category='scraper'`` providers.

All HTTP scraper entry points and ``scraper_runner`` use this module.  Keep
new execution paths out of the registry/adapter modules so this rule cannot be
bypassed by resolving a class directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Provider, ProviderCategory
from ..scrapers import resolve_scraper_class
from ..scrapers.adapters import GenericURLAdapter
from .provider_status import catalogue_source_map
from .scraper_policy import ScraperExecutionIneligible, require_legacy_execution


# These are named legacy implementations that may be removed from
# SCRAPER_REGISTRY in a later, separately-approved retirement.  Listing them
# now makes an accidental registry removal fail closed rather than turning an
# official broadcaster into a speculative ``/api/search`` crawl.
LEGACY_SCRAPER_FALLBACK_DENYLIST = frozenset({
    "bbc_iplayer",
    "nhk_world",
    "raiplay",
})

# These modules parse caller-supplied, validated video URLs for metadata only.
# They are deliberately absent from the provider registry and cannot fall
# through to a generic catalogue crawl.
DISCOVERY_ONLY_SCRAPERS = frozenset({"bilibili_tv", "ok_ru"})


@dataclass(frozen=True)
class ScraperSelection:
    """Selected concrete scraper plus the reason it is eligible."""

    scraper: object
    kind: str  # ``registered`` or ``generic``; useful for diagnostics/tests.


def select_provider_scraper(provider: Provider) -> ScraperSelection:
    """Return the only scraper eligible to run for *provider*.

    Registered parsers do not confer execution permission.  If one is
    absent, canonical catalogue ownership and the explicit denylist are checked
    before the generic adapter.  This ordering is the retirement safety rule.
    """
    slug = (provider.slug or "").strip().lower()
    if slug in DISCOVERY_ONLY_SCRAPERS:
        raise ScraperExecutionIneligible(
            code="discovery_only_provider", provider_slug=slug
        )
    # Known unofficial slugs cannot escape policy through category changes or
    # registry removal. Unknown imported scraper providers are checked below.
    from .discovery_policy import UNOFFICIAL_PROVIDERS
    if slug in UNOFFICIAL_PROVIDERS:
        require_legacy_execution(provider)
    scraper_class = resolve_scraper_class(slug)
    if scraper_class is not None:
        require_legacy_execution(provider)
        return ScraperSelection(scraper=scraper_class(), kind="registered")

    source_slug = catalogue_source_map().get(slug)
    if source_slug:
        raise ScraperExecutionIneligible(
            code="canonical_catalogue_provider",
            provider_slug=slug,
            source_slug=source_slug,
        )
    if slug in LEGACY_SCRAPER_FALLBACK_DENYLIST:
        raise ScraperExecutionIneligible(
            code="retired_named_scraper", provider_slug=slug
        )
    if provider.category == ProviderCategory.SCRAPER:
        require_legacy_execution(provider)
        return ScraperSelection(
            scraper=GenericURLAdapter(
                provider_slug=provider.slug, domain=provider.domain or "example.com"
            ),
            kind="generic",
        )
    raise ScraperExecutionIneligible(code="scraper_not_available", provider_slug=slug)
