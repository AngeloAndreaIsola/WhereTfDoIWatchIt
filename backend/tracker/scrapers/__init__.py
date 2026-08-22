"""Expose scrapers and registry."""
from tracker.scrapers.adapters import AnimeWorldScraper, HydraHDScraper, KissKHScraper
from tracker.scrapers.base import BaseScraper, ContentMatcher, ScrapedItem

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "kisskh": KissKHScraper,
    "hydrahd": HydraHDScraper,
    "animeworld": AnimeWorldScraper,
}

__all__ = [
    "BaseScraper",
    "ScrapedItem",
    "ContentMatcher",
    "KissKHScraper",
    "HydraHDScraper",
    "AnimeWorldScraper",
    "SCRAPER_REGISTRY",
]
