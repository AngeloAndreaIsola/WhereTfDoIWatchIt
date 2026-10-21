"""Watchmode provider-content identity construction.

Why this module exists
----------------------
A Watchmode ``/title/{id}/sources/`` row identifies the **provider** in
``source_id`` (203 = Netflix, 296 = Tubi, …) and exposes no provider-native
content id at all. The identity helper used to return ``wm:{source_id}``, so
every title a provider carried collapsed into one ``ProviderContent`` row:
offers for different films shared an identity, the first title's
``ContentMapping`` became the identity's mapping, and reconciliation marked
every other offer as ``mapping_doubt`` (unavailable). The collision is
provider-scoped and cross-provider at once — one collapsed row per streaming
provider, each holding many titles.

The identity is therefore ``wm:{provider_source_id}:{content_type}:{key}``,
where ``key`` is the requested content's TMDb id (the normal case: Watchmode
is title-targeted and every refresh comes from a tracker ``Movie``), falling
back to its IMDb id, then to a title slug *plus release year*. Region is
deliberately excluded: one provider content entry has offers in every region
the account can read. An identity that would exceed the 100-character column
is deterministically folded to a hash rather than failing the refresh.

This module is intentionally dependency-free (no Django, no models) so the
source adapter, the repair tool and its tests all share exactly one
definition of the format.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

#: Prefix every Watchmode provider-content id starts with.
PREFIX = "wm"

#: ``ProviderContent.provider_content_id`` is a ``CharField(max_length=100)``;
#: an identity longer than this is not persistable, so it is deterministically
#: folded to a hash instead of failing the refresh.
MAX_LENGTH = 100

#: The pre-fix format: ``wm:<provider source id>`` with no content
#: discriminator. Matched exactly (no extra ``:``), so a repaired id can never
#: be mistaken for a collapsed one.
COLLAPSED_RE = re.compile(r"^wm:[^:]+$")


def slug(value: str) -> str:
    """ASCII slug used only for the final, least-stable identity fallback."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "unknown"


def _content_type_value(content_type) -> str:
    return str(getattr(content_type, "value", content_type) or "movie")


def build_watchmode_content_id(
    source_id,
    content_type,
    *,
    tmdb_id=None,
    imdb_id=None,
    title: str = "",
    year=None,
) -> str:
    """Collision-resistant identity for one Watchmode provider row.

    Preference order is TMDb id, IMDb id, then title + year. The first two are
    stable provider-independent identifiers. The title fallback includes the
    year so a remake with the same name at the same provider does not collide;
    when no year is known it is the documented weaker fallback. An identity
    that would exceed the column limit is replaced by a deterministic hash
    (still prefixed with provider and content type), so persistence can never
    fail on a long title.
    """
    tmdb = _as_int(tmdb_id)
    if tmdb is not None:
        key = f"tmdb-{tmdb}"
    elif str(imdb_id or "").strip():
        key = f"imdb-{str(imdb_id).strip()}"
    else:
        key = f"title-{slug(title)}"
        numeric_year = _as_int(year)
        if numeric_year is not None:
            key = f"{key}-{numeric_year}"
    provider = str(source_id or "unknown").strip() or "unknown"
    content = _content_type_value(content_type)
    identity = f"{PREFIX}:{provider}:{content}:{key}"
    if len(identity) <= MAX_LENGTH:
        return identity

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    prefix = f"{PREFIX}:{provider}:{content}:"
    if len(prefix) + 2 + len(digest) <= MAX_LENGTH:
        return f"{prefix}h-{digest}"
    # A pathologically long provider name: keep the ``wm:<...>:<...>`` shape so
    # the result can never be mistaken for a collapsed ``wm:<provider>`` id.
    return f"{PREFIX}:x:{digest[: MAX_LENGTH - len(PREFIX) - 2]}"


def is_collapsed_watchmode_content_id(value) -> bool:
    """True for a pre-fix, provider-level identity such as ``wm:296``."""
    return bool(COLLAPSED_RE.match(str(value or "").strip()))


def collapsed_provider_key(value) -> str:
    """``wm:296`` -> ``296`` (the provider id embedded in a collapsed id)."""
    return str(value or "").strip().split(":", 1)[1] if ":" in str(value or "") else ""


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
