"""Verify Stage E historical pointers against the frozen, sanitized Git record."""

import csv
import io
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
STAGE_C = "7f35a5556d2c03364a6250ca6de4f626e720abce"
RECONCILIATION = "5389a6ca3fea89aab3fdac3655b3b2af2792807e"
CANDIDATE = "docs/audits/issue97/stage-e-candidate-matrix.csv"
ACCEPTED = "docs/audits/stage2b/issue97-accepted-observations.csv"
MATRIX = "docs/audits/stage2b/issue97-reconciliation-matrix-template.csv"
CANDIDATE_SHA256 = "ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d"

# References to reviewed historical observations. Never use this inventory for dispatch.
SCOPE_ANCHORS = {
    "E-C1": "inception_nl-netflix-subscription",
    "E-C2": "OBS-01-prime-subscription",
    "E-C3": "OBS-06-rakuten_tv-rent",
    "E-C4": "OBS-02-netflix-subscription",
    "E-C5": "OBS-04-netflix-subscription",
    "E-C6": "OBS-03-netflix-subscription",
    "E-C7": "OBS-05-netflix-subscription",
    "E-C8": "OBS-07-rakuten_tv-rent",
}
DOCUMENT_HOST = {
    "netflix": "www.netflix.com",
    "prime": "www.primevideo.com",
    "max_amazon": "www.primevideo.com",
    "rakuten_tv": "www.rakuten.tv",
}


def git_blob(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)


def rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))


def unique_index(records, field):
    index = {row[field]: row for row in records}
    assert "" not in index and len(index) == len(records), f"Invalid {field} keys"
    return index


def pair_key(row, *, year="year", market="country", provider="provider_slug"):
    return tuple(row[key] for key in ("media_type", "tmdb_id", year, market, provider, "offer_class"))


def verify_document_scopes(candidates, observations):
    """Resolve each row, including controls, to one historical native document offline."""
    assert {row["capture_scope"] for row in candidates} == SCOPE_ANCHORS.keys()
    for candidate in candidates:
        scope = candidate["capture_scope"]
        anchor = observations[SCOPE_ANCHORS[scope]]
        target = urlsplit(anchor["public_url"])
        assert target.scheme == "https" and target.netloc == DOCUMENT_HOST[candidate["provider_slug"]], scope
        assert target.path.strip("/") and not target.query and not target.fragment, scope
        assert anchor["provider_title_id"] and anchor["market_basis"], scope
        for field, historical_field in (("media_type", "media_type"), ("tmdb_id", "tmdb_id"),
                                        ("year", "canonical_year"), ("market", "country")):
            assert candidate[field] == anchor[historical_field], f"{scope} historical {field} differs"
        if candidate["prior_observation_id"]:
            observed = observations[candidate["prior_observation_id"]]
            for field in ("public_url", "provider_title_id", "raw_sha256", "observed_utc"):
                assert observed[field] == anchor[field], f"{scope} historical document differs in {field}"
    assert len({observations[ref]["public_url"] for ref in SCOPE_ANCHORS.values()}) == len(SCOPE_ANCHORS)


def main():
    candidate_bytes = (ROOT / CANDIDATE).read_bytes()
    assert sha256(candidate_bytes).hexdigest() == CANDIDATE_SHA256, "Frozen candidate panel changed"
    accepted_bytes = git_blob(STAGE_C, ACCEPTED)
    original_matrix_bytes = git_blob(STAGE_C, MATRIX)
    matrix_bytes = git_blob(RECONCILIATION, MATRIX)
    assert (ROOT / ACCEPTED).read_bytes() == git_blob(RECONCILIATION, ACCEPTED) == accepted_bytes
    assert (ROOT / MATRIX).read_bytes() == matrix_bytes

    candidates = unique_index(rows(candidate_bytes), "candidate_id")
    observations = unique_index(rows(accepted_bytes), "id")
    verify_document_scopes(candidates.values(), observations)
    original_pairs = unique_index(rows(original_matrix_bytes), "pair_id")
    pairs = unique_index(rows(matrix_bytes), "pair_id")
    assert len(candidates) == 15 and len(observations) == 13 and len(pairs) == 7
    assert pairs.keys() == original_pairs.keys(), "Stage C pair set changed"
    assert Counter(row["batch"] for row in candidates.values()) == {"A": 9, "B": 6}
    assert len({pair_key(row, market="market") for row in candidates.values()}) == 15
    assert Counter(batch for batch, _ in {
        (row["batch"], row["capture_scope"]) for row in candidates.values()
    }) == {"A": 3, "B": 5}
    assert Counter(batch for batch, _, _ in {
        (row["batch"], row["media_type"], row["tmdb_id"]) for row in candidates.values()
    }) == {"A": 2, "B": 5}
    assert {row["market"] for row in candidates.values()} == {"NL"}
    assert {row["fresh_status"] for row in candidates.values()} == {"pending_authorization"}

    observation_refs = []
    pair_refs = []
    controls = []
    start = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)
    end = start.replace(hour=16)
    for candidate in candidates.values():
        observation_id = candidate["prior_observation_id"]
        pair_id = candidate["stage_c_pair_id"]
        if not observation_id:
            assert not pair_id and candidate["batch"] == "A"
            assert candidate["evidence_rule"] == "free_class_control"
            controls.append(candidate["offer_class"])
            continue

        assert observation_id in observations, "Unresolved accepted observation pointer"
        observation = observations[observation_id]
        observation_refs.append(observation_id)
        key = pair_key(candidate, market="market")
        assert key == pair_key(observation, year="canonical_year", provider="canonical_provider")
        assert observation["decision"] == "ADVERTISED_ONLY" and observation["advertised"] == "positive"
        assert observation["entitlement"] == observation["playback"] == "untested"
        assert re.fullmatch(r"[0-9a-f]{64}", observation["raw_sha256"])
        observed = datetime.fromisoformat(observation["observed_utc"])
        assert start <= observed < end, "Observation escaped the frozen reference hour"

        if not pair_id:
            assert candidate["batch"] == "B"
            continue

        assert candidate["batch"] == "A" and pair_id in pairs, "Unresolved Stage C pair pointer"
        pair = pairs[pair_id]
        original = original_pairs[pair_id]
        pair_refs.append(pair_id)
        shared_fields = pair.keys() & original.keys()
        assert all(pair[field] == original[field] for field in shared_fields), "Stage C fields changed"
        assert key == pair_key(pair)
        assert pair["first_party_observation_id"] == observation_id
        assert pair["first_party_observed_utc"] == observation["observed_utc"]
        assert pair["first_party_price_eur"] == original["first_party_price"] == observation["price_eur"]
        assert original["first_party_offered"] == "yes"
        assert pair["historical_pair_match"] == "yes" and pair["disposition"] == "outside_reviewed_window"
        assert pair["temporal_eligible"] == pair["independently_adjudicated"] == "no"

    assert Counter(observation_refs) == Counter(observations.keys())
    assert Counter(pair_refs) == Counter(pairs.keys())
    assert sorted(controls) == ["free", "free_with_ads"]
    assert len({row["first_party_capture_id"] for row in pairs.values()}) == 3
    assert len({row["raw_sha256"] for row in observations.values()}) == 8

    print("CSV PASS: 15 candidates (A=9, B=6), 13 accepted observations, 7 Stage C pairs, 2 controls")
    print("Provenance PASS: 8 historical capture digests, 3 Stage C capture aliases, 7 ineligible / unadjudicated")
    print("Scope PASS: all 15 candidates resolve to 8 distinct historical native documents (A=3, B=5)")
    print("Fresh adjudicated pairs: 0. Accuracy metrics: UNDEFINED.")
    for alias, data in (
        ("Stage E candidates", candidate_bytes), ("Stage C accepted observations", accepted_bytes),
        ("Stage C original matrix", original_matrix_bytes), ("Stage C sanitized matrix", matrix_bytes),
    ):
        print(f"{alias} SHA-256: {sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
