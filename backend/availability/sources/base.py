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
import json
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


class SourceQuotaExceeded(SourceFetchError):
    """The source refused work because its own budget is spent.

    A quota block is deliberately a different fact from a generic fetch
    failure, because the two have different scheduling consequences:

    - ``kind == "plan_quota"`` is **terminal until the plan resets**. Retrying
      it cannot succeed, so the request must settle as ``blocked``/
      ``quota_exhausted`` instead of burning ``MAX_ATTEMPTS`` and ending as
      ``failed`` (which reads as an outage, not a budget fact).
    - ``kind == "local_budget"`` is also **terminal**: the local pre-request
      guard refused before any upstream call, so retrying cannot spend
      anything until the local window moves.
    - ``kind == "rate_limit"`` is **retryable**. The source is healthy and the
      window is short; the request stays ``pending`` and is retried on a later
      tick. It must not consume an attempt, or a burst of short 429s would
      permanently fail perfectly good work.

    ``unknown`` is treated as retryable on purpose: without evidence that the
    plan itself is exhausted, leaving work pending is the safe choice. A
    terminal block is only recorded when the source actually says so.

    ``quota`` carries the response's quota/rate headers (never credentials) so
    the decision is auditable after the fact. ``retry_after`` is the upstream
    hint in seconds, when one was supplied.
    """

    def __init__(
        self,
        message,
        *,
        kind: str = "unknown",
        retry_after: int | None = None,
        status_code: int | None = None,
        quota: dict | None = None,
        detail: str = "",
    ):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.status_code = status_code
        self.quota = dict(quota or {})
        self.detail = detail or str(message)

    @property
    def terminal(self) -> bool:
        """True for a spent plan or a local budget stop, never a rate window."""
        return self.kind in ("plan_quota", "local_budget")

    def diagnostic(self) -> str:
        """Stable, credential-free summary safe for ``RefreshRequest.last_error``.

        Deliberately includes the quota headers and ``Retry-After``: the live
        Watchmode 429 previously recorded only ``"429"``, which made a monthly
        plan exhaustion indistinguishable from a one-minute rate limit.
        """
        bits = [f"kind={self.kind}"]
        if self.status_code is not None:
            bits.append(f"status={self.status_code}")
        if self.retry_after is not None:
            bits.append(f"retry_after={self.retry_after}")
        if self.quota:
            bits.append(f"quota={json.dumps(self.quota, sort_keys=True)}")
        return f"{type(self).__name__}({', '.join(bits)}): {self.detail}"


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
    # The source can use optional local-catalogue hints (e.g. tracked titles)
    # to prioritise discovery candidates. Hints only select candidates; the
    # source still decides identity through its normal payload/mapping path.
    supports_priority_hints: bool = False
    # The market a catalogue walk covers is only knowable from the *response*,
    # not from configuration: Pluto TV reads its country off the egress region
    # and cannot be told one. Such a source cannot be handed a single
    # checkpoint, because the checkpoint belongs to a market the caller cannot
    # name before fetching. It is given every enabled market's checkpoint
    # instead (keyed by country code) and selects the one matching the region it
    # observes — so one market's progress can never be applied to another.
    country_from_result: bool = False
    # The catalogue walk is *resumable*: a truncated fetch hands back a durable
    # checkpoint that the refresh service persists, and the next fetch continues
    # from it instead of re-walking from the top.
    #
    # Deliberately a separate property from ``supports_incremental_sync``, and
    # neither implies the other:
    #
    # - ``supports_incremental_sync`` is about *offers*: the upstream serves a
    #   delta, so a visit may observe only what changed. Only JustWatch does.
    # - ``supports_resumable_catalogue_sync`` is about *traversal*: a visit may
    #   stop early and be continued later. Every catalogue collector does this,
    #   including the four that declare ``supports_incremental_sync=False``.
    #
    # Conflating the two is what made the earlier flag read as its own opposite
    # (a resumable walker declaring "not incremental"). Consumers that need to
    # know whether a *checkpoint* exists must read this one.
    supports_resumable_catalogue_sync: bool = False
    # The source supports targeted evidence fetching by provider-native identity
    # and URL (without requiring catalogue walk or TMDb lookup).
    supports_targeted_evidence: bool = False


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
    # The market this result was *observed* in, when the source can only learn
    # it from the response (Pluto TV reads its market off the egress region and
    # cannot be told one). ``None`` means "the caller's scope applies" — every
    # other source. Declaring it lets the refresh service attribute a checkpoint
    # to the market that was actually walked instead of a configured default.
    country: Optional[str] = None


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
