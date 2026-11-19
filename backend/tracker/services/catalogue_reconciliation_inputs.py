"""Versioned matching inputs; observation timestamps and pagination are excluded.

Keep v1 stable: migration 0058 uses it to acknowledge the completed inventory
without running classification, matching, TMDb requests or association writes.
"""
import hashlib
import json


MEANINGFUL_KEYS_V1 = (
    "year", "release_year", "original_title", "feed_title", "anime_type",
    "source_kind", "catalogue_partition", "source_media_type",
    "listing_content_type_hint", "external_ids", "external_ids_trusted",
    "tmdb_id", "overview", "synopsis", "description",
)


def fingerprint_v1(record):
    metadata = record.original_metadata if isinstance(record.original_metadata, dict) else {}
    inputs = {field: getattr(record, field) for field in (
        "provider_id", "provider_native_id", "title", "source_url", "identity_url",
        "provenance_state", "access_policy_state",
    )}
    inputs["metadata"] = {key: metadata[key] for key in MEANINGFUL_KEYS_V1 if key in metadata}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
