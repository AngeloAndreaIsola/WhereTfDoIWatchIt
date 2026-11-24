"""Offline cohort preflight and fixture replay for issue #97."""
from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime, timezone

from availability.audit_identity import load_identity_manifest, normalized_title, titles_match
from availability.domain.enums import ContentType, OfferType
from availability.services.experiment_runner import (
    APPROVED_MANIFEST,
    ExperimentBlocked,
    manifest_digest,
    validate_search_payload,
)
from availability.services.normalize import normalize_justwatch_offer_type
from availability.sources.justwatch import JustWatchSource
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
        for offer in node.offers:
            provider = provider_slug_for_package(offer.package)
            offer_class = normalize_justwatch_offer_type(offer.monetization_type)
            if offer_class == OfferType.SUBSCRIPTION and is_addon_package(offer.package):
                offer_class = OfferType.ADDON
            observation = {
                "tmdb_id": scope["tmdb_id"], "media_type": scope["media_type"],
                "country": scope["country"], "season_number": season_number,
                "provider": provider, "offer_class": offer_class,
            }
            if not provider or offer_class == OfferType.UNKNOWN:
                report["unresolved"].append({
                    **observation,
                    "package": offer.package.technical_name,
                    "monetization_type": offer.monetization_type,
                })
            else:
                output.append(observation)

    try:
        page = client.search_titles(
            scope["title"], country=scope["country"],
            object_types=["MOVIE" if scope["media_type"] == "movie" else "SHOW"],
        )
        matches = [node for node in page.titles
                   if JustWatchSource._content_type(node) == scope["media_type"]
                   and node.tmdb_id_int == scope["tmdb_id"]]
        if (len(matches) != 1 or not matches[0].id
                or not titles_match(scope, normalized_title(scope["title"]), normalized_title(matches[0].title))
                or matches[0].year != scope["year"]):
            report["reason"] = "typed_title_missing_ambiguous_or_label_mismatch"
            return report

        match = matches[0]
        matched_node_id = match.id
        record_offers(match, report["title_offers"])
        report["status"] = "observed_fixture_offers" if report["title_offers"] else "unknown"
        if scope["media_type"] == "series" and seasons_response is not None:
            report["season_status"] = "unknown"
            seasons = client.seasons(match.id, country=scope["country"])
            season_counts = Counter(season.season_number for season in seasons)
            for season in seasons:
                if (JustWatchSource._content_type(season) != ContentType.SEASON
                        or type(season.season_number) is not int or season.season_number < 0
                        or season_counts[season.season_number] != 1):
                    report["unresolved"].append({"reason": "invalid_season_identity"})
                    continue
                record_offers(season, report["season_offers"], season.season_number)
            if report["season_offers"]:
                report["season_status"] = "observed_fixture_offers"
    except JustWatchError:
        report["reason"] = "fixture_collection_failed"
    return report
