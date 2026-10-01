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
- offers_deactivated: rows deactivated by this run's snapshot (always 0 unless
  the run qualifies as an authoritative full snapshot);
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
mapping) runs in an earlier one. Any failure rolls every write back
(including cursor writes) and marks the created runs failed, so a broken
refresh can never leave a partially-written or partially deactivated state.
Fetch failures happen before any SyncRun exists (SyncRun attribution —
provider + country — is only derivable from the response) and are re-raised
for the caller to record; nothing was written, so existing offers remain
untouched. A fetch that raises is therefore always distinguishable from a
fetch that succeeds with zero offers.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace

from django.db import transaction

from tracker.models import ExternalProviderMapping, Provider

from ..domain.enums import SyncMode
from ..models import ContentMapping, Country, Source
from ..persistence.repositories import (
    CursorRepository,
    MappingRepository,
    OfferRepository,
    ProviderContentRepository,
    SyncRunRepository,
)
from ..sources import DEFAULT_SOURCE_SLUG, ContentRequest, source_registry
from .matching import match_local_catalog
from .normalize import normalize_content_type

logger = logging.getLogger(__name__)


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
    if content.original_title:
        metadata["original_title"] = content.original_title
    if content.duration:
        metadata["duration"] = content.duration
    return metadata


class AvailabilityRefreshService:
    """Fetch, normalize, and persist offers without participating in reads.

    Depends only on the source interface and repositories; never on Django
    views or on any concrete source class.
    """

    def __init__(self, source=None, offer_repository=None, sync_runs=None,
                 cursor_repository=None, mapping_repository=None,
                 content_repository=None):
        self.source = source if source is not None else source_registry.get(DEFAULT_SOURCE_SLUG)
        self.offers = offer_repository or OfferRepository()
        self.sync_runs = sync_runs or SyncRunRepository()
        self.cursors = cursor_repository or CursorRepository()
        self.mappings = mapping_repository or MappingRepository()
        self.contents = content_repository or ProviderContentRepository()

    def refresh_content(self, movie) -> RefreshResult:
        try:
            result = self.source.fetch(ContentRequest.from_content(movie))
        except Exception as exc:
            logger.exception(
                "availability_refresh_fetch_failed source=%s tmdb_id=%s error=%s",
                self.source.slug, movie.tmdb_id, exc,
            )
            raise
        return self._persist(result, tmdb_id=movie.tmdb_id)

    def refresh_catalogue(self, *, provider=None, country=None, cursor=None) -> RefreshResult:
        """Refresh a catalogue-capable source's whole catalogue.

        Scope comes from the source's declared capabilities: a single supported
        provider/country becomes the default scope, and a caller hint outside
        the declared scope raises instead of silently matching nothing. One
        call = one source fetch (which may internally walk many upstream pages)
        + one transactional canonical write. There is no budget accounting here
        — API budgets belong to title-targeted refreshes; a catalogue source
        reports its full configured scope per fetch.
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
        try:
            result = self.source.fetch(
                None, provider=resolved_provider, country=resolved_country, cursor=cursor
            )
        except Exception as exc:
            logger.exception(
                "availability_refresh_fetch_failed source=%s tmdb_id=%s error=%s",
                self.source.slug, None, exc,
            )
            raise
        return self._persist(result, tmdb_id=None)

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

    def _attach_tmdb_ids(self, result, source, contents):
        """Resolve provider content -> tmdb id for offers that carry none.

        Sources that already know their tmdb ids produce no unresolved offers
        and pay no mapping queries at all. Per content, resolution order is:
        an existing ``ContentMapping`` row (manual mappings are never
        overwritten — ``MappingRepository.record`` enforces precedence),
        otherwise a local title/year match against the tracker catalog.
        Content that resolves neither way yields no offers and is logged as
        unresolved instead of being guessed at — its provider-native row is
        already persisted and stays that way.

        ``contents`` maps every discovered entry to the ``ProviderContent``
        row just written by the discovery step, so mappings link to it
        without a second lookup; an entry discovery skipped simply links
        nothing.
        """
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
                match = match_local_catalog(content)
                if existing is None and match is None:
                    logger.warning(
                        "availability_mapping_unresolved source=%s provider=%s "
                        "content=%s title=%r",
                        self.source.slug, provider.slug,
                        content.provider_content_id, content.title,
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

    def _persist(self, result, *, tmdb_id=None) -> RefreshResult:
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
            provider = self._provider(normalized.provider_id)
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

        processed = created = updated = deactivated = 0
        try:
            with transaction.atomic():
                for run, provider, country, group in runs:
                    seen: set[str] = set()
                    group_created = group_updated = 0
                    for normalized in group:
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
                        seen.add(offer.identity)
                        group_created += int(was_created)
                        group_updated += int(not was_created)

                    group_deactivated = 0
                    self.sync_runs.mark_succeeded(
                        run,
                        items_discovered=len(group),
                        items_processed=len(group),
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
                    processed += len(group)
                    created += group_created
                    updated += group_updated
                    logger.info(
                        "availability_refresh_succeeded source=%s provider=%s "
                        "country=%s tmdb_id=%s sync_run=%s authoritative=%s "
                        "discovered=%s processed=%s created=%s updated=%s "
                        "deactivated=%s",
                        self.source.slug, provider.slug, country.code, tmdb_id,
                        run.pk, may_deactivate, len(group), len(group),
                        group_created, group_updated, group_deactivated,
                    )
                if result.cursor:
                    # Cursor persistence is this layer's job; it commits with the
                    # same transaction and only when a run scope exists to own it.
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

    def _provider(self, external_id):
        external_id = str(external_id).strip().lower()
        mapping = ExternalProviderMapping.objects.filter(
            integration_name=self.source.slug, external_id=external_id
        ).select_related("provider").first()
        if mapping:
            return mapping.provider
        # Existing seeded providers use their Streaming Availability id as slug.
        return Provider.objects.filter(slug=external_id, is_enabled=True).first()
