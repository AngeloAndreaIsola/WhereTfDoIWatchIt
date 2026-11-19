from __future__ import annotations

from tracker.scrapers.base import BaseScraper, ScrapedItem, ContentMatcher
from tracker.scrapers.altadefinizione import AltaDefinizioneScraper
from tracker.scrapers.bilibili_tv import BilibiliTVScraper
from tracker.scrapers.cineblog01 import CineBlog01Scraper
from tracker.scrapers.nhk import NHKScraper
from tracker.scrapers.ok_ru import OKRuScraper
from tracker.scrapers.rai_play import RaiPlayScraper
from tracker.scrapers.streamingcommunity import StreamingCommunityScraper
from tracker.scrapers.adapters import (
    AnimeWorldScraper,
    GenericURLAdapter,
    HydraHDScraper,
    KissKHScraper,
)

# Scraper registry: maps provider slug -> dotted import path, resolved lazily
# by api_v2._resolve_scraper(). The priority sources (RaiPlay, NHK) live in
# dedicated classes (BBC iPlayer was retired in favor of its canonical catalogue
# source BBCIPlayerSource); the legacy independent-source scrapers (KissKH,
# HydraHD, AnimeWorld) remain available for secondary providers. One entry per
# provider — the old alias slugs (rai-play, bbc, nhk) were duplicate provider
# rows and no longer exist (migration 0033_merge_duplicate_providers).
SCRAPER_REGISTRY = {
    "nhk_world": "tracker.scrapers.nhk.NHKScraper",
    "raiplay": "tracker.scrapers.rai_play.RaiPlayScraper",
    "kisskh": "tracker.scrapers.adapters.KissKHScraper",
    "hydrahd": "tracker.scrapers.adapters.HydraHDScraper",
    "animeworld": "tracker.scrapers.adapters.AnimeWorldScraper",
    "streamingcommunity": "tracker.scrapers.streamingcommunity.StreamingCommunityScraper",
    "cineblog01": "tracker.scrapers.cineblog01.CineBlog01Scraper",
    "altadefinizione": "tracker.scrapers.altadefinizione.AltaDefinizioneScraper",
}

__all__ = [
    "BaseScraper",
    "ScrapedItem",
    "ContentMatcher",
    "SCRAPER_REGISTRY",
    "resolve_scraper_class",
    "NHKScraper",
    "RaiPlayScraper",
    "KissKHScraper",
    "HydraHDScraper",
    "AnimeWorldScraper",
    "GenericURLAdapter",
    "StreamingCommunityScraper",
    "CineBlog01Scraper",
    "AltaDefinizioneScraper",
]


def resolve_scraper_class(provider_slug: str):
    """Resolve a provider slug to a scraper class via SCRAPER_REGISTRY.

    Registry values are dotted import paths (strings); this returns the actual
    class so callers can instantiate it. Returns None when the slug is unknown.
    """
    scraper_ref = SCRAPER_REGISTRY.get((provider_slug or "").lower())
    if not scraper_ref:
        return None
    if not isinstance(scraper_ref, str):
        return scraper_ref
    module_path, class_name = scraper_ref.rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)
