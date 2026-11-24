# Issue #97 — Swarm integration and final-review dossier

**Verdict: BLOCKED — no merge authorized.** This is an evidence handoff prepared for
ChatGPT Web, the sole final PR reviewer/merger. Hermes/Orca prepared and verified
evidence only; no PR was approved, merged, closed, pushed, deployed, or granted new
runtime authority. #97 stays OPEN; #103 stays ON HOLD.

## Agent execution summary (verified SHAs)

| Agent | Deliverable | Verdict | Artifact |
|---|---|---|---|
| A1 Security | Read-only audit of all 20 raw canary/spec paths plus 12-file #197 package and both #199 files | `REMEDIATION_REQUIRED` | `/var/folders/dg/jx2907jd1cx9q2dhyljb8fxm0000gn/T/orca-issue97-security-843umsjp/security-audit-2026-11-24.md` |
| A2 Code | All 15 files of #198; 3,787 backend + 167 targeted + 10 independent tests | `REQUEST_CHANGES` | `/tmp/wtdw97-review-a2.81jUwR/pr198-review.md` |
| A3 Evidence | Stage A/C reconciliation; stacked draft PR #200 | Counts: 7 / 7 / 0 / 0 | PR #200 report + matrix at `ed1bea4` |
| A4 Stage D | 13-title NL readiness package, zero live calls | `BLOCKED` | `docs/audits/issue97/stage-d-readiness.md` at `89ccb25` |
| A5 Integration | Delegated run failed (unsupported Antigravity runtime); compiled by orchestrator from the four reports | This document | `docs/audits/issue97-swarm-a5-integration-report.md` |

Verified commits: master `ff150586319e89b8ce4f8dfde9403a33eceea1d1`; #197 `0ee004e3bafcc18dcffb359cb98fcf01dde93ef5` (base `c2ead76`); #198 `ddba3f0e9c8cb91a4413321b62a2cd3d1e4c8973` (base `c2ead76`); #199 `29e15ff54d5e34f60d50397f5ecddbc9b5c57f18` (base `ff15058`); #200 `ed1bea4c7d1f75976644c70c3511b9cb89308c94` (base `docs/issue97-stagec-independent-verification`, stacked on #199). All four PRs OPEN and DRAFT. #97 OPEN, #103 OPEN. Hosted CI: three jobs per PR fail in 2–3 s on billing/spending-limit checks before any test step — a billing blocker, not a pass or a code failure.

## Orchestrator's independent cross-checks

1. The presigned URL in `runs/jw-canary-2026-11-24/function_show_redeploy.json` encodes its query separators as `\u0026`, so naive parsing sees only one parameter. Decoded: eight SigV4 parameters including Credential, Date, Expires, Security-Token, Signature. Signed 15:35:42Z, 3,600 s lifetime, expiry 16:35:42Z; commit timestamp 16:38:48Z; audit ~17:23Z. Expired before commit. No URL fetched, no value printed.
2. The #199 matrix at `29e15ff` had a 20-field header and 21-field rows plus a UUID-shaped provider token in lines 3–6. PR #200 at `ed1bea4` fixed the width to a uniform 17 fields, but the UUID-shaped token is still present (now field 10) and matches a token in raw `offers.json` (intersection count 1). A1's matrix-sanitization finding is therefore NOT resolved by #200.
3. Stage D CSV independently recomputed: 13 rows; 11 `VERIFIED_EXACT`, 1 `VERIFIED_REVIEWED_ALIAS`, 1 `BLOCKED_YEAR_MISMATCH`; 8 movie / 5 series; 13 `GetSearchTitles`; 5 `GetTitleNode`; all NL. Matches A4.
4. All five worktrees clean after the interrupted duplicate agents; no PR head moved.

## Integration verdict table

| Item | Required result |
|---|---|
| Security | `REMEDIATION_REQUIRED`. Expired SigV4 URL, raw provider rows, unpublished function metadata, operational IDs and a provider UUID still carried by #200's matrix. No active credential identified; no history rewrite or revocation justified solely by the expired URL. |
| PR #197 | `COMMENT` — hold. Sanitized 12-file package excludes raw offers on its own tip, but adds no deletion of the 20 raw paths on master; README calls the unpublished function ID public without evidence; full source retention unresolved. |
| PR #198 | `REQUEST_CHANGES` — hold. Three PR correctness findings (two P1, one P2) plus one P2 season identity defect reproduced on master. Migration `0018` is safe on PostgreSQL 16 with ID/timestamp preservation; temporary DB default needs a coordinated cutover if old writers overlap. |
| PR #199 | `REQUEST_CHANGES` — hold. Inherits all 20 raw paths byte-for-byte from `ff15058` and adds the provider identifier. Correctly reports zero temporally eligible comparisons; does not claim accuracy. Superseded in content by the stacked #200 but its branch is still the base. |
| PR #200 | `REQUEST_CHANGES` — hold. Improvements: acknowledges #197, records 7/7 factual matches with 0 temporally eligible and undefined accuracy metrics, fixes matrix width. Defects: raw master base unchanged; matrix still carries the provider UUID flagged by A1; overall verifier exit 1 for missing `download_redeploy.log`. |
| Stage D | `BLOCKED`. 13-title manifest prepared (12 admitted scopes, 5 optional season calls, Coherence held), but expanded runner/signed manifest unimplemented; no current permission, quota/balance, rate-policy evidence or target preflight/HMAC; historical internal JustWatch HTTP count `UNKNOWN`. |
| Stage E | `BLOCKED`. No authorized contemporaneous independent evidence exists; no reviewed comparison window, so no offer-level accuracy adjudication can be performed. |
| Issue #97 | `PARTIAL` overall; accuracy metrics `BLOCKED` / `NOT PROVEN`. Terminal checklist state remains `BLOCKED_ON_SPECIFIC_ACCEPTANCE_GATE`: 0 eligible denominators, no entitlement/playback evidence, hosted CI blocked. |
| Merge order | No merge authorized yet. Preconditions: owner-authorized tip cleanup of master's 20 raw paths; sanitize the #200 matrix token; fix #198 findings 1–3 and rerun; resolve CI billing; then ChatGPT Web review in order #199 → #200 → #198 → #197. |
| Outstanding risks | P0 owner: raw evidence on master and unresolved redistribution authority. P1 PR author: #198 variant-expiry collapse and contradictory replay edge. P1 owner: no fresh independent evidence window. P2 PR author: partial-edge variant loss. P2 owner/author: master-present season ID mismatch. P2 owner: hosted CI billing. |

## Contradictions and reconciliations

- A1 said #199's matrix had a 20/21-field width problem and a provider UUID; A3's #200 fixed the width. Reconciled: width fixed, UUID not fixed (verified independently).
- A2's finding 4 is on master, not introduced by #198. Reconciled: #198 is still `REQUEST_CHANGES` for its three own findings; the season defect is a separate follow-up that must not be credited to the PR.
- A3 reported 7 "historical matches" while maintaining 0 eligible comparisons. Reconciled: factual-level matches only, with inferred TMDb identity; no accuracy classification, no agreement/conflict/FP/FN counts.
- PR #198's reported 3,787 tests passing is corroborated by A2, but its 10 independent tests produced 5 failures. Local pass does not substitute for independent review or hosted CI.

## Dependency order

1. Owner authorization for a narrow tip cleanup of `ff15058` (20 raw paths). A history rewrite is a separate decision; a tip cleanup leaves old Git objects and existing copies.
2. Sanitize the provider token in PR #200's matrix; correct the unsupported "public ID" claim in #197; decide whether to retain full function source in-repo.
3. PR author fixes #198 findings 1–3 and reruns the named regressions; season defect tracked separately.
4. Hosted CI billing resolved and a real pass obtained; local PASS remains distinct.
5. Stacked docs review: #199 then #200, then code #198, then evidence #197. Rebases onto the cleaned master must be reviewed, not performed here.
6. Only after fresh, authorized, contemporaneous independent evidence exists can Stage E be attempted; Stage D needs its own expanded policy and permission evidence first.

## Exact next action

Obtain owner approval for the narrow tip cleanup and for sanitizing the #200 matrix token; send PR #198 back to its author with the four named regressions; keep all PRs draft. Prepare the handoff with PR URLs, issue, base/head/SHA, changes, local tests, repro steps, artifacts and risks, then mark `READY_FOR_CHATGPT_REVIEW` only after those gates hold. Until then: `BLOCKED`.

## Approvals needed from owner

- Narrow tip cleanup of master's 20 raw evidence paths, and a separate decision on history rewrite.
- Confirmation of redistribution authority for provider-derived rows and function source.
- New signed authorization, quota and rate evidence plus Coherence year decision before any Stage D execution; no live collection is authorized by this report.
- Resolution of GitHub billing/spending limits so hosted CI can actually run.

## Correction — independent A5 review supersedes two calls above

The dedicated Orca integration worker completed the assigned A5 task at
`docs/audits/issue97/swarm-integration-final-review.md` (uncommitted, same worktree).
It independently verified all four reports against pinned Git/GitHub state and
explicitly reviewed this fallback dossier. Two of my earlier calls were wrong and are
superseded:

1. **PR #197 verdict: `REQUEST_CHANGES`, not `COMMENT`.** It must correct the
   function-ID and source-retention claims, qualify replay/request counts, and be
   reviewed on the cleaned base.
2. **Merge order: authorized tip cleanup → #197 → #199 → #200**, with #198 landing
   in parallel once fixed and independently retested. My earlier order
   (#199 → #200 → #198 → #197) was wrong: #200 depends on #197's corrected evidence
   and custody, not the reverse. Use forward commits, not force-push rebases; no
   split is needed inside the evidence stack.

Everything else in this dossier — security remediation, the four PR #198 findings,
Stage D/Stage E `BLOCKED`, 7/7 factual matches with 0 eligible and 0 adjudicated
comparisons, and the owner approvals — is consistent with the independent A5 report.
That report also adds a #199/#200 discrepancy: #199 misstates its base as `c2ead76`
(actual parent `ff15058`) and claims no canary output is available although raw
outputs are tracked on its parent.

**Final: BLOCKED — evidence package ready for ChatGPT Web review; no merge, close, deploy, live call or permission change performed.**
