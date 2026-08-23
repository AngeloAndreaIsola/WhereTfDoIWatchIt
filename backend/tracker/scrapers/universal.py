"""Universal scraper engine supporting HTML, JSON, and paginated/crawling websites."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tracker.scrapers.base import BaseScraper, ScrapedItem
from tracker.scrapers.configs import DEFAULT_PROVIDER_CONFIGS, ProviderConfig, merge_provider_config

logger = logging.getLogger(__name__)


class UniversalScraper(BaseScraper):
    """Generic crawler & metadata extractor capable of parsing diverse streaming site architectures."""

    def __init__(self, provider_slug: str = "generic", config: Optional[ProviderConfig] = None, timeout: Optional[int] = None):
        base_cfg = config or DEFAULT_PROVIDER_CONFIGS.get(provider_slug) or DEFAULT_PROVIDER_CONFIGS["generic"]
        self.config = base_cfg
        self.provider_slug = base_cfg.provider_slug or provider_slug
        super().__init__(timeout=timeout or base_cfg.limits.timeout)
        self.max_retries = base_cfg.limits.max_retries

    def _execute_run(self, query: Optional[str] = None) -> List[ScrapedItem]:
        if not query:
            logger.info("UniversalScraper [%s]: empty query provided", self.provider_slug)
            return []

        search_url = self.config.search_url_template.format(
            query=query, domain=self.config.domain
        )
        logger.info("UniversalScraper [%s]: searching URL: %s", self.provider_slug, search_url)

        visited_urls: Set[str] = set()
        crawled_items: List[ScrapedItem] = []

        headers = self.config.custom_headers or {}
        resp = self._fetch_url_with_config(search_url, headers=headers)
        if not resp:
            # Try fallback domains if search failed
            for fallback_domain in self.config.fallback_domains:
                fallback_url = search_url.replace(self.config.domain, fallback_domain)
                logger.info("UniversalScraper [%s]: trying fallback URL %s", self.provider_slug, fallback_url)
                resp = self._fetch_url_with_config(fallback_url, headers=headers)
                if resp:
                    break

        if not resp:
            logger.warning("UniversalScraper [%s]: fetch failed for search query '%s'", self.provider_slug, query)
            return []

        visited_urls.add(search_url)

        # 1. Parse initial search page/response
        items, next_page_url, detail_links = self.parse_response(resp, base_url=search_url)
        crawled_items.extend(items)

        # 2. Follow pagination if limits allow
        current_page = 1
        current_url = next_page_url

        while current_url and current_page < self.config.limits.max_pages and len(crawled_items) < self.config.limits.max_items:
            if current_url in visited_urls:
                break
            visited_urls.add(current_url)
            current_page += 1

            logger.info("UniversalScraper [%s]: fetching pagination page %d: %s", self.provider_slug, current_page, current_url)
            p_resp = self._fetch_url_with_config(current_url, headers=headers)
            if not p_resp:
                break

            p_items, current_url, p_detail_links = self.parse_response(p_resp, base_url=current_url)
            crawled_items.extend(p_items)
            detail_links.extend(p_detail_links)

        # 3. Follow detail links if configured and items are sparse
        if self.config.detail_url_patterns and self.config.limits.max_depth > 1:
            for d_link in detail_links:
                if len(crawled_items) >= self.config.limits.max_items:
                    break
                if d_link in visited_urls:
                    continue
                visited_urls.add(d_link)

                logger.info("UniversalScraper [%s]: following detail link: %s", self.provider_slug, d_link)
                d_resp = self._fetch_url_with_config(d_link, headers=headers)
                if d_resp:
                    d_items, _, _ = self.parse_response(d_resp, base_url=d_link)
                    crawled_items.extend(d_items)

        # 4. Deduplicate items
        unique_items = self.deduplicate_items(crawled_items)
        logger.info("UniversalScraper [%s]: returning %d deduplicated items (raw total: %d)", self.provider_slug, len(unique_items), len(crawled_items))
        return unique_items

    def _fetch_url_with_config(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[dict | str]:
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
        }
        if headers:
            req_headers.update(headers)

        for attempt in range(1 + self.max_retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, headers=req_headers, allow_redirects=True)
                resp.raise_for_status()
                try:
                    return resp.json()
                except ValueError:
                    return resp.text
            except Exception as exc:
                logger.warning(
                    "UniversalScraper %s attempt %d for %s failed: %s",
                    self.provider_slug,
                    attempt + 1,
                    url,
                    exc,
                )
        return None

    def parse_response(self, raw_data: Any, base_url: str = "") -> Tuple[List[ScrapedItem], Optional[str], List[str]]:
        """Parse raw response (JSON dict/list, HTML string) into items, pagination URL, and detail page URLs."""
        if isinstance(raw_data, list):
            return self._parse_json_list(raw_data, base_url=base_url), None, []
        if isinstance(raw_data, dict):
            # Check if dict contains embedded HTML (e.g. AnimeWorld {"html": "..."})
            if "html" in raw_data and isinstance(raw_data["html"], str):
                return self._parse_html_content(raw_data["html"], base_url=base_url)

            # Check JSON path
            json_items = raw_data
            if self.config.json_results_path:
                for key in self.config.json_results_path.split("."):
                    if key and isinstance(json_items, dict):
                        json_items = json_items.get(key)
            if isinstance(json_items, list):
                return self._parse_json_list(json_items, base_url=base_url), None, []
            elif isinstance(json_items, dict):
                return self._parse_json_list([json_items], base_url=base_url), None, []

        if isinstance(raw_data, str):
            return self._parse_html_content(raw_data, base_url=base_url)

        return [], None, []

    def _parse_json_list(self, raw_list: List[Dict[str, Any]], base_url: str = "") -> List[ScrapedItem]:
        items: List[ScrapedItem] = []
        for entry in raw_list or []:
            if not isinstance(entry, dict):
                continue

            title = (
                entry.get("title")
                or entry.get("name")
                or entry.get("scraped_title")
                or entry.get("show_name")
            )
            if not title:
                continue

            year = entry.get("year") or entry.get("releaseYear") or entry.get("anno")
            try:
                year_int = int(year) if year is not None else None
            except (ValueError, TypeError):
                year_int = None

            content_type = entry.get("content_type") or entry.get("type") or ("series" if entry.get("season") or entry.get("episode") else "movie")
            season = entry.get("season") or entry.get("season_number")
            episode = entry.get("episode") or entry.get("episode_number")
            try:
                season_int = int(season) if season is not None else None
            except (ValueError, TypeError):
                season_int = None
            try:
                episode_int = int(episode) if episode is not None else None
            except (ValueError, TypeError):
                episode_int = None

            audio = entry.get("audio_languages") or entry.get("audio") or entry.get("dub") or self.config.default_audio_languages
            subs = entry.get("subtitle_languages") or entry.get("subtitles") or entry.get("sub") or self.config.default_subtitle_languages
            quality = entry.get("quality") or entry.get("res") or entry.get("resolution") or "1080p"
            hdr = entry.get("hdr") or entry.get("hdr_info")

            source_url = (
                entry.get("source_url")
                or entry.get("url")
                or entry.get("link")
                or entry.get("href")
            )
            if not source_url and entry.get("id"):
                source_url = f"https://{self.config.domain}/Drama/{entry.get('id')}"
            if not source_url:
                source_url = base_url

            if source_url and base_url and not source_url.startswith(("http://", "https://")):
                source_url = urljoin(base_url, source_url)

            items.append(
                ScrapedItem(
                    title=str(title),
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=str(content_type),
                    season=season_int,
                    episode=episode_int,
                    episode_title=entry.get("episode_title"),
                    source_url=str(source_url),
                    audio_languages=audio if isinstance(audio, (list, tuple, str)) else [str(audio)],
                    subtitle_languages=subs if isinstance(subs, (list, tuple, str)) else [str(subs)],
                    quality=str(quality) if quality else None,
                    hdr=hdr,
                    availability=True,
                    raw_payload=entry,
                )
            )
        return items

    def _parse_html_content(self, html_str: str, base_url: str = "") -> Tuple[List[ScrapedItem], Optional[str], List[str]]:
        items: List[ScrapedItem] = []
        detail_links: List[str] = []
        next_page_url: Optional[str] = None

        soup = BeautifulSoup(html_str, "html.parser")
        selectors = self.config.selectors

        # 1. Extract container elements
        container_selector = selectors.container or ".item, .movie, .card, .entry, div[class*='item']"
        containers = soup.select(container_selector)

        # Fallback if specific containers not found: look for links or regex patterns
        if not containers:
            logger.debug("UniversalScraper [%s]: CSS containers not found, trying regex fallback", self.provider_slug)
            items = self._regex_parse_html(html_str, base_url=base_url)
            return items, None, []

        for container in containers:
            # Title & Link
            title_el = container.select_one(selectors.title) if selectors.title else None
            if not title_el:
                title_el = container.select_one("a.name, a.title, .title a, h2 a, h3 a, a[href]")

            if not title_el:
                continue

            title_text = title_el.get_text(strip=True)
            if not title_text:
                continue

            href = title_el.get("href") or (container.select_one("a[href]") or {}).get("href") or ""
            source_url = urljoin(base_url, href) if href else base_url

            # Collect potential detail links
            if href and any(re.search(pat, source_url) for pat in self.config.detail_url_patterns):
                detail_links.append(source_url)

            # Year extraction
            year_int = None
            year_el = container.select_one(selectors.year) if selectors.year else None
            year_text = year_el.get_text(strip=True) if year_el else container.get_text()
            year_match = re.search(r'\b(19\d\d|20\d\d)\b', year_text)
            if year_match:
                try:
                    year_int = int(year_match.group(1))
                except ValueError:
                    pass

            # Audio & Subs
            audio_text = (container.select_one(selectors.audio_languages).get_text() if selectors.audio_languages and container.select_one(selectors.audio_languages) else "")
            subs_text = (container.select_one(selectors.subtitle_languages).get_text() if selectors.subtitle_languages and container.select_one(selectors.subtitle_languages) else "")

            audio_langs = audio_text or self.config.default_audio_languages
            sub_langs = subs_text or self.config.default_subtitle_languages

            # Quality & HDR
            quality_text = (container.select_one(selectors.quality).get_text() if selectors.quality and container.select_one(selectors.quality) else "")
            quality_match = re.search(r'\b(2160p|4k|1080p|720p|480p|fhd|hd|sd)\b', container.get_text(), re.IGNORECASE)
            quality_val = quality_text or (quality_match.group(1) if quality_match else "1080p")

            hdr_text = (container.select_one(selectors.hdr).get_text() if selectors.hdr and container.select_one(selectors.hdr) else "")
            hdr_match = re.search(r'\b(hdr10\+|hdr10|hdr|dolby vision|dv)\b', container.get_text(), re.IGNORECASE)
            hdr_val = hdr_text or (hdr_match.group(1) if hdr_match else None)

            # Content type, season, episode
            season_int, episode_int = None, None
            se_match = re.search(r'S(\d+)\s*E(\d+)|Season\s*(\d+)|Episode\s*(\d+)', container.get_text(), re.IGNORECASE)
            content_type = "movie"
            if se_match:
                content_type = "series"
                if se_match.group(1) and se_match.group(2):
                    season_int, episode_int = int(se_match.group(1)), int(se_match.group(2))
                elif se_match.group(3):
                    season_int = int(se_match.group(3))
                elif se_match.group(4):
                    episode_int = int(se_match.group(4))

            items.append(
                ScrapedItem(
                    title=title_text,
                    provider_slug=self.provider_slug,
                    year=year_int,
                    content_type=content_type,
                    season=season_int,
                    episode=episode_int,
                    source_url=source_url,
                    audio_languages=audio_langs if isinstance(audio_langs, (list, tuple)) else [str(audio_langs)],
                    subtitle_languages=sub_langs if isinstance(sub_langs, (list, tuple)) else [str(sub_langs)],
                    quality=quality_val,
                    hdr=hdr_val,
                    availability=True,
                    raw_payload={"html_snippet": str(container)[:500]},
                )
            )

        # Pagination next URL
        if selectors.pagination_next:
            next_el = soup.select_one(selectors.pagination_next)
            if next_el and next_el.get("href"):
                next_page_url = urljoin(base_url, next_el["href"])

        return items, next_page_url, detail_links

    def _regex_parse_html(self, html_str: str, base_url: str = "") -> List[ScrapedItem]:
        items: List[ScrapedItem] = []
        # Pattern: <a ... href="([^"]+)">Title</a>
        matches = re.findall(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>', html_str, re.IGNORECASE)
        years = re.findall(r'\b(19\d\d|20\d\d)\b', html_str)

        for idx, (href, title) in enumerate(matches):
            title_clean = title.strip()
            if not title_clean or len(title_clean) < 2 or title_clean.lower() in ("home", "search", "login", "register"):
                continue

            year_int = None
            if idx < len(years):
                try:
                    year_int = int(years[idx])
                except ValueError:
                    pass

            source_url = urljoin(base_url, href) if href else base_url

            items.append(
                ScrapedItem(
                    title=title_clean,
                    provider_slug=self.provider_slug,
                    year=year_int,
                    source_url=source_url,
                    audio_languages=self.config.default_audio_languages or ["EN"],
                    subtitle_languages=self.config.default_subtitle_languages or ["EN"],
                    quality="1080p",
                    availability=True,
                    raw_payload={"href": href, "title": title_clean},
                )
            )
        return items

    @staticmethod
    def deduplicate_items(items: List[ScrapedItem]) -> List[ScrapedItem]:
        """Deduplicate ScrapedItem objects by (title, year, season, episode, source_url, provider_slug)."""
        seen: Set[Tuple[str, Optional[int], Optional[int], Optional[int], str, str]] = set()
        deduped: List[ScrapedItem] = []

        for item in items:
            key = (
                item.title.strip().lower(),
                item.year,
                item.season,
                item.episode,
                item.source_url.strip(),
                item.provider_slug,
            )
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        return deduped
