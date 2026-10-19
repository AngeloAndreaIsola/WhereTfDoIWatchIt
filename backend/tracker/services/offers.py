"""Canonical offer dataclass for internal use.

Replaces the drifting dict shape used across cache, ranking, and
watchability. Offer instances support both attribute access
(`offer.country`) and dict-like access (`offer.get("country")`) so
existing call sites that mutate or index offers keep working.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


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
    #: Structured display price (``{"amount": "2.99", "currency": "EUR", ...}``)
    #: or None. "No price" is never rendered as "free".
    price: Optional[dict] = None
    #: Every source-specific price row (current and stale) for inspection.
    prices: list[dict] = field(default_factory=list)

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
            price=d.get("price") or None,
            prices=list(d.get("prices") or []),
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
            "price": self.price,
            "prices": self.prices,
        }
