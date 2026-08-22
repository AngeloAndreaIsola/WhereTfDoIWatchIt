"""Site-specific scraper adapters for KissKH, HydraHD, and AnimeWorld."""
from __future__ import annotations

import logging
from typing import Any, Optional

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


class KissKHScraper(BaseScraper):
    provider_slug = "kisskh"

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            return []
        url = f"https://kisskh.co/api/DramaList/Search?q={query}"
        resp_data = self.fetch_url(url)
        if not resp_data or not isinstance(resp_data, list):
            return []
        return self.parse_items(resp_data)

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("name")
            if not title:
                continue
            year = entry.get("year") or entry.get("releaseYear")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("audio_languages") or entry.get("audio") or ["EN"]
            subs = entry.get("subtitle_languages") or entry.get("subtitles") or ["EN"]
            quality = entry.get("quality") or "1080p"
            url_path = entry.get("url") or f"https://kisskh.co/Drama/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type="movie",
                    source_url=str(url_path),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality),
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items


class GenericURLAdapter(BaseScraper):
    """Fallback adapter for dynamically registered provider domains."""

    def __init__(self, provider_slug: str, domain: str, timeout: Optional[int] = None):
        super().__init__(timeout=timeout)
        self.provider_slug = provider_slug
        self.domain = domain

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        target_url = f"https://{self.domain}/api/search?q={query}" if query else f"https://{self.domain}"
        resp_data = self.fetch_url(target_url)

        if isinstance(resp_data, list):
            return self.parse_items(resp_data)
        elif isinstance(resp_data, dict):
            items = resp_data.get("results") or resp_data.get("items") or resp_data.get("data") or []
            return self.parse_items(items)
        return []

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("name")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("audio_languages") or entry.get("audio") or ["EN"]
            subs = entry.get("subtitle_languages") or entry.get("subtitles") or ["EN"]
            quality = entry.get("quality") or entry.get("res") or "1080p"
            url_path = entry.get("url") or entry.get("link") or f"https://{self.domain}/view/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=str(url_path),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality),
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items


class HydraHDScraper(BaseScraper):
    provider_slug = "hydrahd"

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            return []
        url = f"https://hydrahd.ru/api/search?q={query}"
        resp_data = self.fetch_url(url)
        if not resp_data or not isinstance(resp_data, dict):
            return []
        results = resp_data.get("results") or []
        return self.parse_items(results)

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("audio") or ["EN"]
            subs = entry.get("subtitles") or []
            quality = entry.get("res") or "2160p"
            source_url = entry.get("link") or f"https://hydrahd.ru/watch/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality),
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items


class AnimeWorldScraper(BaseScraper):
    provider_slug = "animeworld"

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            return []
        url = f"https://animeworld.ac/api/search?keyword={query}"
        resp_data = self.fetch_url(url)
        if not resp_data or not isinstance(resp_data, dict):
            return []
        animes = resp_data.get("animes") or []
        return self.parse_items(animes)

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []
        for entry in raw_data or []:
            if not isinstance(entry, dict):
                continue
            title = entry.get("name") or entry.get("title")
            if not title:
                continue
            year = entry.get("year")
            try:
                year_int = int(year) if year else None
            except (ValueError, TypeError):
                year_int = None

            audio = entry.get("dub") or ["JA"]
            subs = entry.get("sub") or ["EN"]
            source_url = entry.get("link") or f"https://animeworld.ac/play/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality="1080p",
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items
