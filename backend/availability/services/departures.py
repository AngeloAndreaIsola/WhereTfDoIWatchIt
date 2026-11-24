"""Evidence qualification shared by Leaving Soon and title refresh scheduling."""

from datetime import datetime

from availability.domain.enums import AvailabilityEvidence, ConflictKind, ContentType
from availability.models import OfferEvidence, build_offer_identity


UNSAFE_CONFLICTS = frozenset({
    ConflictKind.AVAILABILITY,
    ConflictKind.MAPPING,
    ConflictKind.WINDOW,
    ConflictKind.OFFER_TYPE,
})


def _source_declares_window(source, content_type):
    # PBS computes group windows from asset dates. Tubi copies the series
    # date to season rows. Neither declares an expiry at those scopes.
    if source == "pbs":
        return content_type == ContentType.EPISODE
    if source == "tubi" and content_type == ContentType.SEASON:
        return False
    return bool(source) and not source.startswith("justwatch")


def recorded_presence(reconciled, country):
    """Current reporting sources for dated identities that have a source ledger."""
    identities = {
        _observation_identity(offer, observation)
        for offers in reconciled.values()
        for offer in offers
        if offer.country.upper() == country and offer.available_until is not None
        for observation in offer.observations
        if observation.available_until is not None
    }
    rows = OfferEvidence.objects.filter(offer_identity__in=identities).values_list(
        "offer_identity", "source__slug", "is_present",
    )
    presence = {}
    for identity, source, present in rows:
        sources = presence.setdefault(identity, set())
        if present:
            sources.add(source)
    return presence


def _observation_identity(offer, observation):
    return build_offer_identity(
        offer.provider, offer.country, offer.content_type, offer.tmdb_id,
        offer.season_number, offer.episode_number, offer.offer_type, observation.provider_content_id,
    )


def confirmed_expiry(offer, now: datetime, *, presence=None):
    """Return (expiry, supporting observations), or (None, ()) when unproven.

    Reconciliation may select an end date reported only by JustWatch while a
    different source verifies *presence*. Those separate facts must never be
    combined into a confirmed departure.
    """
    expiry = offer.available_until
    if (
        not offer.currently_available
        or offer.evidence != AvailabilityEvidence.VERIFIED
        or expiry is None
        or expiry <= now
        or any(conflict.kind in UNSAFE_CONFLICTS for conflict in offer.conflicts)
    ):
        return None, ()

    supporters = tuple(
        observation
        for observation in offer.observations
        if observation.available_until == expiry
        and observation.evidence == AvailabilityEvidence.VERIFIED
        and not observation.mapping_doubt
        and _source_declares_window(observation.source.lower(), offer.content_type)
    )
    if presence:
        # A co-reporting source can keep the canonical row available after the
        # writer of its end date withdrew. Rows predating the ledger still
        # carry their own source observation.
        active_supporters = []
        for observation in supporters:
            sources = presence.get(_observation_identity(offer, observation))
            if sources is None or observation.source in sources:
                active_supporters.append(observation)
        supporters = tuple(active_supporters)
    return (expiry, supporters) if supporters else (None, ())
