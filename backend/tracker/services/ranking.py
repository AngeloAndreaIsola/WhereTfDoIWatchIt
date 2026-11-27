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
from .offers import OfferLineage, Offer, effective_territorial_scope
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
    source_category: str = ""
    territorial_scope: str = ""
    available_from: Optional[str] = None
    available_until: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    lineage: OfferLineage | None = None

    def to_dict(self) -> dict:
        ranking_dict = {
            "audio_rank": self.audio_rank,
            "subtitle_rank": self.sub_rank,
            "quality_rank": self.quality_rank,
            "is_language_match": self.is_language_match,
            "source_rank": self.source_rank,
            "provider_rank": self.provider_rank,
            "source_category": self.source_category,
        }
        if self.ranking_explanation:
            ranking_dict["explanation"] = self.ranking_explanation
        payload = {
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
            "territorial_scope": effective_territorial_scope(
                self.country, self.territorial_scope
            ),
        }
        # Ranked offers also serve the legacy scraper API, where windows and
        # season/episode scope are absent from the response contract.
        for field_name in ("available_from", "available_until", "season_number", "episode_number"):
            value = getattr(self, field_name)
            if value is not None:
                payload[field_name] = value
        if self.lineage:
            payload.update(self.lineage.public_scope())
        return payload


def compute_source_rank(
    source_priority: list[str],
    is_scraped: bool,
    is_local: bool,
    source_category: str = "",
) -> int:
    """Compute the position (index) of an offer based on user source_priority.

    Source category semantics:
    - If `is_scraped`: matches "unofficial"
    - If not `is_scraped`:
        - If `is_local`: matches "local" if "local" appears in priority, and matches "official"
          (takes whichever appears earlier in the user's `source_priority`)
        - If not `is_local`: matches "official"
    """
    category = (source_category or "").lower().strip()
    applicable = {category} if category in {"official", "local", "unofficial"} else set()
    if not category and is_scraped:
        applicable.add("unofficial")
    elif not category:
        applicable.add("official")
        if is_local:
            applicable.add("local")
    elif category != "unofficial" and is_local:
        applicable.add("local")

    best_rank = 99
    for idx, s in enumerate(source_priority):
        if s in applicable:
            best_rank = idx
            break
    return best_rank


def build_offer_sort_key(
    r: RankedOffer,
    *,
    source_priority: list[str],
    prefer_official: bool,
    prefer_local: bool,
    prefer_entitled: bool,
    local_country: str,
    provider_priority: Optional[list[str]] = None,
):
    """Canonical ranking-precedence key shared across ranking and access resolution.

    Contract:
    1. Verified availability evidence, then language match (unknown/mismatching evidence
       cannot outrank a verified language match).
    2. Entitlement tier: subscription/free offers rank before rental/purchase when prefer_entitled is True.
    3. User-configured source priority order (`source_rank` computed from `source_priority`).
       `source_priority` directly controls precedence among source categories (e.g. official vs local vs unofficial).
    4. Guard penalties if individual preference flags are enabled:
       - `official_penalty`: if prefer_official is True, unofficial sources get demoted among ties.
       - `local_penalty`: if prefer_local is True, non-local offers get demoted among ties.
    5. Provider priority rank (user's preferred providers tiebreak within same source category).
    6. Quality & language score tuple.
    """
    score = getattr(r, "score", None) or getattr(r, "ranking_score", ())
    lang_penalty = score[0] if score else 0
    language_match = getattr(r, "is_language_match", None)
    language_rank = 0 if language_match is True else 1 if language_match is None else 2
    evidence = getattr(r, "availability_evidence", "verified")
    evidence_rank = {"verified": 0, "stale": 1, "unknown": 2}.get(evidence, 2)

    entitled_tier = 0
    if prefer_entitled and r.offer_type in ("rent", "buy"):
        entitled_tier = 1

    official_penalty = 0
    if prefer_official and r.is_scraped_source:
        official_penalty = 1

    local_penalty = 0
    country = getattr(r, "country", "") or ""
    if prefer_local and local_country and country.upper() != local_country.upper():
        local_penalty = 1

    source_rank = compute_source_rank(
        source_priority,
        bool(getattr(r, "is_scraped_source", False)),
        bool(local_country and country.upper() == local_country.upper()),
        getattr(r, "source_category", ""),
    )
    service = (getattr(r, "service", "") or "").lower().strip()
    priorities = [p.lower() for p in (provider_priority or [])]
    provider_rank = priorities.index(service) if service in priorities else getattr(r, "provider_rank", 99)

    return (
        evidence_rank,
        language_rank,
        lang_penalty,
        entitled_tier,
        source_rank,
        official_penalty,
        local_penalty,
        provider_rank,
        score,
    )


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
        territorial_scope = effective_territorial_scope(
            country, getattr(offer, "territorial_scope", "")
        )
        service = offer.service
        offer_type = offer.offer_type
        quality = (offer.quality or "").lower()
        url = offer.url
        audio = _normalize_lang_list(offer.audio_languages)
        subs = _normalize_lang_list(offer.subtitle_languages)
        is_scraped = bool(getattr(offer, "is_scraped_source", False))

        if (
            allowed_countries
            and country not in allowed_countries
            and territorial_scope != "worldwide"
        ):
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
                source_category=(
                    getattr(offer, "source_category", "")
                    or ("unofficial" if is_scraped else "official")
                ),
                territorial_scope=territorial_scope,
                available_from=offer.available_from,
                available_until=offer.available_until,
                season_number=offer.season_number,
                episode_number=offer.episode_number,
                lineage=offer.lineage,
            )
        )

    ranked.sort(
        key=lambda r: build_offer_sort_key(
            r,
            source_priority=source_priority,
            prefer_official=prefer_official,
            prefer_local=prefer_local,
            prefer_entitled=prefer_entitled,
            local_country=local_country,
            provider_priority=provider_priority,
        )
    )
    return ranked
