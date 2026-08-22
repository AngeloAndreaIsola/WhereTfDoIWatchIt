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
            logger.info("KissKH scraper: no query provided")
            return []
        url = f"https://kisskh.co/api/DramaList/Search?q={query}"
        resp_data = self.fetch_url(url)
        if not resp_data:
            logger.warning("KissKH scraper: no response data")
            return []
        if not isinstance(resp_data, list):
            logger.warning("KissKH scraper: expected list, got %s", type(resp_data))
            return []
        logger.info("KissKH scraper: got %d entries", len(resp_data))
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
            logger.info("HydraHD scraper: no query provided")
            return []

        # Try the current domain first, fall back to old domain
        urls_to_try = [
            f"https://hydrahd.ws/api/search?q={query}",
            f"https://hydrahd.ru/api/search?q={query}",
        ]

        for url in urls_to_try:
            logger.info("HydraHD scraper: trying %s", url)
            resp_data = self.fetch_url_with_headers(url)
            if resp_data and isinstance(resp_data, dict):
                results = resp_data.get("results") or []
                logger.info("HydraHD scraper: got %d results from %s", len(results), url)
                return self.parse_items(results)

        logger.warning("HydraHD scraper: all URLs failed")
        return []

    def fetch_url_with_headers(self, url: str) -> Optional[dict | str]:
        """Fetch URL with proper headers to avoid 403 blocks."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://hydrahd.ws/",
        }

        for attempt in range(1 + self.max_retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=headers, allow_redirects=True)
                resp.raise_for_status()
                try:
                    return resp.json()
                except ValueError:
                    return resp.text
            except Exception as exc:
                logger.warning(
                    "HydraHD scraper attempt %d for %s failed: %s",
                    attempt + 1,
                    url,
                    exc,
                )
        return None

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
            logger.info("AnimeWorld scraper: no query provided")
            return []
        url = f"https://animeworld.ac/api/search?keyword={query}"
        resp_data = self.fetch_url(url)
        if not resp_data:
            logger.warning("AnimeWorld scraper: no response data")
            return []

        # AnimeWorld API returns {"html": "<div>...</div>"} not a JSON list
        if isinstance(resp_data, dict):
            html_content = resp_data.get("html", "")
            if html_content:
                logger.info("AnimeWorld scraper: parsing HTML response (%d bytes)", len(html_content))
                return self.parse_html(html_content)
            else:
                logger.warning("AnimeWorld scraper: dict response has no 'html' key")
                return []

        logger.warning("AnimeWorld scraper: unexpected response type: %s", type(resp_data))
        return []

    def parse_html(self, html_content: str) -> list[ScrapedItem]:
        """Parse HTML response containing anime items."""
        import re
        items: list[ScrapedItem] = []

        # Extract anime entries using regex
        # Pattern: <a class="name" href="play/...">Title</a>
        name_pattern = r'<a class="name" href="([^"]+)">([^<]+)</a>'
        name_matches = re.findall(name_pattern, html_content)

        # Extract years: Anno: 2007
        year_pattern = r'Anno: (\d{4})'
        year_matches = re.findall(year_pattern, html_content)

        logger.info("AnimeWorld scraper: found %d titles, %d years", len(name_matches), len(year_matches))

        # Match titles with years (they appear in same order in HTML)
        for idx, (href, title) in enumerate(name_matches):
            year_int = None
            if idx < len(year_matches):
                try:
                    year_int = int(year_matches[idx])
                except (ValueError, TypeError):
                    pass

            # Build full URL from relative href
            source_url = f"https://animeworld.ac/{href}" if not href.startswith("http") else href

            items.append(
                ScrapedItem(
                    title=title.strip(),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=source_url,
                    audio_languages=["JA"],  # Default for anime
                    subtitle_languages=["EN", "IT"],  # AnimeWorld is Italian site with subs
                    quality="1080p",
                    availability=True,
                    raw_payload={"href": href, "title": title, "year": year_int},
                )
            )

        logger.info("AnimeWorld scraper: parsed %d items", len(items))
        return items

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        """Legacy method kept for compatibility. New implementation uses parse_html."""
        logger.warning("AnimeWorld parse_items called with legacy format")
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
