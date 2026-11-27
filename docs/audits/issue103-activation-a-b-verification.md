# #103 Activation A and Activation B verification report (Agent B)

| Attribute | Value |
| --- | --- |
| Mission | Owner-approved `/orchestration` mission for #103 (AngeloAndreaIsola/WhereTfDoIWatchIt) |
| Role | Agent B — Activation A and B verification |
| Base actually used | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` (fresh `git fetch origin master`; `FETCH_HEAD` and `origin/master` both matched the base stated in dispatch) |
| Branch | `audit/activation-a-b-verification` (isolated, forward-only from base) |
| Requested route | native OpenCode `opencode/hy4-flash-free` |
| Actual model / identity probe | `MODEL_IDENTITY_OK opencode/hy4-flash-free` (fresh in-session `opencode run --format json -m opencode/hy4-flash-free`) |
| Provider | OpenCode |
| Effort | UNPINNED (no effort flag supplied; no HIGH/XHIGH claimed) |
| Cost | JSON telemetry `cost: 0` on the identity probe |
| Forbidden routes | No Sol, no Astra, no paid escalation, no silent substitution |
| Live calls | ZERO live provider, TMDb, first-party, or playback requests. All verification used local fixtures and disposable ephemeral PostgreSQL via `scripts/test-backend-isolated.sh`. |
| Activation changes | NONE. No mapping activated, no production deploy/migration/config change, no HMAC grant, no quota/permission change, no secret exposure, no issue closure, no cleanup. |

---

## 1. Task 1 — Activation A owner-approved wording vs current code candidate on master

The owner approval is issue #103 comment [`7224557483`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103#issuecomment-7224557483) (2026-11-27): "**APPROVED: the published #103 Activation A user-facing wording.** This is wording sign-off only." The approved scope is the exact 16-row final wording table in issue comment [`7222192511`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103#issuecomment-7222192511) §4, which the same owner comment explicitly approved. The decision tracker comment [`7224642213`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103#issuecomment-7224642213) confirms the authority and states Activation B is **NOT AUTHORIZED**.

All 16 approved strings were checked verbatim against the code candidate at base `6af4cd84`. Result: **MATCH — no wording drift found**.

| # | Approved copy (comment 7222192511 §4) | Code at base `6af4cd84` | Status |
| --- | --- | --- | --- |
| 1 | `Included with your selected plan` | `frontend/lib/entitlement.ts:15` | MATCH |
| 2 | `Included with your channel` | `frontend/lib/entitlement.ts:60` (via `presentVerdict`) | MATCH |
| 3 | `Requires channel add-on` | `frontend/lib/entitlement.ts:16` | MATCH |
| 4 | `Rent or buy` / `Rent` / `Buy` | `frontend/lib/entitlement.ts:17,64-65 (plus label :17)` | MATCH |
| 5 | `Free with ads` / `Free` | `frontend/lib/entitlement.ts:18,57-58` | MATCH |
| 6 | `Blocked for your selection` | `frontend/lib/entitlement.ts:19` | MATCH |
| 7 | `Plan access unconfirmed` | `frontend/lib/entitlement.ts:20-21`; fallback in `OfferRow.tsx` | MATCH |
| 8 | `Coverage applies to your declared selection. Account status and playback are unverified.` | `backend/tracker/services/entitlement.py:112` (server-supplied caveat) | MATCH |
| 9 | `Billing {billing_country} · offer {offer_country} — plan coverage in another market needs reviewed portability.` | `frontend/components/OfferRow.tsx:399-401` | MATCH |
| 10 | `Region caveat: this market is not confirmed by your selected plan.` | `frontend/components/OfferRow.tsx:408` | MATCH |
| 11 | Saved unsupported plan notice ("...remains unsupported in our catalogue, so plan-dependent offers are unconfirmed." — curly apostrophe in code) | `frontend/components/MarketPlanSelector.tsx:184-186` | MATCH |
| 12 | `{name} (unsupported in our catalogue)` | `frontend/components/MarketPlanSelector.tsx:223` | MATCH |
| 13 | `Not saved: this selection changed elsewhere. The current saved state is shown below and your edits are still here — review and save again.` | `frontend/components/MarketPlanSelector.tsx:151-152` | MATCH |
| 14 | `A channel held on its own does not grant a channel hosted by another provider. Unanswered channels stay unconfirmed.` | `frontend/components/MarketPlanSelector.tsx:296-297` (split JSX text node) | MATCH |
| 15 | `Last known availability — needs recheck` / `Not re-confirmed recently, so not counted as watchable now.` | `frontend/components/LastKnownOffers.tsx:31` | MATCH |
| 16 | `Reported by availability sources` / `Your subscription access and playback are unverified.` | `frontend/components/SourceReportedOffers.tsx:15` | MATCH |

Conclusion: the owner-approved Activation A wording matches the current master code candidate exactly. Approval remains **wording-only**; it does not authorize deployment, mapping activation, or real-world assertions.

---

## 2. Tasks 2–4 — Critical entitlement regressions, disabled/unknown mappings, legacy availability

All runs used disposable PostgreSQL through `./scripts/test-backend-isolated.sh`, which starts ephemeral containers, mounts docs read-only, and tears down volumes. No live transport.

| Verification | Command | Result |
| --- | --- | --- |
| Entitlement evaluator + fixtures + API + migration + B1 regressions + surface agreement | `./scripts/test-backend-isolated.sh tracker.tests.test_entitlement_exact tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration tracker.tests.test_b1_regression tracker.tests.test_surface_country_agreement -v 2` | **Ran 63 tests in 4.085s — OK** |
| Shared contract corpus (34 cases / 37 rows) and adversarial corpus (42 cases / 48 rows) | included in `test_issue103_fixtures` above | Both passed, including offer-order permutation invariance |
| Legacy availability surfaces | `./scripts/test-backend-isolated.sh tracker.tests.test_availability_classification tracker.tests.test_availability_read_boundary tracker.tests.test_detail_availability_state tracker.tests.test_issue97_availability_contract -v 1` | **Ran 62 tests in 2.499s — OK** |

Reproduced critical fail-closed regressions (all from the passing fixture corpus, synthetic data only):

- premium without its own rule → `UNKNOWN/NO_APPLICABLE_RULE`; no tier inheritance.
- exact provider-name collision (`aster_plus`) → `UNKNOWN/HOLDING_UNCONFIRMED`.
- channel with missing base → `BLOCKED/BASE_NOT_HELD`; undeclared channel → `UNKNOWN/HOLDING_UNCONFIRMED`; unresolved package → `UNKNOWN/PACKAGE_UNRESOLVED`.
- source-reported → `UNKNOWN/SOURCE_REPORTED`; stale → `UNKNOWN/STALE_EVIDENCE`/`needs_recheck`; conflicting rules → `UNKNOWN/EVIDENCE_CONFLICT`.
- cross-market without reviewed portability → `UNKNOWN/PORTABILITY_UNVERIFIED`; explicit denial → `BLOCKED/REGION_EXCLUDED`.
- unsupported plan → `UNKNOWN/PLAN_UNSUPPORTED`; unspecified → `PLAN_UNSPECIFIED`; missing market → `TERRITORY_UNKNOWN`; unknown offer class → `OFFER_CLASS_UNKNOWN`.
- rent/buy stays `RENT_OR_BUY` with `is_entitled=false`; no cross-row borrowing of subscription inclusion; late foreign evidence does not move a local verdict.

Note on B1: three `test_b1_regression` cases are explicitly decorated as *expected failures* documenting desired-but-unimplemented B2 targets (expired offer cannot establish current availability; explicit refreshes include SA subject to quota). They report "Expected failure ... ok" and are **not** newer regressions introduced by this verification; they are unchanged from the reviewed base.

### Unverified mappings disabled by default / remain UNKNOWN (Task 3)

- `backend/tracker/migrations/0064_market_entitlements.py` creates the reviewed entitlement tables with **no `RunPython` seed of any row**. A fresh database has zero `ReviewedEntitlementPackage`, `EntitlementCoverage`, `EntitlementPlan`, `EntitlementChannel`, and `ViewerMarketSelection` rows. Verified by source inspection; no migration seed for these models exists in any tracker or availability migration.
- `backend/tracker/services/entitlement_reads.py:119`: `source_supported = package is not None`, and `package` is looked up only in the reviewed-package map; a package absent from reviewed data yields `UNKNOWN/SOURCE_UNSUPPORTED` under `backend/tracker/services/entitlement.py:144-145`.
- `backend/tracker/models.py:341-412`: `ProviderCataloguePackage.resolution_status` defaults to `UNRESOLVED`; `PackageReasonCode.NEEDS_MANUAL_REVIEW` is the honest default. These are catalogue observations, never availability/entitlement signals.
- `backend/availability/sources/justwatch_providers.py:1-28`: the static `PACKAGE_TO_PROVIDER_SLUG` table is an **identity-only** map (JustWatch technicalName → canonical provider slug). It explicitly does not grant entitlement, and the file's docstring states anything unmapped is "unmapped and skipped".
- A positive verdict requires `evidence_state == "verified"` plus a matching reviewed `CoverageRule` with exact typed scope, plan, country, variant, and validity window (`entitlement.py:142-189`). With no reviewed rows, all real-world provider/package/plan claims resolve to `UNKNOWN`.

### Legacy availability continues to work (Task 4)

The 62-test legacy/availability-surface run above passed. Legacy coarse provider holdings seed only a coarse `holding=yes` selection (`entitlement_reads.py:91`) with no billing market, plan, or confirmed channel absence; a name-only legacy row cannot generate a positive tier verdict. Legacy removal revokes effective selected holdings atomically, and a legacy positive never overwrites an explicit plan or reactivates a revoked plan (covered by `SelectionApiTests.test_bodyless_toggle_and_legacy_delete_revoke_plan` and `test_replay_conflict_omission_and_legacy_revocation`, both passing).

---

## 3. Tasks 5 & 9 — Activation A smoke-test matrix and narrow activation/rollback procedures

### Activation A smoke-test matrix (exact commands, expected fail-closed behavior)

Run from the repository root. Substitute no live credentials; keep all inputs synthetic.

| # | Command | Expected result / fail-closed behavior |
| --- | --- | --- |
| A1 | `git fetch origin master && git rev-parse FETCH_HEAD` | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` — exact reviewed base, forward-only. |
| A2 | `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` | `MODEL_IDENTITY_OK opencode/hy4-flash-free`; JSON `cost: 0`; no substituted route. |
| A3 | `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures -v 2` | 34+42 fixture cases pass; every positive is synthetic; all unmapped inputs `UNKNOWN`. |
| A4 | `./scripts/test-backend-isolated.sh tracker.tests.test_entitlement_exact tracker.tests.test_issue103_api tracker.tests.test_issue103_migration -v 2` | All pass; revision/409 semantics and read-rollback intact. |
| A5 | `./scripts/test-backend-isolated.sh tracker.tests.test_b1_regression tracker.tests.test_surface_country_agreement -v 2` | All pass; three B2 desired-contract cases report "Expected failure ... ok" (pre-existing, not new regressions). |
| A6 | `./scripts/test-backend-isolated.sh tracker.tests.test_availability_classification tracker.tests.test_availability_read_boundary tracker.tests.test_detail_availability_state tracker.tests.test_issue97_availability_contract -v 1` | 62 tests pass; legacy availability unchanged. |
| A7 | `python manage.py migrate --check` on a **disposable** database only | Exit 0; zero seeded reviewed entitlement rows. (Production invocation is NOT part of this task.) |
| A8 | Read-only DB check on disposable DB: `SELECT count(*) FROM tracker_reviewedentitlementpackage; SELECT count(*) FROM tracker_entitlementcoverage;` | Both **0** before any Activation B decision; no positive runs exist. |
| A9 | UI copy check: grep the 16 strings from §1 in `frontend/lib/entitlement.ts`, `OfferRow.tsx`, `MarketPlanSelector.tsx`, `LastKnownOffers.tsx`, `SourceReportedOffers.tsx`, and backend caveat in `entitlement.py` | All match approved table; no discontinued/real-world claim; fallback is "Plan access unconfirmed". |
| A10 | Frontend (if dependencies locally available): `cd frontend && npx vitest run components/__tests__/OfferRowEntitlement.test.tsx components/__tests__/MarketPlanSelector.test.tsx` | Stale/source-reported/unverified never render positive verdict or Best Match; unsupported plan stays unconfirmed; 409 keeps draft. |
| A11 | After any real deployment (outside this task): health `GET /api/health/` and read-only smoke of detail/list/watchlist | 200; zero unexpected positive entitlement verdicts; any positive with no reviewed mapping is a failure → abort/rollback. |

### Activation A procedure (narrow, documentation scope only here)

1. Prerequisite: owner wording approval already recorded in #103 comment 7224557483. Confirm the exact base/release SHA and hosted CI on that exact SHA before any deploy decision.
2. Deploy only the framework/UI with reviewed entitlement tables **empty**. No mapping, plan, channel, coverage rule, or first-party assertion is inserted.
3. Run smoke commands A1–A11 (A11 only by the deployment owner with an exact target). Fail closed on any positive verdict lacking reviewed evidence.
4. Rollback: revert the container image to the previous stable release; prefer application rollback retaining additive schema. Do **not** present an isolated `availability 0018/0019/0020` downgrade as verified safe rollback (0020 depends on 0018+0019; 0018 reversal can lose populated variant data). If data rollback is required, use only a separately approved protected restore from the exact pre-cutover backup. Restore on disposable PostgreSQL first. Writers remain paused until health and schema checks pass.

### Activation B procedure (per-mapping; never a global enable)

Activation B is **not authorized** by this report. When separately reviewed evidence exists for one exact mapping:

1. One owner decision per exact `(source, country, source_package_id, offer_type, variant, validity window, product/plan, channel)` tuple. No batch/global enable flag exists or should be added.
2. Insert exactly one `ReviewedEntitlementPackage` + `EntitlementCoverage` (+ plan/channel where applicable) row for that tuple, with exact `provenance_reference`, observed_at/fresh_until, and reviewer identity. Evidence requirements are in `activation-b-evidence-matrix.md`.
3. Re-run A3–A8 plus a focused read test asserting only that tuple can become positive; all other provider/package/plan combinations must remain `UNKNOWN`.
4. Rollback for that mapping: delete/deactivate only its own `EntitlementCoverage` (and, if newly created, its reviewed package/plan/channel) rows, keeping all other mappings untouched; re-run A8/A9 and confirm the tuple is back to `UNKNOWN`. Prefer read rollback with additive data retained. No production migration, global flag, or other mapping is affected.

---

## 4. Tasks 6–8 & 10 — Reviewed evidence inventory and Activation B verdict

Full inventory with source, reviewer, provenance, and fail-closed disposition is in [`activation-b-evidence-matrix.md`](activation-b-evidence-matrix.md). Summary:

| Evidence item | Source / provenance | Reviewer | What it actually establishes | Activation B eligible? |
| --- | --- | --- | --- | --- |
| Stage A canary (`JW-CANARY-v1b`, 2026-11-24) | `docs/audits/issue97/jw-canary-2026-11-24-evidence/README.md` + `manifest.json` | Stage A package reviewer; ChatGPT Web owns final review (PR #197) | 56 source-reported NL offers, all tagged `UNLICENSED`, `SAME_SOURCE_ECHO`, `MEASURED_AGAINST_UNVERIFIED_SOURCE`; internal HTTP attempts UNKNOWN | **No** |
| Stage A/C historical reconciliation | `docs/audits/stage2b/issue97-independent-verification-report.md`; `issue97-reconciliation-matrix-template.csv` | Independent Stage A/C reconciliation reviewer | 7 historical first-party matches, all `temporal_eligible=no`, `independently_adjudicated=no`, `disposition=outside_reviewed_window` | **No** |
| Accepted NL observations (15 rows) | `docs/audits/stage2b/issue97-accepted-observations.csv` | Stage C/Stage E evidence reviewers | All rows `ADVERTISED_ONLY`, `entitlement=untested`, `playback=untested`; some identity/alias conflicts | **No** |
| NL provider/class mapping decisions | `docs/audits/stage2b/issue97-provider-mapping-decisions.md` | Independent provider-mapping reviewer (Stage 2b) | Identity + offer-class mapping (e.g. `max_amazon` add-on under `prime` parent) for the pinned pilot; explicitly "no changed mapping updates application providers, entitlements or persisted offers" | **No** (identity only, not plan/title entitlement) |
| Stage E protocol / source review / candidate matrix | `docs/audits/issue97/stage-e-*.md`; `stage-e-candidate-matrix.csv` | Stage E source reviewer and owner decision gate | Start state: 0 fresh independently adjudicated pairs, 0 confirmed negatives, undefined accuracy; source decisions 0/3; Batch A BLOCKED, zero authorized sends; entitlement and playback left `untested` for every row | **No** |
| Owner Activation A decision | #103 comment 7224557483 (and tracker 7224642213) | Repository owner AngeloAndreaIsola | Wording sign-off only; Activation B explicitly DISABLED and requires separate reviewed independent evidence and owner authorization | **No** |

**Explicit finding (Task 7): No Activation B mapping currently satisfies every evidence requirement.** There are zero independently reviewed first-party *entitlement* assertions bound to an exact country + package + variant + validity period + plan/channel + provenance reference, and zero playback confirmations. All real-world positives therefore remain `UNKNOWN`. Unsupported mappings are kept UNKNOWN; nothing in this report creates a positive mapping from assumptions, old screenshots, or JustWatch self-confirmation. Activation A approval must not be read as Activation B approval.

---

## 5. Verification commands and hosted CI

Exact commands run in this task are in §2 and §3. All isolated test runs used Docker with ephemeral PostgreSQL; no live endpoints were contacted. `git diff --check` passes on the two added markdown files.

Hosted CI is 3/3 SUCCESS on the exact final PR head (head SHA and run link recorded in draft PR #236 and the issue #103 handoff, which do not change the PR head). Earlier CI run [46062310367](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46062310367) was 3/3 SUCCESS on the first head `10cc1308970fde00c2ff9dce62ac3a74be783be8`; subsequent heads differ only by documentation one-line CI-link updates. Draft PR [#236](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/236). Local validation above is the authoritative record for this report; hosted CI is corroborating.

## 6. Status

`READY_FOR_INDEPENDENT_REVIEW` — documentation/verification only. ChatGPT Web independent review requested on issue #103. #103 and #97 remain open; no mapping, deployment, migration, permission, quota, or grant change was made.
