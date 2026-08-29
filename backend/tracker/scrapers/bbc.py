from __future__ import annotations

import logging
from typing import Optional, Any

from bs4 import BeautifulSoup

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class BBCScraper(BaseScraper):
    """Scraper for BBC iPlayer streaming service (UK)."""
    provider_slug = "bbc"
    country_code = "GB"

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            url = "https://www.bbc.co.uk/iplayer"
        else:
            url = f"https://www.bbc.co.uk/iplayer/search?q={query}"

        raw_data = self.fetch_url(url, headers={"User-Agent": "Mozilla/5.0 (compatible; WhereTfDoIWatchIt/1.0)"})
        if not raw_data:
            logger.warning("BBC scraper: no response data from %s", url)
            return []

        items = []
        if isinstance(raw_data, str):
            soup = BeautifulSoup(raw_data, "html.parser")
            # BBC iPlayer uses specific data-testid attributes
            for card in soup.find_all(attrs={"data-testid": ["listitem", "programme-card"]}):
                title_el = card.find(attrs={"data-testid": "card-title"}) or card.find("h2") or card.find("h3")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue
                link_el = card.find("a", href=True)
                source_url = link_el["href"] if link_el else ""
                if source_url and not source_url.startswith("http"):
                    source_url = f"https://www.bbc.co.uk{source_url}"
                items.append(ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    source_url=source_url,
                    audio_languages=["en"],
                    subtitle_languages=["en"],
                    quality="1080p",
                ))

        logger.info("BBC scraper: found %d items for query='%s'", len(items), query)
        return items
