"""Expose scrapers, universal crawler engine, and registry."""
from tracker.scrapers.adapters import AnimeWorldScraper, GenericURLAdapter, HydraHDScraper, KissKHScraper
from tracker.scrapers.base import BaseScraper, ContentMatcher, ScrapedItem
from tracker.scrapers.configs import ProviderConfig, DEFAULT_PROVIDER_CONFIGS, validate_provider_config
from tracker.scrapers.universal import UniversalScraper

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    "kisskh": KissKHScraper,
    "hydrahd": HydraHDScraper,
    "animeworld": AnimeWorldScraper,
    "universal": UniversalScraper,
    "generic": GenericURLAdapter,
}

__all__ = [
    "BaseScraper",
    "ScrapedItem",
    "ContentMatcher",
    "UniversalScraper",
    "KissKHScraper",
    "HydraHDScraper",
    "AnimeWorldScraper",
    "GenericURLAdapter",
    "ProviderConfig",
    "DEFAULT_PROVIDER_CONFIGS",
    "validate_provider_config",
    "SCRAPER_REGISTRY",
]
