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
from .score import QUALITY_RANK, evaluate_language_rule, score_offer, _normalize_lang_list

# Re-export for backward compatibility with existing imports.
__all__ = ["RankedOffer", "rank_offers", "QUALITY_RANK", "evaluate_language_rule"]


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

    def to_dict(self) -> dict:
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "audio_languages": self.audio,
            "subtitle_languages": self.subs,
            "is_language_match": self.is_language_match,
            "ranking": {
                "audio_rank": self.audio_rank,
                "subtitle_rank": self.sub_rank,
                "quality_rank": self.quality_rank,
                "is_language_match": self.is_language_match,
            },
        }


def rank_offers(
    offers: Iterable[dict],
    preference: Preference,
    country_filter: Optional[set[str]] = None,
) -> list[RankedOffer]:
    """Strict priority sort over a list of cached offers."""
    allowed_countries = (
        {c.upper() for c in country_filter}
        if country_filter is not None
        else {c.upper() for c in preference.candidate_countries or []}
    )

    ranked: list[RankedOffer] = []
    for offer in offers:
        country = (offer.get("country") or "").upper()
        if allowed_countries and country not in allowed_countries:
            continue

        scored = score_offer(offer, preference)

        raw_audio = offer.get("audio") or offer.get("audio_languages") or []
        raw_subs = offer.get("subs") or offer.get("subtitle_languages") or []
        audio = _normalize_lang_list(raw_audio)
        subs = _normalize_lang_list(raw_subs)

        ranked.append(
            RankedOffer(
                country=country,
                service=offer.get("service", ""),
                offer_type=offer.get("offer_type", ""),
                quality=(offer.get("quality") or "").lower(),
                url=offer.get("url", ""),
                audio=audio,
                subs=subs,
                audio_rank=scored.audio_rank,
                sub_rank=scored.sub_rank,
                quality_rank=scored.quality_rank,
                is_language_match=scored.is_match,
                score=scored.score_tuple,
            )
        )
    ranked.sort(key=lambda r: r.score)
    return ranked
