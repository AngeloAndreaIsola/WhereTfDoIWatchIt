"""Small, persistence-free values at the availability boundaries.

Two directions, one convention (boundary value vs stored row):

- inbound (Phase 4+): ``DiscoveredContent``/``ProviderOffer`` are what a
  source *reported*, before any Django model is touched;
- outbound (Phase 8): ``ProviderContentView`` is what the catalogue query
  path hands the application, instead of a raw ORM row.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class DiscoveredContent:
    """One provider catalogue entry as a source reported it, pre-persistence.

    Named for the discovery act, not for the stored row: it is what the
    source *saw*, while ``availability.models.ProviderContent`` is what the
    availability domain keeps. Same convention as ``ProviderOffer`` (boundary
    value) vs ``Offer`` (canonical row). Mapping/TMDb resolution never happens
    here — a discovered entry is a fact about the provider's catalogue even
    when no TMDb id can be found for it.
    """

    provider_id: str
    provider_content_id: str
    content_type: str
    title: str = ""
    original_title: str = ""
    release_date: Optional[date] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    duration: Optional[int] = None
    url: str = ""
    poster: str = ""
    description: str = ""
    external_ids: dict[str, str] = field(default_factory=dict)
    # True when the adapter wants this observation to replace the saved
    # external-id set, including with an empty set. This is useful when a
    # corrected adapter must clear values it previously synthesized.
    replace_external_ids: bool = False
    # Structured, source-reported identity limits; stored with provider-native
    # metadata so unresolved observations remain auditable.
    identity_evidence: dict = field(default_factory=dict)
    source_id: str = ""
    #: Structured rights evidence for audit/revalidation; never implies availability.
    rights_provenance: dict = field(default_factory=dict)
    #: Series TMDb ID for a season observed under that series. The season's own
    #: TMDb ID, when reported, remains in external_ids.
    parent_tmdb_id: Optional[int] = None


@dataclass(frozen=True)
class ProviderOfferWithdrawal:
    """An explicit withdrawal of this source's offer assertion for an observed item.

    Distinct from an item absent from an incomplete catalogue snapshot.
    Other sources' evidence for the same offer remains intact.
    """
    provider_id: str
    provider_content_id: str
    country: str
    content_type: str
    season_number: Optional[int] = None
    episode_number: Optional[int] = None


@dataclass(frozen=True)
class ProviderOffer:
    """A source-normalized offer, before any Django model is touched."""
    provider_id: str
    provider_content_id: str
    country: str
    content_type: str
    tmdb_id: Optional[int]
    season_number: Optional[int]
    episode_number: Optional[int]
    offer_type: str
    provider_url: str = ""
    quality: str = ""
    audio_languages: Optional[list[str]] = None
    subtitle_languages: Optional[list[str]] = None
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    source_record_id: str = ""
    source_version: str = ""
    #: Structured source price, already validated by
    #: ``availability.services.normalize.normalize_price``. ``None`` means the
    #: source supplied no validated amount — never that the offer is free.
    price_amount: Optional[Decimal] = None
    #: Explicit ISO-4217 code from the source; empty when it supplied none.
    price_currency: str = ""
    #: Why a mentioned price is not usable (blank when there is no price or it
    #: is valid). Persisted so an ambiguous observation is inspectable.
    price_error: str = ""
    source_variant: Optional[dict] = None


def offer_variant_key(variant: dict) -> str:
    """Exact observation fingerprint within an already resolved offer scope."""
    return json.dumps(variant, sort_keys=True)


@dataclass(frozen=True)
class ProviderContentView:
    """One provider catalogue entry as the application reads it (Phase 8).

    The read-side counterpart of ``DiscoveredContent``: stable, persistence-
    free, and honest about the states a catalogue row can be in —
    ``tmdb_id`` is ``None`` while no ``ContentMapping`` has resolved it
    (unresolved != missing: the row is listed either way), and offers are
    summarised rather than copied in.

    Field ownership, deliberately not collapsed:

    - ``provider`` … ``provider_url``: what the provider says exists
      (``provider`` is the stable slug, never a database primary key);
    - ``first_seen``/``last_seen``/``currently_available``: our *observation*
      of the catalogue row — ``last_seen`` moves only when a refresh sees the
      entry again, never because an offer changed and never on absence;
    - ``tmdb_id``: optional mapping outcome, read from ``ContentMapping`` at
      query time, never written here;
    - ``offer_count``/``has_offers``: presence of *currently active* ``Offer``
      rows carrying this provider-native id (the same value-level link
      ``Offer.provider_content_id`` stores, at any season/episode
      granularity). Presence information only — it is never derived from
      ``currently_available`` and never written back to either table.
    """

    provider: str
    provider_content_id: str
    content_type: str
    title: str
    release_date: Optional[date]
    season_number: Optional[int]
    episode_number: Optional[int]
    provider_url: str
    currently_available: bool
    first_seen: datetime
    last_seen: datetime
    tmdb_id: Optional[int] = None
    offer_count: int = 0

    @property
    def has_offers(self) -> bool:
        return self.offer_count > 0


@dataclass(frozen=True)
class UnofficialCatalogueEntry:
    """One unofficial/unresolved catalogue entry for the unified catalogue.

    A superset of ``ProviderContentView`` for the read path behind
    ``GET /api/catalog/unified/``: the canonical ``ProviderContent`` row plus
    the enrichment the response needs, all read from local canonical tables —
    ``ContentMapping`` for the optional TMDb resolution (``tmdb_id``/
    ``confidence``/``match_method``), ``Provider`` for display identity, and
    the row's stored metadata for poster/languages/country. Never carries an
    offer: an unofficial entry is a catalogue observation, so watchability
    stays out of this shape.

    Unlike ``ProviderContentView`` this view is year-precision (the stored
    ``release_date`` collapses to its year) because that is what the API
    contract exposes.

    ``country`` and the observation timestamps exist for the
    ``GET /api/v2/unofficial/`` contract, which serialises the row's scrape
    country and its observation times: ``first_seen``/``updated_at``/
    ``last_seen`` map onto the legacy ``created_at``/``updated_at``/
    ``last_scraped_at`` fields one-to-one.
    """

    content_id: int
    provider: str
    provider_name: str
    provider_category: str
    source_indicator: str
    provider_content_id: str
    content_type: str
    title: str
    year: Optional[int]
    poster_path: str
    provider_url: str
    country: str = ""
    tmdb_id: Optional[int] = None
    confidence: Optional[float] = None
    match_method: str = ""
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    currently_available: bool = True
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    updated_at: Optional[datetime] = None

