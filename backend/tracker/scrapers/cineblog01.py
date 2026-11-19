"""CineBlog01 (CB01) scraper.

The live site renders image-only listing tiles: the anchor text is empty and
the real title is in the ``title`` attribute (or the nested image's ``alt``).
Titles are read from those attributes and paginated listings are browsed
directly, instead of fetching a detail page for every tile.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from tracker.scrapers.base import BaseScraper, ScrapedItem
from tracker.scrapers.listing import dedupe_items, items_from_listing

logger = logging.getLogger(__name__)

# The legacy .tires domain redirects here and is kept as a fallback only.
VERIFIED_DOMAINS = [
    "cineblog001.run",
    "cineblog001.tires",
]

# (path, content_type, number of pages). The site exposes real /page/N/ URLs.
BROWSE_SECTIONS = [
    ("/film/", "movie", 5),
    ("/serie-tv/", "series", 3),
]

TITLE_LANG_PATTERN = re.compile(r"\[(ITA|Sub-ITA|ENG|Sub-ENG|JAP|Sub-JAP)\]", re.IGNORECASE)
HREF_PATTERN = r"/cb01-streaming/\d+-.*-streaming-cb01\.html$"


def detect_languages_from_title(title: str) -> tuple[list[str], list[str]]:
    """Extract audio and subtitle languages from legacy bracket tags."""
    audio_langs = []
    sub_langs = []
    for match in TITLE_LANG_PATTERN.findall(title or ""):
        tag = match.upper()
        if tag == "ITA":
            audio_langs.append("it")
        elif tag == "ENG":
            audio_langs.append("en")
        elif tag == "JAP":
            audio_langs.append("ja")
        elif tag == "SUB-ITA":
            sub_langs.append("it")
        elif tag == "SUB-ENG":
            sub_langs.append("en")
        elif tag == "SUB-JAP":
            sub_langs.append("ja")
    audio_langs = list(dict.fromkeys(audio_langs))
    sub_langs = list(dict.fromkeys(sub_langs))
    return audio_langs, sub_langs


def extract_year_from_title(title: str) -> Optional[int]:
    match = re.search(r"\(((?:19|20)\d{2})\)", title or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def clean_title(title: str) -> str:
    title = TITLE_LANG_PATTERN.sub("", title or "")
    title = re.sub(r"\[(HD|4K|FULL HD|SD)\]", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\((19|20)\d{2}\)\s*$", "", title)
    return title.strip()


class CineBlog01Scraper(BaseScraper):
    """Scraper for CineBlog01 (CB01)."""

    provider_slug = "cineblog01"
    timeout = 15
    max_retries = 1

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.domains = VERIFIED_DOMAINS.copy()
        self.current_domain_index = 0
        self.domain_failures = {d: 0 for d in self.domains}

    def _get_base_url(self) -> str:
        return f"https://{self.domains[self.current_domain_index]}"

    def _try_fetch(self, path: str) -> tuple[Optional[str], Optional[str]]:
        """Try fetching with domain fallback."""
        for i, domain in enumerate(self.domains):
            url = f"https://{domain}{path}"
            logger.info("CineBlog01: trying %s", url)
            resp = self.fetch_url(url)
            if resp and isinstance(resp, str):
                self.current_domain_index = i
                self.domain_failures[domain] = 0
                return resp, domain
            self.domain_failures[domain] += 1
        return None, None

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        return self._browse_catalogue(query_filter=query.lower() if query else None)

    def _browse_catalogue(self, query_filter: Optional[str] = None) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for path, content_type, pages in BROWSE_SECTIONS:
            for page in range(1, pages + 1):
                page_path = path if page == 1 else f"{path}page/{page}/"
                html, domain = self._try_fetch(page_path)
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
