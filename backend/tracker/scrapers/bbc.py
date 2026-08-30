from __future__ import annotations

import json
import logging
import re
from typing import Optional, Any

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)

FEATURED_URL = "https://www.bbc.co.uk/iplayer/categories/films/featured"
STATE_VAR = "__IPLAYER_REDUX_STATE__"
BBC_EPISODE_URL = "https://www.bbc.co.uk/iplayer/episode/"

# Common patterns that indicate the page is geo-blocked or unreachable
GEO_BLOCK_PATTERNS = [
    r"only available in the uk",
    r"bbc iplayer is only available",
    r"you need to be in the uk",
    r"not available in your country",
    r"geo.?block",
    r"restricted to the uk",
    r"you're not authorised",
    r"this content isn't available",
]


class BBCScraper(BaseScraper):
    """Scraper for BBC iPlayer streaming service (UK)."""
    provider_slug = "bbc_iplayer"
    country_code = "GB"

    def __init__(self, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        raw_data = self.fetch_url(
            FEATURED_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; WhereTfDoIWatchIt/1.0)"},
        )
        if not raw_data:
            logger.warning("BBC scraper: no response data from %s", FEATURED_URL)
            return []

        if isinstance(raw_data, str) and self._is_geo_blocked(raw_data):
            logger.warning("BBC scraper: content appears geo-restricted (outside UK)")
            return []

        items = self._parse_state(raw_data)
        logger.info("BBC scraper: found %d items from %s", len(items), FEATURED_URL)
        return items

    def _is_geo_blocked(self, html: str) -> bool:
        """Check if the response indicates the content is geo-restricted."""
        for pattern in GEO_BLOCK_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                return True
        return False

    def _parse_state(self, html: str) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        idx = html.find(f"{STATE_VAR} = ")
        if idx == -1:
            logger.warning("BBC scraper: no %s found in page HTML", STATE_VAR)
            return []

        start = idx + len(f"{STATE_VAR} = ")
        depth = 0
        end = start
        for i, c in enumerate(html[start:]):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = start + i + 1
                    break
        else:
            end = len(html)

        json_str = html[start:end]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("BBC scraper: failed to parse %s: %s", STATE_VAR, exc)
            return []

        for bundle in data.get("bundles", []):
            for entity in bundle.get("entities", []):
                episode = entity.get("episode")
                if not episode or not isinstance(episode, dict):
                    continue

                title_raw = episode.get("title")
                if isinstance(title_raw, dict):
                    title = title_raw.get("default") or title_raw.get("editorial") or ""
                elif isinstance(title_raw, str):
                    title = title_raw
                else:
                    title = ""

                if not title:
                    continue

                episode_id = episode.get("id", "")
                source_url = f"{BBC_EPISODE_URL}{episode_id}" if episode_id else ""

                synopsis_raw = episode.get("synopsis")
                synopsis = ""
                if isinstance(synopsis_raw, dict):
                    synopsis = synopsis_raw.get("small") or synopsis_raw.get("editorial") or ""
                elif isinstance(synopsis_raw, str):
                    synopsis = synopsis_raw

                versions = episode.get("versions", [])
                quality = None
                if versions:
                    v = versions[0]
                    if isinstance(v, dict):
                        avail = v.get("availability")
                        if isinstance(avail, dict):
                            remaining = avail.get("remaining", "")
                            if "Free" in remaining or "Forever" in remaining:
                                quality = "1080p"

                items.append(
                    ScrapedItem(
                        title=str(title),
                        provider_slug=self.provider_slug,
                        source_url=source_url,
                        audio_languages=["en"],
                        subtitle_languages=["en"],
                        quality=quality or "1080p",
                        availability=True,
                        raw_payload={
                            "episode_id": episode_id,
                            "synopsis": synopsis,
                            "bundle": bundle.get("title", {}).get("default", ""),
                            "audio_languages": ["en"],
                            "subtitle_languages": ["en"],
                        },
                    )
                )

        return items
