"""Read-only mapping correction impact planner.

This module evaluates the full impact and blast radius of correcting a content
mapping for a provider and provider-content ID to a proposed TMDb ID and
content type, without performing any mutations or contacting external APIs.

Reuse of existing remediation and audit principles:
---------------------------------------------------
- Strict read-only semantics: no records are updated, created, or deleted.
- Non-authoritative / fail-closed assertions.
- Credential-free database identity capture.
- Deterministic precondition fingerprinting via canonical SHA-256 digests.
- Inspectable JSON manifest emission.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Union

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from tracker.models import Movie, Provider
from ..domain.enums import ContentType, MatchMethod
from ..models import (
    ContentMapping,
    Offer,
    OfferEvidence,
    OfferPrice,
    ProviderContent,
    build_offer_identity,
)
from .mapping_remediation import (
    DATABASE_IDENTITY_SCHEMA,
    _postgres_server_identity,
    serialize_columns,
)

MANIFEST_SCHEMA = "mapping_correction_impact/manifest-v1"
VOLATILE_MANIFEST_FIELDS = frozenset({"generated_at", "digest"})
#: Model fields that are volatile timestamps or purely audit/auto-updated and should
#: be excluded from the deterministic precondition fingerprint so that dry runs
#: or read times don't alter the fingerprint unless material data changed.
VOLATILE_MODEL_FIELDS = frozenset({"updated_at"})


class MappingCorrectionPlannerError(Exception):
    """Raised when planner inputs or preconditions are fundamentally invalid."""


def database_identity() -> dict:
    """Credential-free database identity, reused from remediation principles."""
    settings_dict = connection.settings_dict
    identity = {
        "schema": DATABASE_IDENTITY_SCHEMA,
        "vendor": connection.vendor,
        "name": str(settings_dict.get("NAME", "")),
        "configured": {
            "host": str(settings_dict.get("HOST") or ""),
            "port": str(settings_dict.get("PORT") or ""),
        },
        "server": None,
        "strength": "local",
    }
    if connection.vendor == "postgresql":
        server = _postgres_server_identity()
        identity.update(
            name=server["database"],
            server={
                "address": server["address"],
                "port": server["port"],
                "version": server["version"],
                "system_identifier": server["system_identifier"],
            },
            strength=server["strength"],
        )
    return identity


def _serialize_row_for_fingerprint(instance) -> dict:
    """Serialize model instance columns, excluding volatile fields like updated_at."""
    cols = serialize_columns(instance)
    return {k: v for k, v in sorted(cols.items()) if k not in VOLATILE_MODEL_FIELDS}


def canonical_json_bytes(payload: dict) -> bytes:
    """Encode dictionary canonically for SHA-256 fingerprinting."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")


def compute_digest(payload: dict) -> str:
    """Compute deterministic SHA-256 digest over non-volatile payload elements."""
    body = {
        key: value
        for key, value in payload.items()
        if key not in VOLATILE_MANIFEST_FIELDS
    }
    return "sha256:" + hashlib.sha256(canonical_json_bytes(body)).hexdigest()


class MappingCorrectionImpactPlanner:
    """Read-only planner assessing the impact of a proposed mapping correction."""

    def __init__(
        self,
        *,
        provider: Union[str, Provider],
        provider_content_id: str,
        proposed_tmdb_id: int,
        proposed_content_type: str,
    ):
        self.raw_provider = provider
        self.provider_content_id = str(provider_content_id).strip()
        try:
            self.proposed_tmdb_id = int(proposed_tmdb_id)
        except (TypeError, ValueError) as exc:
            raise MappingCorrectionPlannerError(
                f"Invalid proposed_tmdb_id: {proposed_tmdb_id}"
            ) from exc

        self.proposed_content_type = str(proposed_content_type).strip().lower()
        if self.proposed_content_type not in ContentType.values:
            raise MappingCorrectionPlannerError(
                f"Invalid proposed_content_type '{self.proposed_content_type}'. "
                f"Must be one of: {list(ContentType.values)}"
            )
        if not self.provider_content_id:
            raise MappingCorrectionPlannerError("provider_content_id cannot be blank")

    def _resolve_provider(self) -> Provider:
        if isinstance(self.raw_provider, Provider):
            return self.raw_provider
        slug = str(self.raw_provider).strip()
        provider = Provider.objects.filter(slug=slug).first()
        if not provider:
            raise MappingCorrectionPlannerError(f"Provider not found: {slug}")
        return provider

    def plan(self, *, now: Optional[datetime] = None) -> dict:
        """Run all read-only inspection queries and build the impact report & manifest."""
        now = now or timezone.now()
        provider = self._resolve_provider()
        db_id = database_identity()

        # 1. Existing mapping
        existing_mapping = (
            ContentMapping.objects.select_related("provider", "source", "content")
            .filter(provider=provider, provider_content_id=self.provider_content_id)
            .first()
        )

        mapping_report = None
        former_tmdb_id = None
        former_content_type = None
        if existing_mapping:
            former_tmdb_id = existing_mapping.tmdb_id
            former_content_type = existing_mapping.content_type
            mapping_report = {
                "id": existing_mapping.id,
                "provider": existing_mapping.provider.slug,
                "provider_content_id": existing_mapping.provider_content_id,
                "target_tmdb_id": existing_mapping.tmdb_id,
                "target_content_type": existing_mapping.content_type,
                "match_method": existing_mapping.match_method,
                "confidence": float(existing_mapping.confidence),
                "is_manual": existing_mapping.match_method == MatchMethod.MANUAL.value,
                "source": existing_mapping.source.slug,
                "first_seen": existing_mapping.first_seen.isoformat()
                if existing_mapping.first_seen
                else None,
                "last_verified": existing_mapping.last_verified.isoformat()
                if existing_mapping.last_verified
                else None,
                "columns": serialize_columns(existing_mapping),
            }

        # 2. Provider-native identity & content
        provider_contents = list(
            ProviderContent.objects.select_related("provider", "source")
            .filter(provider=provider, provider_content_id=self.provider_content_id)
            .order_by("id")
        )

        provider_native_identity = {
            "provider_slug": provider.slug,
            "provider_content_id": self.provider_content_id,
            "content_rows_count": len(provider_contents),
            "rows": [
                {
                    "id": row.id,
                    "identity": row.identity,
                    "title": row.title,
                    "content_type": row.content_type,
                    "release_date": row.release_date.isoformat()
                    if row.release_date
                    else None,
                    "season_number": row.season_number,
                    "episode_number": row.episode_number,
                    "currently_available": row.currently_available,
                    "provider_url": row.provider_url,
                    "source": row.source.slug if row.source else None,
                    "metadata": row.metadata,
                    "columns": serialize_columns(row),
                }
                for row in provider_contents
            ],
        }

        # 3. Former and proposed TMDb targets
        def _get_tmdb_target_info(tmdb_id: Optional[int], content_type: Optional[str]) -> Optional[dict]:
            if tmdb_id is None:
                return None
            info: dict[str, Any] = {
                "tmdb_id": tmdb_id,
                "content_type": content_type,
                "in_tracker_movies": False,
                "title": None,
                "year": None,
            }
            if content_type == ContentType.MOVIE:
                m = Movie.objects.filter(tmdb_id=tmdb_id).first()
                if m:
                    info.update(
                        in_tracker_movies=True,
                        title=m.title,
                        year=m.year,
                        imdb_id=getattr(m, "imdb_id", ""),
                    )
            return info

        former_target_info = _get_tmdb_target_info(former_tmdb_id, former_content_type)
        proposed_target_info = _get_tmdb_target_info(
            self.proposed_tmdb_id, self.proposed_content_type
        )

        # 4. Associated Offers
        associated_offers = list(
            Offer.objects.select_related("provider", "country", "source", "sync_run")
            .filter(provider=provider, provider_content_id=self.provider_content_id)
            .order_by("id")
        )
        offer_identities = [offer.identity for offer in associated_offers]

        # 5. Associated OfferEvidence
        associated_evidence = list(
            OfferEvidence.objects.select_related("source")
            .filter(
                Q(offer_identity__in=offer_identities)
                | Q(
                    provider_slug=provider.slug,
                    provider_content_id=self.provider_content_id,
                )
            )
            .order_by("id")
        )
        evidence_offer_identities = {ev.offer_identity for ev in associated_evidence}
        all_related_identities = set(offer_identities) | evidence_offer_identities

        # 6. Associated OfferPrice
        associated_prices = list(
            OfferPrice.objects.select_related("source")
            .filter(
                Q(offer_identity__in=all_related_identities)
                | Q(
                    provider_slug=provider.slug,
                    provider_content_id=self.provider_content_id,
                )
            )
            .order_by("id")
        )

        # 7. Corroborating observations from other sources
        # Same provider, but other content IDs or syncs matching proposed target
        proposed_observations_same_provider = list(
            Offer.objects.select_related("provider", "country", "source")
            .filter(
                provider=provider,
                tmdb_id=self.proposed_tmdb_id,
                content_type=self.proposed_content_type,
            )
            .exclude(provider_content_id=self.provider_content_id)
            .order_by("id")
        )

        # Other providers having offers for proposed target
        proposed_observations_other_providers = list(
            Offer.objects.select_related("provider", "country", "source")
            .filter(
                tmdb_id=self.proposed_tmdb_id,
                content_type=self.proposed_content_type,
            )
            .exclude(provider=provider)
            .order_by("id")
        )

        evidence_for_proposed = list(
            OfferEvidence.objects.select_related("source")
            .filter(
                tmdb_id=self.proposed_tmdb_id,
                content_type=self.proposed_content_type,
            )
            .order_by("id")
        )

        # Corroborating mappings for proposed target
        mappings_for_proposed = list(
            ContentMapping.objects.select_related("provider", "source")
            .filter(
                tmdb_id=self.proposed_tmdb_id,
                content_type=self.proposed_content_type,
            )
            .order_by("id")
        )

        corroboration = {
            "same_provider_other_content_ids": [
                {
                    "offer_id": o.id,
                    "source": o.source.slug,
                    "country": o.country.code,
                    "provider_content_id": o.provider_content_id,
                    "offer_type": o.offer_type,
                    "currently_available": o.currently_available,
                }
                for o in proposed_observations_same_provider
            ],
            "other_providers_offers_count": len(proposed_observations_other_providers),
            "other_providers_sample": [
                {
                    "offer_id": o.id,
                    "provider": o.provider.slug,
                    "source": o.source.slug,
                    "country": o.country.code,
                    "offer_type": o.offer_type,
                    "currently_available": o.currently_available,
                }
                for o in proposed_observations_other_providers[:10]
            ],
            "other_sources_evidence_count": len(evidence_for_proposed),
            "other_mappings_for_proposed": [
                {
                    "mapping_id": m.id,
                    "provider": m.provider.slug,
                    "provider_content_id": m.provider_content_id,
                    "match_method": m.match_method,
                    "confidence": float(m.confidence),
                }
                for m in mappings_for_proposed
            ],
            "is_corroborated_at_provider": len(proposed_observations_same_provider) > 0,
            "is_corroborated_globally": (
                len(proposed_observations_same_provider) > 0
                or len(proposed_observations_other_providers) > 0
                or len(evidence_for_proposed) > 0
                or len(mappings_for_proposed) > 0
            ),
        }

        # 8. Manual mapping conflicts
        conflicting_mappings = list(
            ContentMapping.objects.select_related("provider", "source")
            .filter(
                provider=provider,
                tmdb_id=self.proposed_tmdb_id,
                content_type=self.proposed_content_type,
            )
            .exclude(provider_content_id=self.provider_content_id)
            .order_by("id")
        )

        manual_conflicts = []
        if existing_mapping and existing_mapping.match_method == MatchMethod.MANUAL.value:
            manual_conflicts.append(
                {
                    "type": "target_mapping_already_manual",
                    "mapping_id": existing_mapping.id,
                    "provider_content_id": existing_mapping.provider_content_id,
                    "detail": (
                        f"Existing mapping {existing_mapping.id} is already marked MANUAL "
                        f"pointing to TMDb {existing_mapping.tmdb_id}."
                    ),
                }
            )

        for cm in conflicting_mappings:
            if cm.match_method == MatchMethod.MANUAL.value:
                manual_conflicts.append(
                    {
                        "type": "competing_manual_mapping_for_proposed_tmdb",
                        "mapping_id": cm.id,
                        "provider_content_id": cm.provider_content_id,
                        "detail": (
                            f"Another manual mapping {cm.id} exists for provider "
                            f"{provider.slug}:{cm.provider_content_id} claiming "
                            f"proposed TMDb {self.proposed_tmdb_id}."
                        ),
                    }
                )

        mapping_conflicts = {
            "has_manual_conflicts": len(manual_conflicts) > 0,
            "manual_conflicts": manual_conflicts,
            "competing_mappings_for_proposed_tmdb": [
                {
                    "mapping_id": cm.id,
                    "provider_content_id": cm.provider_content_id,
                    "match_method": cm.match_method,
                    "confidence": float(cm.confidence),
                }
                for cm in conflicting_mappings
            ],
        }

        # 9. Potentially invalid availability after correction
        stale_offers = []
        for offer in associated_offers:
            projected_identity = build_offer_identity(
                provider.slug,
                offer.country.code,
                self.proposed_content_type,
                self.proposed_tmdb_id,
                offer.season_number,
                offer.episode_number,
                offer.offer_type,
                offer.provider_content_id,
            )
            # Discrepancy with the proposed TMDb target
            diverges = offer.identity != projected_identity
            stale_offers.append(
                {
                    "offer_id": offer.id,
                    "country": offer.country.code,
                    "current_tmdb_id": offer.tmdb_id,
                    "current_content_type": offer.content_type,
                    "current_identity": offer.identity,
                    "projected_identity_if_remapped": projected_identity,
                    "identity_diverges": diverges,
                    "currently_available": offer.currently_available,
                    "reconciliation_mapping_doubt_risk": (
                        self.proposed_tmdb_id != offer.tmdb_id
                    ),
                }
            )

        active_offers_on_former = sum(
            1 for o in associated_offers if o.currently_available
        )

        invalid_availability_analysis = {
            "former_target_active_offers_lost": active_offers_on_former,
            "total_associated_offers": len(associated_offers),
            "stale_offer_identities_count": sum(
                1 for so in stale_offers if so["identity_diverges"]
            ),
            "stale_offers": stale_offers,
            "summary": (
                f"{len(associated_offers)} associated offers ({active_offers_on_former} currently active) "
                f"will have their TMDb identity ({former_tmdb_id}) desynchronized from the mapping "
                f"({self.proposed_tmdb_id}). Without offer migration/remediation, reconciliation "
                f"will flag these offers with mapping_doubt and treat them as unavailable."
                if former_tmdb_id and former_tmdb_id != self.proposed_tmdb_id
                else "No TMDb target mismatch between mapping and offers."
            ),
        }

        # 10. Deterministic Precondition Fingerprints
        precondition_state = {
            "database": db_id,
            "inputs": {
                "provider": provider.slug,
                "provider_content_id": self.provider_content_id,
                "proposed_tmdb_id": self.proposed_tmdb_id,
                "proposed_content_type": self.proposed_content_type,
            },
            "mapping": _serialize_row_for_fingerprint(existing_mapping) if existing_mapping else None,
            "provider_contents": [
                _serialize_row_for_fingerprint(pc) for pc in provider_contents
            ],
            "offers": [_serialize_row_for_fingerprint(o) for o in associated_offers],
            "evidence": [_serialize_row_for_fingerprint(e) for e in associated_evidence],
            "prices": [_serialize_row_for_fingerprint(p) for p in associated_prices],
            "competing_mappings": [
                _serialize_row_for_fingerprint(cm) for cm in conflicting_mappings
            ],
        }
        precondition_fingerprint = "sha256:" + hashlib.sha256(
            canonical_json_bytes(precondition_state)
        ).hexdigest()

        # 11. Inspectable JSON Impact Manifest
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generated_at": now.isoformat(),
            "database": db_id,
            "inputs": {
                "provider": provider.slug,
                "provider_content_id": self.provider_content_id,
                "proposed_tmdb_id": self.proposed_tmdb_id,
                "proposed_content_type": self.proposed_content_type,
            },
            "precondition_fingerprint": precondition_fingerprint,
            "report": {
                "existing_mapping": mapping_report,
                "provider_native_identity": provider_native_identity,
                "former_tmdb_target": former_target_info,
                "proposed_tmdb_target": proposed_target_info,
                "associated_offers": [
                    {
                        "id": o.id,
                        "identity": o.identity,
                        "country": o.country.code,
                        "offer_type": o.offer_type,
                        "currently_available": o.currently_available,
                        "source": o.source.slug,
                        "columns": serialize_columns(o),
                    }
                    for o in associated_offers
                ],
                "associated_evidence": [
                    {
                        "id": e.id,
                        "offer_identity": e.offer_identity,
                        "source": e.source.slug,
                        "is_present": e.is_present,
                        "authoritative": e.authoritative,
                        "columns": serialize_columns(e),
                    }
                    for e in associated_evidence
                ],
                "associated_prices": [
                    {
                        "id": p.id,
                        "offer_identity": p.offer_identity,
                        "source": p.source.slug,
                        "amount": str(p.amount) if p.amount is not None else None,
                        "currency": p.currency,
                        "is_current": p.is_current,
                        "columns": serialize_columns(p),
                    }
                    for p in associated_prices
                ],
                "corroborating_observations": corroboration,
                "manual_mapping_conflicts": mapping_conflicts,
                "potentially_invalid_availability": invalid_availability_analysis,
            },
        }

        # Add canonical digest over non-volatile parts
        manifest["digest"] = compute_digest(manifest)
        return manifest
