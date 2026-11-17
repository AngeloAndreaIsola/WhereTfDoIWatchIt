"""The single policy boundary for legacy scraper execution.

Named legacy scrapers are allowed while registered.  A missing registration is
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


class ScraperExecutionIneligible(Exception):
    """Stable, client-safe refusal to execute a scraper for one provider."""

    def __init__(self, *, code: str, provider_slug: str, source_slug: str | None = None):
        self.code = code
        self.provider_slug = provider_slug
        self.source_slug = source_slug
        super().__init__(self.message)

    @property
    def message(self) -> str:
        if self.code == "canonical_catalogue_provider":
            return (
                f"Scraper execution is unavailable for '{self.provider_slug}'; "
                f"use canonical catalogue source '{self.source_slug}'."
            )
        if self.code == "retired_named_scraper":
            return f"Scraper execution is unavailable for retired provider '{self.provider_slug}'."
        if self.code == "discovery_only_provider":
            return (
                f"Catalogue scraping is unavailable for discovery-only provider "
                f"'{self.provider_slug}'."
            )
        return (
            f"No collector is registered for '{self.provider_slug}'; "
            "no eligible scraper is available."
        )

    def payload(self) -> dict:
        """HTTP-safe error schema shared by every scraper endpoint."""
        alternative = None
        if self.source_slug:
            alternative = {"kind": "catalogue", "source": self.source_slug}
        return {
            "code": self.code,
            "error": self.message,
            "provider": self.provider_slug,
            "alternative": alternative,
        }


@dataclass(frozen=True)
class ScraperSelection:
    """Selected concrete scraper plus the reason it is eligible."""

    scraper: object
    kind: str  # ``registered`` or ``generic``; useful for diagnostics/tests.


def select_provider_scraper(provider: Provider) -> ScraperSelection:
    """Return the only scraper eligible to run for *provider*.

    Registered implementations retain their current behaviour.  If one is
    absent, canonical catalogue ownership and the explicit denylist are checked
    before the generic adapter.  This ordering is the retirement safety rule.
    """
    slug = (provider.slug or "").strip().lower()
    if slug in DISCOVERY_ONLY_SCRAPERS:
        raise ScraperExecutionIneligible(
            code="discovery_only_provider", provider_slug=slug
        )
    scraper_class = resolve_scraper_class(slug)
    if scraper_class is not None:
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
        return ScraperSelection(
            scraper=GenericURLAdapter(
                provider_slug=provider.slug, domain=provider.domain or "example.com"
            ),
            kind="generic",
        )
    raise ScraperExecutionIneligible(code="scraper_not_available", provider_slug=slug)
