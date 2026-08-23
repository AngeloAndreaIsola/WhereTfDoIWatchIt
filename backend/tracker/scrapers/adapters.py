"""Site-specific scraper adapters backed by UniversalScraper engine."""
from __future__ import annotations

import logging
from typing import Any, Optional

from tracker.models import Provider
from tracker.scrapers.base import BaseScraper, ScrapedItem
from tracker.scrapers.configs import ProviderConfig
from tracker.scrapers.universal import UniversalScraper

logger = logging.getLogger(__name__)


def _get_provider_config(provider_slug: str) -> Optional[ProviderConfig]:
    try:
        provider = Provider.objects.filter(slug=provider_slug).first()
        if provider:
            return provider.get_merged_config()
    except Exception:
        pass
    return None


class KissKHScraper(UniversalScraper):
    provider_slug = "kisskh"

    def __init__(self, timeout: Optional[int] = None):
        cfg = _get_provider_config("kisskh")
        super().__init__(provider_slug="kisskh", config=cfg, timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("KissKH scraper: no query provided")
            return []
        url = self.config.search_url_template.format(query=query, domain=self.config.domain)
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
        items, _, _ = self.parse_response(raw_data)
        return items


class GenericURLAdapter(UniversalScraper):
    def __init__(self, provider_slug: str, domain: str, timeout: Optional[int] = None):
        cfg = _get_provider_config(provider_slug)
        if not cfg:
            cfg = ProviderConfig(
                provider_slug=provider_slug,
                domain=domain,
                search_url_template=f"https://{domain}/api/search?q={{query}}",
                response_type="auto",
            )
        super().__init__(provider_slug=provider_slug, config=cfg, timeout=timeout)

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items, _, _ = self.parse_response(raw_data)
        return items


class HydraHDScraper(UniversalScraper):
    provider_slug = "hydrahd"

    def __init__(self, timeout: Optional[int] = None):
        cfg = _get_provider_config("hydrahd")
        super().__init__(provider_slug="hydrahd", timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("HydraHD scraper: no query provided")
            return []

        urls_to_try = [
            self.config.search_url_template.format(query=query, domain=self.config.domain)
        ]
        for fallback_domain in self.config.fallback_domains:
            urls_to_try.append(self.config.search_url_template.format(query=query, domain=fallback_domain))

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
        return self._fetch_url_with_config(url, headers=self.config.custom_headers)

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items, _, _ = self.parse_response(raw_data)
        return items


class AnimeWorldScraper(UniversalScraper):
    provider_slug = "animeworld"

    def __init__(self, timeout: Optional[int] = None):
        cfg = _get_provider_config("animeworld")
        super().__init__(provider_slug="animeworld", config=cfg, timeout=timeout)

    def _execute_run(self, query: Optional[str] = None) -> list[ScrapedItem]:
        if not query:
            logger.info("AnimeWorld scraper: no query provided")
            return []
        url = self.config.search_url_template.format(query=query, domain=self.config.domain)
        resp_data = self.fetch_url(url)
        if not resp_data:
            logger.warning("AnimeWorld scraper: no response data")
            return []

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
        items, _, _ = self._parse_html_content(html_content, base_url=f"https://{self.config.domain}")
        logger.info("AnimeWorld scraper: parsed %d items", len(items))
        return items

    def parse_items(self, raw_data: Any) -> list[ScrapedItem]:
        items, _, _ = self.parse_response(raw_data)
        return items
