"""Fail closed for legacy execution; parser registration is not a grant.

Only the pre-existing named broadcaster paths are outside unofficial discovery
policy. No category, domain, provider flag or caller configuration can opt a
source into that exception. This does not grant rights to any new source.
"""
from types import SimpleNamespace

from .discovery_policy import DiscoveryPolicyError, permission_for


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
        if self.code.startswith("source_") or self.code == "legacy_request_envelope_unavailable":
            return f"Legacy scraper execution blocked for '{self.provider_slug}': {self.code}."
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


# Preserve separately managed broadcaster functionality. New sources must use
# the reviewed discovery transport, never this legacy Requests transport.
_EXISTING_BROADCASTER_PATHS = frozenset({"raiplay", "nhk_world"})


def require_legacy_execution(provider):
    """Authorize before scraper dispatch, HTTP, TMDb matching or match writes.

    A discovery grant cannot authorize legacy adapters: they have implicit
    redirects/retries and mirror fallback outside its request envelope. Even a
    future verified grant must use the guarded discovery entry point instead.
    """
    if isinstance(provider, str):
        provider = SimpleNamespace(slug=provider, is_enabled=True, config={})
    slug = (provider.slug or "").strip().lower()
    if not provider.is_enabled:
        raise ScraperExecutionIneligible(code="source_disabled", provider_slug=slug)
    if slug in _EXISTING_BROADCASTER_PATHS:
        return
    config = provider.config if isinstance(provider.config, dict) else {}
    try:
        if config.get("discovery_permission") == "DENIED":
            raise DiscoveryPolicyError("source_permission_denied")
        permission_for(SimpleNamespace(slug=slug)).require(slug)
    except DiscoveryPolicyError as exc:
        raise ScraperExecutionIneligible(code=str(exc), provider_slug=slug) from None
    raise ScraperExecutionIneligible(
        code="legacy_request_envelope_unavailable", provider_slug=slug
    )
