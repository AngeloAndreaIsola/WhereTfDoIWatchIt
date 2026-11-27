# #103 Activation B evidence matrix (Agent B)

**Status: ZERO mappings eligible.** No Activation B row currently satisfies every evidence requirement. Activation A wording approval (#103 comment `7224557483`) does **not** imply Activation B approval. This matrix is documentation-only: no mapping was activated, no production change made, no live provider/TMDb/first-party/playback request issued.

Base: `6af4cd8421ec9c85e4fff724fded60d5cce86c85` (`origin/master`, fresh fetch). Branch: `audit/activation-a-b-verification`. Route: native `opencode/hy4-flash-free`, UNPINNED effort, JSON `cost: 0`.

## 1. Evidence requirements for a positive (every column must be satisfied)

A positive entitlement row requires **all** of: exact country; exact source and source package ID; exact offer class and provider/package identity; exact variant; explicit validity period with observed_at/fresh_until; independently reviewed first-party evidence bound to that exact scope (not a source-reported echo, not an old screenshot, not JustWatch self-confirmation); named reviewer with provenance reference; exact change record and per-mapping rollback. Entitlement and playback are distinct gates and are currently `untested` for all candidates. Source code enforces this at `backend/tracker/services/entitlement.py:124-189` and `backend/tracker/services/entitlement_reads.py:96-136` (a positive needs `evidence_state == "verified"` plus a matching reviewed `CoverageRule`; no reviewed DB rows are seeded by any migration, so default is `UNKNOWN`).

| Column | Meaning / gate |
| --- | --- |
| Country | Exact market (e.g. NL), with explicit territory basis; language/EUR/route slug alone insufficient. |
| Provider | Exact canonical provider slug with independently reviewed identity binding. |
| Package | Exact source + `source_package_id` + offer class; unresolved packages stay UNKNOWN. |
| Variant | Exact variant id/edition; one title-page observation cannot verify multiple variants. |
| Validity period | `starts_at`/`ends_at` and `observed_at`/`fresh_until` inside the reviewed window; half-open boundaries; stale/expired = UNKNOWN. |
| Evidence | Independent first-party, title-scoped, contemporaneous; `SAME_SOURCE_ECHO` and `MEASURED_AGAINST_UNVERIFIED_SOURCE` are disqualifying. |
| Reviewer | Named independent reviewer; negatives and disputed mappings need a second reviewer. |
| Provenance | Stable `provenance_reference` to retained source digest, capture time, reviewer and custody reference. |
| Plan / channel | Exact reviewed `EntitlementPlan` (or parent host + `EntitlementChannel`) matching the viewer's declared selection. |
| Entitlement / playback | Authorized account-specific assertion (`entitlement`) and separate full-feature playback test; both currently `untested`. |

## 2. Inventory of existing independently reviewed provider/package evidence

| # | Country | Provider | Package / class | Variant | Validity period | Evidence type & provenance | Reviewer | Establishes | Meets every requirement? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | NL | netflix | Inception Netflix subscription | not verified | Nov 23 2026 capture vs Nov 24 2026 canary | Stage A canary `JW-CANARY-v1b`, `jw-canary-2026-11-24-evidence/README.md` + `manifest.json` (`UNLICENSED`, `SAME_SOURCE_ECHO`, `MEASURED_AGAINST_UNVERIFIED_SOURCE`) | Stage A package reviewer; ChatGPT Web final review owner (PR #197) | 56 source-reported offers, identity inferred by title/year/media type; no independent adjudication | **NO** |
| 2 | NL | netflix / prime / rakuten_tv | 7 historical advertised pairs (Inception, Dune) | not verified | `temporal_eligible=no`; 23h50m–24h24m apart | `docs/audits/stage2b/issue97-independent-verification-report.md`; `issue97-reconciliation-matrix-template.csv` | Independent Stage A/C reconciliation reviewer | Historical advertised-pair matches; all `independently_adjudicated=no`, `disposition=outside_reviewed_window` | **NO** |
| 3 | NL | netflix, prime, max_amazon, rakuten_tv | 15 accepted observations (`ADVERTISED_ONLY`) | not verified | Nov 23 2026 reference hour | `docs/audits/stage2b/issue97-accepted-observations.csv` | Stage C evidence reviewers | Catalogue presence + advertised availability only; `entitlement=untested`, `playback=untested`; some alias/year conflicts | **NO** |
| 4 | NL | prime / max_amazon etc. | NL provider and class decisions (14 labels, 19 label/class combos) | variant-level not verified | pinned pilot only | `docs/audits/stage2b/issue97-provider-mapping-decisions.md` | Independent Stage 2b provider-mapping reviewer | Identity + offer class; file explicitly says no mapping updates providers/entitlements/persisted offers | **NO** (identity only) |
| 5 | NL | netflix, prime, rakuten_tv | Stage E Batch A candidates E01–E09 | explicitly unscored | proposed 30-min UTC slots, unissued | `docs/audits/issue97/stage-e-evidence-protocol.md`, `stage-e-source-review.md`, `stage-e-candidate-matrix.csv` | Stage E source reviewer / owner gate | 0 fresh independently adjudicated pairs; source decisions 0/3; Batch A BLOCKED, 0 authorized sends; all entitlement/playback `untested` | **NO** |
| 6 | NL | netflix, prime, rakuten_tv | Stage E Batch B candidates E10–E15 | explicitly unscored | not scheduled | same Stage E package | same | Requires separate Batch B decision; not collected | **NO** |
| 7 | — | — | JustWatch technicalName → canonical slug identity table | — | — | `backend/availability/sources/justwatch_providers.py:1-28` | Codebase authors; live-endpoint identity confirmation only | Provider identity, explicitly not entitlement; unmapped packages skipped | **NO** |

## 3. Activation B candidate verdict (Task 7 / Task 8)

**No mapping in the inventory satisfies every requirement.** In particular:

- Row 1 is a `SAME_SOURCE_ECHO` measured against an unverified source and carries `UNLICENSED`; it cannot support a positive.
- Row 2's seven matches are outside the reviewed window and explicitly not independently adjudicated.
- Row 3's 15 accepted observations are advertised-only with entitlement and playback untested; several carry identity/alias conflicts (`YEAR_MISMATCH`, `TITLE_ALIAS_NOT_ALLOWLISTED`, `AMBIGUOUS_OFFER_CLASS`).
- Row 4 resolves identity and offer class, not plan/title coverage or account entitlement.
- Rows 5–6 are a blocked protocol with zero fresh observations and no issued execution window.

Therefore no `ReviewedEntitlementPackage` / `EntitlementCoverage` row may be created for any of these on the strength of this evidence. All remain **UNKNOWN** at runtime. No positive may be derived from assumptions, old screenshots, or JustWatch self-confirmation.

## 4. Exact change and rollback template for a future eligible mapping

This template is **not executed**; it is the narrowly scoped change shape required once real evidence exists.

| Field | Future value |
| --- | --- |
| Exact change | Insert ONE `ReviewedEntitlementPackage` row for `(source, country, source_package_id, offer_type)` and ONE `EntitlementCoverage` row keyed by exact `offer_identity`, `variant_id`, `offer_country`, `effect`, `provenance_reference`, `starts_at`, `revision`, plus the exact `EntitlementPlan` (or parent host + `EntitlementChannel`) for the reviewed market. |
| Scope guard | Focused isolated test asserting only that exact tuple can become positive; every other provider/package/plan tuple stays `UNKNOWN`. |
| Rollback | Delete/deactivate **only** those inserted rows for that tuple (and any newly created plan/channel rows used solely by it); re-run `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures -v 2` and the read-only zero/UNKNOWN checks; confirm the tuple returns to `UNKNOWN`. No global enable, no other mapping touched, no production migration. |
| Approval | Separate owner decision per tuple, explicitly referencing the independent reviewer and provenance reference. Activation A approval is not sufficient. |

## 5. Forbidden-operations statement

No mapping activation, production deployment/migration/config change, live provider/first-party/TMDb/playback request, HMAC grant, quota or permission change, secret exposure, issue closure, or cleanup was performed.
