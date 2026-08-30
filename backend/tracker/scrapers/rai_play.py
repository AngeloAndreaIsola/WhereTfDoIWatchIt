from __future__ import annotations

import json
import logging
import re
from typing import Optional, Any

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

RAIPLAY_FILM_URL = "https://www.raiplay.it/film/"
RAIPLAY_SERIE_URL = "https://www.raiplay.it/serieitaliane"
WASHI_CONTEXT_VAR = "window.WashiContext"

# Common patterns that indicate the page is geo-blocked or unreachable
GEO_BLOCK_PATTERNS = [
    r"non disponibile",
    r"not available in your country",
    r"geo.?block",
    r"content not available",
]


class RaiPlayScraper(BaseScraper):
    """Scraper for RaiPlay streaming service (Italy)."""
    provider_slug = "rai-play"
    country_code = "IT"

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        urls = [RAIPLAY_FILM_URL, RAIPLAY_SERIE_URL]
        if query:
            urls = [f"https://www.raiplay.it/ricerca?search={query}"]

        items: list[ScrapedItem] = []
        seen_weblinks: set[str] = set()

        for url in urls:
            raw_data = self.fetch_url(url)
            if not raw_data or not isinstance(raw_data, str):
                logger.warning("RaiPlay scraper: no HTML response from %s", url)
                continue

            if self._is_geo_blocked(raw_data):
                logger.warning("RaiPlay scraper: content appears geo-restricted for %s", url)
                continue

            page_items = self._parse_washi_context(raw_data, url)
            for item in page_items:
                if item.source_url and item.source_url not in seen_weblinks:
                    seen_weblinks.add(item.source_url)
                    items.append(item)

        logger.info("RaiPlay scraper: found %d unique items", len(items))
        return items

    def _is_geo_blocked(self, html: str) -> bool:
        """Check if the response indicates the content is geo-restricted."""
        for pattern in GEO_BLOCK_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                return True
        return False

    def _parse_washi_context(self, html: str, source_url: str) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []

        idx = html.find(WASHI_CONTEXT_VAR)
        if idx == -1:
            logger.debug("RaiPlay scraper: no %s found in %s", WASHI_CONTEXT_VAR, source_url)
            return items

        json_start = idx + len(WASHI_CONTEXT_VAR + " = ")
        script_end = html.find("</script>", idx)
        if script_end == -1:
            script_end = len(html)

        json_str = html[json_start:script_end].strip()
        if json_str.endswith(";"):
            json_str = json_str[:-1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("RaiPlay scraper: failed to parse WashiContext from %s: %s", source_url, exc)
            return items

        for card in self._extract_programma_cards(data):
            title = card.get("title", "").strip()
            if not title:
                continue

            weblink = card.get("weblink", "")
            if not weblink:
                continue

            full_url = weblink if weblink.startswith("http") else f"https://www.raiplay.it{weblink}"

            typology = card.get("typology", "")
            content_type = "series" if typology in ("Fiction", "Serie") else "movie"

            year_raw = card.get("year")
            year_int: Optional[int] = None
            if year_raw:
                try:
                    year_int = int(str(year_raw).strip())
                except (ValueError, TypeError):
                    pass

            items.append(
                ScrapedItem(
                    title=title,
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=content_type,
                    source_url=full_url,
                    audio_languages=["it"],
                    subtitle_languages=["it"],
                    quality="1080p",
                    raw_payload=card,
                )
            )

        return items

    def _extract_programma_cards(self, obj: Any) -> list[dict]:
        """Recursively extract all RaiPlay Programma Item cards from nested JSON."""
        cards: list[dict] = []
        if isinstance(obj, dict):
            if obj.get("type") == "RaiPlay Programma Item":
                cards.append(obj)
            for v in obj.values():
                cards.extend(self._extract_programma_cards(v))
        elif isinstance(obj, list):
            for item in obj:
                cards.extend(self._extract_programma_cards(item))
        return cards
