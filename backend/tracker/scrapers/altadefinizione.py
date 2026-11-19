"""AltaDefinizione scraper.

Live listing tiles are image-only; titles, years and content type are in
``data-title`` / ``data-year`` / ``data-kind`` attributes. The old ``traduzione``
filter URLs are ignored by the current site, so they are no longer requested.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from tracker.scrapers.base import BaseScraper, ScrapedItem
from tracker.scrapers.listing import dedupe_items, items_from_listing

logger = logging.getLogger(__name__)

# The legacy .fit domain redirects here and is kept as a fallback only.
VERIFIED_DOMAINS = [
    "altadefinizionex.ink",
    "altadefinizionex.fit",
]

BROWSE_PATHS = [
    ("/", "movie"),
    ("/film/", "movie"),
    ("/serie-tv/", "series"),
    ("/archivio/", "movie"),
]

HREF_PATTERN = r"/\d+-.*-streaming\.html$"

LANG_PATTERNS = {
    "audio": {
        "it": [r"\bITA\b", r"Italian", r"Italiano", r"Doppiato"],
        "en": [r"\bENG\b", r"English", r"Originale"],
        "ja": [r"\bJAP\b", r"Japanese", r"Giapponese"],
    },
    "sub": {
        "it": [r"SUB-ITA", r"Sub ITA", r"Sottotitoli Italiano"],
        "en": [r"SUB-ENG", r"Sub ENG", r"Sottotitoli Inglese"],
        "ja": [r"SUB-JAP", r"Sub JAP"],
    },
}


def detect_languages(text: str, patterns: dict[str, list[str]]) -> list[str]:
    """Detect languages from text using pattern matching."""
    found = set()
    text_upper = text.upper()
    for lang, pat_list in patterns.items():
        for pat in pat_list:
            if re.search(pat, text_upper, re.IGNORECASE):
                found.add(lang)
                break
    return list(found)


def extract_year(text: str) -> Optional[int]:
    match = re.search(r"\b(19|20)\d{2}\b", text or "")
    if match:
        try:
            return int(match.group())
        except ValueError:
            pass
    return None


class AltaDefinizioneScraper(BaseScraper):
    """Scraper for AltaDefinizione."""

    provider_slug = "altadefinizione"
    timeout = 15
    max_retries = 1

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.domains = VERIFIED_DOMAINS.copy()
        self.current_domain_index = 0
        self.domain_failures = {d: 0 for d in self.domains}

    def _get_base_url(self) -> str:
        return f"https://{self.domains[self.current_domain_index]}"

    def _try_fetch(self, path: str, params: Optional[dict] = None) -> tuple[Optional[str], Optional[str]]:
        """Try fetching with domain fallback."""
        from urllib.parse import urlencode

        for i, domain in enumerate(self.domains):
            url = f"https://{domain}{path}"
            if params:
                url += "?" + urlencode(params)
            logger.info("AltaDefinizione: trying %s", url)
            resp = self.fetch_url(url)
            if resp and isinstance(resp, str):
                self.current_domain_index = i
                self.domain_failures[domain] = 0
                return resp, domain
            self.domain_failures[domain] += 1
        return None, None

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        query_filter = query.lower() if query else None
        for path, content_type in BROWSE_PATHS:
            html, domain = self._try_fetch(path)
            if not html or not domain:
                continue
            items.extend(
                items_from_listing(
                    html,
                    href_regex=HREF_PATTERN,
                    provider_slug=self.provider_slug,
                    base_url=f"https://{domain}",
                    default_content_type=content_type,
                    query_filter=query_filter,
                    source_label="listing",
                )
            )
        return dedupe_items(items)
