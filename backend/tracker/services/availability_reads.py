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
        _tracker_offer(offer)
        for offer in result.offers
        if offer.currently_available
    ]


def get_local_offers_bulk(movies) -> dict[int, list[Offer]]:
    """Read reconciled offers for many list/report titles in bounded queries.

    This deliberately supports only title-level rows; season detail pages keep
    using :func:`get_local_offers`, whose season boundary is distinct.
    """
    movie_list = list(movies)
    by_movie = {movie.id: [] for movie in movie_list}
    pairs = [(movie.tmdb_id, movie.content_type or "movie") for movie in movie_list]
    reconciled = AvailabilityQueryService().get_reconciled_for_contents(pairs)
    for movie in movie_list:
        by_movie[movie.id] = [
            _tracker_offer(offer)
            for offer in reconciled.get((movie.tmdb_id, movie.content_type or "movie"), [])
        ]
    return by_movie


def _tracker_offer(offer) -> Offer:
    return Offer(
        country=offer.country,
        service=offer.provider,
        offer_type=offer.offer_type,
        quality=offer.quality,
        url=offer.provider_url,
        audio_languages=_as_list(offer.audio_languages),
        subtitle_languages=_as_list(offer.subtitle_languages),
        price=(
            _price_dict(offer.price, is_from=offer.price_is_from)
            if offer.price is not None else None
        ),
        prices=[_price_dict(price) for price in offer.prices],
    )


def _as_list(values):
    """Tracker offers keep list-or-None language fields; DTOs stay immutable."""
    return None if values is None else list(values)


def _price_dict(price, *, is_from: bool = False) -> dict:
    """Serializable price row: amount as a string so Decimal precision survives.

    The amount is sent as a decimal string (never a binary float) and the
    currency as its explicit ISO code, so the frontend never has to parse a
    localized display string. ``is_current`` tells the reader whether the value
    was confirmed by the latest observation.
    """
    return {
        "amount": None if price.amount is None else str(price.amount),
        "currency": price.currency or "",
        "source": price.source,
        "source_offer_id": price.source_offer_id,
        "observed_at": (
            price.observed_at.isoformat() if price.observed_at else None
        ),
        "is_current": bool(price.is_current),
        "is_from": bool(is_from),
        "validation_error": price.validation_error or "",
    }
