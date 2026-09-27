from __future__ import annotations

import logging
from typing import Optional, Any

from bs4 import BeautifulSoup

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class NHKScraper(BaseScraper):
    """Scraper for NHK streaming services (NHK WORLD-JAPAN, etc.).

    NHK is an official public broadcaster and lives in the Free Streaming
    Providers group (slug "nhk_world"), not under Unofficial Sources.
    """
    provider_slug = "nhk_world"
    country_code = "JP"

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            url = "https://www3.nhk.or.jp/nhkworld/"
        else:
            url = f"https://www3.nhk.or.jp/nhkworld/search/?q={query}"

        raw_data = self.fetch_url(url, headers={"User-Agent": "Mozilla/5.0 (compatible; WhereTfDoIWatchIt/1.0)"})
        if not raw_data:
            logger.warning("NHK scraper: no response data from %s", url)
            return []

        items = []
        if isinstance(raw_data, str):
            soup = BeautifulSoup(raw_data, "html.parser")
            # NHK program cards
            for card in soup.find_all(attrs={"data-testid": "program-card"}):
                title_el = card.find(attrs={"data-testid": "program-title"}) or card.find("h2") or card.find("h3")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue
                link_el = card.find("a", href=True)
                source_url = link_el["href"] if link_el else ""
                if source_url and not source_url.startswith("http"):
                    source_url = f"https://www3.nhk.or.jp{source_url}"
                items.append(ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    # NHK WORLD-JAPAN exposes no per-card content type (the homepage
                    # is a JS shell and the search route 404s), so items keep the
                    # "movie" default rather than guessing a type from the title.
                    content_type="movie",
                    source_url=source_url,
                    audio_languages=["ja"],
                    subtitle_languages=["en", "ja"],
                    quality="1080p",
                ))

        logger.info("NHK scraper: found %d items for query='%s'", len(items), query)
        return items
