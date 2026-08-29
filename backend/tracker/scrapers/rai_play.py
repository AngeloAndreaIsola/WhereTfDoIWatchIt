from __future__ import annotations

import logging
from typing import Optional, Any

from bs4 import BeautifulSoup

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class RaiPlayScraper(BaseScraper):
    """Scraper for RaiPlay streaming service (Italy)."""
    provider_slug = "rai-play"
    country_code = "IT"

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            url = "https://www.raiplay.it"
        else:
            url = f"https://www.raiplay.it/ricerca?search={query}"
        
        raw_data = self.fetch_url(url)
        if not raw_data:
            logger.warning("RaiPlay scraper: no response data from %s", url)
            return []

        items = []
        if isinstance(raw_data, str):
            soup = BeautifulSoup(raw_data, "html.parser")
            # RaiPlay program cards use data-testid attributes
            for card in soup.find_all(["div", "a"], attrs={"data-testid": True}):
                title_el = card.find(attrs={"data-testid": "media-title"}) or card.find("h2") or card.find("h3")
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue
                link_el = card.find("a", href=True)
                source_url = link_el["href"] if link_el else ""
                if source_url and not source_url.startswith("http"):
                    source_url = f"https://www.raiplay.it{source_url}"
                items.append(ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    source_url=source_url,
                    audio_languages=["it"],
                    subtitle_languages=["it"],
                    quality="1080p",
                ))
        elif isinstance(raw_data, dict):
            # API JSON response
            results = raw_data.get("results") or raw_data.get("items") or []
            for entry in results:
                title = entry.get("title") or entry.get("name") or ""
                if not title:
                    continue
                items.append(self._parse_api_entry(entry))

        logger.info("RaiPlay scraper: found %d items for query='%s'", len(items), query)
        return items

    def _parse_api_entry(self, entry: dict) -> ScrapedItem:
        title = entry.get("title") or entry.get("name") or ""
        source_url = entry.get("url") or entry.get("link") or ""
        if source_url and not source_url.startswith("http"):
            source_url = f"https://www.raiplay.it{source_url}"
        return ScrapedItem(
            title=title,
            provider_slug=self.provider_slug,
            source_url=source_url,
            audio_languages=entry.get("audio_languages", ["it"]),
            subtitle_languages=entry.get("subtitle_languages", ["it"]),
            quality=entry.get("quality", "1080p"),
            raw_payload=entry,
        )
