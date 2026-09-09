from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from tracker.models import Preference, Provider, ProviderCategory

from .cache import AvailabilityCache
from .enrichment import enrich_movie_from_tmdb
from .preferences import resolve_preferences
from .ranking import rank_offers
from .presentation import present_offer
from .tmdb import TMDbClient
from .streaming_api import service_display_name
from .watchability import get_user_subscriptions


@dataclass
class OfficialOffersPipelineResult:
    offers_out: list[dict]


class OfficialOffersPipeline:
    def __init__(
        self,
        user_id: int = 1,
        default_provider_slug: str = "prime",
        provider_category_set: Optional[set[str]] = None,
    ):
        self.user_id = user_id
        self.default_provider_slug = default_provider_slug
        self.provider_category_set = provider_category_set

    def get_pref(self) -> Preference:
        pref_dict = resolve_preferences(user_id=self.user_id)
        return Preference(
            user_id=self.user_id,
            audio_languages=pref_dict["audio_languages"],
            subtitle_languages=pref_dict["subtitle_languages"],
            candidate_countries=pref_dict["candidate_countries"],
        )

    def _build_provider_map(self) -> dict[str, Provider]:
        return {p.slug: p for p in Provider.objects.all()}

    def _qualify_offers(self, offers, provider_map: dict[str, Provider], provider_slug: str):
        official_categories = self.provider_category_set
        if official_categories is None:
            official_categories = {ProviderCategory.OFFICIAL, ProviderCategory.BVOD, ProviderCategory.AVOD, ProviderCategory.TVOD, ProviderCategory.FAST}

        user_subs = get_user_subscriptions(self.user_id)

        qualifying = []
        for o in offers:
            if (o.offer_type or "").lower() not in ("subscription", "free"):
                continue

            slug = o.service or ""
            prov = provider_map.get(slug)

            if prov is None:
                if slug.lower() == provider_slug.lower():
                    qualifying.append(o)
                continue

            if prov.category in official_categories:
                service_clean = slug.lower().strip()
                is_subscribed = (
                    prov.category in (ProviderCategory.BVOD, ProviderCategory.AVOD, ProviderCategory.TVOD, ProviderCategory.FAST)
                    or service_clean in user_subs
                    or any(
                        sub in service_clean or service_clean in sub
                        for sub in user_subs
                        if len(sub) >= 3
                    )
                )
                if is_subscribed:
                    if not o.country and prov.country_code:
                        o.country = prov.country_code
                    qualifying.append(o)

        return qualifying

    def get_cached_offers(self, movie):
        cache = AvailabilityCache()
        if not getattr(settings, "PRIME_LIVE_FETCH", True):
            return cache.read_offers(movie)

        try:
            cache.get_or_refresh(movie)
        except Exception:
            pass
        return cache.read_offers(movie)

    def rank_and_present(self, qualifying_offers, preference: Preference, provider_map: dict[str, Provider]):
        ranked = rank_offers(qualifying_offers, preference, country_filter=set())
        offers_out = []
        for offer in ranked:
            presented = present_offer(offer, provider_map)
            offer_dict = offer.to_dict()
            offer_dict["service"] = presented.service_display
            offer_dict["source_indicator"] = presented.source_indicator
            offer_dict["country"] = presented.country
            offers_out.append(offer_dict)
        return offers_out

    def run(self, movie):
        pref = self.get_pref()
        provider_map = self._build_provider_map()
        provider_slug = getattr(settings, "DEFAULT_PREFERENCE_PROVIDER", self.default_provider_slug)

        enrich_movie_from_tmdb(movie)
        offers = self.get_cached_offers(movie)
        qualifying = self._qualify_offers(offers, provider_map, provider_slug)
        offers_out = self.rank_and_present(qualifying, pref, provider_map)
        return OfficialOffersPipelineResult(offers_out=offers_out)
