from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from tracker.models import Preference, Provider, ProviderCategory

from .enrichment import enrich_movie_from_tmdb
from .preferences import build_preference, resolve_preferences
from .ranking import rank_offers
from .presentation import present_offer
from .watchability import QUALIFYING_OFFER_TYPES
from .availability_reads import get_local_offers
from .access import build_reach, build_user_access, classify_offer

logger = logging.getLogger(__name__)


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
        """Keep every *catalogued, displayable* offer in this page's scope.

        Entitlement is deliberately **not** decided here. This method answers
        "which offers can the detail page show as concrete availability?" — a
        real catalogue provider in the page's category scope — not "which can
        the user play?". The access resolver (``tracker.services.access``)
        annotates each kept offer with ``is_entitled``/``is_accessible`` so an
        offer from a service the user has not selected still renders, muted and
        labelled, instead of vanishing.

        The gate this replaces dropped every offer from a provider the user had
        not subscribed to. For a title whose only offers were on unowned
        services (movie 194: Netflix/Max/Hulu, none selected) that emptied the
        list while the source-first verdict still said ``available`` — so the
        page showed "a source reported this title as available, but no current
        offer could be displayed" about offers it had silently discarded.
        """
        official_categories = self.provider_category_set
        if official_categories is None:
            official_categories = {ProviderCategory.OFFICIAL, ProviderCategory.BVOD, ProviderCategory.AVOD, ProviderCategory.TVOD, ProviderCategory.FAST}

        qualifying = []
        for o in offers:
            if (o.offer_type or "").lower() not in QUALIFYING_OFFER_TYPES:
                continue

            slug = o.service or ""
            prov = provider_map.get(slug)

            if prov is None:
                # An un-catalogued slug. Only the configured default provider is
                # trusted, so a stray source slug cannot inject a phantom service.
                if slug.lower() == provider_slug.lower():
                    qualifying.append(o)
                continue

            if prov.category in official_categories:
                qualifying.append(o)

        return qualifying

    def get_cached_offers(self, movie, season: Optional[int] = None):
        """Read current offers from the canonical local availability model.

        Refreshing is intentionally not part of this path.  A cron/management
        refresh may still use the legacy cache while that write path is moved,
        but a detail request only consumes persisted Offer rows.
        """
        return get_local_offers(movie, season=season)

    def rank_and_present(
        self,
        qualifying_offers,
        preference: Preference,
        provider_map: dict[str, Provider],
        current_country: Optional[str] = None,
        access=None,
        reach=None,
        classify_preference: Optional[Preference] = None,
    ):
        pref_for_ranking = classify_preference if classify_preference is not None else preference
        ranked = rank_offers(
            qualifying_offers,
            pref_for_ranking,
            country_filter=set(),
            current_country=current_country,
        )
        annotated: list[tuple[tuple[int, int], dict]] = []
        for offer in ranked:
            presented = present_offer(offer, provider_map)
            offer_dict = offer.to_dict()
            offer_dict["service"] = presented.service_display
            offer_dict["source_indicator"] = presented.source_indicator
            offer_dict["country"] = presented.country
            # Sorts the offer the user can actually watch ahead of an offer they
            # merely *could* reach (unowned/foreign), without disturbing the
            # strict-priority ranking *within* either group. Stable, so pure
            # ranking order is preserved among equal offers.
            order_key = (0, 0)
            # Annotate with the shared access model so the detail page reads
            # the same rules as the watchlist badge (see tracker.services.access).
            if access is not None and reach is not None and classify_preference is not None:
                classified = classify_offer(
                    offer,
                    access=access,
                    preference=classify_preference,
                    reach=reach,
                    provider_map=provider_map,
                )
                offer_dict["access_type"] = classified.access_type
                offer_dict["access_label"] = classified.access_label
                offer_dict["locality"] = classified.locality
                offer_dict["is_accessible"] = classified.is_accessible
                offer_dict["is_entitled"] = classified.is_entitled
                offer_dict["requires_payment"] = classified.requires_payment
                offer_dict["is_addon"] = classified.is_addon
                offer_dict["required_base"] = classified.required_base
                offer_dict["required_base_display"] = classified.required_base_display
                offer_dict["rejection_reasons"] = classified.rejection_reasons
                order_key = (
                    0 if classified.is_accessible else 1,
                    0 if classified.is_entitled else 1,
                )
            annotated.append((order_key, offer_dict))
        annotated.sort(key=lambda pair: pair[0])
        return [offer_dict for _, offer_dict in annotated]

    def run(
        self,
        movie,
        current_country: Optional[str] = None,
        season: Optional[int] = None,
    ):
        pref = self.get_pref()
        provider_map = self._build_provider_map()
        provider_slug = getattr(settings, "DEFAULT_PREFERENCE_PROVIDER", self.default_provider_slug)

        enrich_movie_from_tmdb(movie)
        offers = self.get_cached_offers(movie, season=season)
        qualifying = self._qualify_offers(offers, provider_map, provider_slug)
        # Ranking keeps the legacy preference (unchanged ordering); the shared
        # classifier uses the full preference (language rule + VPN) purely to
        # annotate eligibility, never to reorder or drop offers here.
        classify_pref = build_preference(self.user_id)
        access = build_user_access(self.user_id)
        reach = build_reach(classify_pref, current_country)
        offers_out = self.rank_and_present(
            qualifying, pref, provider_map, current_country=current_country,
            access=access, reach=reach, classify_preference=classify_pref,
        )
        return OfficialOffersPipelineResult(offers_out=offers_out)
