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

from tracker.languages import normalize_language_code
from ..models import Preference

QUALITY_RANK = {"4k": 0, "uhd": 0, "hdr": 1, "qhd": 1, "hd": 2, "sd": 3}


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


def evaluate_language_rule(
    rule: Optional[dict],
    offer_audio: Iterable[str],
    offer_subs: Iterable[str],
) -> bool:
    """Evaluate language rule against offer's audio & subtitle languages.

    Supports V1 nested AND/OR group rules and V2 viewers intersection rules:
    V2 structure:
      {
        "type": "viewers",
        "viewers": [
          {"name": "Andrea", "languages": ["EN", "IT"]},
          {"name": "Mayu", "languages": ["JA"]}
        ]
      }
    For V2, an offer is understood if for EVERY viewer, at least one of their spoken languages
    matches either the offer's audio OR subtitle languages.
    """
    if not rule or not isinstance(rule, dict):
        return True

    node_type = rule.get("type")

    if node_type == "viewers":
        viewers = rule.get("viewers") or []
        if not viewers:
            return True

        offer_audio_langs = set(_normalize_lang_list(offer_audio))
        offer_sub_langs = set(_normalize_lang_list(offer_subs))
        offer_all_langs = offer_audio_langs.union(offer_sub_langs)

        for v in viewers:
            if not isinstance(v, dict):
                continue
            v_langs = set(_normalize_lang_list(v.get("languages") or []))
            if not v_langs:
                continue
            if not v_langs.intersection(offer_all_langs):
                return False
        return True

    if node_type == "group":
        op = (rule.get("op") or "AND").upper()
        children = rule.get("rules") or rule.get("children") or []
        if not children:
            return True
        if op == "AND":
            return all(evaluate_language_rule(c, offer_audio, offer_subs) for c in children)
        elif op == "OR":
            return any(evaluate_language_rule(c, offer_audio, offer_subs) for c in children)
        return True

    elif node_type == "condition":
        field_name = (rule.get("field") or rule.get("target") or "audio").lower()
        raw_langs = rule.get("languages") or rule.get("language") or rule.get("value") or []
        if isinstance(raw_langs, str):
            raw_langs = [raw_langs]
        target_langs = set(_normalize_lang_list(raw_langs))

        if not target_langs:
            return True

        if field_name in ("audio", "audio_languages"):
            offer_langs = set(_normalize_lang_list(offer_audio))
        else:
            offer_langs = set(_normalize_lang_list(offer_subs))

        return bool(target_langs.intersection(offer_langs))

    return True


def _normalize_lang_list(raw_list: Iterable[str]) -> list[str]:
    """Normalize a list of language strings/codes into canonical uppercase codes."""
    res = []
    seen = set()
    for item in raw_list or []:
        norm = normalize_language_code(str(item))
        if norm and norm not in seen:
            seen.add(norm)
            res.append(norm)
    return res


def rank_offers(
    offers: Iterable[dict],
    preference: Preference,
    country_filter: Optional[set[str]] = None,
) -> list[RankedOffer]:
    """Strict priority sort over a list of cached offers."""
    lang_rule = getattr(preference, "language_rule", None)
    if lang_rule and isinstance(lang_rule, dict) and lang_rule.get("type") == "viewers":
        viewer_langs = []
        seen = set()
        for v in lang_rule.get("viewers") or []:
            if isinstance(v, dict):
                for l in _normalize_lang_list(v.get("languages") or []):
                    if l not in seen:
                        seen.add(l)
                        viewer_langs.append(l)
        pref_audio = viewer_langs
        pref_subs = viewer_langs
    else:
        pref_audio = _normalize_lang_list(preference.audio_languages or [])
        pref_subs = _normalize_lang_list(preference.subtitle_languages or [])

    audio_priority = {code: i for i, code in enumerate(pref_audio)}
    sub_priority = {code: i for i, code in enumerate(pref_subs)}

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

        raw_audio = offer.get("audio") or offer.get("audio_languages") or []
        raw_subs = offer.get("subs") or offer.get("subtitle_languages") or []

        audio = _normalize_lang_list(raw_audio)
        subs = _normalize_lang_list(raw_subs)

        audio_rank = _min_rank(audio, audio_priority)
        sub_rank = _min_rank(subs, sub_priority)
        quality = (offer.get("quality") or "").lower()
        quality_rank = QUALITY_RANK.get(quality, 99)

        if lang_rule and isinstance(lang_rule, dict) and bool(lang_rule):
            is_language_match = evaluate_language_rule(lang_rule, audio, subs)
            match_score = 0 if is_language_match else 1
            score = (match_score, quality_rank, audio_rank, sub_rank)
        elif pref_audio or pref_subs:
            is_language_match = (audio_rank < 99) or (sub_rank < 99)
            match_score = 0 if is_language_match else 1
            score = (match_score, audio_rank, sub_rank, quality_rank)
        else:
            is_language_match = True
            match_score = 0
            score = (match_score, quality_rank, audio_rank, sub_rank)

        ranked.append(
            RankedOffer(
                country=country,
                service=offer.get("service", ""),
                offer_type=offer.get("offer_type", ""),
                quality=quality,
                url=offer.get("url", ""),
                audio=audio,
                subs=subs,
                audio_rank=audio_rank,
                sub_rank=sub_rank,
                quality_rank=quality_rank,
                is_language_match=is_language_match,
                score=score,
            )
        )
    ranked.sort(key=lambda r: r.score)
    return ranked


def _min_rank(codes: Iterable[str], priority: dict[str, int]) -> int:
    """Return best rank from matching codes; 99 if no match."""
    ranks = [priority[c] for c in codes if c in priority]
    return min(ranks) if ranks else 99
