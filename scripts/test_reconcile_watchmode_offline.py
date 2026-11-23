"""Standard-library regression tests for the #97 offline evidence comparator."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reconcile_watchmode_offline import (
    APP_COLUMNS,
    classify_offer,
    identity_observation_ok,
    load_cohort,
    load_identity_manifest,
    load_pilot_results,
    main,
    provider_index,
    read_csv,
    reconcile,
    resolve_provider,
    safe_url,
)


CASE = {"tmdb_id": 238, "title": "The Godfather", "media_type": "movie", "year": 1972, "country": "NL"}
KEY = (238, "movie", "NL")
AUDIT_DIR = Path(__file__).resolve().parents[1] / "docs/audits/stage2b"
MANIFEST = AUDIT_DIR / "identity_manifest_20261122.csv"


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

    def test_manifest_fallback_requires_audit_acceptance(self):
        manifest = load_identity_manifest(MANIFEST)
        saved = observation(source("Netflix", "sub"))
        local = {**comparison("NOT_COMPARABLE"), "local_identity_state": "MISSING_CANONICAL_TITLE"}
        for audit_source in [{}, {"identity_source": ""}, {"identity_source": "local_movie"}]:
            with self.subTest(audit_source=audit_source):
                rows, summary = reconcile(
                    {KEY: CASE}, {KEY: saved}, [], [{**local, **audit_source}], identity_manifest=manifest,
                )
                gate = summary["title_identity_gates"][str(KEY)]
                for result in (rows[0], gate):
                    self.assertEqual(result["identity_gate"], "BLOCKED")
                    self.assertEqual(result["identity_source"], "")

    def test_manifest_can_supply_identity_without_verifying_catalogue_presence(self):
        saved = observation(source("Netflix", "sub"))
        mappings = [{"watchmode_name": "Netflix", "watchmode_type": "sub", "app_provider_slug": "netflix"}]
        for found, state in [(False, "MISSING_MOVIE"), (True, "MISSING_CANONICAL_TITLE")]:
            with self.subTest(state=state), patch("socket.socket.connect", side_effect=AssertionError("Offline only")):
                rows, summary = reconcile(
                    {KEY: CASE}, {KEY: saved}, [],
                    [{**comparison("NOT_COMPARABLE" if found else "APP_DATA_MISSING", found),
                      "local_identity_state": state, "identity_source": "tmdb_manifest"}], mappings,
                    identity_manifest=load_identity_manifest(MANIFEST),
                )
                self.assertEqual(rows[0]["identity_gate"], "PASS")
                self.assertIs(rows[0]["app_movie_found"], found)
                self.assertEqual(rows[0]["identity_source"], "tmdb_manifest")
                self.assertEqual(rows[0]["decision"], "NO_APP_OFFER")
                gate = summary["title_identity_gates"][str(KEY)]
                self.assertFalse(gate["trusted_local_identity"])
                self.assertEqual(gate["local_identity_state"], state)

    def test_manifest_cannot_override_quarantined_conflicting_or_unknown_local_identity(self):
        manifest = load_identity_manifest(MANIFEST)
        saved = observation(source("Netflix", "sub"))
        for local in [
            [],
            [comparison("NOT_COMPARABLE")],
            [{**comparison(), "local_identity_state": "QUARANTINED", "identity_source": "tmdb_manifest"}],
            [{**comparison(), "local_identity_state": "CONFLICT", "identity_source": "tmdb_manifest"}],
            [{**comparison(), "local_identity_state": "INCOMPLETE", "identity_source": "tmdb_manifest"}],
        ]:
            with self.subTest(local=local):
                rows, _ = reconcile({KEY: CASE}, {KEY: saved}, [], local, identity_manifest=manifest)
                self.assertEqual(rows[0]["identity_gate"], "BLOCKED")
                self.assertEqual(rows[0]["decision"], "IDENTITY_UNVERIFIED")

    def test_review_flags_preserve_coherence_year_and_office_alias(self):
        manifest = load_identity_manifest(MANIFEST)
        cohort = load_cohort(AUDIT_DIR / "nl_pilot_cohort_20261122_v2.csv")
        self.assertEqual(len(manifest), 15)
        for tmdb_id, kind, year, title, flag in [
            (220289, "movie", 2014, "Coherence", "YEAR_MISMATCH"),
            (2316, "series", 2005, "The Office", "TITLE_ALIAS_REVIEW"),
        ]:
            with self.subTest(tmdb_id=tmdb_id):
                key = (tmdb_id, kind, "NL")
                case = cohort[key]
                # Match the cohort in Watchmode to isolate the manifest discrepancy.
                saved = {**observation(), "tmdb_id": tmdb_id, "watchmode_type": kind,
                         "watchmode_name": case["title"], "watchmode_year": case["year"]}
                local = {**comparison(), **case}
                offers, summary = reconcile({key: case}, {key: saved}, [], [local], identity_manifest=manifest)
                self.assertEqual(offers, [])
                gate = summary["title_identity_gates"][str(key)]
                self.assertTrue(gate["watchmode_title_year_match"])
                self.assertEqual((gate["cohort_title"], gate["cohort_year"]), (case["title"], case["year"]))
                self.assertEqual(gate["identity_manifest_year"], year)
                self.assertEqual(gate["identity_manifest_title"], title)
                self.assertEqual(gate["identity_manifest_review"], flag)
                self.assertEqual(gate["identity_gate"], "BLOCKED")
                self.assertEqual(gate["identity_source"], "")

    def test_manifest_rejects_duplicate_and_invalid_evidence_digest(self):
        header, first, *_ = MANIFEST.read_text(encoding="utf-8").splitlines()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "manifest.csv"
            path.write_text(f"{header}\n{first}\n{first}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate identity manifest entry"):
                load_identity_manifest(path)
            digest = load_identity_manifest(MANIFEST)[KEY[:2]].evidence_sha256
            path.write_text(f"{header}\n{first.replace(digest, 'invalid')}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid identity manifest data"):
                load_identity_manifest(path)

    def test_cli_verifies_manifest_evidence_before_exporting_missing_movie_gate(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            path = {name: root / name for name in ("cohort", "v1", "v2", "app", "comparison", "output", "summary")}
            path["cohort"].write_text("tmdb_id,title,media_type,year,country\n238,The Godfather,movie,1972,NL\n",
                                      encoding="utf-8")
            path["app"].write_text(",".join(sorted(APP_COLUMNS)) + "\n", encoding="utf-8")
            local = {**comparison("APP_DATA_MISSING", found=False),
                     "local_identity_state": "MISSING_MOVIE", "identity_source": "tmdb_manifest"}
            path["comparison"].write_text(",".join(local) + "\n" + ",".join(local.values()) + "\n", encoding="utf-8")
            evidence_dir = root / "evidence"
            evidence_dir.mkdir()
            response = evidence_dir / "movie_238.json"
            response_body = b'{"id":238,"title":"The Godfather","release_date":"1972-03-14"}\n'
            response_digest = hashlib.sha256(response_body).hexdigest()
            manifest = root / "manifest.csv"
            header, first, *_ = MANIFEST.read_text(encoding="utf-8").splitlines()
            previous_digest = load_identity_manifest(MANIFEST)[KEY[:2]].evidence_sha256
            manifest.write_text(f"{header}\n{first.replace(previous_digest, response_digest)}\n", encoding="utf-8")
            args = [
                "reconcile_watchmode_offline.py", "--cohort", str(path["cohort"]),
                "--watchmode-v1", str(path["v1"]), "--watchmode-correction", str(path["v2"]),
                "--app-snapshot", str(path["app"]), "--app-comparison", str(path["comparison"]),
                "--identity-manifest", str(manifest), "--manifest-evidence-dir", str(evidence_dir),
                "--output", str(path["output"]), "--summary", str(path["summary"]),
                "--expected-identity-manifest-sha",
            ]
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            saved = {KEY: observation(source("Netflix", "sub"))}
            with (
                patch("reconcile_watchmode_offline.load_pilot_results", return_value=(saved, {})),
                patch("socket.socket.connect", side_effect=AssertionError("Offline only")),
            ):
                for pin, body, message in [
                    ("0" * 64, None, "Evidence SHA-256 mismatch for identity_manifest_sha256"),
                    (digest, None, "Cannot read identity manifest evidence for movie_238.json"),
                    (digest, b"{}\n", "Identity manifest evidence SHA-256 mismatch for movie_238.json"),
                ]:
                    with self.subTest(message=message):
                        if body is not None:
                            response.write_bytes(body)
                        errors = io.StringIO()
                        with patch.object(sys, "argv", [*args, pin]), redirect_stderr(errors), self.assertRaises(SystemExit):
                            main()
                        self.assertIn(message, errors.getvalue())
                        self.assertFalse(path["output"].exists())
                        self.assertFalse(path["summary"].exists())
                response.write_bytes(response_body)
                with patch.object(sys, "argv", [*args, digest]), redirect_stdout(io.StringIO()):
                    main()
            with path["output"].open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual((row["identity_gate"], row["app_movie_found"]), ("PASS", "False"))
            summary = json.loads(path["summary"].read_text(encoding="utf-8"))
            self.assertEqual(summary["evidence_sha256"]["identity_manifest_sha256"], digest)
            self.assertTrue(summary["manifest_evidence_verified"])

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
