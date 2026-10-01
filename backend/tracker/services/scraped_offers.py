"""Scraped (BVOD) offer read seam shared by prime and v2 offer paths.

Two callers previously read ``tracker.ScrapedContentMapping`` directly to
answer "where is this movie watchable through scraped sources?":

1. ``views._build_prime_offers``  (bvod/avod/tvod/fast block)
2. ``api_v2.unofficial_movie_offers``  (scraper categories, optionally + bvod)

Both now read the canonical catalogue through
``ProviderCatalogueQueryService.list_for_tmdb`` (provider observations joined
to their ``ContentMapping`` resolution) and assemble ``Offer`` objects
identically to the legacy block: ``offer_type="free"``, quality from
``Provider.config``, url/languages from the stored row, and
``is_scraped_source=True`` so presentation and ranking treat them as
scraped. The country on each offer is the row's canonical scrape country
(``metadata.country_code``), the value the legacy payload only carried when
a mapping happened to record one.

Local-only: this module reaches no source, HTTP client, or refresh module.
"""
from __future__ import annotations

from tracker.models import Provider
from tracker.services.offers import Offer

from availability.services.catalogue_query import ProviderCatalogueQueryService

# The scraped BVOD block as prime has always presented it: subscription-flavoured
# unofficial categories. v2 asks for {"scraper"} or {"scraper"} + these.
BVOD_CATEGORIES = ("bvod", "avod", "tvod", "fast")


def get_scraped_offers(movie, *, provider_categories):
    """Ranked-input offers + presentation provider map for one tracked item.

    Returns ``(offers, provider_map)`` where *offers* are canonical
    ``Offer`` objects (one per currently-available unofficial row mapped to
    ``movie.tmdb_id``, newest observation first) and *provider_map* is the
    slug → ``Provider`` dict ``present_offer`` needs for display resolution.

    *provider_categories* is the raw provider category scope — prime passes
    the bvod set, v2 the scraper set (or scraper + bvod when
    ``include_bvod=true``). Rows a canonical source observed are excluded
    upstream by the unofficial row predicate: they belong to the official
    read path.
    """
    entries = ProviderCatalogueQueryService().list_for_tmdb(
        movie.tmdb_id,
        provider_categories=set(provider_categories),
    )
    if not entries:
        return [], {}

    provider_map = {
        provider.slug: provider
        for provider in Provider.objects.filter(
            slug__in={entry.provider for entry in entries}
        )
    }
    offers = []
    for entry in entries:
        provider = provider_map.get(entry.provider)
        config = provider.config if provider is not None else None
        quality = config.get("quality") if isinstance(config, dict) else None
        offers.append(
            Offer.from_dict(
                {
                    "country": entry.country,
                    "service": entry.provider,
                    "offer_type": "free",
                    "quality": quality,
                    "url": entry.provider_url,
                    "audio": entry.audio_languages,
                    "subs": entry.subtitle_languages,
                    "is_scraped_source": True,
                }
            )
        )
    return offers, provider_map
