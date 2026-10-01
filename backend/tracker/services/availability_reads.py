"""Compatibility boundary from canonical availability rows to tracker offers.

HTTP read paths use this adapter while the response/ranking layer continues to
consume its established ``tracker.services.offers.Offer`` representation.
It deliberately performs no cache lookup, refresh, or network activity.

Rows are read through the reconciliation layer, so what a viewer sees is one
effective offer per (content, provider, country, offer type) rather than every
raw observation a source happened to write. Raw rows stay untouched; this
module only projects the reconciled view onto the tracker representation.
"""
from __future__ import annotations

from typing import Optional

from availability.services.query import AvailabilityQueryService

from .offers import Offer


def get_local_offers(movie, *, season: Optional[int] = None, country: Optional[str] = None) -> list[Offer]:
    """Return current reconciled offers in the tracker response representation.

    Only offers reconciliation judged currently available are returned; a
    deactivated, expired, or not-yet-started observation never reaches ranking.
    """
    service = AvailabilityQueryService()
    if movie.content_type == "series" and season is not None:
        result = service.get_reconciled_for_series(movie.tmdb_id, season, country)
    else:
        result = service.get_reconciled_for_content(
            movie.tmdb_id, movie.content_type or "movie", country
        )

    return [
        Offer(
            country=offer.country,
            service=offer.provider,
            offer_type=offer.offer_type,
            quality=offer.quality,
            url=offer.provider_url,
            audio_languages=_as_list(offer.audio_languages),
            subtitle_languages=_as_list(offer.subtitle_languages),
        )
        for offer in result.offers
        if offer.currently_available
    ]


def _as_list(values):
    """Tracker offers keep list-or-None language fields; DTOs stay immutable."""
    return None if values is None else list(values)
