"""Canonical offer dataclass for internal use.

Replaces the drifting dict shape used across cache, ranking, and
watchability. Offer instances support both attribute access
(`offer.country`) and dict-like access (`offer.get("country")`) so
existing call sites that mutate or index offers keep working.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


class TerritorialScope:
    """Explicit territory assertion carried separately from source provenance."""

    COUNTRY = "country"
    WORLDWIDE = "worldwide"
    UNKNOWN = "unknown"


def effective_territorial_scope(country: str, asserted_scope: str = "") -> str:
    """Normalize territory without treating provenance or missing data as global."""
    if (asserted_scope or "").lower().strip() == TerritorialScope.WORLDWIDE:
        return TerritorialScope.WORLDWIDE
    return TerritorialScope.COUNTRY if (country or "").upper().strip() else TerritorialScope.UNKNOWN


@dataclass
class Offer:
    country: str = ""
    service: str = ""
    offer_type: str = ""
    quality: str = ""
    url: str = ""
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    is_scraped_source: bool = False
    #: ``worldwide`` is an affirmative source assertion; provenance alone never
    #: grants it. Otherwise reach is determined by ``country`` (or unknown).
    territorial_scope: str = ""
    #: Optional explicit provenance tier. Empty preserves the legacy inference
    #: from ``is_scraped_source``. This field affects source ranking only;
    #: it never changes geographic reach.
    source_category: str = ""
    #: Structured display price (``{"amount": "2.99", "currency": "EUR", ...}``)
    #: or None. "No price" is never rendered as "free".
    price: Optional[dict] = None
    #: Every source-specific price row (current and stale) for inspection.
    prices: list[dict] = field(default_factory=list)
    #: Derived ``AvailabilityEvidence`` value (``verified`` / ``stale`` /
    #: ``unknown``). Defaults to ``verified`` so offers built outside the
    #: canonical read path keep their established meaning.
    availability_evidence: str = "verified"
    #: ISO timestamp of the newest positive observation behind this offer.
    last_seen_at: Optional[str] = None
    #: Source-declared availability window, serialized as UTC ISO 8601.
    available_from: Optional[str] = None
    available_until: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like access for backward compatibility."""
        return getattr(self, key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    @classmethod
    def from_dict(cls, d: dict) -> "Offer":
        """Build an Offer from a legacy dict, normalizing key drift."""
        audio = d.get("audio") or d.get("audio_languages") or []
        subs = d.get("subs") or d.get("subtitle_languages") or []
        return cls(
            country=(d.get("country") or "").upper().strip(),
            service=(d.get("service") or d.get("provider") or "").strip(),
            offer_type=(d.get("offer_type") or "").lower().strip(),
            quality=(d.get("quality") or "").lower(),
            url=(d.get("url") or ""),
            audio_languages=list(audio),
            subtitle_languages=list(subs),
            is_scraped_source=bool(d.get("is_scraped_source", False)),
            territorial_scope=(d.get("territorial_scope") or "").lower().strip(),
            source_category=(d.get("source_category") or "").lower().strip(),
            price=d.get("price") or None,
            prices=list(d.get("prices") or []),
            availability_evidence=d.get("availability_evidence") or "verified",
            last_seen_at=d.get("last_seen_at") or None,
            available_from=d.get("available_from"),
            available_until=d.get("available_until"),
            season_number=d.get("season_number"),
            episode_number=d.get("episode_number"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to a legacy-compatible dict."""
        return {
            "country": self.country,
            "service": self.service,
            "offer_type": self.offer_type,
            "quality": self.quality,
            "url": self.url,
            "audio": self.audio_languages,
            "subs": self.subtitle_languages,
            "audio_languages": self.audio_languages,
            "subtitle_languages": self.subtitle_languages,
            "is_scraped_source": self.is_scraped_source,
            "territorial_scope": effective_territorial_scope(
                self.country, self.territorial_scope
            ),
            "source_category": self.source_category,
            "price": self.price,
            "prices": self.prices,
            "availability_evidence": self.availability_evidence,
            "last_seen_at": self.last_seen_at,
            "available_from": self.available_from,
            "available_until": self.available_until,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
        }
