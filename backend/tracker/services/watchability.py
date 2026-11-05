"""Watchability facade over the shared access resolver.

This module owns no classification logic of its own any more: it is a thin
adapter so existing callers keep their stable shapes
(:class:`WatchabilityStatus`, :class:`WatchabilityResult`,
:class:`EvaluatedOffer`) while the rules live in exactly one place —
:mod:`tracker.services.access`. The watchlist badge and the title-detail
offer list therefore cannot disagree, because both read
:func:`access.resolve_title`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tracker.models import Movie, Preference, Provider, Subscription
from tracker.services.access import (
    INCLUDED_ACCESS_TYPES,
    AccessResult,
    AccessType,
    AccessVerdict,
    ClassifiedOffer,
    RefreshState,
    classify_offer,
    normalize_service_slug,
    UserAccess,
    resolve_title,
)
from tracker.services.offers import Offer

__all__ = [
    "QUALIFYING_OFFER_TYPES",
    "WatchabilityStatus",
    "RefreshState",
    "EvaluatedOffer",
    "WatchabilityResult",
    "get_user_subscriptions",
    "evaluate_watchability",
]

# Offer types that count as "included" for watchability: a subscription the
# user may hold, or a genuinely free stream. ``free_with_ads`` (AVOD) is a free
# stream — Tubi, Pluto TV, YouTube, Mediaset AVOD, ABC iview, PBS and JustWatch
# ``ADS`` all persist it — so it must qualify exactly like ``free``. Rent and
# buy stay excluded by design (SPEC).
QUALIFYING_OFFER_TYPES = frozenset({"subscription", "free", "free_with_ads", "addon"})


class WatchabilityStatus:
    WATCHABLE_HERE = "watchable_here"        # 🟢
    WATCHABLE_VPN = "watchable_vpn"          # 🟡
    NOT_WATCHABLE = "not_watchable"          # ⚫
    UNKNOWN = "unknown"                      # ⚪
    CHECKING = "checking"                    # 🔵
    # Canonical availability is positive, but nothing qualifies for *this*
    # user (no subscription, out-of-region, or a language mismatch).
    AVAILABLE_ELSEWHERE = "available_elsewhere"  # 🟠
    # The only accessible offers require an extra payment (rent/buy). Distinct
    # from 🟠 so "buy it" is never conflated with "you have it".
    PURCHASE_ONLY = "purchase_only"          # 🟣


_VERDICT_TO_STATUS = {
    AccessVerdict.AVAILABLE_HERE: WatchabilityStatus.WATCHABLE_HERE,
    AccessVerdict.AVAILABLE_VPN: WatchabilityStatus.WATCHABLE_VPN,
    AccessVerdict.PURCHASE_ONLY: WatchabilityStatus.PURCHASE_ONLY,
    AccessVerdict.AVAILABLE_ELSEWHERE: WatchabilityStatus.AVAILABLE_ELSEWHERE,
    AccessVerdict.UNAVAILABLE: WatchabilityStatus.NOT_WATCHABLE,
    AccessVerdict.UNKNOWN: WatchabilityStatus.UNKNOWN,
    AccessVerdict.CHECKING: WatchabilityStatus.CHECKING,
}


@dataclass
class EvaluatedOffer:
    country: str
    service: str
    offer_type: str
    quality: str
    url: str
    price_info: str = ""
    service_display: str = ""
    is_qualifying_offer_type: bool = False
    is_user_subscribed: bool = False
    is_language_match: bool = True
    is_current_country: bool = False
    is_candidate_country: bool = False
    is_watchable: bool = False
    rejection_reasons: list[str] = field(default_factory=list)
    ranking_score: tuple = field(default_factory=tuple)
    # Additive classification (shared with the detail path).
    access_type: str = AccessType.UNKNOWN
    access_label: str = ""
    locality: str = ""
    is_accessible: bool = False
    is_entitled: bool = False
    requires_payment: bool = False
    source_indicator: str = ""

    @classmethod
    def from_classified(cls, c: ClassifiedOffer) -> "EvaluatedOffer":
        return cls(
            country=c.country,
            service=c.service,
            offer_type=c.offer_type,
            quality=c.quality,
            url=c.url,
            price_info="",
            service_display=c.service_display,
            is_qualifying_offer_type=c.is_qualifying_offer_type,
            is_user_subscribed=c.is_entitled,
            is_language_match=c.is_language_match,
            is_current_country=c.is_current_country,
            is_candidate_country=c.is_candidate_country,
            is_watchable=c.is_accessible,
            rejection_reasons=list(c.rejection_reasons),
            ranking_score=c.ranking_score,
            access_type=c.access_type,
            access_label=c.access_label,
            locality=c.locality,
            is_accessible=c.is_accessible,
            is_entitled=c.is_entitled,
            requires_payment=c.requires_payment,
            source_indicator=c.source_indicator,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "price_info": self.price_info,
            "service_display": self.service_display,
            "is_qualifying_offer_type": self.is_qualifying_offer_type,
            "is_user_subscribed": self.is_user_subscribed,
            "is_language_match": self.is_language_match,
            "is_current_country": self.is_current_country,
            "is_candidate_country": self.is_candidate_country,
            "is_watchable": self.is_watchable,
            "rejection_reasons": self.rejection_reasons,
            "access_type": self.access_type,
            "access_label": self.access_label,
            "locality": self.locality,
            "is_accessible": self.is_accessible,
            "requires_payment": self.requires_payment,
        }


@dataclass
class WatchabilityResult:
    movie_id: int
    status: str  # watchable_here | watchable_vpn | purchase_only | not_watchable | unknown | checking | available_elsewhere
    status_label: str
    status_symbol: str
    best_offer: Optional[EvaluatedOffer] = None
    watchable_offers: list[EvaluatedOffer] = field(default_factory=list)
    vpn_offers: list[EvaluatedOffer] = field(default_factory=list)
    purchase_offers: list[EvaluatedOffer] = field(default_factory=list)
    excluded_offers: list[EvaluatedOffer] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    refresh_state: Optional[str] = None
    access_flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_label": self.status_label,
            "status_symbol": self.status_symbol,
            "best_offer": self.best_offer.to_dict() if self.best_offer else None,
            "watchable_offers": [o.to_dict() for o in self.watchable_offers],
            "vpn_offers": [o.to_dict() for o in self.vpn_offers],
            "purchase_offers": [o.to_dict() for o in self.purchase_offers],
            "excluded_offers": [o.to_dict() for o in self.excluded_offers],
            "explanation": self.explanation,
            "refresh_state": self.refresh_state,
            "access_flags": self.access_flags,
        }


def get_user_subscriptions(user_id: int = 1) -> set[str]:
    """Return set of normalized provider slugs and subscription names for user.

    Legacy flat set (slugs + names, plus auto-counted broadcaster/scraper
    providers). Kept for callers that only need membership testing; the
    access-mode-aware view of the same rows is :func:`access.build_user_access`.
    """
    subs = list(Subscription.objects.filter(user_id=user_id).values_list("provider__slug", "name"))
    subscribed = set()
    for slug, name in subs:
        if slug:
            canon = normalize_service_slug(slug)
            if canon:
                subscribed.add(canon)
        if name:
            canon = normalize_service_slug(name)
            if canon:
                subscribed.add(canon)

    free_or_scrapers = Provider.objects.filter(
        category__in=["bvod", "scraper"], is_enabled=True
    ).values_list("slug", flat=True)
    for s in free_or_scrapers:
        canon = normalize_service_slug(s)
        if canon:
            subscribed.add(canon)

    subscribed.discard("")
    return subscribed


def _to_watchability_result(result: AccessResult) -> WatchabilityResult:
    return WatchabilityResult(
        movie_id=result.movie_id,
        status=_VERDICT_TO_STATUS.get(result.verdict, WatchabilityStatus.UNKNOWN),
        status_label=result.status_label,
        status_symbol=result.status_symbol,
        best_offer=(
            EvaluatedOffer.from_classified(result.best_offer)
            if result.best_offer else None
        ),
        watchable_offers=[EvaluatedOffer.from_classified(c) for c in result.accessible_offers],
        vpn_offers=[EvaluatedOffer.from_classified(c) for c in result.vpn_offers],
        purchase_offers=[EvaluatedOffer.from_classified(c) for c in result.purchase_offers],
        excluded_offers=[EvaluatedOffer.from_classified(c) for c in result.excluded_offers],
        explanation=result.explanation,
        refresh_state=result.refresh_state,
        access_flags=result.access_flags,
    )


def evaluate_watchability(
    movie: Movie,
    offers: Iterable[Offer],
    preference: Preference,
    current_country: Optional[str] = None,
    user_subscriptions: Optional[set[str]] = None,
    refresh_state: Optional[str] = None,
    access: Optional[UserAccess] = None,
    provider_map: Optional[dict[str, Provider]] = None,
) -> WatchabilityResult:
    """Evaluate full watchability status and best way to watch for a movie/series.

    A façade over :func:`tracker.services.access.resolve_title` — the shared
    resolver the detail page also uses — so the badge here and the offer list
    there are always derived from one set of rules.

    ``refresh_state`` (a :class:`RefreshState` value, or ``None`` when the
    caller has no queue knowledge) is consulted only when the canonical store
    holds no offers for the title. When offers exist they always decide the
    status; a queued re-check never hides a known result.
    """
    result = resolve_title(
        movie,
        offers,
        preference,
        current_country=current_country,
        user_subscriptions=user_subscriptions,
        refresh_state=refresh_state,
        access=access,
        provider_map=provider_map,
    )
    return _to_watchability_result(result)


# Re-exported for callers that imported the classifier helper directly.
classify = classify_offer
INCLUDED_TYPES = INCLUDED_ACCESS_TYPES
