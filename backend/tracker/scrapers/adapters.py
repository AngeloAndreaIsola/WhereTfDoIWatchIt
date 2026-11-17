"""Site-specific scraper adapters for KissKH, HydraHD, and AnimeWorld."""
from __future__ import annotations

import logging
from typing import Any, Optional

from tracker.scrapers.base import BaseScraper, ScrapedItem

logger = logging.getLogger(__name__)


def _content_type_from_url(url: Any, default: str = "movie") -> str:
    """Classify an item from its URL path.

    HydraHD keeps films and shows in separate path namespaces (`/movie/...`
    vs `/watchseries/...`), so the URL carries a usable signal even for search
    results, where no browse category is available. Anything unrecognised keeps
    ``default`` — an unknown signal must never become a series.
    """
    path = str(url or "").split("?", 1)[0]
    if "watchseries/" in path:
        return "series"
    if "/movie/" in path or path.startswith("movie/"):
        return "movie"
    return default


class KissKHScraper(BaseScraper):
    provider_slug = "kisskh"

    # Both hosts serve the same API; keep a fallback so a domain move does not
    # silently zero out the scraper.
    API_BASES = ("https://kisskh.do", "https://kisskh.co")
    # The API defaults to 10 rows per page. `pageSize` is accepted and lets a
    # single request return hundreds of titles instead of ten.
    PAGE_SIZE = 200
    PAGES_PER_TYPE = 3

    def _fetch_api(self, path: str):
        for base in self.API_BASES:
            data = self.fetch_url(f"{base}{path}")
            if data:
                return data
        return None

    # KissKH's API `type` parameter is the site's own structured content flag and
    # the only reliable signal it exposes (payload rows carry no per-item type):
    # 2 = films, 1 = drama series, 3 = anime series. `type=0` (mixed) is avoided
    # because it yields no usable classification, so each type is fetched on its own
    # and the response type stamps every item parsed from it.
    BROWSE_TYPES: tuple[tuple[str, str], ...] = (
        ("2", "movie"),
        ("1", "series"),
        ("3", "series"),
    )

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        items: list[ScrapedItem] = []

        if not query:
            logger.info("KissKH scraper: no query provided, scraping recently added...")
            for type_code, content_type in self.BROWSE_TYPES:
                for page in range(1, self.PAGES_PER_TYPE + 1):
                    path = (
                        f"/api/DramaList/List?page={page}&type={type_code}"
                        f"&sub=0&country=0&status=0&order=1&pageSize={self.PAGE_SIZE}"
                    )
                    logger.info("KissKH scraper: fetching %s", path)
                    resp_data = self._fetch_api(path)
                    if not resp_data or not isinstance(resp_data, dict):
                        break
                    data = resp_data.get("data")
                    if not data:
                        break
                    page_items = self.parse_items(data, content_type=content_type)
                    if not page_items:
                        break
                    items.extend(page_items)
                    if len(data) < self.PAGE_SIZE:
                        break  # Last page for this type
            return self._dedupe(items)

        for type_code, content_type in self.BROWSE_TYPES:
            resp_data = self._fetch_api(
                f"/api/DramaList/Search?q={query}&type={type_code}"
            )
            if not resp_data:
                logger.warning("KissKH scraper: no response data for type=%s", type_code)
                continue
            if not isinstance(resp_data, list):
                logger.warning(
                    "KissKH scraper: expected list for type=%s, got %s",
                    type_code,
                    type(resp_data),
                )
                continue
            logger.info("KissKH scraper: got %d entries", len(resp_data))
            items.extend(self.parse_items(resp_data, content_type=content_type))
        return self._dedupe(items)

    @staticmethod
    def _dedupe(items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Drop rows seen under another type request (the same title can be
        returned by both the series and the film query)."""
        seen: set[Any] = set()
        deduped: list[ScrapedItem] = []
        for item in items:
            payload = item.raw_payload
            entry_id = payload.get("id") if isinstance(payload, dict) else None
            key = entry_id if entry_id is not None else (item.title, item.year)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def parse_items(self, raw_data: Any, content_type: str = "movie") -> list[ScrapedItem]:
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
            url_path = entry.get("url") or f"https://kisskh.do/Drama/{entry.get('id')}"

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=content_type,
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

    # hydrahd.ws now redirects to hydrahd.gd; list the canonical host first and
    # keep the old one as a fallback. hydrahd.ru serves a stub page, so it is
    # deliberately excluded.
    DOMAINS = ("hydrahd.gd", "hydrahd.ws")
    PAGES_PER_CATEGORY = 10

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://hydrahd.gd/",
    }

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("HydraHD scraper: no query provided, scraping recently added...")
            items = []
            for category in ["movies", "tv-shows"]:
                # The browse category only acts as the fallback signal; the item URL
                # itself is authoritative for classification.
                default_content_type = "series" if category == "tv-shows" else "movie"
                for page in range(1, self.PAGES_PER_CATEGORY + 1):
                    for domain in self.DOMAINS:
                        url = f"https://{domain}/{category}/date/{page}"
                        logger.info("HydraHD scraper: fetching %s", url)
                        resp_data = self.fetch_url(url, headers=self.HEADERS)
                        if resp_data and isinstance(resp_data, str):
                            page_items = self.parse_html(
                                resp_data,
                                default_content_type=default_content_type,
                                base_url=f"https://{domain}",
                            )
                            if page_items:
                                items.extend(page_items)
                                break  # This domain worked for this page
            return self._dedupe(items)

        # Try the current domain first, fall back to the old one
        urls_to_try = [
            f"https://{domain}/index.php?menu=search&query={query}"
            for domain in self.DOMAINS
        ]

        for url in urls_to_try:
            logger.info("HydraHD scraper: trying %s", url)
            resp_data = self.fetch_url(url, headers=self.HEADERS)
            if resp_data:
                if isinstance(resp_data, dict):
                    results = resp_data.get("results") or []
                    logger.info("HydraHD scraper: got %d results from JSON %s", len(results), url)
                    return self.parse_items(results)
                elif isinstance(resp_data, str):
                    items = getattr(self, "parse_html", lambda x: [])(resp_data)
                    logger.info("HydraHD scraper: got %d results from HTML %s", len(items), url)
                    if items:
                        return items

        logger.warning("HydraHD scraper: all URLs failed")
        return []

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
                    content_type=_content_type_from_url(source_url),
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, list) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, list) else [str(subs)],
                    quality=str(quality),
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items

    @staticmethod
    def _dedupe(items: list[ScrapedItem]) -> list[ScrapedItem]:
        """Drop titles repeated across browse pages."""
        seen: set[str] = set()
        deduped: list[ScrapedItem] = []
        for item in items:
            if item.source_url in seen:
                continue
            seen.add(item.source_url)
            deduped.append(item)
        return deduped

    def parse_html(
        self,
        html_content: str,
        default_content_type: str = "movie",
        base_url: Optional[str] = None,
    ) -> list[ScrapedItem]:
        import re
        items: list[ScrapedItem] = []
        pattern = re.compile(
            r'<a class="hthis"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>[\s\S]*?<div class="title detz">([^<]+)</div>',
            re.IGNORECASE
        )
        for href, inner_a, title in pattern.findall(html_content):
            year_match = re.search(r'>\s*(\d{4})\s*</span>', inner_a)
            year = year_match.group(1) if year_match else None
            try:
                year_int = int(year) if year else None
            except ValueError:
                year_int = None
            source_url = f"{(base_url or 'https://hydrahd.gd').rstrip('/')}{href}" if href.startswith("/") else href
            items.append(
                ScrapedItem(
                    title=title.strip(),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=_content_type_from_url(source_url, default=default_content_type),
                    source_url=source_url,
                    audio_languages=["EN"],
                    subtitle_languages=[], 
                    quality="1080p",
                    availability=True,
                    raw_payload={"href": href, "title": title, "year": year_int},
                )
            )
        return items

class AnimeWorldScraper(BaseScraper):
    provider_slug = "animeworld"

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("AnimeWorld scraper: no query provided, scraping movies and animes...")
            items = []
            for category in ["movies", "animes"]:
                for page in range(1, 3):
                    url = f"https://www.animeworld.ac/{category}?page={page}"
                    logger.info("AnimeWorld scraper: fetching %s", url)
                    resp = self.session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=self.timeout)
                    if resp.status_code == 200:
                        page_items = self.parse_html(resp.text, category=category)
                        if not page_items:
                            break
                        items.extend(page_items)
            return items

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

    def parse_html(self, html_content: str, category: Optional[str] = None) -> list[ScrapedItem]:
        """Parse HTML response containing anime items.

        ``category`` is the browse section the HTML came from. AnimeWorld has two
        static sections (`/animes` and `/movies`) and every item carries the same
        `/play/...` href, so the section is the only per-item type signal; search
        results arrive without one and keep the default movie type. An unknown
        category never becomes a series.
        """
        import re
        items: list[ScrapedItem] = []
        content_type = "series" if category == "animes" else "movie"

        # Extract anime entries using regex
        # Pattern: <a class="name" href="play/...">Title</a>
        name_pattern = r'<a[^>]*href="([^"]+)"[^>]*class="name"[^>]*>\s*([^<]+?)\s*</a>'
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
                    content_type=content_type,
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
