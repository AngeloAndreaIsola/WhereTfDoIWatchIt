"""Import collector values as catalogue identities, never availability offers.

This boundary performs no HTTP and never calls AvailabilityRefreshService.
Runtime permission remains fail closed. Tests supply fixture permission only by
mocking the same reviewed registry used by discovery; there is no approval flag.
"""
from datetime import date
import hashlib
import json
import unicodedata

from django.db import transaction
from django.utils import timezone

from availability.domain.models import DiscoveredContent
from availability.sources.base import SourceFetchResult
from tracker.models import CatalogueDiscoveryState, DiscoveredCatalogueRecord, Provider
from tracker.services.bulk_refresh import release_source_lock, try_source_lock
from tracker.services.catalogue_identity import normalize_catalogue_url
from tracker.services.discovery_policy import DiscoveryPolicyError, permission_for

# Reviewed parser bindings, not automation grants. Adding a binding does not
# enable the legacy command, CLI choices, scheduler or source permission.
COLLECTORS = {"tubi": {"source": "tubi", "country": "US"}}
SOURCE_KIND = "catalogue_collector_v1"
CHECKPOINT_KEY = "catalogue_only_v1"


def require_collector_permission(provider_slug):
    if provider_slug not in COLLECTORS:
        raise ValueError("No reviewed catalogue collector binding")
    provider = Provider.objects.get(slug=provider_slug)
    if not provider.is_enabled or provider.config.get("discovery_permission") == "DENIED":
        raise DiscoveryPolicyError("source_permission_denied")
    permission = permission_for(provider)
    permission.require(provider_slug)
    return provider, permission


def collector_type_evidence(record):
    """Only the reviewed collector's explicit movie/series type is usable.

    Year/external-ID verification still belongs to the existing exact verifier.
    Episode/season types are never promoted to title identities.
    """
    binding = COLLECTORS.get(record.provider.slug)
    metadata = record.original_metadata
    if (binding and isinstance(metadata, dict)
            and metadata.get("source_kind") == SOURCE_KIND
            and metadata.get("collector_source") == binding["source"]
            and metadata.get("collector_country") == binding["country"]
            and metadata.get("source_media_type") in {"movie", "series"}):
        return metadata["source_media_type"], 0.99, [
            f"reviewed {binding['source']} collector explicit title type"]
    return None


def _normalized(item, provider, permission, binding):
    if not isinstance(item, DiscoveredContent):
        raise ValueError("Expected DiscoveredContent")
    if item.provider_id != provider.slug or item.source_id != binding["source"]:
        raise ValueError("Collector provider/source identity mismatch")
    if item.content_type not in {"movie", "series"} or any(
            v is not None for v in (item.season_number, item.episode_number)):
        raise ValueError("Only title-level movie/series records are accepted")
    if not isinstance(item.provider_content_id, str):
        raise ValueError("Invalid native identity")
    native = item.provider_content_id.strip()
    title = unicodedata.normalize("NFC", item.title).strip()
    # The downstream canonical mapping's native-ID column is 100 characters.
    if not native or len(native) > 100 or not title or len(title) > 255:
        raise ValueError("Invalid catalogue title/native identity")
    permission.validate_url(item.url)
    if len(item.url) > 500:
        raise ValueError("Catalogue URL exceeds model limit")
    if item.release_date is not None and (type(item.release_date) is not date
            or not 1870 <= item.release_date.year <= 2100):
        raise ValueError("Invalid source release date")
    metadata = {
        "source_kind": SOURCE_KIND, "collector_source": binding["source"],
        "collector_country": binding["country"], "source_media_type": item.content_type,
        "year": item.release_date.year if item.release_date else None,
        "release_date": item.release_date.isoformat() if item.release_date else None,
        "original_title": item.original_title, "description": item.description,
        # No collector-supplied ID is promoted to trusted TMDb evidence.
        "external_ids": item.external_ids, "external_ids_trusted": False,
        "identity_evidence": item.identity_evidence,
        "rights_provenance": item.rights_provenance,
        "permission_evidence": permission.evidence,
        "unverified_discovery": True,
    }
    # Validate serialization now, so malformed later records cannot commit a
    # prefix or a checkpoint. Copy mutable collector dictionaries at the seam.
    metadata = json.loads(json.dumps(metadata, allow_nan=False))
    return dict(provider_native_id=native, title=title, source_url=item.url,
                identity_url=normalize_catalogue_url(provider.slug, item.url),
                original_metadata=metadata, provenance_state="PROVIDER_REPORTED",
                access_policy_state="PERMITTED")


def import_catalogue_batch(provider_slug, source_slug, result, *, expected_cursor,
                          observed_at=None, apply=False):
    """Atomic batch import and compare-and-set checkpoint, default read only.

    ``result.offers``/withdrawals are deliberately never read. A repeated last
    batch may replay from its original cursor; a different stale batch fails.
    Every other cursor namespace is preserved. HTTP must finish before entry.
    """
    provider, permission = require_collector_permission(provider_slug)
    binding = COLLECTORS[provider_slug]
    if source_slug != binding["source"] or not isinstance(result, SourceFetchResult):
        raise ValueError("Collector binding/result mismatch")
    if result.country not in (None, binding["country"]):
        raise ValueError("Collector market mismatch")
    if not isinstance(result.contents, list) or len(result.contents) > 250:
        raise ValueError("Catalogue batch must contain at most 250 records")
    for value in (expected_cursor, result.cursor):
        if value is not None and (not isinstance(value, str) or len(value) > 65536):
            raise ValueError("Invalid collector checkpoint")
    observed_at = observed_at or timezone.now()
    if timezone.is_naive(observed_at) or observed_at > timezone.now():
        raise ValueError("Observation timestamp must be aware and not in the future")
    values = [_normalized(item, provider, permission, binding) for item in result.contents]
    unique = {}
    urls = {}
    for value in values:
        native = value["provider_native_id"]
        if native in unique and unique[native] != value:
            raise ValueError("Contradictory duplicate source identity")
        unique[native] = value
        if value["identity_url"] in urls and urls[value["identity_url"]] != native:
            raise ValueError("Catalogue URL has contradictory native identities")
        urls[value["identity_url"]] = native
    values = list(unique.values())
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    checkpoint = dict(source=source_slug, country=binding["country"], cursor=result.cursor,
                      input_cursor=expected_cursor, batch_digest=digest)
    lock = f"unofficial-catalogue:{provider_slug}"
    if not try_source_lock(lock):
        raise RuntimeError("Catalogue discovery is already active")
    try:
        with transaction.atomic():
            # The provider lock also serializes first-state creation.
            locked_provider = Provider.objects.select_for_update().get(pk=provider.pk)
            if not locked_provider.is_enabled or locked_provider.config.get("discovery_permission") == "DENIED":
                raise DiscoveryPolicyError("source_permission_denied")
            permission.require(provider_slug)
            state = CatalogueDiscoveryState.objects.select_for_update().filter(provider=provider).first()
            if state and state.lease_owner and state.lease_expires_at and state.lease_expires_at > timezone.now():
                raise RuntimeError("Catalogue discovery has an active lease")
            root = dict(state.cursor or {}) if state else {}
            prior = root.get(CHECKPOINT_KEY, {})
            if prior and (prior.get("source"), prior.get("country")) != (source_slug, binding["country"]):
                raise ValueError("Checkpoint belongs to another collector scope")
            if prior.get("cursor") != expected_cursor and prior != checkpoint:
                raise ValueError("Stale collector checkpoint; refetch from committed cursor")
            report = dict(provider=provider_slug, source=source_slug, dry_run=not apply,
                          inserted=0, updated=0, unchanged=0, offers_written=0,
                          checkpoint=checkpoint, record_ids=[])
            for value in values:
                record = DiscoveredCatalogueRecord.objects.select_for_update().filter(
                    provider=provider, provider_native_id=value["provider_native_id"]).first()
                other = DiscoveredCatalogueRecord.objects.filter(
                    provider=provider, identity_url=value["identity_url"]).exclude(
                        provider_native_id=value["provider_native_id"])
                if other.exists():
                    raise ValueError("Catalogue URL already belongs to another native identity")
                if record and (record.original_metadata.get("source_kind") != SOURCE_KIND
                        or record.original_metadata.get("collector_source") != source_slug):
                    raise ValueError("Existing discovery belongs to another ingestion path")
                if record and record.access_policy_state in {"DENIED", "BLOCKED", "RESTRICTED"}:
                    raise DiscoveryPolicyError("source_record_access_prohibited")
                changed = record is not None and any(getattr(record, k) != v for k,v in value.items())
                report["inserted" if record is None else "updated" if changed else "unchanged"] += 1
                if apply:
                    seen = list(dict.fromkeys([*(record.source_urls_seen if record else []), value["source_url"]]))
                    if record is None:
                        record = DiscoveredCatalogueRecord.objects.create(provider=provider,
                            **value, source_urls_seen=seen, last_seen_at=observed_at)
                        DiscoveredCatalogueRecord.objects.filter(pk=record.pk).update(discovered_at=observed_at)
                    elif changed:
                        for key, val in value.items():
                            setattr(record, key, val)
                        record.source_urls_seen = seen
                        record.last_seen_at = max(record.last_seen_at, observed_at)
                        record.save()
                    elif observed_at > record.last_seen_at:
                        DiscoveredCatalogueRecord.objects.filter(pk=record.pk).update(last_seen_at=observed_at)
                if record:
                    report["record_ids"].append(record.pk)
            if apply:
                root[CHECKPOINT_KEY] = checkpoint
                if state is None:
                    state = CatalogueDiscoveryState(provider=provider)
                state.cursor = root
                # A bounded traversal result never asserts catalogue completeness.
                if not state.pk:
                    state.status = "PARTIAL"
                state.last_successful_checkpoint = max(
                    state.last_successful_checkpoint or observed_at, observed_at)
                state.save()
            return report
    finally:
        release_source_lock(lock)


def reconcile_catalogue_records(provider_slug, *, record_ids, apply=False):
    """Use the existing exact verifier/outcome writer, with networking disabled.

    This prototype deliberately has no network option and no CLI/scheduler.
    Bounded explicit identities prevent processing an unrelated inventory.
    """
    require_collector_permission(provider_slug)
    if not record_ids or len(record_ids) > 250 or len(set(record_ids)) != len(record_ids):
        raise ValueError("Require 1..250 unique discovery IDs")
    records = list(DiscoveredCatalogueRecord.objects.filter(provider__slug=provider_slug, pk__in=record_ids)
                   .select_related("provider"))
    if len(records) != len(record_ids) or any(not collector_type_evidence(r) for r in records):
        raise ValueError("IDs must belong to the reviewed collector's title observations")
    from tracker.services.incremental_catalogue_reconciliation import _run_reconciliation
    return _run_reconciliation(provider_slug, record_ids=record_ids, limit=len(record_ids),
                               request_budget=1, allow_network=False, retries=0, apply=apply)
