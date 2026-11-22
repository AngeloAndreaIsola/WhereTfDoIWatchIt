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
from tracker.ownership import resolve_personal_owner_id
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
    "LastKnownAvailability",
    "describe_last_known",
    "qualify_watchability",
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
    # No recently verified offer, but positive evidence that is stale ("last
    # seen") or uncorroborated. Never counted as watchable, never read as
    # genuinely unavailable.
    NEEDS_RECHECK = "needs_recheck"          # 🕓


NEEDS_RECHECK_SYMBOL = "🕓"

#: Statuses a last-known (stale/unverified) offer may replace: only "nothing
#: current". A verified result (🟢🟡🟣🟠) and an in-flight check stay as-is.
_QUALIFIABLE = frozenset({
    WatchabilityStatus.NOT_WATCHABLE,
    WatchabilityStatus.UNKNOWN,
    AccessVerdict.UNAVAILABLE,
    AccessVerdict.UNKNOWN,
})


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
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
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
    available_from: Optional[str] = None
    available_until: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

    @classmethod
    def from_classified(cls, c: ClassifiedOffer) -> "EvaluatedOffer":
        return cls(
            country=c.country,
            service=c.service,
            offer_type=c.offer_type,
            quality=c.quality,
            url=c.url,
            audio_languages=list(c.audio_languages),
            subtitle_languages=list(c.subtitle_languages),
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
            available_from=c.available_from,
            available_until=c.available_until,
            season_number=c.season_number,
            episode_number=c.episode_number,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "audio_languages": self.audio_languages,
            "subtitle_languages": self.subtitle_languages,
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
            "available_from": self.available_from,
            "available_until": self.available_until,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
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
    last_known: Optional["LastKnownAvailability"] = None

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
            **_last_known_fields(self.last_known),
        }


def get_user_subscriptions(user_id: int | None = None) -> set[str]:
    """Return set of normalized provider slugs and subscription names for user.

    Legacy flat set (slugs + names, plus auto-counted broadcaster/scraper
    providers). Kept for callers that only need membership testing; the
    access-mode-aware view of the same rows is :func:`access.build_user_access`.
    """
    user_id = resolve_personal_owner_id() if user_id is None else user_id
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


@dataclass
class LastKnownAvailability:
    """Stale/uncorroborated positive offers of one title, classified for the user.

    ``evidence`` is ``stale`` when any offer was last seen by a source whose
    freshness window has lapsed, else ``unknown``. ``status`` is what the
    evidence *would* mean were it current (e.g. ``watchable_here``) — kept
    apart from the title's real status so it is never counted as watchable.
    Entitlement and language flags on ``offers`` come from the shared
    classifier, unchanged.
    """

    evidence: str
    last_seen_at: Optional[str]
    status: str
    offers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence,
            "last_seen_at": self.last_seen_at,
            "status": self.status,
            "offers": self.offers,
        }


def describe_last_known(
    movie: Movie,
    offers: Iterable[Offer],
    preference: Preference,
    *,
    current_country: Optional[str] = None,
    user_subscriptions: Optional[set[str]] = None,
    access: Optional[UserAccess] = None,
    provider_map: Optional[dict[str, Provider]] = None,
) -> Optional[LastKnownAvailability]:
    """Classify last-known offers with the shared resolver, or ``None`` if none."""
    offer_list = list(offers or ())
    if not offer_list:
        return None
    result = resolve_title(
        movie,
        offer_list,
        preference,
        current_country=current_country,
        user_subscriptions=user_subscriptions,
        access=access,
        provider_map=provider_map,
    )
    by_key = {}
    for offer in offer_list:
        by_key.setdefault(_offer_key(offer), offer)
    classified = result.accessible_offers + [
        c for c in result.excluded_offers if c not in result.accessible_offers
    ]
    out: list[dict[str, Any]] = []
    for c in classified:
        source = by_key.get(_offer_key(c))
        item = c.to_dict()
        item["availability_evidence"] = (
            getattr(source, "availability_evidence", "unknown") if source else "unknown"
        )
        item["last_seen_at"] = getattr(source, "last_seen_at", None) if source else None
        out.append(item)
    stale = any(o.availability_evidence == "stale" for o in offer_list)
    seen = [o.last_seen_at for o in offer_list if o.last_seen_at]
    return LastKnownAvailability(
        evidence="stale" if stale else "unknown",
        last_seen_at=max(seen) if seen else None,
        status=_VERDICT_TO_STATUS.get(result.verdict, WatchabilityStatus.UNKNOWN),
        offers=out,
    )


def qualify_watchability(result: WatchabilityResult, last_known: Optional[LastKnownAvailability]) -> WatchabilityResult:
    """Attach last-known evidence; replace a "nothing current" status with 🕓."""
    result.last_known = last_known
    if last_known is not None and result.status in _QUALIFIABLE:
        result.status = WatchabilityStatus.NEEDS_RECHECK
        result.status_label, result.status_symbol = _needs_recheck_label(last_known)
        result.explanation = _needs_recheck_explanation(last_known)
    return result


def qualify_watchability_dict(payload: dict[str, Any], last_known: Optional[LastKnownAvailability]) -> dict[str, Any]:
    """Same as :func:`qualify_watchability` for the detail page's verdict dict."""
    payload.update(_last_known_fields(last_known))
    if last_known is not None and payload.get("status") in _QUALIFIABLE:
        payload["status"] = payload["verdict"] = WatchabilityStatus.NEEDS_RECHECK
        payload["status_label"], payload["status_symbol"] = _needs_recheck_label(last_known)
        payload["explanation"] = _needs_recheck_explanation(last_known)
    return payload


def _last_known_fields(last_known: Optional[LastKnownAvailability]) -> dict[str, Any]:
    return {
        "availability_evidence": last_known.evidence if last_known else None,
        "last_seen_at": last_known.last_seen_at if last_known else None,
        "last_known": last_known.to_dict() if last_known else None,
    }


def _needs_recheck_label(last_known: LastKnownAvailability) -> tuple[str, str]:
    if last_known.evidence == "stale":
        return "Last seen available — needs recheck", NEEDS_RECHECK_SYMBOL
    return "Unverified availability — needs recheck", NEEDS_RECHECK_SYMBOL


def _needs_recheck_explanation(last_known: LastKnownAvailability) -> list[str]:
    when = f" on {last_known.last_seen_at[:10]}" if last_known.last_seen_at else ""
    if last_known.evidence == "stale":
        lines = [
            f"Last seen available{when}, but not re-confirmed within its "
            "source's refresh window."
        ]
    else:
        lines = [
            f"Reported available{when} by a source with no freshness guarantee; "
            "not independently verified."
        ]
    if last_known.status in (WatchabilityStatus.WATCHABLE_HERE, WatchabilityStatus.WATCHABLE_VPN):
        lines.append("When last seen, it was included with your services.")
    return lines


def _offer_key(offer) -> tuple[str, str, str]:
    return (
        (getattr(offer, "service", "") or "").lower(),
        (getattr(offer, "country", "") or "").upper(),
        (getattr(offer, "offer_type", "") or "").lower(),
    )
