"""Strict priority ranking for streaming offers per SPEC.

Priority order (one-line config change later):
1. Audio language match (ranked order of preference.audio_languages).
2. Subtitle language match (ranked order of preference.subtitle_languages).
3. Quality: 4k > hdr > hd > sd.

Lower score = better.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Preference

QUALITY_RANK = {"4k": 0, "uhd": 0, "hdr": 1, "hd": 2, "sd": 3}


@dataclass
class RankedOffer:
    country: str
    service: str
    offer_type: str
    quality: str
    audio: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    audio_rank: int = 99
    sub_rank: int = 99
    quality_rank: int = 99
    score: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "audio_languages": self.audio,
            "subtitle_languages": self.subs,
            "ranking": {
                "audio_rank": self.audio_rank,
                "subtitle_rank": self.sub_rank,
                "quality_rank": self.quality_rank,
            },
        }


def rank_offers(
    offers: Iterable[dict],
    preference: Preference,
    country_filter: Optional[set[str]] = None,
) -> list[RankedOffer]:
    """Strict priority sort over a list of cached offers."""
    audio_priority = {code: i for i, code in enumerate(preference.audio_languages or [])}
    sub_priority = {code: i for i, code in enumerate(preference.subtitle_languages or [])}
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
        audio = [c.lower() for c in offer.get("audio", [])]
        subs = [c.lower() for c in offer.get("subs", [])]
        audio_rank = _min_rank(audio, audio_priority)
        sub_rank = _min_rank(subs, sub_priority)
        quality = (offer.get("quality") or "").lower()
        quality_rank = QUALITY_RANK.get(quality, 99)
        score = (audio_rank, sub_rank, quality_rank)
        ranked.append(
            RankedOffer(
                country=country,
                service=offer.get("service", ""),
                offer_type=offer.get("offer_type", ""),
                quality=quality,
                audio=audio,
                subs=subs,
                audio_rank=audio_rank,
                sub_rank=sub_rank,
                quality_rank=quality_rank,
                score=score,
            )
        )
    ranked.sort(key=lambda r: r.score)
    return ranked


def _min_rank(codes: Iterable[str], priority: dict[str, int]) -> int:
    """Return best rank from matching codes; 99 if no match."""
    ranks = [priority[c] for c in codes if c in priority]
    return min(ranks) if ranks else 99