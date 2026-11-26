"""Recheck Stage E historical timing and exercise the frozen panel offline."""
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import audit_issue97_stage_e as historical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from availability.audit_identity import load_identity_manifest
from availability.services.issue97_stage_e_adjudication import (
    Binding, CaptureReceipt, Observation, ReviewedPair, Scope, Window, adjudicate, summarize,
)


def digest(value):
    return sha256(value.encode()).hexdigest()


def historical_ranges():
    path = ROOT / "docs/audits/issue97/jw-canary-2026-11-24-evidence/sanitized/attempt_accounting.json"
    raw = path.read_bytes()
    assert sha256(raw).hexdigest() == "e93b90a4c059ca31dc7746f4da4939d50eca36a3d68cb4bf1fb915d13e2168d3"
    items = json.loads(raw)["namespaces"]["notte_collection_invocations"]["items"]
    return {r["title"]: tuple(datetime.fromisoformat(t.replace("Z", "+00:00"))
                              for t in r["observed_at_utc_range"])
            for r in items if r["status"] == "closed"}


def frozen_fixture_scope(row, identity):
    # Scope and document bindings come from the frozen matrix plus the independent
    # typed-title and accepted-document inventories. There is no fresh manifest.
    # Every package, edition, action, payload and slot below is a test fixture.
    scope = Scope(row["media_type"], int(row["tmdb_id"]), identity.canonical_title, identity.year,
                  "fixture-edition", row["market"], row["provider_slug"], row["offer_class"],
                  "prime" if row["provider_slug"] == "max_amazon" else None)
    assert (scope.canonical_title, scope.year) == (row["title"], int(row["year"]))
    return scope


def synthetic_smoke(row, anchor, scope):
    owner = scope.parent or scope.provider
    jw = Binding("justwatch", digest("fixture-index-operation"), f"fixture-{scope.tmdb_id}",
                 scope.canonical_title, "fixture-package-" + scope.provider,
                 digest("fixture-index-" + row["candidate_id"]))
    native = Binding(owner, digest(anchor["public_url"]), anchor["provider_title_id"], anchor["page_title"],
                     "fixture-benefit-" + scope.provider, digest("fixture-native-" + row["candidate_id"]))
    slot = datetime(2030, 1, 1, tzinfo=timezone.utc)
    start = slot + timedelta(minutes=1)
    time = Window(start, start, timedelta(0), start)
    pair = ReviewedPair(row["candidate_id"], scope, jw, native, slot,
                        CaptureReceipt(digest("fixture-index-bytes"), time),
                        CaptureReceipt(digest("fixture-native-bytes"), time))
    actions = {"subscription": "subscription_action", "addon": "channel_benefit", "rent": "rental_action",
               "buy": "purchase_action", "free": "full_feature_free_without_ads",
               "free_with_ads": "full_feature_free_with_ads"}
    idx = Observation(scope, jw, time, 200, "title", "request_scoped", "fresh", actions[scope.offer_class],
                      "justwatch", digest("fixture-index-bytes"))
    fp = Observation(scope, native, time, 200, "title", "explicit", "fresh", actions[scope.offer_class],
                     owner, digest("fixture-native-bytes"))
    fresh = adjudicate(pair, idx, fp)
    historical_stamp = datetime.fromisoformat(anchor["observed_utc"])
    # Even fully favorable fixture claims fail when an old event is replayed.
    original = replace(time, original=historical_stamp)
    stale = adjudicate(replace(pair, native_receipt=replace(pair.native_receipt, window=original)),
                       idx, replace(fp, window=original))
    return fresh, stale


def main():
    historical.main()
    identity_path = ROOT / "docs/audits/stage2b/identity_manifest_20261122.csv"
    assert sha256(identity_path.read_bytes()).hexdigest() == (
        "ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925"
    )
    identities = load_identity_manifest(identity_path)
    rows = historical.rows((ROOT / historical.CANDIDATE).read_bytes())
    accepted = historical.unique_index(historical.rows((ROOT / historical.ACCEPTED).read_bytes()), "id")
    ranges = historical_ranges()
    counts = Counter()
    manifest = [(row["candidate_id"], frozen_fixture_scope(
        row, identities[int(row["tmdb_id"]), row["media_type"]])) for row in rows]
    fresh, stale = [], []
    for row, (_, scope) in zip(rows, manifest):
        anchor = accepted[historical.SCOPE_ANCHORS[row["capture_scope"]]]
        if row["stage_c_pair_id"]:
            observed = datetime.fromisoformat(accepted[row["prior_observation_id"]]["observed_utc"])
            earliest, latest = ranges[row["title"]]
            # Even the nearest possible per-offer event exceeds the protocol span.
            assert observed + timedelta(minutes=10) < earliest <= latest
            counts["outside_window"] += 1
        elif row["prior_observation_id"]:
            counts["no_historical_index_pair"] += 1
        else:
            counts["unassigned_control"] += 1
        current, historical_time = synthetic_smoke(row, anchor, scope)
        fresh.append(current)
        stale.append(historical_time)
    assert counts == {"outside_window": 7, "no_historical_index_pair": 6, "unassigned_control": 2}
    complete = summarize(fresh, expected_manifest=manifest)
    assert complete["outcomes"] == {"TP": 15}
    assert complete["full_panel_precision"] == complete["full_panel_recall"] == 1
    summary = summarize(stale, expected_manifest=manifest)
    assert summary["panel_completeness"] == "complete" and summary["planned"] == summary["matched"] == 15
    assert summary["outcomes"] == {"stale": 15}
    assert summary["agreement"].value == summary["full_panel_recall"] == "UNDEFINED"
    subset = summarize(fresh[:1], expected_manifest=manifest)
    assert subset["panel_completeness"] == "incomplete" and len(subset["missing"]) == 14
    assert subset["conditional_precision"].value == subset["conditional_recall"].value == 1
    assert subset["full_panel_precision"] == subset["full_panel_recall"] == "UNDEFINED"
    print("Historical panel: 7 outside the window, 6 without paired index, 2 unassigned controls.")
    print("2030 synthetic smoke: all 15 scopes exercised; all 15 original-time substitutions excluded.")
    print("Historical baseline: 56 source-reported offers, 0 eligible comparisons, 0 confirmed negatives.")
    print("Accuracy and price agreement: UNDEFINED. Entitlement and playback: untested.")


if __name__ == "__main__":
    main()
