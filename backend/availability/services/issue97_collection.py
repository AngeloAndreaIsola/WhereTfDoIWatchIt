"""Offline cohort preflight and fixture replay for issue #97."""
from __future__ import annotations

import csv
from datetime import datetime, timezone

from availability.audit_identity import load_identity_manifest, normalized_title, titles_match
from availability.domain.enums import OfferType
from availability.domain.models import offer_variant_key
from availability.services.experiment_runner import (
    APPROVED_MANIFEST,
    ExperimentBlocked,
    manifest_digest,
    validate_search_payload,
)
from availability.services.normalize import normalize_justwatch_offer_type, normalize_price
from availability.sources.justwatch import JustWatchSource, justwatch_offer_variant
from availability.sources.justwatch_client import GRAPHQL_URL, SEASONS_QUERY, JustWatchClient, JustWatchError
from availability.sources.justwatch_providers import is_addon_package, provider_slug_for_package

# These two cohort labels differ from the reviewed TMDb identity manifest.
# The typed identity and canonical year remain the manifest's.
PILOT_LABELS = {
    ("movie", 220289): (("Coherence", 2013), ("Coherence", 2014)),
    ("series", 2316): (("The Office US", 2005), ("The Office", 2005)),
}


def load_cohort(cohort_path, identity_path):
    def typed_rows(path):
        with open(path, encoding="utf-8", newline="") as stream:
            rows = {}
            for row in csv.DictReader(stream):
                key = (int(row["tmdb_id"]), row["media_type"])
                if key in rows:
                    raise ValueError(f"Duplicate typed identity: {key}")
                rows[key] = row
            return rows

    cohort = typed_rows(cohort_path)
    identities = load_identity_manifest(identity_path)
    if (cohort.keys() != identities.keys()
            or sorted(key[1] for key in cohort) != ["movie"] * 10 + ["series"] * 5):
        raise ValueError("Expected the same ten movie and five series identities in both inputs")

    scopes = []
    for key, row in cohort.items():
        tmdb_id, media_type = key
        identity = identities[key]
        pilot = (row["title"], int(row["year"]))
        canonical = (identity.canonical_title, identity.year)
        if (row["country"] != "NL"
                or (pilot != canonical and PILOT_LABELS.get((media_type, tmdb_id)) != (pilot, canonical))):
            raise ValueError(f"Unreviewed scope or identity labels: {key}")
        scopes.append({
            "scope_id": f"{media_type}-{tmdb_id}-nl",
            "tmdb_id": tmdb_id, "media_type": media_type, "country": "NL",
            "title": canonical[0], "year": canonical[1], "pilot_title": pilot[0],
            "pilot_year": pilot[1], "identity_evidence_sha256": identity.evidence_sha256,
            "endpoint": GRAPHQL_URL,
            "proposed_search_operation": "GetSearchTitles",
            "pending_season_operation": "GetTitleNode" if media_type == "series" else None,
            "live_attempts_allocated": 0,
        })
    return scopes


def collection_preflight(scopes):
    return {
        "verdict": "LIVE_COLLECTION_BLOCKED",
        "legacy_manifest_digest": manifest_digest(APPROVED_MANIFEST),
        "cohort": scopes,
        "blockers": [
            "Current reviewed written permission reference and validity window are missing",
            "Exact expanded title/market/operation coverage and total request ceiling are unknown",
            "Quota classification, remaining balance if metered, and current rate policy are unknown",
            "Approved pacing, concurrency, burst and retry allowance are unknown",
            "Disposable live target identity and separate execution approval are pending",
        ],
        "live_attempts": 0,
    }


def replay_scope(scope, search_response, *, seasons_response=None):
    """Parse synthetic GraphQL fixtures using a transport with no socket path."""
    mock_attempts = []
    matched_node_id = None

    def fixture_post(url, *, json, headers, timeout):
        if url != GRAPHQL_URL:
            raise ExperimentBlocked("Unexpected fixture endpoint")
        operation = json.get("operationName")
        if operation == "GetSearchTitles" and not mock_attempts:
            validate_search_payload(scope, json)
            response = search_response
        elif (operation == "GetTitleNode" and mock_attempts == ["GetSearchTitles"]
                and seasons_response is not None and scope["media_type"] == "series"
                and matched_node_id and json.get("query") == SEASONS_QUERY
                and json["variables"]["nodeId"] == matched_node_id
                and json["variables"]["country"] == scope["country"]):
            response = seasons_response
        else:
            raise ExperimentBlocked("Unexpected or repeated fixture operation")
        mock_attempts.append(operation)

        class FixtureResponse:
            status_code = 200
            headers = {"Content-Type": "application/json"}

            def json(self):
                return response

        return FixtureResponse()

    client = JustWatchClient(http_post=fixture_post, retries=1)
    report = {
        "scope_id": scope["scope_id"], "source": "justwatch",
        "provenance": "SYNTHETIC_FIXTURE", "mock_attempts": mock_attempts,
        "replayed_at_utc": datetime.now(timezone.utc).isoformat(),
        "authoritative": False, "title_offers": [], "season_offers": [], "unresolved": [],
        "season_status": "not_requested", "status": "unknown",
    }
    def record_offers(node, output, season_number=None):
        presence = {}
        for offer in node.offers:
            provider = provider_slug_for_package(offer.package)
            offer_class = normalize_justwatch_offer_type(offer.monetization_type)
            if offer_class == OfferType.SUBSCRIPTION and is_addon_package(offer.package):
                offer_class = OfferType.ADDON
            observation = {
                "tmdb_id": scope["tmdb_id"], "media_type": scope["media_type"],
                "country": scope["country"], "season_number": season_number,
                "provider": provider, "offer_class": offer_class, "jw_id": node.id,
            }
            price = normalize_price(offer.retail_price_value, offer.currency, offer.retail_price)
            variant = justwatch_offer_variant(offer, price)
            if not provider or offer_class == OfferType.UNKNOWN:
                report["unresolved"].append({
                    **observation,
                    "package": offer.package.technical_name,
                    "monetization_type": offer.monetization_type,
                    "variants": [variant],
                })
                continue
            # Compare service/class presence once. Retain the distinct source
            # variants under that fact for offer-level inspection.
            key = (scope["media_type"], scope["tmdb_id"], scope["country"],
                   season_number, provider, offer_class)
            fact, variants = presence.setdefault(key, (observation, {}))
            variants[offer_variant_key(variant)] = variant
        for fact, variants in presence.values():
            output.append({**fact, "variants": [variants[key] for key in sorted(variants)]})

    try:
        page = client.search_titles(
            scope["title"], country=scope["country"],
            object_types=["MOVIE" if scope["media_type"] == "movie" else "SHOW"],
        )
        typed_nodes = [node for node in page.titles if JustWatchSource._content_type(node) == scope["media_type"]]
        exact = [node for node in typed_nodes if node.tmdb_id_int == scope["tmdb_id"] and node.has_native_id]
        native_ids = {node.id for node in exact}
        match = JustWatchSource._merge_title_matches(
            [node for node in typed_nodes if node.has_native_id and node.id in native_ids], page.titles,
        )
        reviewed_title = normalized_title(scope["title"])
        # A typed native-ID anchor authorizes coalescing only if *every*
        # edge for that identity agrees where it states title/year. Blank
        # metadata is common in replay fixtures and remains admissible.
        coalesced = [node for node in typed_nodes if node.has_native_id and node.id in native_ids]
        labels = [(normalized_title(node.title), node.year) for node in coalesced]
        has_reviewed_anchor = any(
            node.tmdb_id_int == scope["tmdb_id"] and label and year == scope["year"]
            for node, (label, year) in zip(coalesced, labels)
        )
        conflicting_label = any(
            (label and not titles_match(scope, reviewed_title, label))
            or (year is not None and year != scope["year"])
            for label, year in labels
        )
        if match is None or not has_reviewed_anchor or conflicting_label:
            report["reason"] = "typed_title_missing_ambiguous_or_label_mismatch"
            return report

        matched_node_id = match.id
        record_offers(match, report["title_offers"])
        report["status"] = "observed_fixture_offers" if report["title_offers"] else "unknown"
        if scope["media_type"] == "series" and seasons_response is not None:
            report["season_status"] = "unknown"
            seasons = client.seasons(match.id, country=scope["country"])
            for season in JustWatchSource._season_matches(seasons):
                if season is None:
                    report["unresolved"].append({"reason": "invalid_season_identity"})
                    continue
                record_offers(season, report["season_offers"], season.season_number)
            if report["season_offers"]:
                report["season_status"] = "observed_fixture_offers"
    except JustWatchError:
        report["reason"] = "fixture_collection_failed"
    return report
