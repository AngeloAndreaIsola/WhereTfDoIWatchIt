"""StreamingCommunity scraper with multi-domain support.

The site moved from ``/en/...`` paths to bare paths and now serves image-only
tiles; titles are in ``data-title`` attributes. The legacy primary domain is
Cloudflare-blocked, so the current working domain is tried first.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlencode, urljoin

from tracker.scrapers.base import BaseScraper, ScrapedItem
from tracker.scrapers.listing import items_from_listing

logger = logging.getLogger(__name__)

# Verified working domains (ordered by preference). The legacy .nexus domain
# redirects to a Cloudflare-protected host and stays last as a fallback only.
VERIFIED_DOMAINS = [
    "streaming-community.army",
    "streamingcommunityz.nexus",
]

# Live paths. The old /en/browse/latest endpoints now return 404.
BROWSE_PATHS = [
    ("/", "movie"),
    ("/film/", "movie"),
    ("/series/", "series"),
    ("/archivio/", "movie"),
]

# Kept for callers that used the old language helper.
AUDIO_LANG_PATTERNS = {
    "it": [r"\[ITA\]", r"\bITA\b", r"Italian", r"Italiano"],
    "en": [r"\[ENG\]", r"\bENG\b", r"English", r"Original"],
    "ja": [r"\[JAP\]", r"\bJAP\b", r"Japanese", r"Giapponese"],
}
SUB_LANG_PATTERNS = {
    "it": [r"\[SUB-ITA\]", r"\[Sub-ITA\]", r"SUB ITA", r"Sub ITA"],
    "en": [r"\[SUB-ENG\]", r"\[Sub-ENG\]", r"SUB ENG", r"Sub ENG"],
    "ja": [r"\[SUB-JAP\]", r"Sub JAP"],
}


def detect_languages(text: str, patterns: dict[str, list[str]]) -> list[str]:
    """Detect languages from text using pattern matching."""
    found = set()
    for lang, pat_list in patterns.items():
        for pat in pat_list:
            if re.search(pat, text or "", re.IGNORECASE):
                found.add(lang)
                break
    return list(found)


class DomainRegistry:
    """Manages verified domains with health tracking."""

    def __init__(self):
        self.domains = VERIFIED_DOMAINS.copy()
        self.health = {d: {"failures": 0, "last_success": None} for d in self.domains}
        self.current_index = 0

    def get_active_domain(self) -> str:
        return self.domains[self.current_index] if self.domains else ""

    def get_fallback_domains(self, exclude: str = "") -> list[str]:
        return [d for d in self.domains if d != exclude]

    def mark_success(self, domain: str):
        import time

        self.health[domain]["failures"] = 0
        self.health[domain]["last_success"] = time.time()
        if domain in self.domains:
            self.domains.remove(domain)
            self.domains.insert(0, domain)
            self.current_index = 0

    def mark_failure(self, domain: str):
        self.health[domain]["failures"] += 1
        if self.health[domain]["failures"] >= 3:
            if domain in self.domains:
                self.domains.remove(domain)
                self.domains.append(domain)
                self.current_index = 0


DOMAIN_REGISTRY = DomainRegistry()


class StreamingCommunityScraper(BaseScraper):
    """Scraper for StreamingCommunity with multi-domain fallback."""

    provider_slug = "streamingcommunity"
    timeout = 15
    max_retries = 1

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.domain_registry = DOMAIN_REGISTRY

    def _get_base_url(self, domain: Optional[str] = None) -> str:
        return f"https://{domain or self.domain_registry.get_active_domain()}"

    def _try_domains(self, path: str, params: Optional[dict] = None) -> tuple[Optional[str], Optional[str]]:
        """Try fetching from domains with fallback. Returns (html, domain)."""
        primary = self.domain_registry.get_active_domain()
        domains_to_try = [primary] + self.domain_registry.get_fallback_domains(primary)

        for domain in domains_to_try:
            if not domain:
                continue
            url = urljoin(self._get_base_url(domain), path)
            if params:
                url += "?" + urlencode(params)

            logger.info("StreamingCommunity: trying %s", url)
            resp = self.fetch_url(url)
            if resp and isinstance(resp, str):
                if "cf-mitigated" in resp or "Attention Required! | Cloudflare" in resp:
                    logger.warning("StreamingCommunity: Cloudflare challenge on %s", domain)
                    self.domain_registry.mark_failure(domain)
                    continue
                self.domain_registry.mark_success(domain)
                return resp, domain
            self.domain_registry.mark_failure(domain)

        return None, None

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        query_filter = query.lower() if query else None

        for path, content_type in BROWSE_PATHS:
            html, domain = self._try_domains(path)
            if not html or not domain:
                continue
            items.extend(
                items_from_listing(
                    html,
                    href_regex=r"/titles/\d+-",
                    provider_slug=self.provider_slug,
                    base_url=self._get_base_url(domain),
                    default_content_type=content_type,
                    query_filter=query_filter,
                    source_label="listing",
                )
            )

        return self._dedupe(items)

    def _dedupe(self, items: list[ScrapedItem]) -> list[ScrapedItem]:
        seen = set()
        deduped = []
        for item in items:
            title_id = item.raw_payload.get("title_id") if isinstance(item.raw_payload, dict) else None
            key = title_id or item.source_url
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped
