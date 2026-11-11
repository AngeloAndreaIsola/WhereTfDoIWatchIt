"""Strict priority ranking for streaming offers per SPEC.

Priority order:
1. Audio language match (ranked order of preference.audio_languages).
2. Subtitle language match (ranked order of preference.subtitle_languages).
3. Quality: 4k > hdr > hd > sd.

Lower score = better.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from tracker.models import Preference
from .offers import Offer
from .score import QUALITY_RANK, evaluate_language_rule, score_offer, _normalize_lang_list
from .preferences import get_default_ranking_preferences

# Re-export for backward compatibility with existing imports.
__all__ = ["RankedOffer", "rank_offers", "QUALITY_RANK", "evaluate_language_rule", "compute_source_rank"]


@dataclass
class RankedOffer:
    country: str
    service: str
    offer_type: str
    quality: str
    url: str = ""
    audio: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    audio_rank: int = 99
    sub_rank: int = 99
    quality_rank: int = 99
    is_language_match: bool = True
    score: tuple = field(default_factory=tuple)
    price: Optional[dict] = None
    prices: list[dict] = field(default_factory=list)
    availability_evidence: str = "verified"
    last_seen_at: Optional[str] = None
    source_rank: int = 0
    provider_rank: int = 99
    ranking_explanation: list[str] = field(default_factory=list)
    is_scraped_source: bool = False

    def to_dict(self) -> dict:
        ranking_dict = {
            "audio_rank": self.audio_rank,
            "subtitle_rank": self.sub_rank,
            "quality_rank": self.quality_rank,
            "is_language_match": self.is_language_match,
            "source_rank": self.source_rank,
            "provider_rank": self.provider_rank,
        }
        if self.ranking_explanation:
            ranking_dict["explanation"] = self.ranking_explanation
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "audio_languages": self.audio,
            "subtitle_languages": self.subs,
            "is_language_match": self.is_language_match,
            "ranking": ranking_dict,
            "price": self.price,
            "prices": self.prices,
            "availability_evidence": self.availability_evidence,
            "last_seen_at": self.last_seen_at,
        }


def compute_source_rank(
    source_priority: list[str],
    is_scraped: bool,
    is_local: bool,
) -> int:
    """Compute the position (index) of an offer based on user source_priority.

    When both 'official' and 'local' can apply (e.g. an official provider in the local country),
    we match the earliest category appearing in the user's source_priority.
    """
    applicable = set()
    if is_scraped:
        applicable.add("unofficial")
    else:
        applicable.add("official")
        if is_local:
            applicable.add("local")

    best_rank = 99
    for idx, s in enumerate(source_priority):
        if s in applicable:
            best_rank = idx
            break
    return best_rank


def rank_offers(
    offers: Iterable[Offer],
    preference: Preference,
    country_filter: Optional[set[str]] = None,
    current_country: Optional[str] = None,
) -> list[RankedOffer]:
    """Strict priority sort over a list of cached offers, honoring user-configurable ranking.

    Invariants preserved:
    - Factual language compatibility, country availability, and entitlement take precedence.
    - User source ranking (official/local/unofficial, provider priority) adjusts ordering
      between feasible offers without promoting ineligible or unverified offers over eligible ones.
    """
    allowed_countries = (
        {c.upper() for c in country_filter}
        if country_filter is not None
        else {c.upper() for c in preference.candidate_countries or []}
    )

    local_country = (current_country or getattr(preference, "current_country", "") or "").upper().strip()

    raw_ranking_pref = getattr(preference, "ranking_preferences", None)
    if isinstance(raw_ranking_pref, dict) and raw_ranking_pref:
        ranking_prefs = raw_ranking_pref
    else:
        ranking_prefs = get_default_ranking_preferences()

    source_priority = ranking_prefs.get("source_priority") or ["official", "local", "unofficial"]
    prefer_official = ranking_prefs.get("prefer_official", True)
    prefer_local = ranking_prefs.get("prefer_local", True)
    prefer_entitled = ranking_prefs.get("prefer_entitled", True)
    provider_priority = [p.lower() for p in (ranking_prefs.get("provider_priority") or [])]

    ranked: list[RankedOffer] = []
    for offer in offers:
        if not isinstance(offer, Offer):
            raise TypeError(f"Expected Offer, got {type(offer).__name__}")

        country = (offer.country or "").upper()
        service = offer.service
        offer_type = offer.offer_type
        quality = (offer.quality or "").lower()
        url = offer.url
        audio = _normalize_lang_list(offer.audio_languages)
        subs = _normalize_lang_list(offer.subtitle_languages)
        is_scraped = bool(getattr(offer, "is_scraped_source", False))

        if allowed_countries and country not in allowed_countries:
            continue

        scored = score_offer(offer, preference)
        is_local = bool(local_country and country == local_country)

        # Source category score from priority order
        source_rank = compute_source_rank(source_priority, is_scraped, is_local)

        # Provider preference rank (0-indexed if in provider_priority list, else 99)
        clean_service = (service or "").lower().strip()
        provider_rank = 99
        if clean_service in provider_priority:
            provider_rank = provider_priority.index(clean_service)

        # Explanations for transparency
        explanations = []
        if is_local and prefer_local:
            explanations.append("Local region offer")
        if not is_scraped and prefer_official:
            explanations.append("Official provider")
        elif is_scraped:
            explanations.append("Community / Scraped source")
        if provider_rank < 99:
            explanations.append(f"Preferred provider (#{provider_rank + 1})")

        ranked.append(
            RankedOffer(
                country=country,
                service=service,
                offer_type=offer_type,
                quality=quality,
                url=url,
                audio=audio,
                subs=subs,
                audio_rank=scored.audio_rank,
                sub_rank=scored.sub_rank,
                quality_rank=scored.quality_rank,
                is_language_match=scored.is_match,
                score=scored.score_tuple,
                price=offer.price,
                prices=list(offer.prices or []),
                availability_evidence=getattr(offer, "availability_evidence", "verified"),
                last_seen_at=getattr(offer, "last_seen_at", None),
                source_rank=source_rank,
                provider_rank=provider_rank,
                ranking_explanation=explanations,
                is_scraped_source=is_scraped,
            )
        )

    # Sort key:
    # 1. Language mismatch penalty: scored.score_tuple[0] is 0 if match, 1 if no match / unknown
    # 2. Feasible offer ranking:
    #    - prefer_entitled: subscription/free offers rank ahead of rent/buy offers.
    #    - prefer_local: local country offers rank ahead of foreign offers when local_country is set.
    #    - prefer_official: official providers rank ahead of scraped/community sources.
    #    - source_rank: position in user's source_priority.
    #    - provider_rank: preferred provider tiebreak.
    #    - quality & language score tuple.
    def sort_key(r: RankedOffer):
        lang_penalty = r.score[0] if r.score else 0

        # Entitlement tier among eligible offers:
        entitled_tier = 0
        if prefer_entitled and r.offer_type in ("rent", "buy"):
            entitled_tier = 1

        local_tier = 0
        if prefer_local and local_country:
            local_tier = 0 if r.country == local_country else 1

        official_tier = 0
        if prefer_official and r.is_scraped_source:
            official_tier = 1

        return (
            lang_penalty,
            entitled_tier,
            official_tier,
            local_tier,
            r.source_rank,
            r.provider_rank,
            r.score,
        )

    ranked.sort(key=sort_key)
    return ranked
