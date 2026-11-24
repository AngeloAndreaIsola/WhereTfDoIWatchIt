"""Write-side orchestration for canonical availability offers.

Pipeline (the WRITE path; reads never come through here):

    refresh content (title-targeted, e.g. streaming_availability)
        -> AvailabilitySource.fetch(ContentRequest) (interface, via registry)
        -> provider/country/content-type resolution (unsupported values are skipped)
        -> SyncRun lifecycle
        -> OfferRepository.upsert()
        -> optional cursor persistence
        -> optional deactivate_unseen() for authoritative snapshots only

    refresh catalogue (catalogue-driven, e.g. cbc_gem)
        -> scope resolution from SourceCapabilities (single-value scopes default,
           unknown scope hints are errors instead of silent no-ops)
        -> AvailabilitySource.fetch(None) (one fetch may walk many upstream pages)
        -> provider-native persistence: every discovered content upserts a
           ``ProviderContent`` row keyed on the provider's own identity
        -> provider content -> tmdb mapping (ContentMapping + local title/year
           matching; sources that already know tmdb ids pay nothing here)
        -> the same resolution/SyncRun/transaction pipeline as targeted refreshes

SyncRun lifecycle: pending -> running (start) -> succeeded | failed.
Each SyncRun covers one (provider, country) group of a single refresh unit.
Counter semantics per run:

- items_discovered: normalized offers the source returned for this group;
- items_processed: offers upserted for this group;
- offers_created / offers_updated: upsert outcomes for this group;
- offers_deactivated: offers that became effectively unavailable under this
  run's snapshot — i.e. no source retained a present observation (§14.10).
  Unseen offers require an authoritative full snapshot; explicit observed
  withdrawals are source-scoped and do not infer anything from absence. An offer
  a co-reporting source still observes is counted as neither deactivated nor
  updated;
- error_count / last_error: populated when the run fails.

Mapping semantics (catalogue sources without external ids): an offer is only
written once its content resolves to a TMDb id — either an existing
``ContentMapping`` (manual mappings win, enforced by ``MappingRepository``) or
an unambiguous local title/year match. Content that resolves neither way
contributes zero offers (``Offer.tmdb_id`` is non-null) and is logged as
unresolved; the refresh still succeeds, because discovery and persistence are
separate outcomes. Its provider-native ``ProviderContent`` row was already
written by the discovery step, so unresolved content is kept and later
re-observed — not discarded — until a mapping appears.

Discovery (``ProviderContent`` upserts) and mapping run in one transaction
before any SyncRun exists, and a failure in either is re-raised — the same
contract as a fetch failure (nothing provider-native, mapping-side, or
offer-side was written).

ProviderContent reconciliation (formalized in Phase 8) — *observed content ->
ProviderContent upsert -> lifecycle update*, and nothing else:

- an entry present in a successful fetch upserts its row (identity slots are
  the key, so repeated refreshes reuse the same primary key) and moves
  ``last_seen`` to this observation while ``first_seen`` never moves;
- an entry absent from a *non-authoritative* (or incremental) fetch changes
  no lifecycle field at all: neither ``last_seen`` nor
  ``currently_available`` move, because absence is not evidence — this is
  why ``may_deactivate`` below is evaluated for the offer pipeline only and
  never for the discovery step;
- re-observation of a previously deactivated row flips
  ``currently_available`` back to True on the same row.

This is deliberately *not* offer reconciliation: ``Offer`` rows follow the
Phase 3 rules through ``OfferRepository`` (authoritative, complete,
non-incremental snapshots only), while a ``ProviderContent`` row can be
observed with zero offers, zero mappings, or a mapping that changes later.

The service depends on the ``AvailabilitySource`` interface only — never on a
concrete source class and never on ``if source == ...`` branches. Source-specific
behaviour is expressed through ``SourceCapabilities`` and the per-result sync
semantics:

- ``authoritative``: the source claims this result covers its scope fully;
- ``complete_snapshot``: the result is the complete offer set for that scope;
- ``sync_mode``: an incremental result may never deactivate anything.

A run may deactivate unseen offers only when ALL of those hold, the source
declares ``supports_authoritative_snapshots``, and the run succeeded — the
repository re-checks status + authoritative itself. Streaming Availability is
permanently non-authoritative (title-targeted, offer-type-filtered subset of
each catalogue), and CBC Gem is permanently non-authoritative too (its category
indices cannot be proven complete and it exposes no removal signal), so neither
ever deactivates anything.

Failure handling: all offer writes for one refresh unit run in a single
transaction, and the whole discovery unit (``ProviderContent`` upserts +
mapping) runs in an earlier one. Any offer/discovery failure rolls those
writes back and marks the created runs failed, so a broken refresh can never
leave a partially-written or partially deactivated state. A catalogue
checkpoint is written only after that work commits and only for a successful
batch; a checkpoint-write failure fails the refresh (the offers are already
committed, and the previous checkpoint remains, so the next run retries rather
than skips). Fetch failures happen before any SyncRun exists
(SyncRun attribution — provider + country — is only derivable from the
response) and are re-raised for the caller to record; nothing was written, so
existing offers remain untouched. A fetch that raises is therefore always
distinguishable from a fetch that succeeds with zero offers.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace

from django.db import transaction
from django.utils import timezone

from tracker.models import ExternalProviderMapping, Provider

from ..domain.enums import MatchMethod, SyncMode
from ..domain.models import offer_variant_key
from .identity_policy import mapping_is_trusted
from ..models import ContentMapping, Country, Source, build_offer_identity
from ..sources.justwatch_providers import provider_slug_for_package
from ..persistence.repositories import (
    PROVIDER_CONTENT_ID_MAX_LENGTH,
    CursorRepository,
    MappingRepository,
    OfferPriceRepository,
    OfferRepository,
    ProviderContentRepository,
    SyncRunRepository,
)
from ..sources import DEFAULT_SOURCE_SLUG, ContentRequest, source_registry
from ..sources.base import accepts_kwarg
from .matching import match_local_catalog
from .normalize import normalize_content_type, truncate_field
from .write_barrier import writer_barrier

logger = logging.getLogger(__name__)

#: Confidence for a mapping a source stated outright (it reported the TMDb id
#: itself). Below a title/year match on purpose: an exact external id is the
#: strongest *automated* evidence available, but it is still a source's claim,
#: whereas a manual mapping outranks everything (``MappingRepository.record``
#: enforces manual > confidence > never-overwrite-fuzzy).
EXTERNAL_ID_CONFIDENCE = 0.95


@dataclass(frozen=True)
class RefreshResult:
    sync_runs: list = field(default_factory=list)
    discovered: int = 0
    processed: int = 0
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    skipped: int = 0
    # Offers whose content never resolved to a TMDb id: discovered, kept as
    # provider-native ProviderContent rows, but not persistable as offers
    # (Offer.tmdb_id is non-null). Reporting them here keeps summaries honest
    # about the offer gap.
    unresolved: int = 0


def _content_row_key(provider, content, content_type) -> tuple:
    """Identity key shared by the discovery and mapping steps.

    Both iterate the same ``result.contents``, so both can find the
    ``ProviderContent`` row discovered for an entry without a second query.
    """
    return (
        provider.pk,
        content.provider_content_id,
        content_type,
        content.season_number,
        content.episode_number,
    )


def _offer_observations(group, provider, country):
    """Keep each source variant while writing one canonical offer and price row."""
    by_identity = defaultdict(dict)
    for offer in group:
        if offer.source_variant is None:
            yield offer, ()
            continue
        identity = build_offer_identity(
            provider.slug, country.code, offer.content_type, offer.tmdb_id,
            offer.season_number, offer.episode_number, offer.offer_type,
            truncate_field(offer.provider_content_id, PROVIDER_CONTENT_ID_MAX_LENGTH),
        )
        by_identity[identity][offer_variant_key(offer.source_variant)] = offer

    now = timezone.now()
    for variants in by_identity.values():
        ordered = [variants[key] for key in sorted(variants)]
        observations = [
            (offer, (offer.available_from is None or offer.available_from <= now)
             and (offer.available_until is None or offer.available_until > now))
            for offer in ordered
        ]
        candidates = [offer for offer, window_open in observations if window_open] or ordered
        priced = [
            offer for offer in candidates
            if offer.price_amount is not None and offer.price_currency and not offer.price_error
        ]
        # Keep a single display price tied to the representative rendition.
        # The currency and amount win in that order, independent of input order.
        representative = min(
            priced, key=lambda offer: (offer.price_currency, offer.price_amount), default=candidates[0],
        )
        yield representative, [
            {**offer.source_variant, "available_from": offer.available_from.isoformat() if offer.available_from else None, "available_until": offer.available_until.isoformat() if offer.available_until else None, "window_open_at_observation": window_open}
            for offer, window_open in observations
        ]


def _content_metadata(content) -> dict:
    """The provider metadata that has no first-class column, without noise.

    Empty values are omitted so a source that simply does not carry a field
    never stores an empty key — and, combined with the repository's
    last-writer-wins merge, never erases one recorded earlier.
    """
    metadata = {}
    if content.poster:
        metadata["poster"] = content.poster
    if content.description:
        metadata["description"] = content.description
    if content.external_ids:
        metadata["external_ids"] = dict(content.external_ids)
    elif content.replace_external_ids:
        metadata["external_ids"] = {}
    if content.identity_evidence:
        metadata["identity_evidence"] = dict(content.identity_evidence)
    if content.original_title:
        metadata["original_title"] = content.original_title
    if content.duration:
        metadata["duration"] = content.duration
    if content.rights_provenance:
        metadata["rights_provenance"] = dict(content.rights_provenance)
    return metadata


class AvailabilityRefreshService:
    """Fetch, normalize, and persist offers without participating in reads.

    Depends only on the source interface and repositories; never on Django
    views or on any concrete source class.
    """

    def __init__(self, source=None, offer_repository=None, sync_runs=None,
                 cursor_repository=None, mapping_repository=None,
                 content_repository=None, price_repository=None):
        self.source = source if source is not None else source_registry.get(DEFAULT_SOURCE_SLUG)
        self.offers = offer_repository or OfferRepository()
        self.sync_runs = sync_runs or SyncRunRepository()
        self.cursors = cursor_repository or CursorRepository()
        self.mappings = mapping_repository or MappingRepository()
        self.contents = content_repository or ProviderContentRepository()
        self.prices = price_repository or OfferPriceRepository()

    def refresh_content(self, movie, *, country=None) -> RefreshResult:
        """Title-targeted refresh, optionally scoped to an explicit *country*.

        ``country`` is forwarded as a scope hint only. A source that ignores
        scope hints (Streaming Availability answers for every country in one
        call) is unaffected, while one that cannot answer without an explicit
        market (JustWatch) raises rather than guessing a country. Callers that
        omit it keep the previous behaviour exactly.
        """
        try:
            request = ContentRequest.from_content(movie)
            if country:
                result = self.source.fetch(request, country=country)
            else:
                result = self.source.fetch(request)
        except Exception as exc:
            logger.exception(
                "availability_refresh_fetch_failed source=%s tmdb_id=%s error=%s",
                self.source.slug, movie.tmdb_id, exc,
            )
            raise
        return self._persist(result, tmdb_id=movie.tmdb_id)

    def refresh_catalogue(self, *, provider=None, country=None, cursor=None,
                          pages: int = 1, priority=None) -> RefreshResult:
        """Refresh a catalogue-capable source's whole catalogue.

        Scope comes from the source's declared capabilities: a single supported
        provider/country becomes the default scope, and a caller hint outside
        the declared scope raises instead of silently matching nothing. One
        call = one source fetch (which may internally walk many upstream pages)
        + one transactional canonical write. There is no budget accounting here
        — API budgets belong to title-targeted refreshes; a catalogue source
        reports its full configured scope per fetch.

        ``pages`` bounds how many upstream pages one call walks. It is a
        throughput knob only: the source stops early on an empty page, so a
        partition can never be walked unbounded by raising it.

        It is passed on **only** to sources whose ``fetch`` accepts it. Every
        other catalogue source has the pre-P8 signature, and a keyword they do
        not declare is a hard ``TypeError`` — which would break all of them.
        Declared-ness is checked rather than assumed, so adding the parameter to
        another source later needs no change here.
        """
        capabilities = self.source.capabilities
        if not capabilities.supports_catalogue_sync:
            raise ValueError(
                f"Source {self.source.slug!r} does not support catalogue sync"
            )
        resolved_provider = self._resolve_scope(
            "provider", provider, capabilities.supported_providers
        )
        resolved_country = self._resolve_scope(
            "country", country, capabilities.supported_countries
        )
        provider_row = self._provider(resolved_provider) if resolved_provider else None
        country_row = (
            Country.objects.filter(
                code=str(resolved_country).upper(), enabled=True
            ).first()
            if resolved_country
            else None
        )
        source_row = Source.objects.filter(slug=self.source.slug).first()
        # Resume from the durable checkpoint unless the caller supplied one
        # explicitly (JustWatch's partition coordinator does). A source that
        # never writes a cursor is unaffected: its row is absent or empty.
        # No Source row means no checkpoint can exist; the row is created by
        # the persistence step only after a successful fetch.
        explicit_cursor = cursor is not None
        if cursor is None and source_row is not None and provider_row is not None \
                and country_row is not None:
            stored = self.cursors.get(
                provider=provider_row, source=source_row, country=country_row
            )
            if stored is not None and stored.cursor:
                cursor = stored.cursor
        elif cursor is None and capabilities.country_from_result \
                and source_row is not None and provider_row is not None:
            # A source that can only learn its market from the response cannot
            # be handed one checkpoint. Hand it every enabled market's
            # checkpoint, keyed by country code, and let it select by the region
            # it actually observes. Passing a single row here would risk
            # resuming one market's walk with another market's progress; passing
            # nothing (the previous behaviour) meant such a source never resumed
            # at all, which is the bug this exists to prevent.
            candidates = {}
            for code in sorted(capabilities.supported_countries or ()):
                row = Country.objects.filter(
                    code=str(code).upper(), enabled=True
                ).first()
                if row is None:
                    continue
                stored = self.cursors.get(
                    provider=provider_row, source=source_row, country=row
                )
                if stored is not None and stored.cursor:
                    candidates[str(code).upper()] = stored.cursor
            if candidates:
                cursor = candidates
                logger.info(
                    "availability_cursor_candidates source=%s markets=%d",
                    self.source.slug, len(candidates),
                )
        fetch_kwargs = {
            "provider": resolved_provider,
            "country": resolved_country,
            "cursor": cursor,
        }
        if pages is not None and accepts_kwarg(self.source.fetch, "pages"):
            fetch_kwargs["pages"] = pages
        if priority is not None and accepts_kwarg(self.source.fetch, "priority"):
            fetch_kwargs["priority"] = priority
        try:
            result = self.source.fetch(None, **fetch_kwargs)
        except Exception as exc:
            logger.exception(
                "availability_refresh_fetch_failed source=%s tmdb_id=%s error=%s",
                self.source.slug, None, exc,
            )
            raise
        # A source whose market is only knowable *after* the fetch (Pluto TV
        # reads it off the egress region) declares it on the result. Honour that
        # over the configured scope, or the checkpoint would be attributed to a
        # market the walk never touched — and for a source that declares many
        # countries the configured scope resolves to nothing at all, so the
        # checkpoint would be neither read nor written.
        #
        # The declaration is only trusted when it names a market the source is
        # actually allowed to serve. Without that check an unexpected value could
        # silently redirect checkpoint persistence to an unrelated country's row
        # (and, for a resumable source, resume one market's walk with another's
        # progress). An out-of-scope declaration is logged and ignored: the
        # configured scope stands.
        declared_country = str(getattr(result, "country", "") or "").strip().upper()
        if declared_country and declared_country != (resolved_country or ""):
            supported = capabilities.supported_countries
            in_scope = (
                supported is None
                or declared_country in {str(c).upper() for c in supported}
            )
            declared_row = (
                Country.objects.filter(code=declared_country, enabled=True).first()
                if in_scope
                else None
            )
            if not in_scope:
                logger.warning(
                    "availability_catalogue_country_rejected source=%s declared=%s "
                    "reason=not_a_supported_market supported=%s "
                    "checkpoint_not_redirected",
                    self.source.slug, declared_country,
                    ",".join(sorted(str(c).upper() for c in (supported or ()))) or "any",
                )
            elif declared_row is not None:
                logger.info(
                    "availability_catalogue_country_declared source=%s "
                    "declared=%s configured=%s",
                    self.source.slug, declared_country, resolved_country or "",
                )
                resolved_country = declared_country
                country_row = declared_row
            else:
                logger.warning(
                    "availability_catalogue_country_declared source=%s "
                    "declared=%s reason=no_enabled_country_row "
                    "checkpoint_not_persisted",
                    self.source.slug, declared_country,
                )
        refresh_result = self._persist(result, tmdb_id=None, persist_cursor=False)
        # Progress is a property of the *batch*, not of the offers it happened
        # to produce: a successful batch that maps zero titles still advances
        # the checkpoint. Writing it after the offer transaction means a
        # failed batch leaves the previous checkpoint intact and retries the
        # same window instead of silently skipping it.
        if result.cursor and provider_row is not None and country_row is not None:
            try:
                if source_row is None:
                    source_row = Source.objects.get(slug=self.source.slug)
                self.cursors.set(
                    provider=provider_row, source=source_row,
                    country=country_row, cursor=result.cursor,
                )
            except Exception as exc:
                logger.exception(
                    "availability_cursor_persist_failed source=%s provider=%s "
                    "country=%s error=%s",
                    self.source.slug, resolved_provider, resolved_country, exc,
                )
                raise
            logger.info(
                "availability_cursor_advanced source=%s provider=%s country=%s",
                self.source.slug, resolved_provider, resolved_country,
            )
        elif not explicit_cursor and provider_row is not None and country_row is not None:
            # The source reported no further checkpoint, so its traversal is
            # complete. Dropping a leftover checkpoint is what stops the next
            # run from resuming a finished walk (and from resurrecting a
            # cursor written by an older, longer catalogue). Only a walk the
            # source itself owns is cleared: a caller that supplied its own
            # cursor (JustWatch's partition coordinator, whose cursor row also
            # carries *other* partitions' offsets) keeps what it passed.
            #
            # Note where completion is *observable*: deleting the row removes
            # the only place a per-row timestamp could live, so this path
            # deliberately does not pretend to stamp one. Per-source completion
            # is recorded durably on the run audit row instead —
            # ``RefreshRun(service=slug, error="", finished_at=...)`` — which is
            # what ``stale_catalogue_sources`` already reads to decide
            # freshness. ``last_successful_sync`` therefore means "the last
            # completed traversal of a walk whose checkpoint row still exists",
            # which is the cycling and multi-partition case (see the model).
            try:
                if source_row is None:
                    source_row = Source.objects.get(slug=self.source.slug)
                self.cursors.delete(
                    provider=provider_row, source=source_row, country=country_row,
                )
            except Exception as exc:
                logger.exception(
                    "availability_cursor_clear_failed source=%s provider=%s "
                    "country=%s error=%s",
                    self.source.slug, resolved_provider, resolved_country, exc,
                )
                raise
            logger.info(
                "availability_catalogue_completed source=%s provider=%s country=%s",
                self.source.slug, resolved_provider, resolved_country,
            )
        return refresh_result

    @staticmethod
    def _resolve_scope(kind, requested, supported):
        """Default a scope hint from single-value capabilities; reject unknown hints."""
        if supported is None:
            return requested
        normalize = str.lower if kind == "provider" else str.upper
        declared = {normalize(value) for value in supported}
        if requested is None:
            return next(iter(declared)) if len(declared) == 1 else None
        if normalize(requested) not in declared:
            raise ValueError(
                f"Source does not support {kind} {requested!r} "
                f"(declared: {sorted(declared)})"
            )
        return normalize(requested)

    def _persist_contents(self, result, source) -> dict:
        """Upsert every catalogue entry the source discovered (Phase 7).

        Each ``DiscoveredContent`` becomes — or refreshes — one
        ``ProviderContent`` row keyed on the provider's own identity, so a
        catalogue entry persists even when TMDb matching never resolves it:
        unresolved != discarded. Entries outside the source's declared
        content types or from an unsupported provider are skipped with the
        same warnings offers use, and a repeated entry within one fetch (one
        per country offer) upserts once. Anything else that raises rolls back
        the whole discovery transaction.

        Returns the discovered rows keyed by ``_content_row_key`` so the
        mapping step can link them without a second query.
        """
        rows = {}
        if not result.contents:
            return rows
        supported = self.source.capabilities.supported_content_types
        try:
            seen = set()
            for content in result.contents:
                content_type = normalize_content_type(content.content_type)
                if supported is not None and content_type not in supported:
                    logger.warning(
                        "availability_content_skipped source=%s content_type=%s "
                        "content=%s reason=unsupported_content_type",
                        self.source.slug, content_type,
                        content.provider_content_id,
                    )
                    continue
                provider = self._provider(content.provider_id)
                if provider is None:
                    logger.warning(
                        "availability_content_skipped source=%s provider=%s "
                        "content=%s reason=unsupported_provider",
                        self.source.slug, content.provider_id,
                        content.provider_content_id,
                    )
                    continue
                key = _content_row_key(provider, content, content_type)
                if key in seen:
                    continue
                seen.add(key)
                row, _created = self.contents.upsert(
                    provider=provider,
                    source=source,
                    provider_content_id=content.provider_content_id,
                    content_type=content_type,
                    title=content.title,
                    release_date=content.release_date,
                    season_number=content.season_number,
                    episode_number=content.episode_number,
                    provider_url=content.url,
                    metadata=_content_metadata(content),
                )
                rows[key] = row
        except Exception as exc:
            logger.exception(
                "availability_contents_failed source=%s error=%s",
                self.source.slug, exc,
            )
            raise
        return rows

    @staticmethod
    def _exact_tmdb_id(content) -> Optional[int]:
        """TMDb id the source stated for this content, or ``None``.

        Read from the boundary value's ``external_ids`` — the field a source
        adapter fills when the upstream payload carries a real id. Anything
        non-numeric or falsy (``""``, ``"0"``) answers ``None``: a source that
        reports no id must not be recorded as TMDb 0, which would otherwise
        become a permanent mapping to a nonexistent movie.
        """
        raw = (content.external_ids or {}).get("tmdb")
        if raw in (None, ""):
            return None
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return value or None

    def _attach_tmdb_ids(self, result, source, contents):
        """Resolve provider content -> tmdb id and persist what we learn.

        Two independent jobs run here, and conflating them is what previously
        made the persistence model inconsistent between sources:

        1. **Persist an exact external-id mapping** for any discovered content
           that carries a TMDb id itself (JustWatch, Watchmode). Such a mapping
           is not a guess — the source stated the id — so it is recorded with
           ``EXTERNAL_ID`` confidence through the same
           ``MappingRepository.record`` seam as every other mapping. It buys
           real provenance: ``ProviderContent -> ContentMapping -> Offer``
           becomes true for *every* source rather than only the ones that
           happen to lack ids, and later reconciliation/debugging can answer
           "which source said this was TMDb 12345, and when" from the database
           instead of from ``Offer.source`` alone.

        2. **Resolve ids we do not have.** Only offers with ``tmdb_id is None``
           need lookup: an existing ``ContentMapping`` row (manual mappings are
           never overwritten — ``MappingRepository.record`` enforces
           precedence), otherwise a local title/year match against the tracker
           catalog. Content that resolves neither way yields no offers and is
           logged as unresolved instead of being guessed at — its
           provider-native row is already persisted and stays that way.

        A season/episode node inherits the series' TMDb id, and is therefore
        mapped too: its provider-native id is JustWatch's season id, which is
        exactly the provider-native identity a mapping should key on.

        ``contents`` maps every discovered entry to the ``ProviderContent``
        row just written by the discovery step, so mappings link to it
        without a second lookup; an entry discovery skipped simply links
        nothing.
        """
        exact: dict[tuple[str, str], int] = {}
        for content in result.contents:
            tmdb_id = self._exact_tmdb_id(content)
            if tmdb_id is not None:
                exact[(content.provider_id, content.provider_content_id)] = tmdb_id

        if exact and result.contents:
            try:
                for content in result.contents:
                    key = (content.provider_id, content.provider_content_id)
                    tmdb_id = exact.get(key)
                    if tmdb_id is None:
                        continue
                    provider = self._provider(content.provider_id)
                    if provider is None:
                        continue
                    content_type = normalize_content_type(content.content_type)
                    row = contents.get(_content_row_key(provider, content, content_type))
                    self.mappings.record(
                        provider=provider,
                        source=source,
                        provider_content_id=content.provider_content_id,
                        content_type=content_type,
                        tmdb_id=tmdb_id,
                        confidence=EXTERNAL_ID_CONFIDENCE,
                        match_method=MatchMethod.EXTERNAL_ID,
                        content=row,
                    )
            except Exception as exc:
                logger.exception(
                    "availability_mapping_failed source=%s error=%s",
                    self.source.slug, exc,
                )
                raise

        unresolved = {
            (offer.provider_id, offer.provider_content_id)
            for offer in result.offers
            if offer.tmdb_id is None
        }
        if not unresolved or not result.contents:
            return result

        tmdb_by_content = {}
        try:
            for content in result.contents:
                key = (content.provider_id, content.provider_content_id)
                if key not in unresolved:
                    continue
                provider = self._provider(content.provider_id)
                if provider is None:
                    logger.warning(
                        "availability_mapping_skipped source=%s provider=%s "
                        "content=%s reason=unsupported_provider",
                        self.source.slug, content.provider_id,
                        content.provider_content_id,
                    )
                    continue
                content_type = normalize_content_type(content.content_type)
                row = contents.get(_content_row_key(provider, content, content_type))
                existing = ContentMapping.objects.filter(
                    provider=provider,
                    provider_content_id=content.provider_content_id,
                ).first()
                allow_title_mapping = self.source.capabilities.supports_automatic_title_mapping
                trusted_existing = bool(
                    existing
                    and mapping_is_trusted(
                        provider.slug,
                        existing.match_method,
                        existing.source.slug,
                    )
                    and existing.content_type == content_type
                )
                match = match_local_catalog(content) if allow_title_mapping else None
                if existing is None and match is None:
                    logger.warning(
                        "availability_mapping_unresolved source=%s provider=%s "
                        "content=%s title=%r",
                        self.source.slug, provider.slug,
                        content.provider_content_id, content.title,
                    )
                    continue
                if not allow_title_mapping and not trusted_existing:
                    logger.warning(
                        "availability_mapping_unresolved source=%s provider=%s "
                        "content=%s reason=explicit_manual_mapping_required",
                        self.source.slug, provider.slug,
                        content.provider_content_id,
                    )
                    continue
                if match is not None:
                    mapping, _created = self.mappings.record(
                        provider=provider,
                        source=source,
                        provider_content_id=content.provider_content_id,
                        content_type=content_type,
                        tmdb_id=match.tmdb_id,
                        confidence=match.confidence,
                        match_method=match.method,
                        content=row,
                    )
                else:
                    # Re-verify what is already stored: the content still
                    # exists upstream, so the mapping stays current.
                    mapping, _created = self.mappings.record(
                        provider=provider,
                        source=source,
                        provider_content_id=content.provider_content_id,
                        content_type=existing.content_type,
                        tmdb_id=existing.tmdb_id,
                        confidence=existing.confidence,
                        match_method=existing.match_method,
                        content=row,
                    )
                tmdb_by_content[key] = mapping.tmdb_id
        except Exception as exc:
            logger.exception(
                "availability_mapping_failed source=%s error=%s",
                self.source.slug, exc,
            )
            raise

        if not tmdb_by_content:
            return result
        return replace(
            result,
            offers=[
                offer if offer.tmdb_id is not None
                else replace(
                    offer,
                    tmdb_id=tmdb_by_content.get(
                        (offer.provider_id, offer.provider_content_id)
                    ),
                )
                for offer in result.offers
            ],
        )

    def _persist(self, result, *, tmdb_id=None, persist_cursor=True) -> RefreshResult:
        """Persist one refresh unit behind the shared availability write barrier.

        All canonical availability writes (ProviderContent, ContentMapping,
        Offer, OfferEvidence, OfferPrice) happen inside this method.  The shared
        advisory lock held here is what lets the remediation command prove that
        no refresh writer can start (or finish) around its mutation;
        ``write_barrier`` documents the protocol and its deployment limits.
        """
        with writer_barrier(f"availability refresh for {self.source.slug}"):
            return self._persist_locked(
                result, tmdb_id=tmdb_id, persist_cursor=persist_cursor
            )

    def _persist_locked(
        self, result, *, tmdb_id=None, persist_cursor=True
    ) -> RefreshResult:
        capabilities = self.source.capabilities

        source, _ = Source.objects.get_or_create(
            slug=self.source.slug,
            defaults={"name": self.source.name, "type": self.source.source_type},
        )
        # Discovery phase: provider-native rows plus their mappings, committed
        # together before any SyncRun exists. A failure anywhere here rolls
        # the whole phase back, so a broken refresh never leaves provider-native
        # rows behind either.
        with transaction.atomic():
            contents = self._persist_contents(result, source)
            result = self._attach_tmdb_ids(result, source, contents)
            withdrawn = 0
            for content in result.offer_withdrawals:
                if (capabilities.supported_content_types is not None
                        and content.content_type not in capabilities.supported_content_types):
                    continue
                provider = self._provider(content.provider_id)
                country = Country.objects.filter(code=content.country.upper(), enabled=True).first()
                if provider is not None and country is not None:
                    withdrawn += self.offers.withdraw_observed(
                        source=source, provider=provider, country=country, content=content,
                    )
        # Everything still without a tmdb id is provider content that could
        # not be mapped (Offer.tmdb_id is non-null): discovered, counted,
        # logged by the mapping step, kept as a ProviderContent row, but never
        # written as an offer.
        unresolved = sum(1 for offer in result.offers if offer.tmdb_id is None)
        # Deactivation needs the full stack: a source claiming this result is
        # authoritative, the result being the complete offer set of its scope,
        # a non-incremental sync mode, and a source that supports authoritative
        # snapshots at all. Capability/semantics — never the source slug.
        may_deactivate = bool(
            result.authoritative
            and result.complete_snapshot
            and result.sync_mode != SyncMode.INCREMENTAL
            and capabilities.supports_authoritative_snapshots
        )
        if result.authoritative and not may_deactivate:
            logger.warning(
                "availability_authoritative_ignored source=%s tmdb_id=%s "
                "complete_snapshot=%s sync_mode=%s capability=%s",
                self.source.slug, tmdb_id, result.complete_snapshot,
                result.sync_mode, capabilities.supports_authoritative_snapshots,
            )

        grouped = defaultdict(list)
        skipped = 0
        for normalized in result.offers:
            if normalized.tmdb_id is None:
                logger.warning(
                    "availability_offer_skipped source=%s tmdb_id=%s reason=missing_tmdb",
                    self.source.slug, tmdb_id,
                )
                skipped += 1
                continue
            if (
                capabilities.supported_content_types is not None
                and normalized.content_type not in capabilities.supported_content_types
            ):
                logger.warning(
                    "availability_offer_skipped source=%s content_type=%s "
                    "tmdb_id=%s reason=unsupported_content_type",
                    self.source.slug, normalized.content_type, normalized.tmdb_id,
                )
                skipped += 1
                continue
            # Country-scoped: an aggregator's provider ids differ per market,
            # so the observed offer's own country drives the resolution.
            provider = self._provider(normalized.provider_id, country=normalized.country)
            country = Country.objects.filter(
                code=normalized.country.upper(), enabled=True
            ).first()
            if provider is None or country is None:
                logger.warning(
                    "availability_offer_skipped source=%s provider=%s country=%s "
                    "tmdb_id=%s reason=%s",
                    self.source.slug, normalized.provider_id, normalized.country,
                    normalized.tmdb_id,
                    "unsupported_provider" if provider is None else "unsupported_country",
                )
                skipped += 1
                continue
            grouped[(provider, country)].append(normalized)

        runs = [
            (
                self.sync_runs.start(
                    provider=provider,
                    source=source,
                    country=country,
                    mode=result.sync_mode,
                    authoritative=may_deactivate,
                ),
                provider,
                country,
                group,
            )
            for (provider, country), group in grouped.items()
        ]

        processed = created = updated = 0
        deactivated = withdrawn
        try:
            with transaction.atomic():
                for run, provider, country, group in runs:
                    seen: set[str] = set()
                    group_created = group_updated = 0
                    for normalized, variants in _offer_observations(group, provider, country):
                        offer, was_created = self.offers.upsert(
                            provider=provider, country=country, source=source,
                            sync_run=run,
                            content_type=normalized.content_type,
                            tmdb_id=normalized.tmdb_id,
                            season_number=normalized.season_number,
                            episode_number=normalized.episode_number,
                            offer_type=normalized.offer_type,
                            provider_content_id=normalized.provider_content_id,
                            provider_url=normalized.provider_url,
                            quality=normalized.quality,
                            audio_languages=normalized.audio_languages,
                            subtitle_languages=normalized.subtitle_languages,
                            available_from=normalized.available_from,
                            available_until=normalized.available_until,
                            source_record_id=normalized.source_record_id,
                            source_version=normalized.source_version,
                        )
                        # The price is a property of the observed offer. It is
                        # written beside the offer, in the same transaction, so
                        # a failed refresh leaves no price behind either.
                        self.prices.record(
                            offer=offer,
                            source=source,
                            amount=normalized.price_amount,
                            currency=normalized.price_currency,
                            source_offer_id=normalized.source_record_id,
                            error=normalized.price_error,
                            variants=variants,
                        )
                        seen.add(offer.identity)
                        group_created += int(was_created)
                        group_updated += int(not was_created)

                    group_deactivated = 0
                    group_processed = group_created + group_updated
                    self.sync_runs.mark_succeeded(
                        run,
                        items_discovered=len(group),
                        items_processed=group_processed,
                        offers_created=group_created,
                        offers_updated=group_updated,
                    )
                    if may_deactivate:
                        # Only a successful authoritative full snapshot may deactivate;
                        # the repository re-checks both conditions itself.
                        group_deactivated = self.offers.deactivate_unseen(run, seen)
                        run.offers_deactivated = group_deactivated
                        run.save(update_fields=["offers_deactivated", "updated_at"])
                        deactivated += group_deactivated
                    processed += group_processed
                    created += group_created
                    updated += group_updated
                    logger.info(
                        "availability_refresh_succeeded source=%s provider=%s "
                        "country=%s tmdb_id=%s sync_run=%s authoritative=%s "
                        "discovered=%s processed=%s created=%s updated=%s "
                        "deactivated=%s",
                        self.source.slug, provider.slug, country.code, tmdb_id,
                        run.pk, may_deactivate, len(group), group_processed,
                        group_created, group_updated, group_deactivated,
                    )
                if persist_cursor and result.cursor:
                    # Title-targeted refreshes keep the legacy contract: the
                    # cursor commits with the offer transaction and only when a
                    # run scope exists to own it. Catalogue refreshes persist
                    # theirs in ``refresh_catalogue`` instead, because a
                    # successful batch with zero mapped offers has no run at
                    # all and must still advance.
                    for _run, provider, country, _group in runs:
                        self.cursors.set(
                            provider=provider, source=source, country=country,
                            cursor=result.cursor,
                        )
        except Exception as exc:
            # The transaction has already rolled back every offer write; the
            # run rows were created outside it and are marked failed here.
            for run, _provider, _country, _group in runs:
                self.sync_runs.mark_failed(run, exc)
            logger.exception(
                "availability_refresh_failed source=%s tmdb_id=%s sync_runs=%s "
                "error=%s",
                self.source.slug, tmdb_id, [run.pk for run, *_ in runs], exc,
            )
            raise

        return RefreshResult(
            sync_runs=[run for run, *_ in runs],
            discovered=len(result.offers),
            processed=processed,
            created=created,
            updated=updated,
            deactivated=deactivated,
            skipped=skipped,
            unresolved=unresolved,
        )

    def persist_targeted_offers(
        self,
        offers: list[ProviderOffer],
        *,
        source_model: Source,
    ) -> tuple[int, int, list[str], list[SyncRun]]:
        """Persist fresh, already-validated targeted ProviderOffers.

        Reuses the canonical offer, price, evidence, and SyncRun persistence
        machinery without modifying ProviderContent, without calling TMDb or
        creating/altering ContentMapping, without advancing SyncCursor, and
        without deactivating any unseen offers.

        Must be called behind the write_barrier within the caller's locked
        atomic transaction.
        """
        capabilities = self.source.capabilities
        grouped = defaultdict(list)
        skipped = 0

        for normalized in offers:
            if normalized.tmdb_id is None:
                logger.warning(
                    "availability_offer_skipped source=%s reason=missing_tmdb",
                    self.source.slug,
                )
                skipped += 1
                continue
            if (
                capabilities.supported_content_types is not None
                and normalized.content_type not in capabilities.supported_content_types
            ):
                logger.warning(
                    "availability_offer_skipped source=%s content_type=%s "
                    "tmdb_id=%s reason=unsupported_content_type",
                    self.source.slug, normalized.content_type, normalized.tmdb_id,
                )
                skipped += 1
                continue
            provider = self._provider(normalized.provider_id, country=normalized.country)
            country = Country.objects.filter(
                code=normalized.country.upper(), enabled=True
            ).first()
            if provider is None or country is None:
                logger.warning(
                    "availability_offer_skipped source=%s provider=%s country=%s "
                    "tmdb_id=%s reason=%s",
                    self.source.slug, normalized.provider_id, normalized.country,
                    normalized.tmdb_id,
                    "unsupported_provider" if provider is None else "unsupported_country",
                )
                skipped += 1
                continue
            grouped[(provider, country)].append(normalized)

        runs = [
            (
                self.sync_runs.start(
                    provider=provider,
                    source=source_model,
                    country=country,
                    mode=SyncMode.TARGETED,
                    authoritative=False,
                ),
                provider,
                country,
                group,
            )
            for (provider, country), group in grouped.items()
        ]

        created_count = 0
        updated_count = 0
        persisted_identities: list[str] = []

        try:
            for run, provider, country, group in runs:
                group_created = 0
                group_updated = 0
                for normalized, variants in _offer_observations(group, provider, country):
                    offer, was_created = self.offers.upsert(
                        provider=provider,
                        country=country,
                        source=source_model,
                        sync_run=run,
                        content_type=normalized.content_type,
                        tmdb_id=normalized.tmdb_id,
                        season_number=normalized.season_number,
                        episode_number=normalized.episode_number,
                        offer_type=normalized.offer_type,
                        provider_content_id=normalized.provider_content_id,
                        provider_url=normalized.provider_url,
                        quality=normalized.quality,
                        audio_languages=normalized.audio_languages,
                        subtitle_languages=normalized.subtitle_languages,
                        available_from=normalized.available_from,
                        available_until=normalized.available_until,
                        source_record_id=normalized.source_record_id,
                        source_version=normalized.source_version,
                    )
                    self.prices.record(
                        offer=offer,
                        source=source_model,
                        amount=normalized.price_amount,
                        currency=normalized.price_currency,
                        source_offer_id=normalized.source_record_id,
                        error=normalized.price_error,
                        variants=variants,
                    )
                    persisted_identities.append(offer.identity)
                    group_created += int(was_created)
                    group_updated += int(not was_created)

                self.sync_runs.mark_succeeded(
                    run,
                    items_discovered=len(group),
                    items_processed=group_created + group_updated,
                    offers_created=group_created,
                    offers_updated=group_updated,
                )
                created_count += group_created
                updated_count += group_updated
        except Exception as exc:
            for run, _provider, _country, _group in runs:
                self.sync_runs.mark_failed(run, exc)
            raise

        return created_count, updated_count, persisted_identities, [run for run, *_ in runs]

    def _provider(self, external_id, country=None):
        """Resolve a source-reported provider id to a canonical ``Provider``.

        Country-aware by design: ``ExternalProviderMapping`` is keyed on
        ``(provider, integration_name, country)`` because aggregators rename
        providers per market — JustWatch reports Amazon Prime Video as
        ``amazonprimevideo`` in NL but ``amazonprime`` in US/GB. Resolution
        therefore tries, in order:

        1. the row for *this* market (``country=<cc>``) — the precise match;
        2. an unscoped row (``country=""``) — the backward-compatible path
           every pre-existing integration still uses;
        3. the provider slug itself — the seeded Streaming Availability id.

        A market-specific miss falling through to an unscoped row is the
        intended behavior, not a bug: an integration whose ids are
        country-independent keeps resolving from its single global row, and
        one that *is* country-specific simply has no row for a market it was
        never measured in. It never resolves to a *different* provider's
        market-specific id.
        """
        external_id = str(external_id).strip().lower()
        integration = self.source.slug
        mapping_qs = ExternalProviderMapping.objects.filter(
            integration_name=integration, external_id=external_id
        ).select_related("provider")
        country_code = str(country or "").strip().upper()
        if country_code:
            mapping = (
                mapping_qs.filter(country=country_code).first()
                or mapping_qs.filter(country="").first()
            )
        else:
            mapping = mapping_qs.filter(country="").first()
        if mapping:
            return mapping.provider
        # JustWatch reports several technical names for one provider, but
        # ``ExternalProviderMapping`` stores only one per (provider, market).
        # When the stored slot holds a sibling spelling (``netflixkids`` for
        # ``netflix``, ``viaplaysports`` for ``viaplay``) the precise lookup
        # above misses. Consult the static identity table before the slug
        # fallback so the other spelling still resolves — including while the
        # provider is still disabled, since a mapped-but-inactive provider's
        # offers are kept and annotated, not silently dropped.
        if integration == "justwatch":
            slug = provider_slug_for_package(external_id)
            if slug:
                return Provider.objects.filter(slug=slug).first()
        # Existing seeded providers use their Streaming Availability id as slug.
        return Provider.objects.filter(slug=external_id, is_enabled=True).first()
