"""Provider presentation seam: resolve slug → display name, country, badge.

Three independent paths previously answered "given a raw offer, what
does the UI show?":

1. views.py:prime_movie  (lines 1160–1177)
2. watchability.py:evaluate_watchability  (lines 119–164)
3. api_v2.py:unofficial_movie_offers  (lines 320–332)

This module is the single source of truth. Each caller builds a
provider_map once and passes it in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from tracker.models import ProviderCategory
from tracker.services.offers import Offer


@dataclass
class PresentedOffer:
    service_display: str       # provider.name or service_display_name(slug)
    source_indicator: str      # provider.source_indicator or "Official"
    country: str               # observed offer country; never provider home country
    is_official: bool          # provider.category in {OFFICIAL, FREE}


def present_offer(
    offer: Any,
    provider_map: dict[str, "Provider"],
) -> PresentedOffer:
    """Resolve an offer into its UI-facing display fields.

    Accepts any object with ``.service`` and ``.country`` attributes
    (Offer, RankedOffer, etc.).
    """

    from tracker.services.streaming_api import service_display_name

    service = offer.service or ""
    country = (offer.country or "").upper().strip()

    service_clean = service.lower().strip()
    prov = provider_map.get(service_clean)

    if prov is not None:
        service_display = prov.name
        source_indicator = prov.source_indicator
        is_official = prov.category != ProviderCategory.SCRAPER
    else:
        # Legacy: unknown slug — fall back to the mapping table.
        service_display = service_display_name(service)
        source_indicator = "Official"
        is_official = True
        # country stays as-is (may be empty for unmapped legacy slugs)

    return PresentedOffer(
        service_display=service_display,
        source_indicator=source_indicator,
        country=country,
        is_official=is_official,
    )
