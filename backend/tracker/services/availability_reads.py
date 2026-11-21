"""Compatibility boundary from canonical availability rows to tracker offers.

HTTP read paths use this adapter while the response/ranking layer continues to
consume its established ``tracker.services.offers.Offer`` representation.
It deliberately performs no cache lookup, refresh, or network activity.

Rows are read through the reconciliation layer, so what a viewer sees is one
effective offer per (content, provider, country, offer type) rather than every
raw observation a source happened to write. Raw rows stay untouched; this
module only projects the reconciled view onto the tracker representation.

Freshness is applied here, not by rewriting rows: only ``verified`` offers are
"current". Stale and uncorroborated positives are returned separately as
``last_known`` so callers can show them as history ("last seen … needs
recheck") without counting them as watchable now.
"""
from __future__ import annotations

from datetime import UTC
from typing import Literal, NamedTuple, Optional

from availability.domain.enums import AvailabilityEvidence
from availability.domain.reconciliation import ReconciledOffer
from availability.services.query import AvailabilityQueryService

from .offers import Offer


class OfferEvidence(NamedTuple):
    """One title's reconciled offers split by how well they are evidenced.

    ``current`` holds only recently verified offers — what ranking,
    watchability, filters and counts may treat as watchable now.
    ``last_known`` holds positive offers that are stale ("last seen") or
    uncorroborated (unknown freshness, mapping doubt): discoverable as
    history, never counted as current, never read as unavailable.
    """

    current: list[Offer]
    last_known: list[Offer]


def get_offer_evidence(
    movie, *, season: Optional[int] = None, country: Optional[str] = None
) -> OfferEvidence:
    """Read one title's reconciled offers, split into current and last-known."""
    service = AvailabilityQueryService()
    if movie.content_type == "series" and season is not None:
        result = service.get_reconciled_for_series(movie.tmdb_id, season, country)
    else:
        result = service.get_reconciled_for_content(
            movie.tmdb_id, movie.content_type or "movie", country
        )
    return _split(result.offers)


def get_local_offers(movie, *, season: Optional[int] = None, country: Optional[str] = None) -> list[Offer]:
    """Return current reconciled offers in the tracker response representation.

    Only offers reconciliation judged currently available *and* recently
    verified are returned; a deactivated, expired, not-yet-started, stale or
    uncorroborated observation never reaches ranking.
    """
    return get_offer_evidence(movie, season=season, country=country).current


def get_offer_evidence_bulk(movies) -> dict[int, OfferEvidence]:
    """Read reconciled offers for many list/report titles in bounded queries.

    This deliberately supports only title-level rows; season detail pages keep
    using :func:`get_offer_evidence`, whose season boundary is distinct.
    """
    movie_list = list(movies)
    pairs = [(movie.tmdb_id, movie.content_type or "movie") for movie in movie_list]
    reconciled = AvailabilityQueryService().get_reconciled_for_contents(pairs)
    return {
        movie.id: _split(
            reconciled.get((movie.tmdb_id, movie.content_type or "movie"), [])
        )
        for movie in movie_list
    }


def get_local_offers_bulk(movies) -> dict[int, list[Offer]]:
    """Current (verified) offers only, per title; see :func:`get_offer_evidence_bulk`."""
    return {
        movie_id: evidence.current
        for movie_id, evidence in get_offer_evidence_bulk(movies).items()
    }


def _split(offers) -> OfferEvidence:
    current: list[Offer] = []
    last_known: list[Offer] = []
    for offer in offers:
        # ``currently_available`` already excludes deactivated, out-of-window
        # and mapping-doubted rows (those may belong to another title).
        if not offer.currently_available:
            continue
        if offer.evidence == AvailabilityEvidence.VERIFIED:
            current.append(_tracker_offer(offer))
        else:
            last_known.append(_tracker_offer(offer))
    return OfferEvidence(current, last_known)


def _tracker_offer(offer: ReconciledOffer) -> Offer:
    last_seen = offer.last_verified_at or offer.last_seen_available_at
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
        availability_evidence=str(offer.evidence),
        last_seen_at=last_seen.isoformat() if last_seen else None,
        available_from=_window_timestamp(offer, "available_from"),
        available_until=_window_timestamp(offer, "available_until"),
        season_number=offer.season_number,
        episode_number=offer.episode_number,
    )


def _window_timestamp(
    offer: ReconciledOffer, field_name: Literal["available_from", "available_until"]
) -> str | None:
    value = getattr(offer, field_name)
    if value is None:
        return None
    # Keep the reconciled value only when supported by an observation for
    # this title. JustWatch's availableTo has unverified expiry semantics.
    if not any(
        getattr(observation, field_name) == value
        and not observation.mapping_doubt
        and (field_name != "available_until" or observation.source != "justwatch")
        for observation in offer.observations
    ):
        return None
    return value.astimezone(UTC).isoformat()


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
