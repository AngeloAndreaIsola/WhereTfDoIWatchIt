"""Resolve user preferences, falling back to hardcoded defaults.

In v0 the source of truth for preferences is server-side (settings.DEFAULT_PREFERENCE).
When no Preference row exists for the user, we return the hardcoded defaults
so the ranking path is exercised end-to-end without a DB migration.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from tracker.languages import normalize_language_code
from tracker.ownership import resolve_personal_owner_id
from ..models import Preference

logger = logging.getLogger(__name__)


def _norm_lang_list(langs: list) -> list[str]:
    res = []
    seen = set()
    for item in langs or []:
        norm = normalize_language_code(str(item))
        if norm and norm not in seen:
            seen.add(norm)
            res.append(norm)
    return res


DEFAULT_RANKING_PREFERENCES: dict[str, Any] = {
    "version": 1,
    "source_priority": ["official", "local", "unofficial"],
    "prefer_official": True,
    "prefer_local": True,
    "prefer_entitled": True,
    "provider_priority": [],
}


def get_default_ranking_preferences() -> dict[str, Any]:
    return dict(DEFAULT_RANKING_PREFERENCES)


def resolve_preferences(user_id: int | None = None) -> dict[str, Any]:
    """Return preference dict for a user.

    Keys: audio_languages, subtitle_languages, candidate_countries, current_country,
    language_rule, vpn_enabled, ranking_preferences.
    Resolves each preference domain independently so that saving ranking preferences
    never wipes out server-side defaults for language configuration on a migration-seeded row.
    """
    user_id = resolve_personal_owner_id() if user_id is None else user_id
    row = Preference.objects.filter(user_id=user_id).first()
    defaults = getattr(settings, "DEFAULT_PREFERENCE", {})

    # 1. Language domain: if row exists and has audio/subtitle/language_rule, use DB; else fallback to defaults
    if row is not None and (row.audio_languages or row.subtitle_languages or row.language_rule):
        audio_languages = _norm_lang_list(row.audio_languages)
        subtitle_languages = _norm_lang_list(row.subtitle_languages)
        language_rule = row.language_rule
    else:
        audio_languages = _norm_lang_list(defaults.get("audio_languages", []))
        subtitle_languages = _norm_lang_list(defaults.get("subtitle_languages", []))
        language_rule = defaults.get("language_rule", None)

    # 2. Country & VPN domain
    if row is not None:
        candidate_countries = list(row.candidate_countries) if row.candidate_countries else list(defaults.get("candidate_countries", []))
        current_country = row.current_country if row.current_country is not None else defaults.get("current_country", "")
        vpn_enabled = bool(row.vpn_enabled) if hasattr(row, "vpn_enabled") else bool(defaults.get("vpn_enabled", False))
    else:
        candidate_countries = list(defaults.get("candidate_countries", []))
        current_country = defaults.get("current_country", "")
        vpn_enabled = bool(defaults.get("vpn_enabled", False))

    # 3. Ranking domain
    default_ranking = defaults.get("ranking_preferences") or get_default_ranking_preferences()
    if row is not None and isinstance(row.ranking_preferences, dict) and row.ranking_preferences:
        ranking_preferences = row.ranking_preferences
    else:
        ranking_preferences = default_ranking

    return {
        "audio_languages": audio_languages,
        "subtitle_languages": subtitle_languages,
        "candidate_countries": candidate_countries,
        "current_country": current_country,
        "language_rule": language_rule,
        "vpn_enabled": vpn_enabled,
        "ranking_preferences": ranking_preferences,
    }


def build_preference(user_id: int | None = None) -> Preference:
    """An (unsaved) ``Preference`` instance from :func:`resolve_preferences`.

    Shared so every read path (detail verdict, access classification) scores
    and filters with the exact same language rule, countries, VPN setting,
    and ranking preferences.
    """
    user_id = resolve_personal_owner_id() if user_id is None else user_id
    data = resolve_preferences(user_id=user_id)
    return Preference(
        user_id=user_id,
        audio_languages=data.get("audio_languages") or [],
        subtitle_languages=data.get("subtitle_languages") or [],
        candidate_countries=data.get("candidate_countries") or [],
        current_country=data.get("current_country") or "",
        language_rule=data.get("language_rule"),
        vpn_enabled=bool(data.get("vpn_enabled")),
        ranking_preferences=data.get("ranking_preferences") or get_default_ranking_preferences(),
    )


def resolve_source_country(user_id: int | None = None) -> str | None:
    """The user's selected market, for sources that cannot answer without one.

    JustWatch is queried per market, so a title-targeted refresh has to say
    which one. Previously nothing passed it and every such fetch died on
    ``justwatch requires an explicit country code`` — the guard was correct,
    the callers were incomplete.

    Reads ``Preference.current_country``, the user's own selection, through
    ``resolve_preferences`` so the DB row and the server-side default are
    honoured the same way as everywhere else. It is never inferred from IP,
    VPN or browser locale: an aggregator asked about the wrong market returns
    confidently wrong offers, which is worse than an explicit error.

    Returns ``None`` when no country is selected, and callers pass that
    through unchanged so the source keeps deciding for itself. Streaming
    Availability answers for every country in one call and is unaffected.
    Lives here rather than in either refresh path because both need it and
    one already imports the other.
    """
    user_id = resolve_personal_owner_id() if user_id is None else user_id
    try:
        selected = resolve_preferences(user_id).get("current_country")
    except Exception:  # noqa: BLE001 - preferences must never break a refresh
        logger.warning(
            "availability_country_unreadable user_id=%s", user_id, exc_info=True
        )
        return None
    code = str(selected or "").strip().upper()
    return code or None
