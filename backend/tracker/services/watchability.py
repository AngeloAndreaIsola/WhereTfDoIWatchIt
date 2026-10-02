"""Centralized Watchability Evaluation Engine.

Single source of truth for evaluating title watchability across Catalog, Homepage,
Wishlist, Detail Pages, and Redirect/VPN checks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tracker.models import Movie, Preference, Provider, Subscription
from tracker.services.offers import Offer
from tracker.services.presentation import present_offer
from tracker.services.score import score_offer
from tracker.services.streaming_api import service_display_name


class WatchabilityStatus:
    WATCHABLE_HERE = "watchable_here"   # 🟢
    WATCHABLE_VPN = "watchable_vpn"     # 🟡
    NOT_WATCHABLE = "not_watchable"     # ⚫
    UNKNOWN = "unknown"                 # ⚪
    CHECKING = "checking"               # 🔵


class RefreshState:
    """Per-title refresh-queue state, derived from ``RefreshRequest`` rows.

    The vocabulary the read path uses to tell "no offers because never
    checked" apart from "no offers because a check found none" (and to show
    queued/failed checks) — produced by
    ``tracker.services.refresh_requests.refresh_states_for`` and consumed by
    :func:`evaluate_watchability` when the canonical store holds no offers.
    """

    PENDING = "pending"            # a refresh for this title is queued
    CHECKED = "checked"            # at least one refresh completed successfully
    FAILED = "failed"              # attempts exist, none ever succeeded
    NEVER_CHECKED = "never_checked"  # no refresh request at all


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
        }


@dataclass
class WatchabilityResult:
    movie_id: int
    status: str  # watchable_here | watchable_vpn | not_watchable | unknown | checking
    status_label: str
    status_symbol: str
    best_offer: Optional[EvaluatedOffer] = None
    watchable_offers: list[EvaluatedOffer] = field(default_factory=list)
    excluded_offers: list[EvaluatedOffer] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    refresh_state: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_label": self.status_label,
            "status_symbol": self.status_symbol,
            "best_offer": self.best_offer.to_dict() if self.best_offer else None,
            "watchable_offers": [o.to_dict() for o in self.watchable_offers],
            "excluded_offers": [o.to_dict() for o in self.excluded_offers],
            "explanation": self.explanation,
            "refresh_state": self.refresh_state,
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

    # BVOD and scraper providers auto-count as available sources
    free_or_scrapers = Provider.objects.filter(
        category__in=["bvod", "scraper"], is_enabled=True
    ).values_list("slug", flat=True)
    for s in free_or_scrapers:
        subscribed.add(s.lower().strip())

    return subscribed


def evaluate_watchability(
    movie: Movie,
    offers: Iterable[Offer],
    preference: Preference,
    current_country: Optional[str] = None,
    user_subscriptions: Optional[set[str]] = None,
    refresh_state: Optional[str] = None,
) -> WatchabilityResult:
    """Evaluate full watchability status and best way to watch for a movie/series.

    ``refresh_state`` (a :class:`RefreshState` value, or ``None`` when the
    caller has no queue knowledge) is consulted only when the canonical
    store holds no offers for the title: it is what separates "checked, the
    providers really have nothing" (⚫ Not available) from "queued right
    now" (🔵 Checking…) and "never fetched at all" (⚪ Unknown). When offers
    exist they always decide the status; a queued re-check never hides a
    known result.
    """
    if user_subscriptions is None:
        user_subscriptions = get_user_subscriptions(preference.user_id if preference else 1)

    curr_country = (current_country or "").upper().strip()
    candidate_countries = {c.upper().strip() for c in (preference.candidate_countries or [])}

    provider_map = {
        p.slug: p
        for p in Provider.objects.only("slug", "name", "country_code")
    }

    if not offers:
        # Checked beats pending: a title whose last known state is "nothing
        # out there" keeps ⚫ while a re-check runs (the queue state is still
        # exposed on refresh_state), so the badge only drops to 🔵 for a
        # title we have never successfully checked.
        if refresh_state == RefreshState.CHECKED:
            return WatchabilityResult(
                movie_id=movie.id,
                status=WatchabilityStatus.NOT_WATCHABLE,
                status_label="Not available",
                status_symbol="⚫",
                refresh_state=refresh_state,
                explanation=[
                    "Availability was checked — no qualifying offers found "
                    "for this title."
                ],
            )
        if refresh_state == RefreshState.PENDING:
            return WatchabilityResult(
                movie_id=movie.id,
                status=WatchabilityStatus.CHECKING,
                status_label="Checking availability…",
                status_symbol="🔵",
                refresh_state=refresh_state,
                explanation=[
                    "A refresh for this title is queued — results appear "
                    "once the worker gets to it."
                ],
            )
        if refresh_state == RefreshState.FAILED:
            return WatchabilityResult(
                movie_id=movie.id,
                status=WatchabilityStatus.UNKNOWN,
                status_label="Unknown — last check failed",
                status_symbol="⚪",
                refresh_state=refresh_state,
                explanation=[
                    "The last availability check failed — it is retried on "
                    "the next refresh."
                ],
            )
        return WatchabilityResult(
            movie_id=movie.id,
            status=WatchabilityStatus.UNKNOWN,
            status_label="Unknown",
            status_symbol="⚪",
            refresh_state=refresh_state,
            explanation=["No availability data found for this title."],
        )

    watchable_offers: list[EvaluatedOffer] = []
    excluded_offers: list[EvaluatedOffer] = []
    explanations: list[str] = []

    for raw in offers:
        if not isinstance(raw, Offer):
            raise TypeError(f"Expected Offer, got {type(raw).__name__}")

        country = (raw.country or "").upper().strip()
        service = raw.service or ""
        offer_type = (raw.offer_type or "").lower().strip()
        quality = raw.quality or ""
        url = raw.url or ""
        price_info = ""
        is_scraped = raw.is_scraped_source
        raw_audio = raw.audio_languages
        raw_subs = raw.subtitle_languages

        service_clean = service.lower().strip()
        presented = present_offer(raw, provider_map)
        service_display = presented.service_display
        country_display = presented.country
        has_lang_info = bool(raw_audio or raw_subs)

        scored = score_offer(raw, preference)
        is_lang_match = scored.is_match
        ranking_score = scored.score_tuple

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

        # Empty candidate_countries means "whole world" — all countries accessible
        whole_world = not candidate_countries
        is_watchable = is_qualifying_type and is_subscribed and is_lang_match and (whole_world or is_curr or is_cand)

        eval_offer = EvaluatedOffer(
            country=country_display,
            service=service,
            service_display=service_display,
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
        refresh_state=refresh_state,
    )
