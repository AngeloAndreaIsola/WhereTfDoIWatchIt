# Issue #103 — Complete Integration Verification Record (Phase 3 Final)

| Field | Value |
| --- | --- |
| Task | Issue #103 Phase 3 Integration — market-scoped entitlement verification |
| Repository | `AngeloAndreaIsola/WhereTfDoIWatchIt` |
| Coordinator Session | `orca_session_id:7634066b-7076-4272-bba1-abf79d3a776f` |
| Worker Session | `orca_session_id:8ceaea8e-150c-414d-9307-782bb0c7ab4c` (Agent E) |
| Task ID / Dispatch ID | `task_4d6da936dacd` / `ctx_8d5b6716156a` |
| Integration Branch | `integration/103-combined` |
| Tracking PR | PR #225 (DRAFT) |
| Target Base | `master` (`8985946b61c24c4b0fa9d6e0b2d19b45fd720cca`) |
| Transport & Boundaries | **Strictly mocked transport only. Zero live provider, first-party, TMDb or playback calls. Zero production DB writes, zero active HMAC grants, zero force-pushes, all PRs kept in DRAFT.** |

---

## 1. Verified Component Revisions & Git Ancestry

All constituent heads were fetched and verified against origin:

| Role | Branch / PR | Verified Head SHA | Ancestry Status in Integration Head |
| --- | --- | --- | --- |
| Base | `master` | `8985946b61c24c4b0fa9d6e0b2d19b45fd720cca` | Direct ancestor (`git merge-base --is-ancestor` PASS) |
| Architecture Contract | PR #220 | `2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6` | Direct ancestor (merged into master at `8985946`) |
| Corrected Backend (Agent B) | `impl/103-entitlement-backend` (PR #222) | `aa6db9b690089ea452aae47fabfac55ef4f717d2` | Direct ancestor (`git merge-base --is-ancestor` PASS) |
| Corrected Frontend (Agent A) | `impl/103-entitlement-frontend-free` (PR #224) | `351e28160acfac2db215d315785bb7286363d800` | Direct ancestor (`git merge-base --is-ancestor` PASS) |
| Adversarial Test Matrix & Fixtures | `qa/103-adversarial-tests` (PR #223) | `e8280ee95f4209d738ff47ff7253a5f245de72cf` | Direct ancestor (`git merge-base --is-ancestor` PASS) |
| Repair Matrix & Acceptance Cases | `repair/103-matrix-c` (PR #227) | `87c62385c8c3478af8c6b776c7b8717c1e89165b` | Direct ancestor (`git merge-base --is-ancestor` PASS) |
| Previous Integration Head | `integration/103-combined` (PR #225) | `bb438d3d1fd6f2ae1da28e9de43dff7febc37e83` | Direct ancestor (`git merge-base --is-ancestor` PASS) |

### Ancestry Resolution of PR #224 Stale Base Ref
PR #224 was originally branched from the initial backend commit `6667ebcae41bfcad18928f190ddf23ef17f9b3e7`. In this Phase 3 integration:
- The integration DAG incorporates both `aa6db9b690089ea452aae47fabfac55ef4f717d2` (PR #222 corrected head) and `351e28160acfac2db215d315785bb7286363d800` (PR #224 corrected head).
- Because `6667ebca` is the direct parent of `aa6db9b6` and ancestor of `351e281`, the integration merge cleanly unifies frontend fixes and backend repairs.
- Previous integration head `bb438d3` is an ancestor of this integration commit, enabling a pure fast-forward update of `integration/103-combined` without any force-push.
- No duplicate migrations exist: only migration `0064_market_entitlements.py` is present.

---

## 2. Independent Regression Findings (B1–B5, U1–U5) Verification

All ten repair matrix regressions documented in PR #227 and PR #226 were verified on the integrated tree:

| ID | Domain | Description | Reproduction & Validation Test | Result |
| --- | --- | --- | --- | --- |
| **B1** | Backend | Cross-package leakage: rights and language from distinct packages combining into false watchability. | `tracker.tests.test_issue103_api.SelectionApiTests.test_exact_offer_requires_both_plan_and_channel_for_bundle` | **PASS** |
| **B2** | Backend | Excluded package occlusion: later excluded package hiding a valid included one. | `tracker.tests.test_issue103_api.SelectionApiTests.test_exact_offer_precedence_and_package_isolation` | **PASS** |
| **B3** | Backend | Channel-only qualification: channel without base subscription falsely granting access. | `tracker.tests.test_issue103_api.SelectionApiTests.test_channel_requires_active_base_subscription` | **PASS** |
| **B4** | Backend | Stale write precedence: selection write ordering vs catalogue membership validation. | `tracker.tests.test_issue103_api.SelectionApiTests.test_stale_write_precedes_catalogue_membership_validation` | **PASS** |
| **B5** | Backend | Retired plan mutation: editing unrelated channels altering or invalidating retired plans. | `tracker.tests.test_issue103_api.SelectionApiTests.test_channel_edit_can_preserve_only_the_saved_retired_plan` | **PASS** |
| **U1** | Frontend | TV purchase offers rendering independently of subscription offers for buy/rent series and seasons. | `frontend/__tests__/series-page.test.tsx` (`TV show detail page entitlement displays buy/rent offers when show has no subscription offers`) | **PASS** |
| **U2** | Frontend | HTTP 409 conflict unsaved draft preservation across polls with explicit discard action. | `frontend/components/__tests__/MarketPlanSelector.test.tsx` (`shows conflict banner and handles resolve actions on 409 Conflict`) | **PASS** |
| **U3** | Frontend | Retain existing retired plan IDs on unrelated channel edits. | `frontend/components/__tests__/MarketPlanSelector.test.tsx` (`retains existing retired planId when toggling channels`) & `subscriptions-market.test.tsx` | **PASS** |
| **U4** | Frontend | Gate positive verdict badge and Best Match on verified evidence; mask contradictory/stale to UNKNOWN. | `frontend/components/__tests__/OfferRowEntitlement.test.tsx` (`renders UNKNOWN and suppresses Best Match for ambiguous or contradictory offer`) | **PASS** |
| **U5** | Frontend | Display market-specific add-on caveats beside channel options. | `frontend/components/__tests__/MarketPlanSelector.test.tsx` (`renders market-specific add-on caveat next to channel options`) | **PASS** |

---

## 3. Test Execution and Results

### A. Contract and Adversarial Fixtures
- **Command:** `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures`
  - Contract corpus (`docs/contracts/issue103-entitlement-examples.json`): 10 cases, 12 rows — **PASS**
  - Adversarial corpus (`docs/tests/issue103-adversarial-cases.json`): 42 cases, 48 rows — **PASS**
  - Directional symmetry checks (reversed rows): **PASS**
  - Machine-readable repair cases validation: `python3 -m json.tool docs/tests/issue103-repair-cases.json` — **PASS**

### B. Focused Backend Suite (48 tests)
- **Command:** `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_api tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_migration tracker.tests.test_entitlement_exact -v 2`
  - Output: `Ran 48 tests in 2.395s — OK`
  - Includes B1–B5, legacy rollback, idempotence, selection revision checks, host-variant portability, toggle & delete lifecycles.

### C. Full Backend Test Suite (3,935 tests)
- **Command:** `./scripts/test-backend-isolated.sh --noinput`
  - Output: `Ran 3935 tests in 157.241s — OK`
  - Zero failures, zero errors, clean teardown of disposable database.

### D. Migration 0064 & Schema Drift Verification
- **Empty & Seeded Upgrade:** `test_seeded_upgrade_idempotence_and_legacy_read_rollback` in `tracker.tests.test_issue103_migration` (**PASS**)
  - Rehearses migration back to `0063_title_check_report_provenance`
  - Seeds legacy subscriptions, provider variants, and access modes
  - Migrates forward to `0064_market_entitlements`
  - Re-applies idempotently
  - Verifies read rollback leaving additive declarations intact
- **Schema Drift Check:** `manage.py makemigrations --check --dry-run`
  - Output: `No changes detected`

### E. Standalone Audit Test Suite
- **Command:** `PYTHONPATH=. pytest -q -rs scripts/test_*.py`
  - Output: `46 passed, 1 skipped in 0.23s` (1 skipped for optional manual `--tmdb-evidence-dir`)
- **Unittest runner:** `python3 -m unittest discover -s scripts -p "test_*.py"`
  - Output: `Ran 47 tests in 0.188s — OK (skipped=1)`

### F. Focused Frontend Suite (46 tests)
- **Command:** `cd frontend && npx vitest run __tests__/series-page.test.tsx components/__tests__/MarketPlanSelector.test.tsx components/__tests__/OfferRowEntitlement.test.tsx __tests__/subscriptions-market.test.tsx`
  - Output: `4 passed (4), 46 passed (46) — Duration: 1.57s`

### G. Full Frontend Test Suite (445 tests)
- **Command:** `cd frontend && npm test`
  - Output: `51 passed (51), 445 passed (445) — Duration: 15.00s`

### H. Frontend TypeScript, Lint & Production Build
- **Typecheck:** `cd frontend && npx tsc --noEmit` — **0 errors**
- **Lint:** `cd frontend && npm run lint` — **0 errors** (2 pre-existing image/hook warnings)
- **Build:** `cd frontend && NEXT_TELEMETRY_DISABLED=1 npm run build`
  - Output: `✓ Compiled successfully`, `✓ Generating static pages (16/16)`, all routes optimized.

### I. Browser Extension Suite (34 tests)
- **Typecheck:** `cd extension && npm run typecheck` — **0 errors**
- **Tests:** `cd extension && npm test` — `4 passed, 34 passed`
- **Build & Package Validation:** `npm run build && npm run validate:package` — `Package validation passed`

---

## 4. Route Provenance and Model Anomaly Record

- **Requested Route:** `antigravity/gemini-3.8-flash-high`
- **Fallback Event:** The primary account reported individual quota exhaustion with reset ~166 hours away. Do-not-retry instruction respected.
- **Owner-Approved Fallback Route:** `ag-gemini-3.8-flash-tiered` via OmniRoute through pi agent at HIGH reasoning effort.
- **Recorded Substitution Anomaly:** Direct OmniRoute API probe returned `response.model = gemini-3.8-flash-tiered`; the pi wrapper self-reported `gemini-3.8-flash` with `responseModel = keepalive` and `responseId = chatcmpl-keepalive`. The original Antigravity HIGH route did not run.
- **Cost:** Recorded per OmniRoute provider metrics (**COST_MEASURED / TIERED**).

---

## 5. Operational Boundaries & Hard Invariants

- **Mocked Transport Only:** Zero live provider, first-party, TMDb or playback calls were made during integration and verification.
- **Disposable PostgreSQL Only:** Ephemeral containers via `scripts/test-backend-isolated.sh` were used for all database tests. No writes to production databases.
- **Permissions & Secrets:** Zero active HMAC grants, zero token modifications, zero secret exposures.
- **Git State:** Zero force-pushes, zero branch deletions, zero worktree removals.
- **Review Authority:** All PRs remain in **DRAFT**. Issue #103 and #97 remain open. ChatGPT Web owns final review and merge.

---

## 6. Assumptions and Unresolved Risks

1. **Synthetic & Mocked Contract Testing:** All entitlement assertions certify logic against the agreed specification (`#220`) and mocked inputs. Real-world provider catalog drift and account-level playback authentication are not asserted by offline tests.
2. **Issue #97 Separation:** Stage E evidence reconciliation and live provider authorization hold operations remain separate under issue #97 and are untouched by this integration.
