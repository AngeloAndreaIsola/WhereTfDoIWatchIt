"""Provider-independent availability source interface (Phase 4).

Dependency direction:

    AvailabilitySource.fetch(ContentRequest) -> SourceFetchResult
        -> AvailabilityRefreshService (SyncRun lifecycle, normalization orchestration)
        -> OfferRepository -> availability.Offer

Rules this module encodes:

- A source retrieves and parses external data; it never writes to
  ``availability.Offer`` and never resolves ``Provider``/``Country`` rows.
  It returns persistence-free values only.
- ``Source.slug`` (provenance: where the information came from) is a different
  concept from ``Offer.provider`` (where the user can actually watch). Several
  sources may feed the same provider; they stay distinguishable by source.
- Canonical interpretation of offer type, country, language, content type and
  dates belongs to ``availability.services.normalize`` — collectors call those
  helpers instead of implementing their own interpretation.
- Fetch failure vs empty result: raising (ideally :class:`SourceFetchError`)
  means the source could not retrieve data; returning a
  ``SourceFetchResult`` with no offers means the source successfully reported
  nothing. The refresh service propagates failures without creating SyncRuns;
  empty results are a normal outcome.
- Sync semantics (``authoritative``, ``complete_snapshot``, ``sync_mode``)
  travel on every result and are never inferred from the source name. Only an
  explicitly authoritative, complete, non-incremental result may lead to
  deactivation of unseen offers, and only if the source declares
  ``supports_authoritative_snapshots``.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from ..domain.enums import SourceType, SyncMode
from ..domain.models import DiscoveredContent, ProviderOffer


def accepts_kwarg(func, name):
    """Whether *func* declares a keyword argument called *name*.

    Catalogue sources gain optional knobs at different times, and a keyword a
    source never declared is a hard ``TypeError`` rather than a silent default.
    This lets a caller pass a newer keyword only to the sources that actually
    declare it, so adding an optional parameter to one source never breaks the
    others.

    Deliberately conservative: anything not provably able to take the keyword
    is called without it. The worst case is then a source ignoring the knob, not
    a refresh failing in production.
    """
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    parameter = signature.parameters.get(name)
    if parameter is None:
        return False
    return parameter.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


class SourceFetchError(Exception):
    """A source failed to retrieve data — never an empty (but valid) result."""


@dataclass(frozen=True)
class ContentRequest:
    """What one fetch is about: persistence-free, no tracker/Django models.

    ``content_type`` stays the raw tracker value ("movie"/"series"); sources
    run it through ``normalize`` when they need the canonical form.
    """

    tmdb_id: int
    content_type: str = "movie"
    title: str = ""
    year: Optional[int] = None
    seasons_data: list = field(default_factory=list)

    @classmethod
    def from_content(cls, content) -> "ContentRequest":
        """Build from a tracker ``Movie`` or any object exposing its fields."""
        return cls(
            tmdb_id=content.tmdb_id,
            content_type=getattr(content, "content_type", None) or "movie",
            title=getattr(content, "title", None) or "",
            year=getattr(content, "year", None),
            seasons_data=list(getattr(content, "seasons_data", None) or []),
        )


@dataclass(frozen=True)
class SourceCapabilities:
    """What a source supports, so orchestration never branches on slugs.

    ``None`` for the collection fields means "unrestricted" — the source may
    return whatever providers/countries/content types it finds. Concrete
    values narrow the scope; the refresh service skips offers whose content
    type is outside a declared set (the provider/country filtering already
    happens against enabled DB rows).

    ``supports_title_fetch`` and ``supports_catalogue_sync`` are **independent
    capabilities**, and neither implies the other:

    - ``supports_title_fetch`` — ``fetch(content=...)`` answers one title. It
      is the discriminator for "may hold a per-title ``RefreshRequest``".
    - ``supports_catalogue_sync`` — ``fetch(content=None)`` walks a whole
      provider catalogue (partition/page/cursor) for discovery.

    A source can support either, both, or neither. Catalogue-only collectors
    (Prime Video, BBC iPlayer, CBC Gem, Tubi, Pluto TV, RaiPlay, PBS, ABC
    iview) set only ``supports_catalogue_sync`` and reject ``content``;
    title-only sources (Streaming Availability, Watchmode, YouTube, Mediaset
    Infinity) set only ``supports_title_fetch`` and reject ``content=None``;
    JustWatch implements **both** and sets both flags.
    """

    supported_providers: Optional[frozenset[str]] = None
    supported_countries: Optional[frozenset[str]] = None
    supported_content_types: Optional[frozenset[str]] = None
    supports_incremental_sync: bool = False
    supports_title_fetch: bool = False
    supports_catalogue_sync: bool = False
    supports_authoritative_snapshots: bool = False
    supports_tmdb_ids: bool = True
    supports_seasons: bool = False
    supports_episodes: bool = False
    supports_languages: bool = True


@dataclass(frozen=True)
class SourceFetchResult:
    """Normalized output of one source fetch.

    ``offers`` are already canonical (``ProviderOffer``) — adapters map their
    provider-specific payload through ``availability.services.normalize``,
    they never interpret offer types, countries, languages or dates on their
    own. Persistence stays with the refresh service.

    ``contents`` are the catalogue entries the fetch observed, identified the
    way *the provider* identifies them (no TMDb id, no offers implied). They
    are what lets the refresh service persist provider-native content even
    when mapping never resolves — unresolved discovery is not nothing.

    ``authoritative`` + ``complete_snapshot`` + ``sync_mode`` communicate the
    snapshot semantics of *this* result (never derived from the slug):
    deactivation of unseen offers requires an authoritative, complete,
    non-incremental result from a source that supports authoritative
    snapshots at all.
    """

    offers: list[ProviderOffer] = field(default_factory=list)
    contents: list[DiscoveredContent] = field(default_factory=list)
    authoritative: bool = False
    complete_snapshot: bool = False
    sync_mode: SyncMode = SyncMode.TARGETED
    cursor: Optional[str] = None


@runtime_checkable
class AvailabilitySource(Protocol):
    """Structural interface every availability source implements."""

    slug: str
    name: str
    source_type: SourceType
    capabilities: SourceCapabilities

    def fetch(
        self,
        content: Optional[ContentRequest] = None,
        *,
        provider: Optional[str] = None,
        country: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> SourceFetchResult:
        """Retrieve and normalize source data for the requested scope.

        ``provider``/``country``/``cursor`` are optional scope hints for
        catalogue-style sources; title-targeted sources may ignore them.
        Raises (ideally :class:`SourceFetchError`) on retrieval failure and
        returns an empty ``SourceFetchResult`` when the source genuinely has
        nothing to report.
        """
        ...
