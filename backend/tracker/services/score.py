"""Unified scoring for streaming offers.

Both ranking.py and watchability.py need the same audio/sub/quality scoring.
This module is the single source of truth — consumers call score_offer() and
keep their own output shapes separate (RankedOffer vs EvaluatedOffer).

Priority order (lower score = better):
1. Language rule match (0 = passes, 1 = fails)
2. Audio language rank
3. Subtitle language rank
4. Quality rank
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from tracker.languages import normalize_language_code
from tracker.models import Preference
from .offers import Offer

QUALITY_RANK = {"4k": 0, "uhd": 0, "hdr": 1, "qhd": 1, "hd": 2, "sd": 3}


@dataclass
class ScoredOffer:
    is_match: bool
    score_tuple: tuple
    audio_rank: int
    sub_rank: int
    quality_rank: int


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


def _min_rank(codes: Iterable[str], priority: dict[str, int]) -> int:
    """Return best rank from matching codes; 99 if no match."""
    ranks = [priority[c] for c in codes if c in priority]
    return min(ranks) if ranks else 99


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


def score_offer(
    offer: Offer,
    preference: Preference,
) -> ScoredOffer:
    """Score a single offer against a preference."""
    if not isinstance(offer, Offer):
        raise TypeError(f"Expected Offer, got {type(offer).__name__}")

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

    raw_audio = offer.audio_languages
    raw_subs = offer.subtitle_languages
    quality = (offer.quality or "").lower()

    audio = _normalize_lang_list(raw_audio)
    subs = _normalize_lang_list(raw_subs)

    audio_rank = _min_rank(audio, audio_priority)
    sub_rank = _min_rank(subs, sub_priority)
    quality_rank = QUALITY_RANK.get(quality, 99)

    if lang_rule and isinstance(lang_rule, dict) and bool(lang_rule):
        is_match = evaluate_language_rule(lang_rule, audio, subs)
        match_score = 0 if is_match else 1
        score_tuple = (match_score, quality_rank, audio_rank, sub_rank)
    elif pref_audio or pref_subs:
        is_match = (audio_rank < 99) or (sub_rank < 99)
        match_score = 0 if is_match else 1
        score_tuple = (match_score, audio_rank, sub_rank, quality_rank)
    else:
        is_match = True
        match_score = 0
        score_tuple = (match_score, quality_rank, audio_rank, sub_rank)

    return ScoredOffer(
        is_match=is_match,
        score_tuple=score_tuple,
        audio_rank=audio_rank,
        sub_rank=sub_rank,
        quality_rank=quality_rank,
    )
