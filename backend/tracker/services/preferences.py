"""Resolve user preferences, falling back to hardcoded defaults.

In v0 the source of truth for preferences is server-side (settings.DEFAULT_PREFERENCE).
When no Preference row exists for the user, we return the hardcoded defaults
so the ranking path is exercised end-to-end without a DB migration.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings

from tracker.languages import normalize_language_code
from ..models import Preference


def _norm_lang_list(langs: list) -> list[str]:
    res = []
    seen = set()
    for item in langs or []:
        norm = normalize_language_code(str(item))
        if norm and norm not in seen:
            seen.add(norm)
            res.append(norm)
    return res


def resolve_preferences(user_id: int = 1) -> dict[str, Any]:
    """Return preference dict for a user.

    Keys: audio_languages, subtitle_languages, candidate_countries, language_rule.
    Falls back to settings.DEFAULT_PREFERENCE when no DB row exists,
    or when the DB row has empty audio/subtitle languages (migration
    seeds an empty row in v0; server-side defaults are the source of truth).
    """
    row = Preference.objects.filter(user_id=user_id).first()
    defaults = getattr(settings, "DEFAULT_PREFERENCE", {})
    if row is not None and (row.audio_languages or row.subtitle_languages or row.language_rule):
        return {
            "audio_languages": _norm_lang_list(row.audio_languages),
            "subtitle_languages": _norm_lang_list(row.subtitle_languages),
            "candidate_countries": list(row.candidate_countries),
            "language_rule": row.language_rule,
        }
    # No DB row, or empty row (migration-seeded) — use server-side defaults.
    return {
        "audio_languages": _norm_lang_list(defaults.get("audio_languages", [])),
        "subtitle_languages": _norm_lang_list(defaults.get("subtitle_languages", [])),
        "candidate_countries": list(defaults.get("candidate_countries", [])),
        "language_rule": defaults.get("language_rule", None),
    }
