# Issue #97 — Agent E independent exact-head verification of PR #207

**Agent:** Agent E (Hermes), model `hy4-flash:free`
**Subject:** PR #207 — `integration/97-phase3-final`
**Verified exact head:** `2ce0eb7d42068cf686d377271937d7bbe7cdf7f3`
**Base:** `54bd248060880523ce63701b46c031508f99338f`
**Tested application revision named by the integration report:** `9df0eae6ebe0ed88d9c7e2c3a808e3310b81bb72`
**PR state measured:** `open`, `draft=true` (GitHub API, this session)
**Result class:** all test results below are **LOCAL PASS**. They are not GitHub CI passes.

**Forbidden scope — nothing here does any of the following:** approve the exception, grant a CI waiver, approve or merge any PR, close #97, unhold #103, execute Stage D/E live collection, issue execution grants, write to production data, deploy, or rewrite Git history.

**Verdict vocabulary:** `PASS` | `PARTIAL` | `FAIL` | `BLOCKED` | `Not proven`. A value is "measured" only when a command in this session returned it.

---

## 1. Numbered acceptance items (each with its own measured status)

| # | Item | Measured result | Verdict |
|---:|---|---|---|
| 1 | Exact-head identity | Local worktree `HEAD` = `2ce0eb7…`; GitHub API `pulls/207.head.sha` = `2ce0eb7…`; base `54bd248…` | **PASS** |
| 2 | Tested revision vs published head | `git diff 9df0eae… 2ce0eb7…` = one file, `docs/audits/issue97/final-integration-report.md`, +189 lines; no executable change | **PASS** |
| 3 | Backend full suite, CI command | `Ran 3861 tests in 143.895s` / `OK`; rc=0 (Python 3.12.16, PostgreSQL 16.15, tmpfs, disposable DB guard, empty API keys) | **PASS** |
| 4 | Migration/model sync | `makemigrations --check --dry-run`: `No changes detected`; rc=0 | **PASS** |
| 5 | Standalone tracker suite | `Ran 2173 tests in 83.469s` / `OK`; rc=0 (overlaps item 3; not additive) | **PASS** |
| 6 | Frontend gates | `npm ci` rc=0; 47 files / 403 tests passed; `npm run lint` rc=0 with 2 pre-existing warnings; `npm run build` rc=0; Node 22.23.1 / npm 10.9.8 | **PASS** |
| 7 | Extension gates | `npm ci` rc=0; `typecheck` rc=0; 4 files / 34 tests passed; `build` rc=0; `validate:package` rc=0 | **PASS** |
| 8 | Migration graph + physical schema | Both orders applied `0017 → 0018 → 0019 → 0020` and `0017 → 0019 → 0018 → 0020`; each step rc=0; global `migrate` rc=0; `migrate --check` rc=0; `variants` JSONB NOT NULL column count=1; enabled `stage_d_ledger_immutable` triggers count=2 in both databases | **PASS** |
| 9 | Stage D offline manifest | `Ran 6 tests` / `OK (skipped=1)`; skip is the retained-raw-TMDb-evidence case | **PASS** |
| 10 | Stage E provenance audit | 15 candidates (A=9, B=6), 13 accepted observations, 7 Stage C pairs, 2 controls; 8 capture digests; four artifact SHA-256 values match the checked-in rehearsal document | **PASS** |
| 11 | Stage E fixture rehearsal | `Ran 60 tests in 5.212s` / `OK`; rc=0 on disposable internal-network PostgreSQL with mocked transports | **PASS** |
| 12 | Security / sanitization scan | 1,078 text files scanned; 0 AWS key IDs, 0 `X-Amz-*` parameters, 0 presigned URLs, 0 GitHub tokens, 0 Basic auth headers; 2 bearer-header literals in `extension/tests/handshake.test.ts` (test assertions, not live credentials); 0 raw-evidence-named paths | **PASS** |
| 13 | Repository hygiene | `git diff --check 54bd248… 2ce0eb7…` rc=0; `sh -n scripts/rehearse_issue97_stage_e.sh` OK | **PASS** |
| 14 | Hosted CI | 3 jobs (`Backend Tests`, `Frontend Lint, Build & Test`, `Browser Extension Build, Typecheck & Tests`), each `conclusion=failure`, each with **0 executed steps**; annotation on all three: *"The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings"* | **BLOCKED** — infrastructure, not a test failure; no CI pass exists |
| 15 | Fresh adjudicated accuracy pairs | 0; accuracy metrics **UNDEFINED**; no live provider observation in this verification | **Not proven** |
| 16 | Stage E live execution authorization | Checked-in authorization template is blank and unapproved; no execution grants, tokens or runtime permissions were touched | **BLOCKED** |
| 17 | Five source PRs | #198, #205, #192, #206, #204 are each still `open`, `draft=true`; no merge was performed; the retarget plan in §6 is unexecuted | **PASS** (state measured), no action taken |

Minor discrepancy, disclosed: the checked-in Stage E rehearsal document records 57 passing tests; the independent run at this exact head measured **60**. Both runs used the same listed test targets. The difference is reported, not silently absorbed; it does not change the passing verdict.

---

## 2. Reproducible commands

All commands were run from a fresh worktree created at the exact head:

```sh
git worktree add -b <review-branch> <worktree> 2ce0eb7d42068cf686d377271937d7bbe7cdf7f3
```

Backend (disposable Compose project, internal network, tmpfs DB, empty external API keys):

```sh
PROJ=wtdw-agent-e-207-backend-$$
dc() { docker compose -p "$PROJ" -f docker-compose.test.yml --profile test "$@"; }
dc up -d --wait db-test && dc build backend-test
dc run --rm --no-deps -e TMDB_API_KEY= -e RAPIDAPI_KEY= -e WATCHMODE_API_KEY= \
  backend-test manage.py test tracker availability -v 2 --noinput
dc run --rm --no-deps -e TMDB_API_KEY= -e RAPIDAPI_KEY= -e WATCHMODE_API_KEY= \
  backend-test manage.py makemigrations --check --dry-run
dc down --volumes --remove-orphans
```

Standalone tracker: same project, `backend manage.py test tracker -v 1 --noinput`.

Frontend (`frontend/`): `npm ci --no-audit --no-fund && npm test && npm run lint && NEXT_TELEMETRY_DISABLED=1 npm run build`.

Extension (`extension/`): `npm ci --no-audit --no-fund && npm run typecheck && npm test && npm run build && npm run validate:package`.

Migration rehearsal: fresh databases per order, step targets `0017`, first sibling, second sibling, `0020`; after each, `migrate --noinput`, `migrate --check`, then SQL counts on `information_schema.columns` (`availability_offerprice.variants`, `jsonb`, NOT NULL) and `pg_trigger` (`stage_d_ledger_immutable`, `tgenabled='O'`).

Offline audits:

```sh
PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v
python3 -E scripts/audit_issue97_stage_e.py
sh -n scripts/rehearse_issue97_stage_e.sh
sh scripts/rehearse_issue97_stage_e.sh \
  availability.tests.test_issue97_stage_e_rehearsal \
  availability.tests.test_experiment_runner.GuardedTransportTests \
  availability.tests.test_experiment_runner.PostgreSQLReservationTests \
  availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_issue97_collection \
  tracker.tests.test_issue97_availability_contract
```

CI evidence: `gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/pulls/207`, `gh api repos/…/actions/runs/45616938271/jobs`, `gh api repos/…/check-runs/<id>/annotations`.

---

## 3. CI failure assessment

The hosted run `45616938271` failed before any test command executed: all three jobs have **zero steps** and one billing annotation each. That is an account/billing condition, not a code regression. It is also **not** a green result: no required status check passed for `2ce0eb7…`. Restoring hosted CI and obtaining a successful run on the exact head remains the clean path to merge eligibility.

---

## 4. Security and migration assessment

- **Sanitization:** the checked-in tree at the verified head contains no AWS access-key IDs, no `X-Amz-*` parameters (including security tokens), no presigned URLs, no GitHub tokens and no Basic authorization headers. The two bearer-header strings are literal expectations inside `extension/tests/handshake.test.ts:181` and `:259`; they are test fixtures, not credential material. No raw provider payload, cookie jar or signed URL was copied into this report or any log published here.
- **Evidence custody:** only counts, paths and SHA-256 digests are published. Raw responses were neither fetched nor preserved during this verification.
- **Migrations:** the graph forks at 0017 and rejoins at 0020; both sibling orders apply on fresh databases. Physical schema was checked, not just migration state: one NOT NULL JSONB `variants` column and two enabled immutability triggers. All databases were disposable; no production migration, deployment or data repair occurred.
- **Runtime isolation:** backend tests used an internal Compose network, tmpfs PostgreSQL and the repository's disposable-database identity guard. Stage E rehearsal used an internal network, tmpfs PostgreSQL and mocked transports. No live provider request was made.

---

## 5. One-time CI exception recommendation — recommendation only, not an approval

**Agent E recommends that the release owner and ChatGPT Web consider a one-time, scoped CI exception** for PR #207 **at exact head `2ce0eb7d42068cf686d377271937d7bbe7cdf7f3` only**, on the following recorded basis:

1. The three hosted jobs did not execute any step; the failure cause is account billing/spending limit, not a test or build failure.
2. An independent verifier reproduced the CI command set locally at the exact head with matching toolchain generations: 3,861 backend tests, 2,173 standalone tracker tests, 403 frontend tests, 34 extension tests, lint/typecheck/build/validate/package checks, both migration orders and physical schema checks all passed.
3. The published head differs from the tested application revision by a documentation-only commit.

**Conditions that must hold for the exception to be usable:**

- The PR head remains exactly `2ce0eb7…`. Any new executable commit voids this evidence and requires a fresh verification run.
- The exception is scoped to this one PR/head and this one billing-blocked CI run; it is not a standing waiver and does not cover any other PR or future run.
- A separate, explicit release-owner approval is still required, **or** hosted CI is restored and passes on this exact head. Independent verification and this recommendation do not themselves satisfy that gate.
- The per-PR retarget plan in §6 is followed; #207 is not merged wholesale.
- The exception, its scope and this report are recorded in the PR/issue trail before any merge decision.

**Agent E does not approve the exception, waive CI, approve the PR, or merge anything.** ChatGPT Web remains the sole final reviewer and merger. #97 stays OPEN and #103 stays ON HOLD. Stage D/E live grants, stop-writers cutover and production changes remain out of scope.

---

## 6. Per-PR final merge/retarget plan (plan only — not executed)

1. Do **not** merge #207 wholesale; its five source PRs are still open drafts and were forward-merged only inside the integration branch.
2. Take #198 (`85774f4…`) first as the application candidate. Verify and merge it on its own exact head, then rerun the full gate set on the updated master.
3. Retarget #205 (`7be721b…`) onto that master, rerun gates, then merge. Repeat in order for #192 (`2a832ed…`) and #206 (`3256aae…`), retesting against the actual master after each merge.
4. Treat #204 (`4542786…`) as design/authorization documentation, not live approval; ensure no execution grant or runtime permission ships with it.
5. Preserve the `0018`-then-`0019` release migration order when the final master is built; re-run both-order rehearsal if the migration graph changes.
6. Each candidate needs hosted CI green on its own final head, or a separately approved, explicitly scoped exception recorded for that head.
7. Keep production cutover, historical data repair and Stage D/E live collection blocked pending separate owner authorization.

---

## 7. Residual risk

- Hosted CI never executed: a local matrix, even at matching Python/PostgreSQL/Node generations, is not a GitHub Actions result and cannot satisfy required status checks.
- The billing condition is unresolved; subsequent runs may fail the same way.
- No live provider observation occurred. Fresh adjudicated pairs are 0 and accuracy metrics are undefined; the integration does not establish availability accuracy.
- Forward merges in #207 are not on master; per-PR retargeting can expose conflicts not visible in the integrated branch.
- The Stage E rehearsal count differs from its document (60 measured vs 57 documented); the discrepancy is recorded above.
- Host scanner found two fixture bearer strings; they are test literals, but any future change that replaces them with a real token would need a new scan.

---

## 8. Evidence log custody (names and hashes only; no payloads)

| Log | Bytes | SHA-256 |
|---|---:|---|
| `backend_tests.log` | 1,350,415 | `418e61094f4c55db5704ac979e78aae192c4e5befebbad31cc00029aa983f211` |
| `tracker_tests.log` | 134,396 | `537ad43dfaae115fe5514993eacbb769f89efd850f47891875d18fd136a86f08` |
| `makemigrations.log` | 183 | `0d21e5d746819c5eba385fdd506072ab53a2611308c7042c7d0e81fb70f4a1fe` |
| `frontend_test.log` | 1,410 | `c597b0893892676dfd47c77e2d06509b70b0187f5051b9d216a2fe8d39c12226` |
| `frontend_lint.log` | 700 | `c3a4a0c4d0c2e7fc42c626348e6fb8475a5184580a333434a2eb32de56c57f13` |
| `frontend_build.log` | 2,562 | `1c88f4d7e8ba8d3923ef747551cd39c5ec0fe7cfc0513a0fd803628cedb81cd3` |
| `extension_typecheck.log` | 63 | `7ba7291cf3aa553ba896f8b1406d961f4359833e42bf9755ec7160dcf5ff2c13` |
| `extension_test.log` | 316 | `3370cbe42d60492639d90a392e90619c149e5adfe6a861ddb2184baee7b5e4ef` |
| `extension_build.log` | 185 | `82ef32679edb1c4eaccfc807f7e0afde8e87b048604efbbd1b3517d816f579c2` |
| `extension_validate.log` | 117 | `3e5d061e1f3a3cc0caae2217fc2def62c0252f0f920141ff07e1d8ff48b2d6dd` |
| `stage_d.log` | 747 | `3ae3226160e8961fcecd0911d8f4a7e434393893106cc714ca62df095b7cdaa6` |
| `stage_e_audit.log` | 644 | `5abb179eee32587389a0dc1c8bcf66befd2d572141cbe92df7e4984a71263366` |
| `stage_e_rehearsal.log` | 19,288 | `6a8d3ad2a80fc5776c0aa8453124b747dfe2056887d5ca058bd48360b391f620` |
| `security_scan.log` | 979 | `3e9f597fbf72446bb5fd62c9ed5f943493325bcb077ba638906e107c5f8e93d7` |

Logs were kept outside the Git worktree; only their digests are published.

---

**Verified final SHA:** `2ce0eb7d42068cf686d377271937d7bbe7cdf7f3`
**Recommendation status:** prepared for owner review — **not approved, not waived, not merged**.
