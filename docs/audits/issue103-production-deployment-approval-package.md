# #103 Production Deployment Approval Package (Owner Sign-Off Target)

**Status:** READY_FOR_OWNER_SIGN_OFF — deployment and production cutover remain explicitly **NOT AUTHORIZED** until owner executes sign-off on this document.
**Issue:** #103 (Subscription plan tiers, channel add-ons, offer entitlement verdicts).
**Base SHA / Source Target:** `331dbe2f7731078c93e5e93eb4620c6e7d07e507` (Merged master, comprising PR #230 Merge `2bcbc051ea528d3570381805746fdc0286abec2b` and PR #231 Merge `331dbe2f7731078c93e5e93eb4620c6e7d07e507`). All hashes refer to git state; no self-referential head SHA is claimed inside this document.
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

## 3. Backup Requirements

1. **Pre-Cutover Backup Type:** Full logical PostgreSQL dump (`pg_dump -Fc -v --dbname=$PROD_DATABASE_URL > pre_cutover_backup_$(date +%Y%m%d_%H%M%S).dump`).
2. **Checksum / Fingerprint Fields (Owner to Supply):**
   - SHA-256 checksum of the backup dump file.
   - Row count fingerprint of tables `tracker_movie`, `tracker_availability`, `tracker_subscription`, `tracker_preference`, `tracker_watchlistitem`, `tracker_languageinfo`.
3. **Retention Policy:** Retain pre-cutover backup on secure offline storage for at least 30 days post-cutover.
4. **Restore Verification Step:** Test restore on a disposable staging container before executing production cutover:
   ```bash
   pg_restore --clean --if-exists --dbname=$STAGING_DATABASE_URL pre_cutover_backup_*.dump
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
   - **Old/New Writer Compatibility:** Additive database columns with nullable defaults or safe defaults. No destructive schema drops.
   - **Stop-Writers Gate:** Inbound scheduled refresh / cron workers must be paused via Django management command or container stop before migration execution to prevent mid-migration race conditions.
   - **Read/Write Compatibility Checks:** Run health endpoints and smoke tests immediately following migration and before resuming writers.

---

## 5. Downtime Window

- **Proposed Structure:**
  1. Maintenance announcement & active write quiesce (Estimated: 5 minutes).
  2. Stop-writers gate confirmation (Estimated: 2 minutes).
  3. Pre-cutover backup & checksum verification (Estimated: 10 minutes).
  4. Migration execution (`python manage.py migrate`) & `--check` verification (Estimated: 5 minutes).
  5. Read-only health check & smoke verification (Estimated: 5 minutes).
  6. Resume writers gate & final health check (Estimated: 3 minutes).
  - **Total Proposed Window:** ~30 minutes.
- **Actual Duration:** **UNKNOWN** (Must be measured and recorded by the owner during live execution; no simulated duration is claimed as observed).

---

## 6. Rollback and Abort Conditions

1. **Trigger Conditions:**
   - Migration command fails or raises unhandled exceptions.
   - Post-migration health check (`/api/health/`) returns non-ok status or database error.
   - Core API endpoints (watchlist / availability / entitlements) fail smoke tests.
2. **Code Rollback:** Revert container image tag to previous stable release.
3. **Database Rollback Feasibility:**
   - Forward migration reversal (`python manage.py migrate tracker 0063` / `availability 0019`) verified on disposable test database.
   - Full database restore from pre-cutover backup (Section 3) acting as the authoritative abort path if migration rollback encounters data truncation conflicts.
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
