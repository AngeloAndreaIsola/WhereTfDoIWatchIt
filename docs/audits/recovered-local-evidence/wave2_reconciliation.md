# Wave 2 integration reconciliation report

**Repository:** `AngeloAndreaIsola/WhereTfDoIWatchIt`
**Workspace:** isolated worktree at `origin/master` (no application code modified)
**Purpose:** Determine whether the `wave2-integration-validation` worktree contains any
unique, valuable functionality not incorporated into current master, and whether the
worktree can eventually be retired.

This is a **documentation-only** report. No application code was changed. No worktree,
branch, stash, or container was removed or started.

---

## A. Repository state

| Item | Value |
|---|---|
| Current `origin/master` | `7cec749f40ec1bfad51ac6d3c54a5b80485fc5ec` (branch `master`) |
| Wave 2 HEAD | `2fa128a048fcc780f7c257a11ca47876e889b914` (branch `integration/wave2-validation`) |
| Wave 2 previously observed HEAD | `2fa128a` — matches, inventory still accurate |
| Merge base (master ⋈ wave2) | `afdfdad1a80f25560a4da27acecaaf53a0e315ed` |
| Wave 2 unique commits (in wave2, not in master) | 3 — all merge commits: `bcd8dd8` (#50), `04352d7` (#52), `2fa128a` (#53) |
| Master unique commits (in master, not in wave2) | Many — PRs #77–#90 and later (e.g. `7cec749` #89, `d700bd5` #87, …) |
| Wave 2 worktree tracked modifications | None (clean working tree) |
| Wave 2 untracked files | `docker-compose.wave2.yml` |
| Wave 2 ignored files of possible value | `.env` (gitignored secrets file — not inspected, not committed) |
| Wave 2 worktree registration | Registered as `.worktrees/wave2-integration-validation` under the canonical checkout |
| Active agent/process ownership | No agent process owns this worktree; no locks pending on it (verified via process and worktree listing) |

### Branch divergence summary

The Wave 2 branch is **three merge commits ahead of the merge base** `afdfdad1`, and
**master is many commits ahead of that same base**. Every implementation and security
commit between `afdfdad1` and the Wave 2 tip (the PR #50/#52/#53 feature work plus the
credential-sanitization, CSRF, loopback, and proxy-topology hardening commits
`afdfdad1`, `87f2ec2`, `d5c35d3`, `e7e541c`, `155d44f`, `1cacd1b`, `482188b`,
`b0e668c`, `2604d93`, `5addd48`) is **already an ancestor of `master`** (`git merge-base
--is-ancestor` → YES for each).

The only commits reachable from Wave 2 but **not** from master are the three merge
commits themselves (`bcd8dd8`, `04352d7`, `2fa128a`). Their **content** (the actual PR
changes) is already present in master, as verified below.

---

## B. Feature comparison

| Feature | Wave 2 implementation | Current master | Classification | Evidence |
|---|---|---|---|---|
| Verified TMDb mapping acceptance (PR #50) | `backend/availability/services/candidate_acceptance.py`, `candidate_admin_views.py`, migration `0014_wave2a_verified_mapping_acceptance.py`, `candidate_gateway.py`, `candidate_scoring.py`, `tests/test_candidate_acceptance.py`, `urls.py`, settings, `models.py` (CandidateReviewAudit, ContentMapping, ProviderContentCandidate) | All present; `candidate_acceptance.py`, `candidate_admin_views.py`, migration, and `test_candidate_acceptance.py` are **byte-identical** to Wave 2 | **Fully integrated** | `git diff` identical; master `candidate_acceptance.py` contains full `CandidateAcceptanceService`/`CandidateRejectionService` |
| Candidate review interface (PR #52) | `frontend/app/candidate-review/page.tsx`, `frontend/components/candidate-review/{CandidateComparison,MappingAvailabilitySummary,ReviewActionModal,ReviewQueue}.tsx`, `frontend/lib/candidateReviewApi.ts`, `frontend/lib/candidateReviewFixtures.ts`, `frontend/__tests__/candidate-review.test.tsx` | All present in master | **Fully integrated** | `ls frontend/app/candidate-review/`, `frontend/components/candidate-review/`, `frontend/lib/`, `frontend/__tests__/` — all files exist in master |
| Targeted offer recovery execution (PR #53) | `backend/availability/services/offer_recovery.py`, `backend/availability/management/commands/recover_provider_evidence.py`, `backend/availability/tests/test_offer_recovery.py` | Present; master `offer_recovery.py` is a **hardened superset** (adds `mapping_is_trusted` gating returning `OUTCOME_MAPPING_CONFLICT` / `unverified_mapping_provenance`) | **Fully integrated** (master supersedes) | Master `offer_recovery.py` diff vs Wave 2 adds only trust-gating; `recover_provider_evidence.py` command present |
| API credential sanitization | `sanitize_error_detail()` in `offer_recovery.py` (redacts URLs, Bearer tokens, api_key/token/secret/password) | Present in master `offer_recovery.py` | **Fully integrated** | Same function in master |
| CSRF protections | Commit `87f2ec2` "secure Compose frontend proxy topology and staff CSRF" + `1cacd1b` "harden mutation endpoints and error disclosure" | CSRF in `backend/config/settings.py`; security commits are ancestors of master | **Fully integrated** | `git merge-base --is-ancestor 87f2ec2 master` → YES; `backend/config/settings.py` present |
| Loopback / host restrictions | Commits `155d44f` "tighten loopback security guard", `1cacd1b` "harden mutation endpoints" | Present in master (settings `ALLOWED_HOSTS`, loopback guard) | **Fully integrated** | Security commits are ancestors of master |
| Frontend proxy topology | Commit `87f2ec2` "secure Compose frontend proxy topology and staff CSRF" | Present in master | **Fully integrated** | Security commit is an ancestor of master |
| Isolated Docker validation (`docker-compose.wave2.yml`) | Untracked Compose file: db (postgres:16-alpine), backend, frontend, worker, catalogue-worker | Master `docker-compose.yml` has the same five services, same env, same volumes/healthchecks/entrypoint | **Redundant** | Wave 2 compose differs only in port numbers (db `5499`, backend `8100`, frontend `3002` vs master db unexposed/backend `8002`/frontend `3001`) and adds an exposed postgres port `127.0.0.1:5499:5432` |

### Classification rationale

Every Wave 2 component is either **Fully integrated** (same intended functionality and required behavior covered in master) or **Redundant** (the Compose file). No component is Missing, Partially integrated, or Unknown. The master versions of `offer_recovery.py` and the experiment models represent a *better* replacement (additional trust gating), so the Wave 2 offer-recovery behavior is **Superseded** in quality but the functionality itself is **Fully integrated**; no recovery is required.

---

## C. Unique commits

| Commit | Purpose | Already implemented elsewhere? | Recovery needed? |
|---|---|---|---|
| `bcd8dd8` | Merge PR #50 Verified TMDb Mapping Acceptance (candidate acceptance/rejection, admin views, migration, tests) | Yes — content byte-identical in master (`candidate_acceptance.py`, `candidate_admin_views.py`, `0014_wave2a...`, `test_candidate_acceptance.py`) | **No** |
| `04352d7` | Merge PR #52 Candidate Review Interface (candidate-review frontend pages/components/api/fixtures/tests) | Yes — all frontend files present in master | **No** |
| `2fa128a` | Merge PR #53 Targeted Offer Recovery Execution (`offer_recovery.py`, `recover_provider_evidence.py`, tests) | Yes — `offer_recovery.py` present in master as a hardened superset (adds `mapping_is_trusted` gating) | **No** |

**Note:** These three commits are *merge commits* and are **not ancestors of `master`**, so deleting the `integration/wave2-validation` branch would make them unreachable as commits. However, **no information is lost**: every file and behavior they introduced exists in master (verified identical or as a superset). Per policy, entire historical merge commits are **not** cherry-picked merely because they are non-ancestors.

The implementation/security commits that actually carry the changes (`afdfdad1` … `5addd48`) **are** ancestors of master, so the substantive work is already in master's history.

---

## D. Untracked Compose file (`docker-compose.wave2.yml`)

**Purpose:** An isolated local Docker Compose stack for Wave 2 development/testing: postgres `db`, `backend` (gunicorn), `frontend` (npm start), `worker` (title lane), `catalogue-worker` (catalogue lane), with a shared `pgdata` volume.

**Security assessment:**
- Hardcoded credentials: **None** — all credentials sourced from environment variables (`POSTGRES_USER/PASSWORD/DB`, `DJANGO_SECRET_KEY`, `TMDB_API_KEY`, `RAPIDAPI_KEY`, `WATCHMODE_API_KEY`, etc.).
- Production secrets: **None committed** — secrets are variable references only.
- Unsafe port exposure: **Yes — one concern.** The file publishes postgres to `127.0.0.1:5499:5432`. Master's `docker-compose.yml` exposes **no** postgres port. Publishing the database to localhost is a broader attack surface than master's choice. Backend (`127.0.0.1:8100:8000`) and frontend (`127.0.0.1:3002:3000`) are loopback-only, consistent with master's intent ("local Compose must never publish refresh/admin mutations to the LAN").
- Unexpected host mounts: **None.**
- Persistent volume conflicts: **None** — uses its own named `pgdata` volume; distinct file from master's `docker-compose.yml`.
- Obsolete image tags: **No** — `postgres:16-alpine` matches master.
- Duplicate services: **None** — db, backend, frontend, worker, catalogue-worker; `catalogue-worker` inherits from `worker` via YAML anchor (same pattern as master).

**Differences from current infrastructure:**
- Master `docker-compose.yml`: same services/topology/env; db **unexposed**; backend `127.0.0.1:8002:8000`; frontend `127.0.0.1:3001:3000`.
- Wave 2 file: db exposed on `127.0.0.1:5499:5432`; backend `127.0.0.1:8100:8000`; frontend `127.0.0.1:3002:3000`.
- `docker-compose.dev.yml` (master) is a separate overlay with volume mounts for hot-reload; Wave 2 file has no dev overlay equivalent.

**Recommendation:** **Do not preserve.** The Wave 2 Compose file provides no genuinely useful functionality not already available in master's `docker-compose.yml`. It is a near-duplicate with arbitrary port changes and one *less-safe* choice (exposed postgres port). If an isolated dev/test Compose configuration is needed, master's `docker-compose.yml` (with `docker-compose.dev.yml` for hot-reload) already covers it. The file is redundant/obsolete; it should not be carried forward. (No containers were started as part of this audit.)

---

## E. Missing functionality

**No genuinely missing functionality was identified.**

Every Wave 2 feature (verified TMDb mapping acceptance, candidate review interface, targeted offer recovery, API credential sanitization, CSRF protections, loopback/host restrictions, frontend proxy topology) is present in current master, and the isolated Docker stack is redundant with master's `docker-compose.yml`. Therefore:

- There is **nothing to recover**, and **PR B is not created** (per policy: "If no functional differences require recovery, do not create PR B").
- No regression tests are needed beyond master's existing suites.
- No dependencies beyond master's existing stack.

---

## F. Cleanup recommendation

The `wave2-integration-validation` worktree contains **no unique data that would be lost if removed**:

- All tracked work (PR #50/#52/#53 implementations + security hardening) is already in master.
- The only untracked file, `docker-compose.wave2.yml`, is redundant with master's `docker-compose.yml` and adds an exposed postgres port (a security regression, not a benefit).
- The ignored `.env` is a secrets file; it was not inspected and holds no project value to preserve.
- The three unique merge commits carry no content absent from master (their trees are subsumed).
- No active agent owns the worktree; no locks or pending development remain.

**Verdict:** `wave2-integration-validation` is a candidate for later cleanup once this documentation is archived and the three unique merge commits' redundancy is recorded. Do not remove it as part of this audit (task constraint); the removal decision belongs to a separate, later cleanup operation.

---

## Validation

- Every claim about master was verified against `origin/master` `7cec749f40ec1bfad51ac6d3c54a5b80485fc5ec` (file presence, byte-identity via `git diff`, `git merge-base --is-ancestor`).
- No application code was modified; no containers were started; no live provider requests or paid API calls were made.
- No credentials appear in this report or in the archived Compose file (secrets are environment-variable references only).
- The three original worktrees were inspected read-only; none was modified.
