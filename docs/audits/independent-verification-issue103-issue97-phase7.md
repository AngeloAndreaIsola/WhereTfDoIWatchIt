# Independent verification — issue #103 Activation / unblock #97 (Phase 7)

**Role:** independent reviewer (dispatched Orca worker), not any implementation agent.
**Mission:** owner-approved `/orchestration` mission: Execute #103 Activation and Unblock #97.
**Scope:** independent verification only of draft PRs #234, #235, #236, #237; one new audit document; two issue handoffs. No gate is opened by this review.

## 0. Model / provider / effort / cost record (own in-session probe)

| Field | Recorded value |
| --- | --- |
| Preferred route (mission) | `opencode/deepseek-v4.1-flash-free` — probed twice by coordinator with no response / timeout; not used here |
| Specified fallback | `omniroute/ag-gemini-3.8-flash-tiered` — coordinator reported repeated 503 `ALL_TARGETS_SKIPPED`; not used |
| Verified fallback used | `opencode/hy4-flash-free` (native OpenCode CLI) |
| Requested model (this session) | `opencode/hy4-flash-free` |
| Actual self-report (this session) | `MODEL_IDENTITY_OK opencode/hy4-flash-free` — requested = actual, no silent substitution |
| Probe command | `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` |
| Provider | OpenCode (native CLI), session id `ses_de1476e5fffe1NH3Tu8zJL86VR` |
| Effort | UNPINNED — no effort flag supplied; no HIGH/XHIGH claim |
| Cost | JSON telemetry `cost: 0` on `step_finish` (input 64,534 / output 15 / reasoning 35 tokens) |
| Forbidden routes | No Sol, no Astra, no paid escalation, no silent substitution |

Fallback reasons recorded verbatim from the mission: preferred route timed out twice; specified fallback failed repeatedly with 503 `ALL_TARGETS_SKIPPED`; native `opencode/hy4-flash-free` was verified by a fresh coordinator probe (`MODEL_IDENTITY_OK opencode/hy4-flash-free`, cost 0, effort UNPINNED) and independently re-probed here with the same result.

## 1. Exact base / head / hosted CI (authoritative GitHub metadata; re-fetched)

Common base re-fetched from `origin`: `6af4cd8421ec9c85e4fff724fded60d5cce86c85`
(`Merge PR #233: #97 offline prerequisites dossier`, 2026-11-27 20:46:53 +0100). All four PRs use this exact base per `gh api …/pulls/<n>` `base.sha`.

| PR | Expected head | Actual head (`gh api` + `git rev-parse origin/pr/<n>`) | Match | Hosted CI (check-runs on exact head) | Jobs |
| --- | --- | --- | --- | --- | --- |
| #234 | `524ae05d5fbc1ed85c7761b5e14dc3533474a99c` | `524ae05d5fbc1ed85c7761b5e14dc3533474a99c` | yes | [run 46062151975](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46062151975) | Backend Tests SUCCESS; Frontend Lint, Build & Test SUCCESS; Browser Extension Build, Typecheck & Tests SUCCESS (3/3) |
| #235 | `ad58af9aa908ba2929ef64cd0aede09a64b1871c` | `ad58af9aa908ba2929ef64cd0aede09a64b1871c` | yes | [run 46062730381](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46062730381) | Frontend Lint, Build & Test SUCCESS; Backend Tests SUCCESS; Browser Extension Build, Typecheck & Tests SUCCESS (3/3) |
| #236 | `33a0a2c476c83e2461f64817f36c3004ec75b0aa` | `33a0a2c476c83e2461f64817f36c3004ec75b0aa` | yes | [run 46063472347](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46063472347) | Browser Extension Build, Typecheck & Tests SUCCESS; Frontend Lint, Build & Test SUCCESS; Backend Tests SUCCESS (3/3) |
| #237 | `5560b20fa59b463305f68fdade0fcf1ace46016f` | `5560b20fa59b463305f68fdade0fcf1ace46016f` | yes | [run 46063400928](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46063400928) | Frontend Lint, Build & Test SUCCESS; Browser Extension Build, Typecheck & Tests SUCCESS; Backend Tests SUCCESS (3/3) |

Verification commands:

```sh
git fetch origin '+refs/pull/234/head:refs/remotes/origin/pr/234' … '+refs/pull/237/head:…'
git rev-parse origin/pr/234 origin/pr/235 origin/pr/236 origin/pr/237
gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/pulls/<n> --jq '{base:.base.sha,head:.head.sha,draft:.draft}'
gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/commits/<head>/check-runs --jq '.check_runs[]|{name,status,conclusion,html_url}'
```

All four PRs remain OPEN drafts. Issue handoff comments on #103/#97 cite the correct current heads and CI runs (comments `7227368798`, `7227560418`, `7227457240`, `7227481808`).

## 2. Independent verification by claim

### Production target identity and absence of inferred access (#234)

- `docs/DEPLOYMENT.md` states verbatim: "does **not** contain evidence of a deployed VPS, reverse proxy, authentication service, or production database" and that ports bind `127.0.0.1`; publishing is a separate design needing auth boundary, TLS, host allow-list, non-placeholder `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=0`. `docker-compose.yml` publishes loopback ports only. `ops/deployment/compose.release.json` requires operator-supplied placeholders. Therefore Gate `103-DEPLOY BLOCKED_ON_ACCESS` with host/DB/version/migration/revision UNKNOWN is correct; no access was inferred.
- `gh api …/issues/comments/7224557483` confirms owner decision: "APPROVED: the published #103 Activation A user-facing wording. This is wording sign-off only"; Activation B DISABLED; #97 Stage D/E BLOCKED; production deployment/cutover/migration NOT AUTHORIZED. Comment `7224642213` reiterates. No gate authorization creep found in the substance of the reports.

### Backup integrity and restore feasibility (#234)

- `docs/audits/issue103-production-deployment-approval-package.md` §3 requires `to_regclass` table validation, exact `$BACKUP_PATH` (`pg_dump -Fc`), SHA-256 recorded off-repo, `pg_restore --list`, and isolated restore verification before cutover; §4 mandates coordinated stop-writers and explicitly rejects rolling/old-writer compatibility; §6 leaves numeric downtime UNKNOWN. The tracked `backups/tracker_backup_20260926_140128.sql` has independent SHA-256 `693f7b40976b71cf7e19a4d01d69f9e5b8209f94083db5efac2b9cfa33a6d2a0` and belongs to the divergent 0033 lineage — consistent with the report's "never identify backups by filename alone."
- Historical restore evidence (`docs/audits/MIGRATION_0033_INCIDENT_2026-11-17.md`) is correctly scoped as local/disposable and not as current production restore proof. No claim of a verified production restore was accepted. Correct.

### Stop-writers completeness and rejection of rolling/old-writer compatibility (#234)

- `guarded_migrate.py` requires `--confirm-target=<name>@<host>` against `--expected-database`/`--expected-host`; `ops/deployment/release.py` asserts `--quiescent` equals exact `<project>:<database>@<host>` for maintenance gates. The report enumerates backend replicas, title/catalogue workers, all cron slots, manual writers and ingress removal, and rejects rolling/old-writer compatibility citing availability `0018` dropping its temporary default. This matches the merged approval package and no claim of rolling safety exists. Correct; exact replica counts/in-flight durations honestly UNKNOWN.

### Migration compatibility (#234 / #236)

- `git grep` on `backend/tracker/migrations/0064_market_entitlements.py` at base shows table creation for `ReviewedEntitlementPackage`/`EntitlementCoverage` with **no `RunPython` seed**; `entitlement_reads.py:119` computes `source_supported = package is not None`; `justwatch_providers.py` declares an identity-only static table and skips unmapped packages. These independently confirm "zero reviewed rows by default" and "no Activation B activation in migration." The stop-writers rationale (0018 default drop, 0020 depending on 0018/0019) matches the approval package; no isolated downgrade claimed safe. Correct.

### Activation A fail-closed behavior and approved wording (#236)

- Owner approval is verified via comments `7224557483`, `7222192511` (16-row table in §4), `7224642213` — exact wording sign-off only. Independent command checks at base confirm representative approved strings exist verbatim (`git grep -F` for "Included with your selected plan", "Requires channel add-on", "Plan access unconfirmed", "Blocked for your selection", "unsupported in our catalogue" at `frontend/components/MarketPlanSelector.tsx:186,223`).
- Reproduced critical fail-closed corpus on disposable PostgreSQL at exact base:
  `./scripts/test-backend-isolated.sh tracker.tests.test_entitlement_exact tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration tracker.tests.test_b1_regression tracker.tests.test_surface_country_agreement -v 1` → **Ran 63 tests … OK**.
  Legacy surfaces: `./scripts/test-backend-isolated.sh tracker.tests.test_availability_classification tracker.tests.test_availability_read_boundary tracker.tests.test_detail_availability_state tracker.tests.test_issue97_availability_contract -v 1` → **Ran 62 tests … OK**.
  (An initial run in my workspace checkout at `8985946` failed with `ModuleNotFoundError: tracker.tests.test_issue103_api` because that pre-base revision lacks those tests; re-run from a worktree at exact base `6af4cd8` reproduced the claimed results. This is a workspace-state artifact, not a finding against the PR.)
- B1 "expected failures" are transparently described as pre-existing desired-but-unimplemented B2 targets, not regressions. Correct.

### Activation B evidence provenance and all mappings UNKNOWN (#236)

- Evidence inventory cites exact retained sources and reviewers; the matrix verdict "ZERO mappings eligible" is consistent with the Stage E package: 7 historical matches are `temporal_eligible=no`/`independently_adjudicated=no`, 15 accepted observations are `ADVERTISED_ONLY` with entitlement/playback untested, identity/offer-class mapping explicitly does not update entitlements, and Stage E has 0 fresh adjudicated pairs with source decisions 0/3. With no reviewed DB rows seeded, runtime positives are impossible. No assertion taken as evidence without a cited command/output or primary source. Correct.

### Stage D runtime receipt/quota validity, HMAC boundaries, manifest digest and request ceilings (#237)

- `backend/availability/services/issue97_stage_d_policy.py:17` pins `MANIFEST_SHA256 = d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`; independent recomputation of the canonical JSON digest of `issue97_stage_d_titles.json` using the runner's canonicalization reproduced **exactly** `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`.
- `check_authorization` (`issue97_stage_d_policy.py:147-165`) requires a ≥32-byte key, HMAC-SHA256 over the canonical payload, `compare_digest`, pops `expires_at`, enforces canonical digest equality with expected scope and `now < expiry <= evidence_expiry`; scope includes database identity, run id, manifest digest, ordered scope ids, document digest, attempt ceilings/retries, checkpoint and evidence/receipt digests. This matches the report's boundary claims. I could not recompute `document_sha256` from source without Django/`requests` installed in this checkout; the code cross-reference (`issue97_stage_d_policy.py:35` verifies `sha256(SEARCH_TITLES_QUERY) == policy["document_sha256"]`) and the passing standalone suite are the retained evidence. Ceilings (`maximum_attempts=12`, batch=2, retries=0, in-flight=1) match pin.
- Reproduced locally: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v` → **7 tests, 6 passed, 1 skipped (raw TMDb opt-in), 0 failed**. The receipt/quota/exclusive-grant/retained-target/HMAC-token blockers are all correctly assigned `BLOCKED_ON_SPECIFIC_EVIDENCE` / `BLOCKED_ON_ACCESS/EVIDENCE`; disposable fingerprint is correctly marked non-transferable; zero live sends. Correct.

### Stage E source-access conditions, evidence custody and accuracy-denominator correctness (#235)

- Frozen matrix digest independently reproduced: `shasum -a 256 docs/audits/issue97/stage-e-candidate-matrix.csv` → `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d`.
- `python3 -BE scripts/audit_issue97_stage_e.py` → CSV PASS 15 candidates (A=9, B=6), 13 accepted observations, 7 Stage C pairs, 2 controls, 8 documents; 0 fresh adjudicated pairs; accuracy UNDEFINED; all digests match the report. `python3 -BE -m unittest -q scripts.test_audit_issue97_stage_e` → **Ran 5 tests — OK**. `sh -n scripts/rehearse_issue97_stage_e.sh` → exit 0. `backend/availability/tests/test_issue97_stage_e_adjudication.py` contains 27 test methods, consistent with the named denominator-exclusion tests. The accuracy denominator is correctly UNDEFINED, and invalid observations (login wall, shell, 404, stale, out-of-window, wrong scope/territory, missing controls) are excluded from TP/FP/FN/TN. Source decisions 0/3 → `BLOCKED_ON_ACCESS`; custody is restricted off-repo (0700/0600), no raw bytes in Git. Correct.
- Note: the 64-test mocked rehearsal (8.717 s) and the terminal-metadata custody check were not re-executed here (no live send attempted); they are retained primary-source claims from the report, corroborated by the 5 passing local unit tests and the frozen-digest reproductions.

## 3. Findings requiring correction (not gates)

1. **#235 stale current-SHA self-reference and stale CI link (documentation accuracy).** The report §14 names head `255e39c2d050b1f7bd735f5df0cf6e2cf81be75a` and CI run `46062258574` as "at verification"/current, while the authoritative head is `ad58af9aa908ba2929ef64cd0aede09a64b1871c` with run `46062730381`. The PR body likewise states head `255e39c2…`. Issue handoff `7227457240` is correct. This is the same class of defect previously cited in PR #228 review (current-SHA self-reference); it must be corrected to avoid reviewing a non-final artifact. Does not change the gate verdict.
2. **#235 wrong preferred-route identifier.** Report §0 names the preferred route as `omniroute/ag-gemini-3.5-flash-lite`, but the mission's preferred route is `opencode/deepseek-v4.1-flash-free` and specified fallback is `omniroute/ag-gemini-3.8-flash-tiered`. Correct the route record; no substitution occurred in practice (actual probe is `opencode/hy4-flash-free`).
3. **#235 unsupported certainty about a CI link in the PR body.** The body says "Hosted CI link: to be appended in the issue #97 handoff after PR creation," while §14 records a run; reconcile to the exact-head run `46062730381`.
4. **#236 stale head in PR body.** Body §Head states `10cc1308970fde00c2ff9dce62ac3a74be783be8` ("first head"); authoritative final head is `33a0a2c476c83e2461f64817f36c3004ec75b0aa`. The report correctly cites the final head's CI in prose without printing the SHA; add the exact final head to the report and correct the body's head field.
5. **#237 stale head in PR body.** Body states head `ddcdd184edbf06cf899e6893cf6a4b31491b8bf9`; authoritative final head is `5560b20fa59b463305f68fdade0fcf1ace46016f`. Report §12 says exact head is "recorded in issue #97 handoff and `gh pr view 237`" rather than inline; add it inline.

None of these findings authorize or de-authorize any gate; they are documentation-accuracy corrections only.

## 4. Final gate status table (independent verdicts)

| PR | Mission gate | Independent verdict | Exact findings |
| --- | --- | --- | --- |
| #234 | 103-DEPLOY `BLOCKED_ON_ACCESS` | **READY_FOR_CHATGPT_REVIEW** | Correct gate; no production target inferred; backup/restore scoped to historical disposable only; stop-writers complete and rolling/old-writer compatibility explicitly rejected; exact base/head/CI verified. |
| #235 | 97-E `BLOCKED_ON_ACCESS` | **REQUEST_CHANGES** | Gate and evidence accuracy correct (0/3 source decisions, UNDEFINED denominator), but stale head/CI self-reference (§14 + PR body) and wrong preferred-route identifier in §0 must be corrected. |
| #236 | Activation A wording-only approval; Activation B disabled | **REQUEST_CHANGES** | All substantive findings reproduced (63+62 tests OK at exact base; 16 wording strings; zero Activation B rows; all mappings UNKNOWN). Correct PR body head field and add exact final head inline to report. |
| #237 | 97-D `BLOCKED_ON_SPECIFIC_EVIDENCE` | **REQUEST_CHANGES** | Gate correct; manifest digest reproduced exactly; HMAC/ceiling boundaries verified; 7/6/1 manifest suite reproduced. Correct PR body head field and add exact final head inline. |

No PR is `BLOCKED_ON_SPECIFIC_EVIDENCE` as an independent verdict: the specific evidence blocking the *live* gates is precisely what each report correctly declares, and the deliverables themselves are complete apart from the documentation corrections above.

## 5. Forbidden-operation statement

No production deployment, migration, config change or production DB write; no live provider/TMDb/first-party/playback request; no HMAC grant; no quota/runtime permission change; no credential or raw-evidence exposure; no mapping activation; no issue closure; no branch/PR/worktree cleanup of others' work; no merge. No approval for one gate was treated as authorization for another. All verification commands were local/offline, used disposable PostgreSQL or read-only GitHub API/retained sources, and the only writes were this audit document, the draft PR and the two handoff comments. #103 and #97 remain open; all four reviewed PRs remain open drafts.

## 6. ChatGPT Web review request

ChatGPT Web independent review is explicitly requested for all four draft PRs (#234, #235, #236, #237) and for this independent audit, with the corrections in §3 applied before final review. No gate may be opened, no issue closed and no PR merged on the strength of this document alone.

## 7. Addendum — focused re-review of documentation corrections (§3)

Scope: correction verification only. Gate conclusions in §4 are unchanged; the verdicts below are **correction-verification statuses**, not new gate decisions. No substantive review was repeated.

### 7.1 Freshly verified heads and hosted CI

All values re-read with `gh pr view`, `gh pr checks`, `gh api …/pulls/<n>` and `gh api …/actions/runs/<run>/jobs`; heads re-fetched via `refs/pull/<n>/head`.

| PR | Reviewed head (substantive) | Current corrected head (PR metadata) | Hosted CI on corrected head | Jobs | Correction handoff |
| --- | --- | --- | --- | --- | --- |
| #234 | `524ae05d5fbc1ed85c7761b5e14dc3533474a99c` | unchanged `524ae05d…` | [run 46062151975](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46062151975) | 3/3 SUCCESS | prior handoff comment `7227368798` |
| #235 | `ad58af9aa908ba2929ef64cd0aede09a64b1871c` | `f0b762fb4dc176ce6dc7daa4e76cf54ce3367e4b` | [run 46066247827](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46066247827) | Backend Tests SUCCESS; Frontend Lint, Build & Test SUCCESS; Browser Extension Build, Typecheck & Tests SUCCESS (3/3) | #97 comment [7227955064](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97#issuecomment-7227955064) |
| #236 | `33a0a2c476c83e2461f64817f36c3004ec75b0aa` | `f2edda937453271fe9143ea2f564741ff31cf12f` | [run 46066280562](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46066280562) | Browser Extension Build, Typecheck & Tests SUCCESS; Backend Tests SUCCESS; Frontend Lint, Build & Test SUCCESS (3/3) | #103 comment [7227952176](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103#issuecomment-7227952176) |
| #237 | `5560b20fa59b463305f68fdade0fcf1ace46016f` | `54ac141ea2677c4f1847ff65bb0548571d06fdbb` | [run 46066271305](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46066271305) | Backend Tests SUCCESS; Frontend Lint, Build & Test SUCCESS; Browser Extension Build, Typecheck & Tests SUCCESS (3/3) | #97 comment [7227941918](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97#issuecomment-7227941918) |

All four PRs remain OPEN drafts on the exact base `6af4cd8421ec9c85e4fff724fded60d5cce86c85`.

### 7.2 Correction checks

- **#235:** report §0 now names preferred `opencode/deepseek-v4.1-flash-free`, fallback `omniroute/ag-gemini-3.8-flash-tiered`, and explicitly states `ag-gemini-3.5-flash-lite` was not the preferred route for this review task. §14 now names reviewed head `ad58af9…` and run `46062730381` (3/3 SUCCESS) with "PR metadata authoritative" phrasing; `255e39c2…`, run `46062258574` and "CI link to be appended" are gone from the report and PR body (`git grep` over `origin/pr/235` changed files). PR body contains exact CI link `46066247827` with all three job conclusions.
- **#236:** PR body `Reviewed head:` field names `33a0a2c…` with "GitHub PR metadata remains authoritative for the current head"; report line 9 has inline `| Reviewed head | 33a0a2c… |` (same metadata-authoritative phrasing); stale `10cc1308…` and its CI self-reference are gone from the changed files.
- **#237:** report line 6 now states inline "The reviewed head for this correction is `5560b20…`; PR metadata remains authoritative for the current head"; the stale `ddcdd184…` self-reference and "Exact final head and hosted run are recorded in issue #97 handoff" are gone. PR body names the current corrected head `54ac141e…` and explicitly delegates authority to PR metadata.
- **#234:** unchanged at `524ae05…`; prior READY verdict stands.

Commands used (results above): `gh pr view <n> --json state,isDraft,baseRefOid,headRefOid`, `gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/pulls/<n>`, `gh api …/actions/runs/<run>/jobs`, `gh api …/commits/<head>/check-runs`, `git fetch origin '+refs/pull/<n>/head:refs/remotes/origin/pr/<n>'`, `git rev-parse origin/pr/<n>`, `git show origin/pr/<n>:<report> | grep -n …`, `git grep -n … origin/pr/<n> -- docs/audits`.

### 7.3 Correction-verification verdicts

| PR | Prior verdict | Correction-verification status |
| --- | --- | --- |
| #234 | READY_FOR_CHATGPT_REVIEW | **READY_FOR_CHATGPT_REVIEW** (unchanged) |
| #235 | REQUEST_CHANGES | **READY_FOR_CHATGPT_REVIEW** — all three §3 corrections applied and verified |
| #236 | REQUEST_CHANGES | **READY_FOR_CHATGPT_REVIEW** — PR body and inline report field corrected |
| #237 | REQUEST_CHANGES | **READY_FOR_CHATGPT_REVIEW** — PR body and inline report field corrected |

Gate status table in §4 is intentionally not modified; only the correction-verification statuses above are new.

### 7.4 Route record for this addendum

Own fresh probe: `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` → `MODEL_IDENTITY_OK opencode/hy4-flash-free`, session `ses_de13351d2ffe2Ty5r7dnVzDXY9`, provider OpenCode, effort UNPINNED, JSON `cost: 0` (input 64,576 / output 15 / reasoning 50). Preferred route `opencode/deepseek-v4.1-flash-free` timed out twice and specified fallback `omniroute/ag-gemini-3.8-flash-tiered` failed with 503 `ALL_TARGETS_SKIPPED`; verified fallback used. No silent substitution, Sol, Astra or paid escalation.

### 7.5 Forbidden operations (addendum)

No merge, deployment, live request, HMAC grant, quota/runtime permission change, mapping activation, gate opening, issue closure or cleanup was performed. All checks used read-only GitHub API and local `git show`/`git grep`; the only writes are this addendum, its draft-PR push and the two updated handoff comments. #103 and #97 remain open; all reviewed PRs remain open drafts. Explicit ChatGPT Web final review requested.
