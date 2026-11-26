"""Reconciliation of raw availability observations into effective local offers.

Phase 13. Raw rows are never rewritten and never deleted: this module reads
``Offer`` (plus the ``ContentMapping`` and ``SyncRun`` rows that give it
meaning) and returns derived ``ReconciliationResult`` values. Nothing here
persists, fetches, refreshes, ranks, or scores — it only reports what the raw
observations say once they are compared with each other.

What "reconciled" means here:

* **One decision per key.** Observations are grouped by
  ``(content_type, tmdb_id, season_number, episode_number, provider,
  country)`` and, inside a group, by ``offer_type``. Two sources describing the
  same provider's offer for the same content are two observations of one offer.
* **No source ranking.** When sources disagree on a value, reconciliation
  cannot pick a winner (there is no trusted source), so it widens the value to
  "unknown", records a ``ReconciliationConflict``, and keeps every observation.
* **Availability is conservative.** An observation only counts as available
  when its raw lifecycle flag is set, its availability window is open at
  ``now``, and its provider content does not resolve to some other TMDB id.
  The effective offer is available when at least one observation is.
 * **Freshness qualifies positive evidence.** The freshness of an observation
  is classified against its source's declared refresh window — a per-source
  policy (``availability.freshness``) that ``Source.configuration`` can
  override per source. A source that declares neither reports ``unknown``
  rather than silently fresh. ``currently_available`` keeps its lifecycle and
  window meaning; the additive ``evidence`` field says whether that positive
  is recently verified, stale ("last seen"), or uncorroborated, and only
  ``verified`` may be presented as current watchable availability.

Query shape is deliberately fixed: one row query plus one bulk lookup each for
mappings, cross-content claims and sync runs — no per-offer query.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional

from django.db.models import F, Exists, OuterRef, Q
from django.utils import timezone

from ..domain.enums import AvailabilityEvidence, ConflictKind, ContentType, FreshnessState, MatchMethod, SyncMode, SyncStatus
from ..domain.models import offer_variant_key
from ..domain.reconciliation import (
    OfferObservation,
    PriceObservation,
    ReconciledOffer,
    ReconciliationAudit,
    ReconciliationConflict,
    ReconciliationDiagnostics,
    ReconciliationGroup,
    ReconciliationResult,
    SourceFact,
)
# Shared with the refresh path: one declared freshness policy per source.
# Re-exported here so existing importers keep working.
from ..freshness import FRESHNESS_INTERVAL_KEY, TITLE_AGGREGATORS, freshness_state, title_refresh_interval
from .departures import UNSAFE_CONFLICTS
from ..models import ContentMapping, ProviderContent
from .identity_policy import (
    EXTERNAL_ID_ASSERTION_SOURCES,
    TITLE_SEARCH_IDENTITY_SOURCES,
    reviewed_provider_series_tmdb_id,
)
from .normalize import normalize_justwatch_quality, offer_variant_window_open
from ..persistence.repositories import (
    MappingRepository,
    OfferPriceRepository,
    OfferRepository,
    SyncRunRepository,
)


class AvailabilityReconciliationService:
    """Read-only reconciliation of canonical availability rows."""

    def __init__(self, offer_repository=None, mapping_repository=None,
                 sync_run_repository=None, price_repository=None):
        self.offers = offer_repository or OfferRepository()
        self.mappings = mapping_repository or MappingRepository()
        self.sync_runs = sync_run_repository or SyncRunRepository()
        self.prices = price_repository or OfferPriceRepository()

    # ------------------------------------------------------------------ API

    def reconcile(self, *, content_types, tmdb_id, country=None,
                  season_number=None, episode_number=None, now=None):
        """Reconcile every observation of one TMDB content.

        Args:
            content_types: iterable of canonical ContentType values to include
                (a bare string is treated as a single content type).
            tmdb_id: TMDB id the observations claim.
            country: optional two-letter country code to reconcile within.
            season_number / episode_number: optional season/episode scoping,
                matching the raw read boundaries.
            now: evaluation instant; defaults to the current time. Naive
                values are made aware so window and freshness comparisons are
                well defined.

        Returns:
            ReconciliationResult with the effective offers, every conflict
            found, and counts describing the pass. Empty result when no raw
            observations exist — never an error, never a fetch.
        """
        content_types = self._validate_content_types(content_types)
        now = self._now(now)
        country = country.upper() if country else None
        rows = list(self.offers.observations_for(
            tmdb_id=tmdb_id,
            content_types=content_types,
            season_number=season_number,
            episode_number=episode_number,
            country_code=country,
        ))
        if not rows:
            return ReconciliationResult.empty(
                content_types=content_types,
                tmdb_id=tmdb_id,
                country=country,
                season_number=season_number,
                episode_number=episode_number,
                now=now,
            )

        offers, conflicts, statuses = self._reconcile_rows(rows, now=now)
        return ReconciliationResult(
            content_types=content_types,
            tmdb_id=tmdb_id,
            country=country,
            season_number=season_number,
            episode_number=episode_number,
            offers=tuple(offers),
            conflicts=tuple(conflicts),
            diagnostics=self._diagnostics(rows=rows, offers=offers,
                                          conflicts=conflicts, statuses=statuses),
            now=now,
        )

    def reconcile_content(self, *, content_type, tmdb_id, country=None, now=None):
        """Single-content convenience wrapper around :meth:`reconcile`."""
        return self.reconcile(
            content_types=(content_type,),
            tmdb_id=tmdb_id,
            country=country,
            now=now,
        )

    def audit(self, *, now=None):
        """Database-wide reconciliation summary for observability.

        Costs one pass over every observation plus one count of provider
        contents with no stored mapping. It is not part of any read path.
        """
        now = self._now(now)
        rows = list(self.offers.all_observations())
        if rows:
            offers, conflicts, statuses = self._reconcile_rows(rows, now=now)
        else:
            offers, conflicts, statuses = [], [], {}
        diagnostics = self._diagnostics(rows=rows, offers=offers,
                                        conflicts=conflicts, statuses=statuses)
        groups = {
            (offer.content_type, offer.tmdb_id, offer.season_number,
             offer.episode_number, offer.provider, offer.country)
            for offer in offers
        }
        return ReconciliationAudit(
            observations=diagnostics.observations,
            groups=len(groups),
            effective_offers=diagnostics.effective_offers,
            conflicts=diagnostics.conflicts,
            conflicts_by_kind=diagnostics.conflicts_by_kind,
            stale_observations=diagnostics.stale_observations,
            failed_syncs=diagnostics.failed_syncs,
            unresolved_contents=self._unresolved_contents(),
            sources=diagnostics.sources,
            now=now,
        )

    # ------------------------------------------------------------ internals

    @staticmethod
    def _validate_content_types(content_types) -> tuple[str, ...]:
        if isinstance(content_types, str):
            content_types = (content_types,)
        values = tuple(content_types)
        if not values:
            raise ValueError("content_types must not be empty")
        for value in values:
            if value not in ContentType.values:
                raise ValueError(
                    f"Invalid content_type: {value}. "
                    f"Must be one of: {ContentType.MOVIE}, {ContentType.SERIES}"
                )
        return values

    @staticmethod
    def _now(now: Optional[datetime]) -> datetime:
        return _aware(now if now is not None else timezone.now())

    def _reconcile_rows(self, rows, *, now):
        """Group raw rows, then reconcile each group. Returns offers,
        deduped conflicts, and the latest sync status per observed scope."""
        mappings = self.mappings.trusted_for_provider_content(rows)
        claims = self.offers.alternate_content_claims(rows)
        statuses = self.sync_runs.latest_by_scope(
            (row.provider_id, row.source_id, row.country_id) for row in rows
        )
        # Prices are read once, by canonical offer identity, and handed to every
        # observation of that identity. A price row can therefore never be
        # attached to an offer it does not belong to.
        prices_by_identity: dict[str, list] = {}
        for price in self.prices.for_identities({row.identity for row in rows}):
            prices_by_identity.setdefault(price.offer_identity, []).append(price)

        title_checks = self._title_checks(rows)
        grouped: dict[ReconciliationGroup, dict[str, list[OfferObservation]]] = {}
        conflicts: list[ReconciliationConflict] = []
        for row in rows:
            group = ReconciliationGroup(
                content_type=row.content_type,
                tmdb_id=row.tmdb_id,
                season_number=row.season_number,
                episode_number=row.episode_number,
                provider=row.provider.slug,
                country=row.country.code,
            )
            reviewed_series = reviewed_provider_series_tmdb_id(
                group.provider, row.provider_content_id,
            )
            if reviewed_series is not None and (
                row.tmdb_id != reviewed_series
                or row.content_type not in (ContentType.SERIES, ContentType.SEASON, ContentType.EPISODE)
            ):
                conflicts.append(self._conflict(
                    ConflictKind.MAPPING,
                    group=group,
                    offer_ids=[row.pk],
                    sources=[row.source.slug],
                    facts=[SourceFact(
                        row.source.slug, f"programme belongs to tmdb series {reviewed_series}",
                    )],
                ))
                continue
            observation = self._observe(
                row,
                now=now, mappings=mappings, claims=claims, statuses=statuses,
                prices=prices_by_identity.get(row.identity, ()),
                title_checks=title_checks,
                has_reviewed_identity=reviewed_series is not None,
            )
            grouped.setdefault(group, {}).setdefault(row.offer_type, []).append(observation)

        offers: list[ReconciledOffer] = []
        for group, subgroups in grouped.items():
            group_offers, group_conflicts = self._reconcile_group(group, subgroups)
            offers.extend(group_offers)
            conflicts.extend(group_conflicts)

        # Raw reads are ordered by id; keeping the group's lowest observation
        # first preserves that order for the common one-row-per-group case.
        offers.sort(key=lambda offer: offer.offer_ids[0])
        conflicts = _dedupe(conflicts)
        conflicts.sort(key=lambda c: (c.provider, c.country, str(c.kind), c.offer_ids))
        return offers, conflicts, statuses

    @staticmethod
    def _title_checks(rows):
        from tracker.models import TitleAvailabilityCheck

        ids = {
            row.tmdb_id for row in rows
            if row.source.slug in TITLE_AGGREGATORS and row.sync_run_id
            and row.sync_run.mode == SyncMode.TARGETED
        }
        if not ids:
            return {}
        return {
            (check.tmdb_id, check.content_type, check.country, check.source): check
            for check in TitleAvailabilityCheck.objects.filter(
                tmdb_id__in=ids, source__in=TITLE_AGGREGATORS,
                tmdb_id=F("movie__tmdb_id"), content_type=F("movie__content_type"),
            )
        }

    def _observe(self, row, *, now, mappings, claims, statuses, prices=(),
                 title_checks=None, has_reviewed_identity=False) -> OfferObservation:
        detail = ""
        provider_content_id = row.provider_content_id
        identity_sensitive_provider = row.provider.slug in TITLE_SEARCH_IDENTITY_SOURCES
        has_trusted_source_identity = row.source.slug in EXTERNAL_ID_ASSERTION_SOURCES
        # A stored wrong-work claim cannot invalidate a correctly keyed row.
        if provider_content_id and not has_reviewed_identity:
            key = (row.provider_id, provider_content_id)
            mapping = mappings.get(key)
            if key in mappings and mapping is None and not has_trusted_source_identity:
                detail = "provider-content mapping provenance is unverified"
            elif mapping is not None and mapping.tmdb_id != row.tmdb_id:
                if not _is_justwatch_season_child_mapping(row, mapping):
                    detail = f"mapping points to tmdb {mapping.tmdb_id}"
            elif identity_sensitive_provider and key not in mappings and not has_trusted_source_identity:
                detail = "provider-content mapping is unresolved"
            others = sorted(claims.get(key, set()) - {row.tmdb_id})
            if others:
                suffix = "also active for " + ", ".join(f"tmdb {other}" for other in others)
                detail = f"{detail}; {suffix}" if detail else suffix
        elif not provider_content_id and identity_sensitive_provider and not has_trusted_source_identity:
            detail = "provider-content mapping is unresolved"

        raw_window_open = (
            (row.available_from is None or _aware(row.available_from) <= now)
            and (row.available_until is None or _aware(row.available_until) > now)
        )
        source_prices = tuple(_price_observation(price, now=now) for price in prices)
        own_price = next(
            (price for price in source_prices if price.source == row.source.slug),
            None,
        )
        # Legacy prices have no variant snapshot and retain their scalar window.
        window_open = bool(own_price.open_variants) if own_price and own_price.variants else raw_window_open
        provider_url, quality = row.provider_url or "", row.quality or ""
        if own_price and own_price.open_variants:
            variant = own_price.selected_variant or min(
                own_price.open_variants, key=lambda variant: not variant.get("url"),
            )
            provider_url, quality = _variant_presentation(variant)
        freshness = freshness_state(
            row.source.slug,
            row.last_seen,
            configuration=row.source.configuration,
            now=now,
        )

        title_checked_at = None
        if row.source.slug in TITLE_AGGREGATORS and row.sync_run_id and row.sync_run.mode == SyncMode.TARGETED:
            # Only a completed routine check can vouch for this exact run.
            # An empty, failed or interrupted check leaves older positives as history.
            freshness = FreshnessState.STALE
            kind = ContentType.SERIES if row.content_type in (ContentType.SEASON, ContentType.EPISODE) else row.content_type
            check = (title_checks or {}).get((row.tmdb_id, kind, row.country.code, row.source.slug))
            if (
                check and not check.in_progress and check.last_succeeded_at
                and check.last_succeeded_at >= check.last_attempted_at
                and row.sync_run_id in check.successful_run_ids
                and row.sync_run.status == SyncStatus.SUCCEEDED
                and now - title_refresh_interval(row.source.slug, row.source.configuration)
                    <= (_aware_opt(row.observed_at) or _aware(row.last_seen)) <= now
                and _aware(row.last_seen) <= now
            ):
                title_checked_at = check.last_succeeded_at

        return OfferObservation(
            offer_id=row.pk,
            source=row.source.slug,
            sync_run_id=row.sync_run_id,
            provider_content_id=provider_content_id,
            provider_url=provider_url,
            quality=quality,
            audio_languages=_languages(row.audio_languages),
            subtitle_languages=_languages(row.subtitle_languages),
            available_from=_aware_opt(row.available_from),
            available_until=_aware_opt(row.available_until),
            first_seen=_aware(row.first_seen),
            last_seen=_aware(row.last_seen),
            currently_available=bool(row.currently_available),
            available_now=bool(row.currently_available) and window_open and not detail,
            freshness=freshness,
            sync_status=statuses.get((row.provider_id, row.source_id, row.country_id)),
            mapping_doubt=detail,
            price=own_price,
            source_prices=source_prices,
            window_open=window_open,
            window_unknown=bool(own_price and own_price.variant_window_unknown),
            observed_at=_aware_opt(row.observed_at),
            title_checked_at=title_checked_at,
            offer_identity=row.identity,
        )

    def _reconcile_group(self, group, subgroups):
        """Reconcile one (content, provider, country) group.

        Returns the effective offers (one per offer_type) and every conflict
        found in the group, group-level ones included.
        """
        all_observations = sorted(
            (obs for observations in subgroups.values() for obs in observations),
            key=lambda obs: (obs.source, obs.offer_id),
        )
        group_conflicts: list[ReconciliationConflict] = []

        # A group-level disagreement: sources describing this content at this
        # provider as different kinds of offer.
        types_by_source: dict[str, set[str]] = {}
        for offer_type, observations in subgroups.items():
            for obs in observations:
                types_by_source.setdefault(obs.source, set()).add(offer_type)
        if len(types_by_source) > 1 and len({
            frozenset(types) for types in types_by_source.values()
        }) > 1:
            group_conflicts.append(self._conflict(
                ConflictKind.OFFER_TYPE,
                group=group,
                offer_ids=[obs.offer_id for obs in all_observations],
                sources=list(types_by_source),
                facts=[
                    SourceFact(source, ",".join(sorted(types)))
                    for source, types in types_by_source.items()
                ],
            ))

        offers: list[ReconciledOffer] = []
        for offer_type in sorted(subgroups):
            observations = sorted(
                subgroups[offer_type], key=lambda obs: (obs.source, obs.offer_id),
            )
            offers.append(self._reconcile_offer(
                group, offer_type, observations, extra_conflicts=group_conflicts,
            ))

        conflicts = list(group_conflicts)
        for offer in offers:
            conflicts.extend(offer.conflicts)
        return offers, conflicts

    def _reconcile_offer(self, group, offer_type, observations, *,
                         extra_conflicts=()) -> ReconciledOffer:
        """Merge every observation of one offer_type into a single value."""
        conflicts: list[ReconciliationConflict] = []

        known_availability = [obs for obs in observations if not obs.window_unknown or not obs.currently_available]
        if len({obs.available_now for obs in known_availability}) > 1:
            conflicts.append(self._conflict(
                ConflictKind.AVAILABILITY,
                group=group,
                offer_ids=[obs.offer_id for obs in known_availability],
                sources=[obs.source for obs in known_availability],
                facts=[
                    SourceFact(obs.source, "available" if obs.available_now else "unavailable")
                    for obs in known_availability
                ],
            ))

        doubted = [obs for obs in observations if obs.mapping_doubt]
        if doubted:
            conflicts.append(self._conflict(
                ConflictKind.MAPPING,
                group=group,
                offer_ids=[obs.offer_id for obs in doubted],
                sources=[obs.source for obs in doubted],
                facts=[SourceFact(obs.source, obs.mapping_doubt) for obs in doubted],
            ))

        quality, quality_conflict = self._quality_value(group, observations)
        if quality_conflict is not None:
            conflicts.append(quality_conflict)

        audio, audio_conflict = self._language_value(
            group, observations, "audio_languages", "audio",
        )
        if audio_conflict is not None:
            conflicts.append(audio_conflict)

        subtitle, subtitle_conflict = self._language_value(
            group, observations, "subtitle_languages", "subtitles",
        )
        if subtitle_conflict is not None:
            conflicts.append(subtitle_conflict)

        available_from, available_until, window_conflict = self._window_values(
            group, observations,
        )
        if window_conflict is not None:
            conflicts.append(window_conflict)

        if len({obs.freshness for obs in observations}) > 1:
            conflicts.append(self._conflict(
                ConflictKind.FRESHNESS,
                group=group,
                offer_ids=[obs.offer_id for obs in observations],
                sources=[obs.source for obs in observations],
                facts=[SourceFact(obs.source, obs.freshness.value) for obs in observations],
            ))

        with_url = [obs for obs in observations if obs.provider_url]
        provider_url = ""
        if with_url:
            # Freshest source wins the link; ties break on the stable slug so
            # the result never depends on row order.
            provider_url = max(
                with_url, key=lambda obs: (obs.last_seen, obs.source),
            ).provider_url

        prices = _dedupe_prices(
            price for obs in observations for price in obs.source_prices
        )
        price, price_is_from, price_conflict = self._price_value(
            group, observations, prices,
        )
        if price_conflict is not None:
            conflicts.append(price_conflict)
        if price and price.selected_variant is not None:
            provider_url, quality = _variant_presentation(price.selected_variant)

        evidence, last_verified_at, last_seen_available_at = _evidence_values(observations)
        all_conflicts = tuple(_dedupe(list(conflicts) + list(extra_conflicts)))
        if evidence == AvailabilityEvidence.SOURCE_REPORTED and any(
            conflict.kind in UNSAFE_CONFLICTS for conflict in all_conflicts
        ):
            evidence = AvailabilityEvidence.UNKNOWN

        return ReconciledOffer(
            provider=group.provider,
            country=group.country,
            content_type=group.content_type,
            tmdb_id=group.tmdb_id,
            season_number=group.season_number,
            episode_number=group.episode_number,
            offer_type=offer_type,
            currently_available=any(obs.available_now for obs in observations),
            quality=quality,
            audio_languages=audio,
            subtitle_languages=subtitle,
            provider_url=provider_url,
            available_from=available_from,
            available_until=available_until,
            first_seen=min(obs.first_seen for obs in observations),
            last_seen=max(obs.last_seen for obs in observations),
            sources=tuple(sorted({obs.source for obs in observations})),
            offer_ids=tuple(sorted(obs.offer_id for obs in observations)),
            observations=tuple(observations),
            conflicts=all_conflicts,
            price=price,
            price_is_from=price_is_from,
            prices=prices,
            evidence=evidence,
            last_verified_at=last_verified_at,
            last_seen_available_at=last_seen_available_at,
        )

    def _price_value(self, group, observations, prices):
        """Select one display price and report source disagreement.

        ``prices`` are every source-specific row for the group's offers. Only
        current, fully validated prices can be displayed; a stale row is kept
        but never selected, so an unconfirmed price is never shown as fresh.
        """
        current = [price for price in prices if price.is_current and price.is_valid]
        conflict = None
        if len({(price.currency, price.amount) for price in current}) > 1:
            priced = [obs for obs in observations if obs.source_prices]
            conflict = self._conflict(
                ConflictKind.PRICE,
                group=group,
                offer_ids=[obs.offer_id for obs in (priced or observations)],
                sources=[price.source for price in current],
                facts=[
                    SourceFact(price.source, f"{price.currency} {price.amount}")
                    for price in current
                ],
            )
        selected, is_from = self._select_price(current)
        return selected, is_from, conflict

    @staticmethod
    def _select_price(current):
        """Deterministic display-price choice among current, valid prices.

        Rule (documented in the redesign report):

        1. the currency reported by the most sources wins; ties break on the
           ISO code, so the choice never depends on row order;
        2. within that currency the **lowest** amount wins (what "from €X"
           means for the user);
        3. amount ties break on ``(source, source_offer_id)``.

        No single "canonical price" is invented: different amounts in the same
        currency set ``is_from``, and every source-specific row is exposed
        alongside, so a disagreement is visible rather than silently averaged.
        """
        if not current:
            return None, False
        counts = Counter(price.currency for price in current)
        currency = sorted(counts, key=lambda code: (-counts[code], code))[0]
        same_currency = [price for price in current if price.currency == currency]
        chosen = min(
            same_currency,
            key=lambda price: (price.amount, price.source, price.source_offer_id),
        )
        amounts = {price.amount for price in same_currency}
        for price in same_currency:
            amounts.update(
                Decimal(variant["amount"]) for variant in price.open_variants
                if variant["currency"] == currency and variant["amount"] is not None
                and not variant["validation_error"]
            )
        return chosen, len(amounts) > 1

    def _quality_value(self, group, observations):
        """Known quality wins over unknown; two known values cannot be ranked."""
        known = [obs for obs in observations if obs.quality]
        distinct = {obs.quality for obs in known}
        if len(distinct) > 1:
            return "", self._conflict(
                ConflictKind.QUALITY,
                group=group,
                offer_ids=[obs.offer_id for obs in known],
                sources=[obs.source for obs in known],
                facts=[SourceFact(obs.source, obs.quality) for obs in known],
            )
        if not known:
            return "", None
        return known[0].quality, None

    def _language_value(self, group, observations, attribute, label):
        """Known language lists win over unknown; ordering never conflicts."""
        known = [obs for obs in observations if getattr(obs, attribute) is not None]
        distinct = {frozenset(getattr(obs, attribute)) for obs in known}
        if len(distinct) > 1:
            return None, self._conflict(
                ConflictKind.LANGUAGE,
                group=group,
                offer_ids=[obs.offer_id for obs in known],
                sources=[obs.source for obs in known],
                facts=[
                    SourceFact(
                        obs.source,
                        f"{label}={','.join(sorted(getattr(obs, attribute)))}",
                    )
                    for obs in known
                ],
            )
        if not known:
            return None, None
        # Order-insensitive comparison above; the stored order of the single
        # agreeing observation is what readers see.
        return getattr(known[0], attribute), None

    def _window_values(self, group, observations):
        """Availability window: agree -> that value, unknown -> nothing known,
        disagree -> unknown plus a conflict naming both ends."""
        values: dict[str, Optional[datetime]] = {}
        facts: list[SourceFact] = []
        involved_ids: list[int] = []
        involved_sources: list[str] = []
        for name in ("available_from", "available_until"):
            known = [obs for obs in observations if getattr(obs, name) is not None]
            distinct = {getattr(obs, name) for obs in known}
            if len(distinct) > 1:
                values[name] = None
                for obs in known:
                    facts.append(
                        SourceFact(obs.source, f"{name}={getattr(obs, name).isoformat()}")
                    )
                    involved_ids.append(obs.offer_id)
                    involved_sources.append(obs.source)
            elif known:
                values[name] = known[0].__getattribute__(name)
            else:
                values[name] = None
        conflict = None
        if facts:
            conflict = self._conflict(
                ConflictKind.WINDOW,
                group=group,
                offer_ids=involved_ids,
                sources=involved_sources,
                facts=facts,
            )
        return values["available_from"], values["available_until"], conflict

    @staticmethod
    def _conflict(kind, *, group, offer_ids, sources, facts) -> ReconciliationConflict:
        normalized = sorted(
            set(facts), key=lambda fact: (fact.source, fact.value),
        )
        return ReconciliationConflict(
            kind=kind,
            provider=group.provider,
            country=group.country,
            offer_ids=tuple(sorted(set(offer_ids))),
            sources=tuple(sorted(set(sources))),
            facts=tuple(normalized),
        )

    @staticmethod
    def _diagnostics(*, rows, offers, conflicts, statuses) -> ReconciliationDiagnostics:
        stale = sum(
            1
            for offer in offers
            for obs in offer.observations
            if obs.freshness == FreshnessState.STALE
        )
        failed = sum(
            1 for status in statuses.values() if status == SyncStatus.FAILED
        )
        kinds = Counter(conflict.kind for conflict in conflicts)
        return ReconciliationDiagnostics(
            observations=len(rows),
            effective_offers=len(offers),
            providers=len({offer.provider for offer in offers}),
            conflicts=len(conflicts),
            conflicts_by_kind=tuple(
                sorted(kinds.items(), key=lambda item: str(item[0]))
            ),
            stale_observations=stale,
            failed_syncs=failed,
            sources=tuple(sorted({row.source.slug for row in rows})),
        )

    @staticmethod
    def _unresolved_contents() -> int:
        """Provider catalogue rows with no mapping trusted for their source."""
        has_any_mapping = Exists(
            ContentMapping.objects.filter(
                provider=OuterRef("provider"),
                provider_content_id=OuterRef("provider_content_id"),
            )
        )
        has_trusted_mapping = Exists(
            ContentMapping.objects.filter(
                provider=OuterRef("provider"),
                provider_content_id=OuterRef("provider_content_id"),
            ).filter(
                Q(match_method=MatchMethod.MANUAL)
                | Q(
                    match_method=MatchMethod.EXTERNAL_ID,
                    source__slug__in=EXTERNAL_ID_ASSERTION_SOURCES,
                )
            )
        )
        return ProviderContent.objects.annotate(
            has_any_mapping=has_any_mapping,
            has_trusted_mapping=has_trusted_mapping,
        ).filter(
            Q(provider__slug__in=TITLE_SEARCH_IDENTITY_SOURCES, has_trusted_mapping=False)
            | (~Q(provider__slug__in=TITLE_SEARCH_IDENTITY_SOURCES) & Q(has_any_mapping=False))
        ).count()


_EVIDENCE_STRENGTH = (
    AvailabilityEvidence.VERIFIED,
    AvailabilityEvidence.SOURCE_REPORTED,
    AvailabilityEvidence.STALE,
    AvailabilityEvidence.UNKNOWN,
    AvailabilityEvidence.UNAVAILABLE,
)
_POSITIVE_EVIDENCE = frozenset(_EVIDENCE_STRENGTH[:-1])


def _is_justwatch_season_child_mapping(row, mapping):
    """Recognize the legacy child key only when the typed parent is corroborated."""
    if (
        row.content_type != ContentType.SEASON or row.source.slug != "justwatch"
        or not row.sync_run_id or row.sync_run.mode != SyncMode.TARGETED
        or row.sync_run.status != SyncStatus.SUCCEEDED
        or mapping.content_type != ContentType.SEASON or mapping.source.slug != "justwatch"
        or mapping.match_method != MatchMethod.EXTERNAL_ID
    ):
        return False
    content = mapping.content
    return bool(
        content is not None and content.source and content.source.slug == "justwatch"
        and content.provider_id == row.provider_id
        and content.provider_content_id == row.provider_content_id
        and content.content_type == row.content_type
        and content.season_number == row.season_number
        and content.episode_number == row.episode_number
        and content.metadata.get("identity_evidence", {}).get("justwatch_season") == {
            "series_tmdb_id": row.tmdb_id, "child_tmdb_id": mapping.tmdb_id,
        }
        and content.metadata.get("external_ids", {}).get("tmdb") == str(mapping.tmdb_id)
    )


def _evidence_values(observations):
    """Strongest evidence of one offer plus its verification timestamps.

    Sources are evaluated independently: one source's fresh observation makes
    the offer verified, but never makes another source's stale row fresh.
    """
    by_evidence = {obs.offer_id: obs.evidence for obs in observations}
    evidence = min(
        by_evidence.values(), key=_EVIDENCE_STRENGTH.index,
        default=AvailabilityEvidence.UNAVAILABLE,
    )
    verified = [
        obs.last_seen for obs in observations
        if by_evidence[obs.offer_id] == AvailabilityEvidence.VERIFIED
    ]
    positive = [
        obs.last_seen for obs in observations
        if by_evidence[obs.offer_id] in _POSITIVE_EVIDENCE
    ]
    return (
        evidence,
        max(verified, default=None),
        max(positive, default=None),
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _aware_opt(value: Optional[datetime]) -> Optional[datetime]:
    return None if value is None else _aware(value)


def _languages(value) -> Optional[tuple[str, ...]]:
    """None stays unknown; a known empty list stays known but empty."""
    if value is None:
        return None
    return tuple(str(code) for code in value)


def _dedupe(conflicts: Iterable[ReconciliationConflict]) -> list[ReconciliationConflict]:
    seen = set()
    unique = []
    for conflict in conflicts:
        if conflict.dedupe_key in seen:
            continue
        seen.add(conflict.dedupe_key)
        unique.append(conflict)
    return unique


def _price_observation(row, *, now) -> PriceObservation:
    """Adapt one stored ``OfferPrice`` row to its derived value object.

    Historical rows retain their last reported scalar. A current snapshot
    can supply a display price only through a priced variant open at read time.
    """
    variants = tuple(row.variants)
    windows = [(variant, offer_variant_window_open(variant, now)) for variant in variants]
    open_variants = tuple(variant for variant, window in windows if window is True)
    amount, currency = row.amount, row.currency or ""
    source_offer_id = row.source_offer_id or ""
    chosen = None
    effective_is_current = bool(row.is_current)
    if row.is_current and variants:
        usable = (
            variant for variant in open_variants if variant.get("amount") is not None
            and variant.get("currency") and not variant.get("validation_error")
        )
        chosen = min(
            usable, key=lambda variant: (variant["currency"], Decimal(variant["amount"])),
            default=None,
        )
        if chosen is not None:
            amount, currency = Decimal(chosen["amount"]), chosen["currency"]
            source_offer_id = chosen.get("source_offer_id") or ""
        else:
            # Keep the scalar in history when no priced variant is open.
            effective_is_current = False
    return PriceObservation(
        source=row.source.slug,
        amount=amount,
        currency=currency,
        source_offer_id=source_offer_id,
        observed_at=_aware_opt(row.observed_at),
        is_current=effective_is_current,
        validation_error=row.validation_error or "",
        variants=variants,
        open_variants=open_variants,
        selected_variant=chosen,
        variant_window_unknown=not open_variants and any(window is None for _, window in windows),
    )


def _variant_presentation(variant):
    return variant.get("url") or "", normalize_justwatch_quality(variant.get("presentation_type"))


def _dedupe_prices(prices: Iterable[PriceObservation]) -> tuple[PriceObservation, ...]:
    """Collapse identical rows and order deterministically.

    Two observations with the same identity cannot both exist (the canonical
    offer identity is unique), but a group can hold several identities whose
    price rows coincide; the reader sees one row per distinct value.
    """
    seen = set()
    unique = []
    for price in prices:
        key = (
            price.source,
            price.source_offer_id,
            price.amount,
            price.currency,
            price.is_current,
            tuple(offer_variant_key(variant) for variant in price.variants),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(price)
    unique.sort(
        key=lambda price: (
            price.source,
            str(price.amount if price.amount is not None else ""),
            price.currency,
            price.source_offer_id,
        )
    )
    return tuple(unique)
