"""Versioned, checksum-verified export/import for raw catalogue inventory."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.db import transaction

from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Provider


FORMAT = "wtdw-discovered-catalogue"
VERSION = 1
RECORD_FIELDS = (
    "provider_slug", "source_record_id", "provider_native_id", "title", "source_url",
    "identity_url", "source_urls_seen", "discovered_at", "original_metadata",
    "classification_state", "classified_content_type", "discovery_verification_state",
    "provenance_state", "access_policy_state",
)


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _date(value):
    return value.isoformat() if value else None


def export_catalogue_inventory(output_dir):
    """Write deterministic compressed JSONL plus an atomic integrity manifest."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / f"catalogue-inventory-v{VERSION}.jsonl.gz"
    manifest_path = output_dir / f"catalogue-inventory-v{VERSION}.manifest.json"
    records = DiscoveredCatalogueRecord.objects.select_related("provider").order_by("provider__slug", "id")
    counts = {}
    raw_bytes = bytearray()
    for record in records.iterator(chunk_size=500):
        row = {
            "provider_slug": record.provider.slug,
            "source_record_id": record.pk,
            "provider_native_id": record.provider_native_id,
            "title": record.title,
            "source_url": record.source_url,
            "identity_url": record.identity_url,
            "source_urls_seen": record.source_urls_seen or [],
            "discovered_at": _date(record.discovered_at),
            "original_metadata": record.original_metadata or {},
            "classification_state": record.classification_state,
            "classified_content_type": record.classified_content_type,
            "discovery_verification_state": record.discovery_verification_state,
            "provenance_state": record.provenance_state,
            "access_policy_state": record.access_policy_state,
        }
        raw_bytes.extend((_canonical_json(row) + "\n").encode("utf-8"))
        counts[record.provider.slug] = counts.get(record.provider.slug, 0) + 1

    compressed = gzip.compress(bytes(raw_bytes), mtime=0)
    _atomic_write(data_path, compressed)
    checkpoints = {}
    for state in CatalogueDiscoveryState.objects.select_related("provider").order_by("provider__slug"):
        checkpoints[state.provider.slug] = {
            "status": state.status, "mode": state.mode, "cursor": state.cursor or {},
            "report": state.report or {}, "started_at": _date(state.started_at),
            "last_success_at": _date(state.last_success_at),
            "last_successful_checkpoint": _date(state.last_successful_checkpoint),
            "last_error": state.last_error,
        }
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "record_count": sum(counts.values()),
        "records_by_provider": counts,
        "data_file": data_path.name,
        "data_sha256": hashlib.sha256(compressed).hexdigest(),
        "data_bytes": len(compressed),
        "checkpoints": checkpoints,
    }
    _atomic_write(manifest_path, (_canonical_json(manifest) + "\n").encode("utf-8"))
    return {"data_path": str(data_path), "manifest_path": str(manifest_path), **manifest}


def import_catalogue_inventory(data_path, manifest_path=None):
    """Validate first, then import idempotently without canonical projection."""
    data_path = Path(data_path)
    manifest_path = Path(manifest_path) if manifest_path else data_path.with_name(
        f"catalogue-inventory-v{VERSION}.manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("Unsupported catalogue staging format or version")
    payload = data_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != manifest.get("data_sha256"):
        raise ValueError("Catalogue staging checksum mismatch")
    rows = []
    for line in gzip.decompress(payload).decode("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if len(rows) != manifest.get("record_count"):
        raise ValueError("Catalogue staging record count mismatch")
    counts = {}
    for row in rows:
        counts[row["provider_slug"]] = counts.get(row["provider_slug"], 0) + 1
    if counts != manifest.get("records_by_provider"):
        raise ValueError("Catalogue staging provider counts mismatch")
    providers = {p.slug: p for p in Provider.objects.filter(slug__in=counts)}
    missing = set(counts) - set(providers)
    if missing:
        raise ValueError(f"Providers missing from target database: {sorted(missing)}")

    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "checkpoints_restored": 0}
    with transaction.atomic():
        for row in rows:
            provider = providers[row["provider_slug"]]
            native_id = row.get("provider_native_id") or ""
            prior = DiscoveredCatalogueRecord.objects.filter(
                provider=provider, provider_native_id=native_id
            ).first() if native_id else None
            if prior is None and row.get("identity_url"):
                prior = DiscoveredCatalogueRecord.objects.filter(
                    provider=provider, identity_url=row["identity_url"]
                ).first()
            if prior is None and native_id and row.get("identity_url"):
                prior = DiscoveredCatalogueRecord.objects.filter(
                    provider=provider, provider_native_id="", identity_url=row["identity_url"]
                ).first()
            values = {
                "provider_native_id": native_id,
                "title": row["title"],
                "source_url": row["source_url"],
                "identity_url": row.get("identity_url", ""),
                "source_urls_seen": row.get("source_urls_seen") or [row["source_url"]],
                "original_metadata": row.get("original_metadata") or {},
                "classification_state": row.get("classification_state", "PENDING"),
                "classified_content_type": row.get("classified_content_type"),
                "discovery_verification_state": row.get("discovery_verification_state", "OBSERVED"),
                "provenance_state": row.get("provenance_state", "PROVENANCE_UNVERIFIED"),
                "access_policy_state": row.get("access_policy_state", "UNKNOWN"),
            }
            if prior is None:
                prior = DiscoveredCatalogueRecord.objects.create(provider=provider, **values)
                DiscoveredCatalogueRecord.objects.filter(pk=prior.pk).update(discovered_at=row["discovered_at"])
                stats["inserted"] += 1
                continue
            changed = any(getattr(prior, field) != value for field, value in values.items())
            # Do not roll a target's later classification backwards to PENDING.
            if prior.classification_state == "CLASSIFIED":
                values["classification_state"] = prior.classification_state
                values["classified_content_type"] = prior.classified_content_type
                changed = any(getattr(prior, field) != value for field, value in values.items())
            if not changed:
                stats["unchanged"] += 1
                continue
            for field, value in values.items():
                setattr(prior, field, value)
            prior.save(update_fields=[*values, "updated_at"])
            stats["updated"] += 1

        for slug, checkpoint in manifest.get("checkpoints", {}).items():
            provider = providers.get(slug) or Provider.objects.filter(slug=slug).first()
            if not provider:
                continue
            state, _ = CatalogueDiscoveryState.objects.get_or_create(provider=provider)
            for field in ("status", "mode", "cursor", "report", "started_at", "last_success_at",
                          "last_successful_checkpoint", "last_error"):
                setattr(state, field, checkpoint.get(field))
            state.save()
            stats["checkpoints_restored"] += 1
    return {**stats, "record_count": len(rows), "records_by_provider": counts, "data_sha256": digest}


def _atomic_write(path, data):
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
