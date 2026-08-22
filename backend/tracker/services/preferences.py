"""Resolve user preferences, falling back to hardcoded defaults.

In v0 the source of truth for preferences is server-side (settings.DEFAULT_PREFERENCE).
When no Preference row exists for the user, we return the hardcoded defaults
so the ranking path is exercised end-to-end without a DB migration.
"""
from __future__ import annotations

from typing import Any

from django.conf import settings

from ..models import Preference


def resolve_preferences(user_id: int = 1) -> dict[str, Any]:
    """Return preference dict for a user.

    Keys: audio_languages, subtitle_languages, candidate_countries.
    Falls back to settings.DEFAULT_PREFERENCE when no DB row exists,
    or when the DB row has empty audio/subtitle languages (migration
    seeds an empty row in v0; server-side defaults are the source of truth).
    """
    row = Preference.objects.filter(user_id=user_id).first()
    defaults = getattr(settings, "DEFAULT_PREFERENCE", {})
    if row is not None and (row.audio_languages or row.subtitle_languages):
        return {
            "audio_languages": list(row.audio_languages),
            "subtitle_languages": list(row.subtitle_languages),
            "candidate_countries": list(row.candidate_countries),
        }
    # No DB row, or empty row (migration-seeded) — use server-side defaults.
    return {
        "audio_languages": list(defaults.get("audio_languages", [])),
        "subtitle_languages": list(defaults.get("subtitle_languages", [])),
        "candidate_countries": list(defaults.get("candidate_countries", [])),
    }
