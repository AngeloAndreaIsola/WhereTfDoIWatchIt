"""Compatibility boundary from canonical availability rows to tracker offers.

HTTP read paths use this adapter while the response/ranking layer continues to
consume its established ``tracker.services.offers.Offer`` representation.
It deliberately performs no cache lookup, refresh, or network activity.

Rows are read through the reconciliation layer, so what a viewer sees is one
effective offer per (content, provider, country, offer type) rather than every
raw observation a source happened to write. Raw rows stay untouched; this
module only projects the reconciled view onto the tracker representation.

Verified offers qualify for watchability. Recent title reports and older
positive observations are returned separately with their original timestamps.
"""
from __future__ import annotations

from datetime import UTC
from typing import Literal, NamedTuple, Optional
from urllib.parse import urlsplit

from availability.domain.enums import AvailabilityEvidence
from availability.domain.reconciliation import ReconciledOffer
from availability.services.offer_variant_projection import public_offer_variant
from availability.services.query import AvailabilityQueryService

from .offers import Offer, OfferLineage


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


class TitleOfferEvidence(NamedTuple):
    current: list[Offer]
    last_known: list[Offer]
    source_reported: list[Offer]

    @property
    def confirmed_and_history(self) -> OfferEvidence:
        return OfferEvidence(self.current, self.last_known)


def get_title_offer_evidence(
    movie, *, season: Optional[int] = None, country: Optional[str] = None, now=None,
) -> TitleOfferEvidence:
    """Read one title's reconciled offers, split into current and last-known."""
    service = AvailabilityQueryService()
    if movie.content_type == "series" and season is not None:
        result = service.get_reconciled_for_series(movie.tmdb_id, season, country, now=now)
    else:
        result = service.get_reconciled_for_content(
            movie.tmdb_id, movie.content_type or "movie", country, now=now,
        )
    return _split(result.offers)


def get_offer_evidence(movie, *, season=None, country=None) -> OfferEvidence:
    return get_title_offer_evidence(movie, season=season, country=country).confirmed_and_history


def get_local_offers(movie, *, season: Optional[int] = None, country: Optional[str] = None) -> list[Offer]:
    """Return current reconciled offers in the tracker response representation.

    Only offers reconciliation judged currently available *and* recently
    verified are returned; a deactivated, expired, not-yet-started, stale or
    uncorroborated observation never reaches ranking.
    """
    return get_offer_evidence(movie, season=season, country=country).current


def get_title_offer_evidence_bulk(movies, *, now=None) -> dict[int, TitleOfferEvidence]:
    """Read reconciled offers for many list/report titles in bounded queries.

    This deliberately supports only title-level rows; season detail pages keep
    using :func:`get_offer_evidence`, whose season boundary is distinct.
    """
    movie_list = list(movies)
    pairs = [(movie.tmdb_id, movie.content_type or "movie") for movie in movie_list]
    reconciled = AvailabilityQueryService().get_reconciled_for_contents(pairs, now=now)
    return {
        movie.id: _split(
            reconciled.get((movie.tmdb_id, movie.content_type or "movie"), [])
        )
        for movie in movie_list
    }



def get_offer_evidence_bulk(movies) -> dict[int, OfferEvidence]:
    return {
        movie_id: evidence.confirmed_and_history
        for movie_id, evidence in get_title_offer_evidence_bulk(movies).items()
    }

def get_local_offers_bulk(movies) -> dict[int, list[Offer]]:
    """Current (verified) offers only, per title; see :func:`get_offer_evidence_bulk`."""
    return {
        movie_id: evidence.current
        for movie_id, evidence in get_offer_evidence_bulk(movies).items()
    }


def _split(offers) -> TitleOfferEvidence:
    current: list[Offer] = []
    last_known: list[Offer] = []
    source_reported: list[Offer] = []
    for offer in offers:
        # ``currently_available`` already excludes deactivated, out-of-window
        # and mapping-doubted rows (those may belong to another title).
        if not offer.currently_available:
            continue
        if offer.evidence == AvailabilityEvidence.VERIFIED:
            current.append(_tracker_offer(offer))
        elif offer.evidence == AvailabilityEvidence.SOURCE_REPORTED:
            source_reported.append(_tracker_offer(offer))
        else:
            last_known.append(_tracker_offer(offer))
    return TitleOfferEvidence(current, last_known, source_reported)


def _tracker_offer(offer: ReconciledOffer) -> Offer:
    last_seen = offer.last_verified_at or offer.last_seen_available_at
    report = max(
        (obs for obs in offer.observations if obs.evidence == AvailabilityEvidence.SOURCE_REPORTED),
        key=lambda obs: (obs.last_seen, obs.source),
        default=None,
    ) if offer.evidence == AvailabilityEvidence.SOURCE_REPORTED else None
    url = offer.provider_url
    if report and (offer.price is None or offer.price.selected_variant is None):
        url = report.provider_url
    if url and any(url == variant.get("url") for price in offer.prices for variant in price.variants):
        url = _variant_display_url(url)
    return Offer(
        country=offer.country,
        service=offer.provider,
        offer_type=offer.offer_type,
        quality=offer.quality,
        url=url,
        audio_languages=_as_list(offer.audio_languages),
        subtitle_languages=_as_list(offer.subtitle_languages),
        price=(
            _price_dict(offer.price, offer, is_from=offer.price_is_from)
            if offer.price is not None else None
        ),
        prices=[_price_dict(price, offer) for price in offer.prices],
        availability_evidence=str(offer.evidence),
        last_seen_at=report.last_seen.isoformat() if report else last_seen.isoformat() if last_seen else None,
        observed_at=report.observed_at.isoformat() if report and report.observed_at else None,
        last_checked_at=report.title_checked_at.isoformat() if report else None,
        reporting_source=report.source if report else "",
        available_from=_window_timestamp(offer, "available_from"),
        available_until=_window_timestamp(offer, "available_until"),
        season_number=offer.season_number,
        episode_number=offer.episode_number,
        lineage=_offer_lineage(offer, report),
    )


def _safe_package_value(value):
    if value is None or isinstance(value, bool):
        return ""
    value = str(value)
    return value if len(value) <= 150 and all(c.isascii() and (c.isalnum() or c in "_.-") for c in value) else ""


def _offer_lineage(offer, report):
    """Bind to the observation behind the rendered rendition and its own variant."""
    base = {"content_type": offer.content_type, "title_id": offer.tmdb_id}
    candidates = [obs for obs in offer.observations if getattr(obs, "offer_identity", "") and obs.evidence == offer.evidence]
    if report and getattr(report, "offer_identity", ""):
        candidates = [report]
    chosen_price = offer.price if offer.price and offer.price.selected_variant else None
    if chosen_price:
        candidates = [obs for obs in candidates if obs.price == chosen_price]
    else:
        candidates = [obs for obs in candidates if obs.provider_url == offer.provider_url]
    if len(candidates) != 1:
        return OfferLineage(**base)
    observation = candidates[0]
    observed_at = observation.observed_at or observation.last_seen
    price = observation.price
    if price is None or price.observed_at is None or price.observed_at < observed_at:
        return OfferLineage(**base, identity=observation.offer_identity, source=observation.source,
                            observed_at=observed_at)
    variants = price.open_variants if offer.evidence == AvailabilityEvidence.VERIFIED else price.variants
    selected = chosen_price.selected_variant if chosen_price else None
    if selected is None:
        variants = [variant for variant in variants if variant.get("url", "") == observation.provider_url]
        selected = variants[0] if len(variants) == 1 else {}
    variant_id = str(selected.get("source_offer_id") or "")
    if sum(str(variant.get("source_offer_id") or "") == variant_id for variant in variants) > 1:
        variant_id = ""
    # Group-level mixed commercial actions are independent. A disagreement
    # over availability/window of the same stable observation is material.
    conflicted = any(
        conflict.kind in {"availability", "mapping", "window"}
        and observation.offer_id in conflict.offer_ids
        for conflict in offer.conflicts
    )
    return OfferLineage(
        **base, identity=observation.offer_identity, source=observation.source,
        package_id=_safe_package_value(selected.get("package_id")),
        package_label=_safe_package_value(selected.get("package")),
        variant_id=variant_id,
        observed_at=observed_at, conflicted=conflicted,
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


def _variant_display_url(value: object) -> str:
    """Project an HTTP(S) destination without provider query or fragment data."""
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        return ""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username is not None:
            return ""
    except ValueError:
        return ""
    if "\\" in url.netloc or "\\" in url.path:
        return ""
    return url._replace(query="", fragment="").geturl()


def _price_dict(price, offer: ReconciledOffer, *, is_from: bool = False) -> dict:
    """Serializable price row: amount as a string so Decimal precision survives.

    The amount is sent as a decimal string (never a binary float) and the
    currency as its explicit ISO code, so the frontend never has to parse a
    localized display string. ``is_current`` tells the reader whether the value
    was confirmed by the latest observation.
    """
    result = {
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
    if price.variants:
        result["variants"] = [
            {
                **public_offer_variant(
                    variant, provider=offer.provider, country=offer.country, offer_class=offer.offer_type,
                ),
                "url": _variant_display_url(variant.get("url")),
            }
            for variant in price.variants
        ]
    return result
