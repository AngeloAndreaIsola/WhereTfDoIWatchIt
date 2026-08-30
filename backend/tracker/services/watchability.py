"""Centralized Watchability Evaluation Engine.

Single source of truth for evaluating title watchability across Catalog, Homepage,
Wishlist, Detail Pages, and Redirect/VPN checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tracker.models import Movie, Preference, Provider, Subscription
from tracker.services.ranking import (
    QUALITY_RANK,
    _min_rank,
    _normalize_lang_list,
    evaluate_language_rule,
)


class WatchabilityStatus:
    WATCHABLE_HERE = "watchable_here"   # 🟢
    WATCHABLE_VPN = "watchable_vpn"     # 🟡
    NOT_WATCHABLE = "not_watchable"     # ⚫
    UNKNOWN = "unknown"                 # ⚪


@dataclass
class EvaluatedOffer:
    country: str
    service: str
    offer_type: str
    quality: str
    url: str
    price_info: str = ""
    is_qualifying_offer_type: bool = False
    is_user_subscribed: bool = False
    is_language_match: bool = True
    is_current_country: bool = False
    is_candidate_country: bool = False
    is_watchable: bool = False
    rejection_reasons: list[str] = field(default_factory=list)
    ranking_score: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "price_info": self.price_info,
            "is_qualifying_offer_type": self.is_qualifying_offer_type,
            "is_user_subscribed": self.is_user_subscribed,
            "is_language_match": self.is_language_match,
            "is_current_country": self.is_current_country,
            "is_candidate_country": self.is_candidate_country,
            "is_watchable": self.is_watchable,
            "rejection_reasons": self.rejection_reasons,
        }


@dataclass
class WatchabilityResult:
    movie_id: int
    status: str  # watchable_here | watchable_vpn | not_watchable | unknown
    status_label: str
    status_symbol: str
    best_offer: Optional[EvaluatedOffer] = None
    watchable_offers: list[EvaluatedOffer] = field(default_factory=list)
    excluded_offers: list[EvaluatedOffer] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_label": self.status_label,
            "status_symbol": self.status_symbol,
            "best_offer": self.best_offer.to_dict() if self.best_offer else None,
            "watchable_offers": [o.to_dict() for o in self.watchable_offers],
            "excluded_offers": [o.to_dict() for o in self.excluded_offers],
            "explanation": self.explanation,
        }


def get_user_subscriptions(user_id: int = 1) -> set[str]:
    """Return set of normalized provider slugs and subscription names for user."""
    subs = list(Subscription.objects.filter(user_id=user_id).values_list("provider__slug", "name"))
    subscribed = set()
    for slug, name in subs:
        if slug:
            subscribed.add(slug.lower().strip())
        if name:
            subscribed.add(name.lower().strip())

    # Free scraped providers that are enabled also count as subscribed sources
    scrapers = Provider.objects.filter(category="scraper", is_enabled=True).values_list("slug", flat=True)
    for s in scrapers:
        subscribed.add(s.lower().strip())

    return subscribed


def evaluate_watchability(
    movie: Movie,
    offers: Iterable[dict],
    preference: Preference,
    current_country: Optional[str] = None,
    user_subscriptions: Optional[set[str]] = None,
) -> WatchabilityResult:
    """Evaluate full watchability status and best way to watch for a movie/series."""
    if user_subscriptions is None:
        user_subscriptions = get_user_subscriptions(preference.user_id if preference else 1)

    curr_country = (current_country or "").upper().strip()
    candidate_countries = {c.upper().strip() for c in (preference.candidate_countries or [])}

    if not offers:
        return WatchabilityResult(
            movie_id=movie.id,
            status=WatchabilityStatus.UNKNOWN,
            status_label="Unknown",
            status_symbol="⚪",
            explanation=["No availability data found for this title."],
        )

    watchable_offers: list[EvaluatedOffer] = []
    excluded_offers: list[EvaluatedOffer] = []
    explanations: list[str] = []

    pref_audio = _normalize_lang_list(preference.audio_languages or [])
    pref_subs = _normalize_lang_list(preference.subtitle_languages or [])
    audio_priority = {code: i for i, code in enumerate(pref_audio)}
    sub_priority = {code: i for i, code in enumerate(pref_subs)}
    lang_rule = getattr(preference, "language_rule", None)

    for raw in offers:
        country = (raw.get("country") or "").upper().strip()
        service = raw.get("service") or raw.get("provider") or ""
        service_clean = service.lower().strip()
        offer_type = (raw.get("offer_type") or "").lower().strip()
        quality = raw.get("quality") or ""
        url = raw.get("url") or ""
        price_info = raw.get("price_info") or ""
        is_scraped = raw.get("is_scraped_source", False)

        raw_audio = raw.get("audio") or raw.get("audio_languages") or []
        raw_subs = raw.get("subs") or raw.get("subtitle_languages") or []
        audio = _normalize_lang_list(raw_audio)
        subs = _normalize_lang_list(raw_subs)
        has_lang_info = bool(audio or subs)

        audio_rank = _min_rank(audio, audio_priority)
        sub_rank = _min_rank(subs, sub_priority)
        quality_clean = quality.lower()
        quality_rank = QUALITY_RANK.get(quality_clean, 99)

        if lang_rule and isinstance(lang_rule, dict) and bool(lang_rule):
            is_lang_match = evaluate_language_rule(lang_rule, audio, subs)
            match_score = 0 if is_lang_match else 1
            ranking_score = (match_score, quality_rank, audio_rank, sub_rank)
        elif pref_audio or pref_subs:
            is_lang_match = (audio_rank < 99) or (sub_rank < 99)
            match_score = 0 if is_lang_match else 1
            ranking_score = (match_score, audio_rank, sub_rank, quality_rank)
        else:
            is_lang_match = True
            match_score = 0
            ranking_score = (match_score, quality_rank, audio_rank, sub_rank)

        reasons = []

        # 1. Offer type requirement: subscription or free
        is_qualifying_type = offer_type in ("subscription", "free")
        if not is_qualifying_type:
            price_str = f" ({price_info})" if price_info else ""
            reasons.append(f"Only available to {offer_type}{price_str} (not included in subscription)")

        # 2. Subscription check
        is_subscribed = service_clean in user_subscriptions or any(
            sub in service_clean or service_clean in sub for sub in user_subscriptions if len(sub) >= 3
        )
        if not is_subscribed:
            reasons.append(f"No active subscription for {service}")

        # 3. Language match check
        if not is_lang_match:
            if not has_lang_info:
                reasons.append("Language information unknown")
            else:
                reasons.append("Does not satisfy language rules")

        # 4. Country / VPN check — scraped sources are accessible from anywhere
        if is_scraped:
            is_curr = True
            is_cand = True
        else:
            is_curr = bool(curr_country and country == curr_country)
            is_cand = country in candidate_countries

            if not is_curr and not is_cand:
                if candidate_countries:
                    reasons.append(f"Available in {country}, but {country} is not in your configured VPN regions")
                elif curr_country and country != curr_country:
                    reasons.append(f"Available in {country}, but no VPN regions are configured")

        is_watchable = is_qualifying_type and is_subscribed and is_lang_match and (is_curr or is_cand)

        eval_offer = EvaluatedOffer(
            country=country,
            service=service,
            offer_type=offer_type,
            quality=quality,
            url=url,
            price_info=price_info,
            is_qualifying_offer_type=is_qualifying_type,
            is_user_subscribed=is_subscribed,
            is_language_match=is_lang_match,
            is_current_country=is_curr,
            is_candidate_country=is_cand,
            is_watchable=is_watchable,
            rejection_reasons=reasons,
            ranking_score=ranking_score,
        )

        if is_watchable:
            watchable_offers.append(eval_offer)
        else:
            excluded_offers.append(eval_offer)

    # Sort watchable offers by preference ranking score
    watchable_offers.sort(key=lambda o: (
        0 if o.is_current_country else 1,  # Prefer current country if score tied
        o.ranking_score,
    ))

    # Determine overall watchability status
    if any(o.is_current_country for o in watchable_offers):
        status = WatchabilityStatus.WATCHABLE_HERE
        status_label = "Watchable here"
        status_symbol = "🟢"
    elif watchable_offers:
        status = WatchabilityStatus.WATCHABLE_VPN
        status_label = "Watchable with VPN"
        status_symbol = "🟡"
    else:
        status = WatchabilityStatus.NOT_WATCHABLE
        status_symbol = "⚫"

        # Check if any offer was accessible & subscribed but excluded strictly by language rules
        lang_excluded = [
            o for o in excluded_offers
            if o.is_qualifying_offer_type and o.is_user_subscribed and (o.is_current_country or o.is_candidate_country) and not o.is_language_match
        ]

        if lang_excluded:
            has_unknown = any("Language information unknown" in o.rejection_reasons for o in lang_excluded)
            if has_unknown:
                status_label = "Not matching language filter (language data unknown)"
            else:
                status_label = "Not matching language filter"
        else:
            status_label = "Not watchable with your subscriptions"

        # Compile user-friendly summary explanation
        seen_exp = set()
        for o in excluded_offers:
            for r in o.rejection_reasons:
                if r not in seen_exp:
                    seen_exp.add(r)
                    explanations.append(r)

    best_offer = watchable_offers[0] if watchable_offers else None

    return WatchabilityResult(
        movie_id=movie.id,
        status=status,
        status_label=status_label,
        status_symbol=status_symbol,
        best_offer=best_offer,
        watchable_offers=watchable_offers,
        excluded_offers=excluded_offers,
        explanation=explanations,
    )
