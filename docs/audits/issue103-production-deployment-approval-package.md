# #103 Production Deployment Approval Package (Owner Sign-Off Target)

**Status:** DRAFT — REQUEST_CHANGES corrections applied; awaiting ChatGPT Web independent re-review. Deployment and production cutover remain explicitly **NOT AUTHORIZED**.
**Issue:** #103 (Subscription plan tiers, channel add-ons, offer entitlement verdicts).
**Historical Baseline Source Target:** `331dbe2f7731078c93e5e93eb4620c6e7d07e507` (merged master baseline at review time, comprising PR #230 Merge `2bcbc051ea528d3570381805746fdc0286abec2b` and PR #231 Merge `331dbe2f7731078c93e5e93eb4620c6e7d07e507`). This is a historical baseline, not the exact deployment image/revision to approve after this PR merges. Owner sign-off must name a fresh pinned release SHA and require current production preflight. No self-referential head SHA is claimed inside this document.
**Authoritative CI Verification:** Hosted CI run [46042534871](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46042534871) on exact merged master SHA `331dbe2f7731078c93e5e93eb4620c6e7d07e507` completed successfully (3/3 jobs success: Frontend Lint/Build/Test, Backend Tests, Browser Extension Build/Typecheck/Tests). Disposable isolated PostgreSQL harness verified 3,943 tests passing (`./scripts/test-backend-isolated.sh tracker availability -v 1`).

---

## 0. Forbidden Operations & Safety Statement

Nothing in this work executes or authorizes:
- Production deployment, migration, or production database writes.
- Live provider, TMDb, first-party, or playback API calls.
- HMAC token grants, permission, quota, or scope changes.
- Issue closures, branch merges, force-pushes, or credential/secret exposure.
- Activation B real-world provider/package mapping activation (Activation B remains disabled).

---

## 1. Exact Source Target

- **Base Git Commit SHA:** `331dbe2f7731078c93e5e93eb4620c6e7d07e507` (Master head as of 2026-11-27).
- **Reviewed Prerequisite PRs:**
  - PR #230: Offline Stage D identity provisioning (`2bcbc051ea528d3570381805746fdc0286abec2b`).
  - PR #231: Stop-writers release readiness plan (`331dbe2f7731078c93e5e93eb4620c6e7d07e507`).
- **Exclusion:** This document does not self-reference its own commit head; all references anchor to master SHA `331dbe2f7731078c93e5e93eb4620c6e7d07e507` and GitHub PR metadata.

---

## 2. Exact Runtime and Database Target

| Component | Target Specification | Offline Verification Status |
|---|---|---|
| **Database Engine** | PostgreSQL 16 (hosted via docker-compose on self-hosted VPS) | **UNKNOWN** for live production hostnames/DSNs/credentials (explicitly marked UNKNOWN per offline repository constraints). Verified locally via ephemeral test containers. |
| **Backend Runtime** | Django + DRF on Python 3.11+ container | Verified locally via `./scripts/test-backend-isolated.sh`. |
| **Frontend Runtime** | Next.js + Tailwind (port 3001 host / 3000 container) | Verified locally via build & test suite. |
| **DSNs & Secrets** | Production DSN, AWS/TMDb/RapidAPI credentials | **UNKNOWN / EXTERNAL** — owner must supply via secure environment vault. |

---

## 3. Backup and Preflight Target Validation

1. **Exact Checked Target:** The owner must supply and verify the exact database host, port, name, role, release SHA and cutover window through a secure channel. Do not use broad globs, inferred hosts or unchecked target lists.
2. **Table Validation Before Backup:** Validate current table names with `SELECT to_regclass('public.<table>')` before recording counts or hashes. The offline-derived Django table list is: `tracker_movie`, `tracker_availability`, `tracker_titleavailabilitycheck`, `tracker_entitlementplan`, `tracker_entitlementchannel`, `tracker_reviewedentitlementpackage`, `tracker_entitlementcoverage`, `availability_providercontent`, `availability_offer`, `availability_offerprice`, `availability_offerevidence`. This list is **not proof of the live schema**; owner preflight must verify every name against the actual target schema and fail closed on any null `to_regclass`, missing table or mismatch.
3. **Count/Hash Procedure:** For each validated table, record `SELECT count(*) FROM public.<table>` and a deterministic content hash via `COPY (SELECT * FROM public.<table> ORDER BY id) TO STDOUT WITH CSV HEADER | sha256sum` into private protected custody. Store only sanitized counts/hashes in Git; never store raw production rows.
4. **Pre-Cutover Backup:** Full logical PostgreSQL dump of the exact checked target: `pg_dump -Fc -v --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" --dbname "$DB_NAME" --file "$BACKUP_PATH"`. Record SHA256 off-repo.
5. **Required Backup Validation:** `pg_restore --list "$BACKUP_PATH"` succeeds; isolated restoration rehearsal on disposable PostgreSQL confirms schema, validated table counts and content hashes; only then may the cutover proceed.
6. **Retention:** Private encrypted storage with owner-managed retention and review policy; no credentials, DSNs or raw production data in repository or issue comments.
7. **Restore Verification Step:** Test restore on a disposable staging container before executing production cutover. Use only the exact checked backup path recorded in §3.4; no filename wildcard is permitted:
   ```bash
   pg_restore --list "$BACKUP_PATH"
   pg_restore --dbname "$STAGING_DATABASE_URL" "$BACKUP_PATH"
   python manage.py migrate --check
   ```

---

## 4. Migration Plan

1. **Migration Sequence:**
   - Tracker app migrations up to `0064` (subscription plan state & channel add-ons).
   - Availability app migrations up to `0020` (offer entitlement verdicts).
2. **Migration Verification Command:**
   ```bash
   python manage.py migrate --check
   ```
3. **Compatibility Strategy:**
   - **Coordinated stop-writers cutover is mandatory.** No rolling deployment and no old-writer compatibility is claimed. Merged #231 §4 documents that availability `0018` drops its temporary DB default and pre-`0018` writers can fail inserts.
   - Inbound scheduled refresh, cron workers and all application writers must be paused before migration execution and remain paused until post-migration read/write checks pass.
   - **Read/Write Compatibility Checks:** Run health endpoints and smoke tests immediately following migration and before resuming writers.

---

## 5. Downtime Window

The sequence below is illustrative only and contains **no numeric estimate**. Merged #231 documents that no duration can be substantiated without production DB size, replica count and backup throughput.

1. Maintenance announcement and active write quiesce.
2. Stop-writers gate confirmation.
3. Exact-target preflight, backup and checksum verification.
4. Migration execution (`python manage.py migrate`) and `migrate --check` verification.
5. Read-only health check and smoke verification.
6. Resume-writers gate and final health check.

- **Numeric downtime window:** **UNKNOWN — owner-supplied, measured value required before sign-off.** No simulated duration is claimed as observed.

---

## 6. Rollback and Abort Conditions

1. **Trigger Conditions:**
   - Migration command fails or raises unhandled exceptions.
   - Post-migration health check (`/api/health/`) returns non-ok status or database error.
   - Core API endpoints (watchlist / availability / entitlements) fail smoke tests.
2. **Code Rollback:** Revert container image tag to previous stable release.
3. **Database Rollback Feasibility:**
   - Do **not** present an isolated `availability 0019` downgrade as a verified safe rollback target. `0020` depends on both `0018` and `0019`, and reversal of `0018` can lose populated variant data.
   - Preferred first-line recovery is application rollback while retaining the additive schema, followed by health and compatibility verification.
   - If schema/data rollback is required, use only a separately approved protected restore from the exact pre-cutover backup (Section 3), with explicit data-loss treatment and owner authorization. Disposable reverse/reapply evidence is not production rollback authorization.
4. **Verification After Rollback:** Execute test suite and `/api/health/` check.
5. **Resume-Writers Criteria:** Writers remain paused until system stability, zero error logs, and correct database schema checksums are confirmed.

---

## 7. Activation A/B Separation

- **Activation A (Approved):** Published user-facing wording and UI copy explaining subscription plan tiers and market selections. **Approved by owner on 2026-11-27 (Issue #103 comment 7224557483).**
- **Activation B (Disabled / UNKNOWN):** Real-world provider, channel, and package mappings remain **disabled and UNKNOWN**. No live provider mapping logic is activated by this deployment package.

---

## 8. Approval Blocks & Review Sign-Offs

- **Owner Approval Block:**
  - Status: `PENDING_OWNER_SIGN_OFF`
  - Approved Scope: Activation A wording only (Issue #103 comment 7224557483).
  - Production Deployment / Cutover: **NOT AUTHORIZED**.
  - Signature: ___________________________ Date: _________

- **ChatGPT Web Final Technical Review Block:**
  - Review Status: Completed & Verified (PRs #230 and #231 reviewed).
  - CI Status: 3/3 jobs passing on master SHA `331dbe2f7731078c93e5e93eb4620c6e7d07e507`.
  - Recommendation: Ready for owner review; awaiting owner signature.
