"""Unit and regression tests for Issue 97 public Git history security inventory."""
from __future__ import annotations

import unittest
from scripts.audit_issue97_public_history import (
    TARGET_COMMIT,
    VERIFIED_MASTER,
    EXPECTED_RESTRICTED_FILES,
    check_commit_reachability,
    inventory_commit_restricted_files,
    audit_sigv4_presigned_url,
    audit_operational_identifiers,
    verify_master_tree_sanitized,
    perform_full_audit,
)


class TestPublicHistorySecurityInventory(unittest.TestCase):
    def test_reachability_in_public_history(self):
        reachability = check_commit_reachability(TARGET_COMMIT, VERIFIED_MASTER)
        self.assertTrue(
            reachability["is_ancestor_of_head"],
            f"Commit {TARGET_COMMIT} must be an ancestor of verified master {VERIFIED_MASTER}",
        )
        self.assertGreaterEqual(
            reachability["commit_distance_to_head"],
            50,
            "Commit distance to master should be at least 50 commits",
        )
        self.assertGreater(
            reachability["remote_branches_count"],
            0,
            "Commit must be contained in multiple remote branches",
        )

    def test_restricted_file_inventory_count_and_paths(self):
        inventory = inventory_commit_restricted_files(TARGET_COMMIT)
        self.assertEqual(len(inventory), EXPECTED_RESTRICTED_FILES)
        
        runs_files = [item for item in inventory if item["path"].startswith("runs/")]
        spec_files = [item for item in inventory if item["path"].startswith("spec/")]
        
        self.assertEqual(len(runs_files), 15)
        self.assertEqual(len(spec_files), 5)

        # Check total bytes match custody documentation (336,717 bytes)
        total_bytes = sum(item["bytes"] for item in inventory)
        self.assertEqual(total_bytes, 336717)

    def test_sigv4_presigned_url_expiration(self):
        audit = audit_sigv4_presigned_url(TARGET_COMMIT)
        self.assertTrue(audit["found"])
        self.assertEqual(audit["algorithm"], "AWS4-HMAC-SHA256")
        self.assertEqual(audit["credential_type"], "STS_TEMPORARY")
        self.assertEqual(audit["lifetime_seconds"], 3600)
        self.assertTrue(
            audit["expired_at_commit_time"],
            "SigV4 URL must be expired by commit timestamp",
        )
        self.assertTrue(
            audit["expired_at_audit_time"],
            "SigV4 URL must be expired at current audit timestamp",
        )
        self.assertFalse(
            audit["has_secret_key_in_url"],
            "SigV4 URL must never carry secret access key",
        )

    def test_operational_identifiers_and_redacted_probe_headers(self):
        op_ids = audit_operational_identifiers(TARGET_COMMIT)
        self.assertTrue(op_ids["notte_function_id"].startswith("fe1b97b6"))
        self.assertTrue(op_ids["probe_request_headers_redacted"])

    def test_master_tree_sanitization(self):
        tree_check = verify_master_tree_sanitized(VERIFIED_MASTER)
        self.assertTrue(tree_check["is_sanitized"])
        self.assertEqual(tree_check["restricted_files_in_tree_count"], 0)
        self.assertEqual(tree_check["sigv4_matches_count"], 0)

    def test_full_audit_verdict(self):
        report = perform_full_audit()
        self.assertEqual(report["verdict"], "EXPOSURE_FOUND")
        self.assertEqual(report["inventory_count"], EXPECTED_RESTRICTED_FILES)


if __name__ == "__main__":
    unittest.main()
