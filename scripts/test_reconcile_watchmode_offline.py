"""Standard-library regression tests for the #97 offline evidence comparator."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from reconcile_watchmode_offline import (
    classify_offer,
    identity_observation_ok,
    load_cohort,
    load_pilot_results,
    provider_index,
    read_csv,
    reconcile,
    resolve_provider,
    safe_url,
)


CASE = {"tmdb_id": 238, "title": "The Godfather", "media_type": "movie", "year": 1972, "country": "NL"}
KEY = (238, "movie", "NL")


def observation(*sources):
    return {
        "tmdb_id": 238, "media_type": "movie", "exact_tmdb_match": True,
        "watchmode_id": 100, "watchmode_name": "The Godfather",
        "watchmode_year": 1972, "watchmode_type": "movie", "nl_sources": list(sources),
    }


def source(provider, offer_type, source_id=1):
    return {"name": provider, "type": offer_type, "source_id": source_id,
            "region": "NL", "web_url": "https://example.org/title?q=remove-token"}


def app(provider_slug, provider_name, offer_type, source_slug, freshness,
        confidence="verified", offer_id="1"):
    return {
        "tmdb_id": "238", "media_type": "movie", "country": "NL", "offer_id": offer_id,
        "source_slug": source_slug, "provider_slug": provider_slug,
        "provider_name": provider_name, "offer_type": offer_type,
        "evidence_freshness": freshness, "availability_confidence": confidence,
        "currently_available": "True",
    }


def comparison(state="INSUFFICIENT_EXTERNAL_EVIDENCE", found=True):
    return {"tmdb_id": "238", "media_type": "movie", "country": "NL",
            "app_movie_found": str(found), "comparison_state": state}


class ReconciliationTests(unittest.TestCase):
    def test_one_row_per_external_offer_no_provider_class_cross_product(self):
        offers = [app("netflix", "Netflix", "subscription", "watchmode", "fresh"),
                  app("netflix", "Netflix", "rent", "justwatch", "stale", offer_id="2")]
        saved = observation(source("Netflix", "sub", 1), source("Netflix", "rent", 2),
                            source("Netflix", "buy", 3))
        rows, summary = reconcile({KEY: CASE}, {KEY: saved}, offers, [comparison()])
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["candidate_state"] for r in rows], [
            "SAME_SOURCE_ECHO", "STALE_OR_UNVERIFIED_OVERLAP", "NO_APP_OFFER"])
        self.assertEqual([r["decision"] for r in rows], [
            "SAME_SOURCE_ECHO", "STALE_OR_UNVERIFIED_OVERLAP", "NO_APP_OFFER"])
        self.assertEqual(summary["external_offer_rows"], 3)
        self.assertEqual(rows[0]["sanitized_provider_url"], "https://example.org/title")

    def test_reconciled_confidence_is_not_independent_source_verification(self):
        offers = [app("netflix", "Netflix", "subscription", "watchmode", "fresh"),
                  app("netflix", "Netflix", "subscription", "justwatch", "fresh", offer_id="2")]
        rows, summary = reconcile({KEY: CASE}, {KEY: observation(source("Netflix", "sub"))},
                                  offers, [comparison()])
        self.assertEqual(rows[0]["candidate_state"], "STALE_OR_UNVERIFIED_OVERLAP")
        self.assertIn("not streaming accuracy rates", summary["limitations"][-1])
        self.assertEqual(rows[0]["matching_app_sources"], "justwatch;watchmode")

    def test_missing_canonical_identity_blocks_even_candidate_overlap(self):
        saved = observation(source("Netflix", "sub"))
        offers = [app("netflix", "Netflix", "subscription", "justwatch", "fresh")]
        rows, _ = reconcile({KEY: CASE}, {KEY: saved}, offers,
                            [comparison("NOT_COMPARABLE")])
        self.assertEqual(rows[0]["candidate_state"], "STALE_OR_UNVERIFIED_OVERLAP")
        self.assertEqual(rows[0]["decision"], "IDENTITY_UNVERIFIED")
        self.assertEqual(rows[0]["identity_gate"], "BLOCKED")

    def test_missing_movie_is_unverified_not_false_negative(self):
        rows, _ = reconcile({KEY: CASE}, {KEY: observation(source("Netflix", "sub"))},
                            [], [comparison(found=False)])
        self.assertEqual(rows[0]["decision"], "IDENTITY_UNVERIFIED")
        self.assertEqual(rows[0]["candidate_state"], "UNMAPPED_PROVIDER_OR_CLASS")

    def test_duplicate_application_comparison_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Duplicate application comparison identity"):
            reconcile({KEY: CASE}, {KEY: observation(source("Netflix", "sub"))},
                      [], [comparison(), comparison()])

    def test_only_exact_name_or_explicit_type_specific_mapping(self):
        offers = [app("max", "HBO Max", "subscription", "justwatch", "stale")]
        index = provider_index(offers)
        self.assertEqual(resolve_provider("HBO Max", "sub", index), "max")
        self.assertEqual(resolve_provider("MAX (Via Amazon Prime)", "sub", index), "")
        index = provider_index(offers, [{"watchmode_name": "MAX (Via Amazon Prime)",
                                         "watchmode_type": "sub", "app_provider_slug": "max_amazon"}])
        self.assertEqual(resolve_provider("MAX (Via Amazon Prime)", "sub", index), "max_amazon")
        self.assertEqual(resolve_provider("MAX (Via Amazon Prime)", "rent", index), "")

    def test_external_title_year_and_type_must_match(self):
        self.assertTrue(identity_observation_ok(CASE, observation()))
        saved = observation()
        saved["watchmode_name"] = "Capture the Flag"
        self.assertFalse(identity_observation_ok(CASE, saved))
        saved = observation()
        saved["watchmode_year"] = 2015
        self.assertFalse(identity_observation_ok(CASE, saved))
        saved = observation()
        saved["watchmode_type"] = "tv_series"
        self.assertFalse(identity_observation_ok(CASE, saved))

    def test_correction_replaces_wrong_id_without_changing_raw_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cohort_path, v1, v2 = root / "cohort.csv", root / "v1.json", root / "v2.json"
            with cohort_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["tmdb_id", "title", "media_type", "year", "country"])
                writer.writeheader()
                writer.writerow({"tmdb_id": 220289, "title": "Coherence", "media_type": "movie",
                                 "year": 2013, "country": "NL"})
            v1.write_text(json.dumps({"results": [{"tmdb_id": 300602, "expected_name": "Coherence",
                                                   "media_type": "movie", "nl_sources": []}]}), encoding="utf-8")
            v2.write_text(json.dumps({
                "calls": [{"path": "/search/?search_field=tmdb_movie_id&search_value=220289"},
                          {"path": "/title/178814/sources/?regions=NL"}],
                "sanity": {"result_count": 1, "exact_id_match": True, "name": "Coherence",
                           "year": 2013, "type": "movie", "wm_id": 178814},
                "sources": [],
            }), encoding="utf-8")
            cohort = load_cohort(cohort_path)
            result, hashes = load_pilot_results(v1, v2, cohort)
            key = (220289, "movie", "NL")
            self.assertEqual(set(result), {key})
            self.assertEqual(result[key]["watchmode_id"], 178814)
            self.assertEqual(result[key]["nl_sources"], [])
            self.assertIn("v1_sha256", hashes)

    def test_missing_csv_columns_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad.csv"
            path.write_text("tmdb_id,title,media_type,expected_year\n220289,Coherence,movie,2013\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing columns"):
                read_csv(path, {"tmdb_id", "title", "media_type", "year", "country"})

    def test_url_query_and_embedded_credentials_not_exported(self):
        self.assertEqual(safe_url("https://example.org/movie?id=secret"), "https://example.org/movie")
        self.assertEqual(safe_url("https://person:token@example.org/movie"), "")


if __name__ == "__main__":
    unittest.main()
