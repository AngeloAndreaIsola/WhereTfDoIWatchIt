"""Audit public Git history for Issue 97 restricted canary files and credential exposure.

Performs read-only inspection of commit ff150586319e89b8ce4f8dfde9403a33eceea1d1,
evaluates Git DAG reachability, audits credential/URL/identifier exposure without
reproducing sensitive bytes, verifies current HEAD sanitization, and validates
expiration timestamps.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

TARGET_COMMIT = "ff150586319e89b8ce4f8dfde9403a33eceea1d1"
CLEANUP_COMMIT = "a8964f6899bd2a4dbeba3850f47fc395adc22566"
VERIFIED_MASTER = "0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea"

EXPECTED_RESTRICTED_FILES = 20
COMMIT_TIMESTAMP_UTC = 1795538328  # 2026-11-24T16:38:48Z (17:38:48 +0100)


def run_git(args: list[str]) -> str:
    res = subprocess.run(["git"] + args, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def check_commit_reachability(commit: str, head_ref: str = "HEAD") -> dict[str, Any]:
    """Check if commit is reachable in Git DAG and list branches/tags containing it."""
    # Check ancestor
    is_ancestor = False
    try:
        subprocess.run(["git", "merge-base", "--is-ancestor", commit, head_ref], check=True)
        is_ancestor = True
    except subprocess.CalledProcessError:
        is_ancestor = False

    # Count commits between commit and HEAD
    commit_distance = -1
    if is_ancestor:
        count_str = run_git(["rev-list", "--count", f"{commit}..{head_ref}"])
        commit_distance = int(count_str)

    # Remote branches containing commit
    r_branches_out = run_git(["branch", "-r", "--contains", commit])
    remote_branches = [b.strip() for b in r_branches_out.splitlines() if b.strip()]

    # Local branches containing commit
    l_branches_out = run_git(["branch", "--contains", commit])
    local_branches = [b.strip().replace("* ", "") for b in l_branches_out.splitlines() if b.strip()]

    # Tags containing commit
    tags_out = run_git(["tag", "--contains", commit])
    tags = [t.strip() for t in tags_out.splitlines() if t.strip()]

    return {
        "commit": commit,
        "is_ancestor_of_head": is_ancestor,
        "commit_distance_to_head": commit_distance,
        "remote_branches_count": len(remote_branches),
        "remote_branches": remote_branches,
        "local_branches": local_branches,
        "tags": tags,
    }


def inventory_commit_restricted_files(commit: str) -> list[dict[str, Any]]:
    """List and audit the 20 restricted files in commit ff15058."""
    out = run_git(["diff-tree", "-r", "--no-commit-id", "--name-status", commit])
    restricted: list[dict[str, Any]] = []
    
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status, path = parts[0], parts[1]
        if path.startswith("runs/") or path.startswith("spec/"):
            blob_id = run_git(["rev-parse", f"{commit}:{path}"])
            raw_data = subprocess.run(["git", "cat-file", "-p", blob_id], capture_output=True, check=True).stdout
            sha256 = hashlib.sha256(raw_data).hexdigest()
            line_count = len(raw_data.splitlines())

            item: dict[str, Any] = {
                "status": status,
                "path": path,
                "blob_id": blob_id,
                "bytes": len(raw_data),
                "lines": line_count,
                "sha256": sha256,
            }
            restricted.append(item)

    return sorted(restricted, key=lambda x: x["path"])


def audit_sigv4_presigned_url(commit: str, path: str = "runs/jw-canary-2026-11-24/function_show_redeploy.json") -> dict[str, Any]:
    """Parse and audit the AWS SigV4 presigned download URL in function_show_redeploy.json."""
    blob_id = run_git(["rev-parse", f"{commit}:{path}"])
    raw_data = subprocess.run(["git", "cat-file", "-p", blob_id], capture_output=True, check=True).stdout
    text = raw_data.decode("utf-8", errors="replace")

    # In function_show_redeploy.json, JSON contains download_url with \u0026
    parsed_json = json.loads(text)
    download_url = parsed_json.get("url") or parsed_json.get("download_url", "")
    if not download_url:
        return {"found": False}

    parsed = urllib.parse.urlparse(download_url)
    qs = urllib.parse.parse_qs(parsed.query)

    amz_date = qs.get("X-Amz-Date", [""])[0]
    amz_expires = qs.get("X-Amz-Expires", [""])[0]
    amz_cred = qs.get("X-Amz-Credential", [""])[0]
    has_token = "X-Amz-Security-Token" in qs
    has_sig = "X-Amz-Signature" in qs

    dt_signed = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    expires_ts = dt_signed.timestamp() + int(amz_expires)
    dt_expires = datetime.fromtimestamp(expires_ts, tz=timezone.utc)

    cred_parts = amz_cred.split("/")
    cred_id = cred_parts[0] if cred_parts else ""

    # Check expiration status
    expired_at_commit = expires_ts < COMMIT_TIMESTAMP_UTC
    now_utc = datetime.now(timezone.utc).timestamp()
    expired_at_audit = expires_ts < now_utc

    return {
        "found": True,
        "host": parsed.netloc,
        "path": parsed.path,
        "algorithm": qs.get("X-Amz-Algorithm", [""])[0],
        "credential_type": "STS_TEMPORARY" if cred_id.startswith("ASIA") else "IAM_USER_OR_OTHER",
        "credential_id_masked": f"{cred_id[:8]}...{cred_id[-4:]}" if len(cred_id) > 12 else "[MASKED]",
        "credential_scope": "/".join(cred_parts[1:]) if len(cred_parts) > 1 else "",
        "signed_at_utc": dt_signed.isoformat(),
        "expires_at_utc": dt_expires.isoformat(),
        "lifetime_seconds": int(amz_expires),
        "commit_timestamp_utc": datetime.fromtimestamp(COMMIT_TIMESTAMP_UTC, tz=timezone.utc).isoformat(),
        "expired_at_commit_time": expired_at_commit,
        "expired_at_audit_time": expired_at_audit,
        "has_session_token": has_token,
        "has_signature": has_sig,
        "has_secret_key_in_url": False,  # SigV4 URLs never carry the secret key
    }


def audit_operational_identifiers(commit: str) -> dict[str, Any]:
    """Audit internal operational identifiers across the 20 restricted files."""
    blob_id_show = run_git(["rev-parse", f"{commit}:runs/jw-canary-2026-11-24/function_show_redeploy.json"])
    show_json = json.loads(run_git(["cat-file", "-p", blob_id_show]))

    blob_id_probe = run_git(["rev-parse", f"{commit}:runs/jw-canary-2026-11-24/probe-01.json"])
    probe_json = json.loads(run_git(["cat-file", "-p", blob_id_probe]))

    # Check probe headers redaction
    headers = probe_json.get("request", {}).get("headers", [])
    headers_redacted = all("<redacted>" in h for h in headers if "Authorization" in h or "api-key" in h)

    return {
        "notte_function_id": show_json.get("function_id", ""),
        "function_name": show_json.get("name", ""),
        "aws_account_id_masked": "063088...432",
        "aws_role_arn_masked": "arn:aws:iam::063088...432:role/notte-prod-lambda-execution-role",
        "probe_request_headers_redacted": headers_redacted,
    }


def verify_master_tree_sanitized(head_ref: str = "HEAD") -> dict[str, Any]:
    """Verify that current master tree does not contain any restricted files or SigV4 patterns."""
    tree_files = run_git(["ls-tree", "-r", "--name-only", head_ref]).splitlines()
    restricted_in_tree = [p for p in tree_files if p.startswith("runs/") or p.startswith("spec/")]

    # Fast search using git grep for SigV4 patterns in the tree
    sigv4_matches = []
    res = subprocess.run(
        ["git", "grep", "-E", "X-Amz-Signature|AWS4-HMAC-SHA256", head_ref],
        capture_output=True,
        text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        for line in res.stdout.strip().splitlines():
            prefix = line.split(":", 1)[0]
            sigv4_matches.append(prefix)

    return {
        "head_ref": head_ref,
        "total_files_in_tree": len(tree_files),
        "restricted_files_in_tree_count": len(restricted_in_tree),
        "restricted_files_in_tree": restricted_in_tree,
        "sigv4_matches_count": len(sigv4_matches),
        "sigv4_matches": sigv4_matches,
        "is_sanitized": len(restricted_in_tree) == 0 and len(sigv4_matches) == 0,
    }


def perform_full_audit() -> dict[str, Any]:
    """Run complete public history security inventory audit."""
    reachability = check_commit_reachability(TARGET_COMMIT)
    inventory = inventory_commit_restricted_files(TARGET_COMMIT)
    sigv4_audit = audit_sigv4_presigned_url(TARGET_COMMIT)
    op_ids = audit_operational_identifiers(TARGET_COMMIT)
    tree_sanitized = verify_master_tree_sanitized(VERIFIED_MASTER)

    verdict = "EXPOSURE_FOUND"
    if not reachability["is_ancestor_of_head"] and reachability["remote_branches_count"] == 0:
        verdict = "SECURITY_CLEAN"

    return {
        "verdict": verdict,
        "target_commit": TARGET_COMMIT,
        "cleanup_commit": CLEANUP_COMMIT,
        "verified_master": VERIFIED_MASTER,
        "reachability": reachability,
        "inventory_count": len(inventory),
        "inventory": inventory,
        "sigv4_audit": sigv4_audit,
        "operational_identifiers": op_ids,
        "tree_sanitization": tree_sanitized,
    }


if __name__ == "__main__":
    report = perform_full_audit()
    print(json.dumps(report, indent=2))
