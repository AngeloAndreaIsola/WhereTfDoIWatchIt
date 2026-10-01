"""Derived reconciliation values (Phase 13).

Raw vs derived:

- **Raw** lives in ``availability.models``: ``Offer``, ``ProviderContent``,
  ``ContentMapping``, ``SyncRun`` rows. Reconciliation reads them, never
  rewrites them, and never moves ``currently_available``.
- **Derived** lives here: frozen value objects computed at read time from raw
  rows. Nothing in this module is persisted, so no field here can produce a
  migration.

The reconciliation key is ``(content_type, tmdb_id, season_number,
episode_number, provider, country)`` — one decision per content, per provider,
per country. ``provider_content_id`` and ``source`` deliberately do *not* form
part of the key: two sources describing the same provider's offer for the same
content are observations of one offer, not two offers. Their disagreement is
kept as provenance (``OfferObservation``) and, when it changes what a viewer
would see, as a ``ReconciliationConflict``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import ConflictKind, FreshnessState, SyncStatus


@dataclass(frozen=True)
class SourceFact:
    """One source's contribution to a conflict: (source slug, observed value)."""

    source: str
    value: str


@dataclass(frozen=True)
class ReconciliationConflict:
    """Two or more sources describing the same key in incompatible ways.

    Conflicts are surfaced, never auto-resolved: there is no source ranking, so
    reconciliation cannot pick a winner — it can only report that the raw
    observations disagree and let the reader decide.
    """

    kind: ConflictKind
    provider: str
    country: str
    offer_ids: tuple[int, ...]
    sources: tuple[str, ...]
    facts: tuple[SourceFact, ...]

    @property
    def dedupe_key(self):
        return (self.kind, self.provider, self.country, self.offer_ids,
                self.sources, self.facts)


@dataclass(frozen=True)
class OfferObservation:
    """One raw ``Offer`` row as reconciliation interpreted it.

    Provenance side of the split: every field here is traceable to a single
    row (``offer_id``) or to the run that produced it (``sync_run_id``).
    """

    offer_id: int
    source: str
    sync_run_id: Optional[int]
    provider_content_id: str
    provider_url: str
    quality: str
    audio_languages: Optional[tuple[str, ...]]
    subtitle_languages: Optional[tuple[str, ...]]
    available_from: Optional[datetime]
    available_until: Optional[datetime]
    first_seen: datetime
    last_seen: datetime
    currently_available: bool
    """Raw lifecycle flag, exactly as stored (authoritative deactivation sets it False)."""
    available_now: bool
    """Derived: lifecycle flag AND window open AND not mapping-doubted."""
    freshness: FreshnessState
    sync_status: Optional[SyncStatus]
    mapping_doubt: str
    """Non-empty when this observation's provider content resolves to another TMDB id."""


@dataclass(frozen=True)
class ReconciliationGroup:
    """The unit reconciliation decides about: content + provider + country.

    Hashable so it can key the grouping pass; ordering helpers sort by explicit
    fields rather than relying on field declaration order.
    """

    content_type: str
    tmdb_id: int
    season_number: Optional[int]
    episode_number: Optional[int]
    provider: str
    country: str


@dataclass(frozen=True)
class ReconciledOffer:
    """One effective offer for the key: merged observations, kept disagreements."""

    provider: str
    country: str
    content_type: str
    tmdb_id: int
    season_number: Optional[int]
    episode_number: Optional[int]
    offer_type: str
    currently_available: bool
    quality: str
    audio_languages: Optional[tuple[str, ...]]
    subtitle_languages: Optional[tuple[str, ...]]
    provider_url: str
    available_from: Optional[datetime]
    available_until: Optional[datetime]
    first_seen: datetime
    last_seen: datetime
    sources: tuple[str, ...]
    offer_ids: tuple[int, ...]
    observations: tuple[OfferObservation, ...]
    conflicts: tuple[ReconciliationConflict, ...]

    @property
    def source_urls(self) -> tuple[tuple[str, str], ...]:
        """Per-source URLs, so a losing source's link is never thrown away."""
        return tuple(
            (obs.source, obs.provider_url)
            for obs in self.observations
            if obs.provider_url
        )

    @property
    def sync_run_ids(self) -> tuple[int, ...]:
        return tuple(sorted({
            obs.sync_run_id for obs in self.observations
            if obs.sync_run_id is not None
        }))


@dataclass(frozen=True)
class ReconciliationDiagnostics:
    """Counts describing one reconciliation pass (explainability, not ranking)."""

    observations: int
    effective_offers: int
    providers: int
    conflicts: int
    conflicts_by_kind: tuple[tuple[ConflictKind, int], ...]
    stale_observations: int
    failed_syncs: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ReconciliationResult:
    """Everything one reconcile() call decided, plus the question it answered."""

    content_types: tuple[str, ...]
    tmdb_id: int
    country: Optional[str]
    season_number: Optional[int]
    episode_number: Optional[int]
    offers: tuple[ReconciledOffer, ...]
    conflicts: tuple[ReconciliationConflict, ...]
    diagnostics: ReconciliationDiagnostics
    now: datetime

    @classmethod
    def empty(cls, *, content_types, tmdb_id, country=None, season_number=None,
              episode_number=None, now=None) -> "ReconciliationResult":
        return cls(
            content_types=tuple(content_types),
            tmdb_id=tmdb_id,
            country=country,
            season_number=season_number,
            episode_number=episode_number,
            offers=(),
            conflicts=(),
            diagnostics=ReconciliationDiagnostics(
                observations=0,
                effective_offers=0,
                providers=0,
                conflicts=0,
                conflicts_by_kind=(),
                stale_observations=0,
                failed_syncs=0,
                sources=(),
            ),
            now=now,
        )


@dataclass(frozen=True)
class ReconciliationAudit:
    """Database-wide reconciliation summary (observability, not a read path)."""

    observations: int
    groups: int
    effective_offers: int
    conflicts: int
    conflicts_by_kind: tuple[tuple[ConflictKind, int], ...]
    stale_observations: int
    failed_syncs: int
    unresolved_contents: int
    sources: tuple[str, ...]
    now: datetime
