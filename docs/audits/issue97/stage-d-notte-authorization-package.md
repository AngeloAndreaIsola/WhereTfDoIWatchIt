# Issue #97 — Stage D Notte execution-readiness / authorization package (Agent B)

**Gate 97-D-NOTTE: `BLOCKED_ON_SPECIFIC_EVIDENCE`.** This package prepares the exact Stage D Batch 1 execution-authorization inputs and confirms fail-closed behavior with mocked transport only. **Live `GetSearchTitles` execution remains strictly NOT AUTHORIZED.** No HMAC grant was issued, no quota or permission was changed, no credential was exposed, no raw evidence was committed, no production or retained database was written, no scope was expanded, no issue was closed, and no branch was merged. Every unresolved input is an owner-supplied evidence item listed in §10.

- Exact base (re-fetched at dispatch): `3e439e6a7cab7a72834e710352da7cbf0f0b739f` — `Merge #238: independent #97/#103 review` — confirmed `origin/master` in this checkout.
- Isolated forward-only branch: `docs/97-stage-d-notte-authorization-package-agent-b`, created from that exact base; the only commit adds this file.
- Issue: [#97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97). No closure, no cleanup, no merge.
- Scope: Stage D Batch 1 **only** — The Godfather (1972) TMDb 238; Breaking Bad (2008) TMDb 1396; Country NL; operation `GetSearchTitles`; at most 2 first-send requests; 0 retries; concurrency 1; no fallback endpoints; no season queries. No expansion of any kind.
- Route disclosure: preferred `omniroute/ag-gemini-3.8-flash-tiered` failed two fresh coordinator probes with 503 `ALL_TARGETS_SKIPPED`; verified fallback is native OpenCode. No silent substitution, no Sol, no Astra, no paid escalation. See §1.

This package does not repeat the completed documentation reviews; it consumes the previously reviewed Stage D corpus at the exact base and validates the runner against it.

---

## 0. Reviewed identity of the existing Stage D runner (Task 1)

At exact base `3e439e6`, the bounded runner is implemented and reviewed. Identity and provenance:

| Path at exact base | Role |
| --- | --- |
| `backend/availability/services/issue97_stage_d_policy.py` | Fixed policy, evidence/receipt validation, HMAC scope binding (`MANIFEST_SHA256`, `check_authorization`, `authorization_scope`). |
| `backend/availability/services/issue97_stage_d_runner.py` | Durable reservation, handoff, single send, outcome and checkpoint accounting. |
| `backend/availability/services/issue97_stage_d_titles.json` | Immutable 12-title, three-batch manifest; canonical digest computed in §8. |
| `backend/availability/management/commands/issue97_stage_d.py` | Read-only default preflight; gated `--execute-token-file` / `--confirm-database-identity` paths. |
| `backend/availability/management/commands/issue97_stage_d_identities.py` | Gated offline provisioning of the 12 typed titles (no live provider calls). |
| `backend/availability/migrations/0019_stage_d_run_ledger.py` + merge `0020` | Append-only run/attempt/event ledger and immutability trigger. |
| `docs/operations/issue97_stage_d_bounded_runner.md` | Runbook: evidence matrix, signing envelope, stop conditions, reconciliation. |

The runner pins `GetSearchTitles` to one POST at `https://apis.justwatch.com/graphql`, enforces HMAC-SHA256 scope equality, durable single-ledger reservations, source serialization, burst 1 / in-flight 1, a 5 s floor, zero retries, no redirects, no adapter retries, no season lookups and no fallback endpoints. This is a configuration/code review plus mocked validation; it is **not** permission to send.

---

## 1. Route — requested/actual model, provider, effort, cost, fallback (Task 1 / dispatch)

| Field | Recorded value |
| --- | --- |
| Requested route | `omniroute/ag-gemini-3.8-flash-tiered` (preferred) |
| Preferred-route outcome | Two fresh coordinator probes: HTTP 503 `ALL_TARGETS_SKIPPED` — no model identity obtained, cost not charged per failed target selection. |
| Fallback reason | Preferred route unavailable (503 ALL_TARGETS_SKIPPED); owner-approved fallback is native OpenCode from the verified prior probe; no paid escalation. |
| Actual route used | native OpenCode CLI `1.19.6` (`opencode`) |
| Requested model | `opencode/hy4-flash-free` |
| Actual model (fresh in-session probe) | `opencode/hy4-flash-free` — exact text `MODEL_IDENTITY_OK opencode/hy4-flash-free` |
| Effort | **UNPINNED** — OpenCode exposes no reliable per-run effort flag; owner requested HIGH, which is not pinnable here except via a wrapper. No override was set. |
| Cost | JSON `step_finish.cost: 0` (USD) for the identity probe; no paid escalation, no Sol/Astra substitution. |

Fresh probe executed in this task:

```sh
opencode --version                                      # 1.19.6
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
# text part: MODEL_IDENTITY_OK opencode/hy4-flash-free ; step_finish.cost: 0
```

The probe is a model-identity check only — no provider source request and no live JustWatch call.

---

## 2. Notte function / version / runtime receipt (Task 2)

Authorized read-only inspection used only the in-repository sanitized custody records and local metadata; no live Notte endpoint or function invocation was contacted.

| Field | Reviewed, recorded value | Source |
| --- | --- | --- |
| Function alias | `stage-a-notte-function` (personal, unpublished, `shared: false`, status active) | `docs/audits/issue97/jw-canary-2026-11-24-evidence/sanitized/function_show.json` |
| Latest version (redeploy) | `v20261124_150434` (original at first show: `v20261124_135310`) | `function_show.json`, `function_show_original.json` |
| Version history | `v20261124_135051`, `135131`, `135152`, `135310`, `150411`, `150434` | `function_show.json` |
| Export tool version | Notte CLI `v0.0.79` (`functions list/show/download`) | `function_schema.json`; decision record `docs/source-permission/decision-2026-11-24.md` |
| Source spec digest | SHA-256 `faf8b4ba69835f0428821b71a0c29f6072fe0aab0647016b8148120005e0a23f` (4665 bytes) | `function_schema.json` |
| Raw show digest (redeploy) | SHA-256 `e25f372188c7c1f9c17c898af1b930ff482b7addae8e9a82c690e8036a9a219c` (5604 bytes) | `function_show.json` |
| Required secrets count | 0 | `function_show.json` |
| Historical invocation accounting | 1/3 validation probes, 3/6 collection calls; internal JustWatch HTTP attempt count **UNKNOWN** (`measured_count: null`, ledger not retained); static estimate 4 nominal browser actions per success is `ESTIMATE_NOT_MEASURED` | `attempt_accounting.json` |

**Current-function verification:** no authorized live/metadata Notte function-show tool is exposed in this session. The current deployed version and any runtime receipt therefore cannot be independently re-verified now; only the reviewed historical custody record above is available. Marked `BLOCKED_ON_SPECIFIC_EVIDENCE` for the "current" requirement.

**Runtime receipt:** absent. The historical canary is a function-invocation budget, not an HTTP receipt; its owner risk acceptance leaves `justwatch_permission` **OPEN** and grants no Stage D scope. See §5.

**Notte call accounting:** **zero live Notte calls were made in this task.** The accounting template in §9 records the identity probe (OpenCode, not Notte) and the zero-value Notte row.

---

## 3. Remaining quota and actual request costs (Task 3)

- Authorized read-only quota source queried: `omniroute_check_quota` (scope `read:quota`), generated `2026-11-27T21:30:48.637Z`, 79 OmniRoute provider connections. None is JustWatch or Notte. `percentRemaining` is `null` for essentially every connection; token statuses are `valid`/`expiring`/`expired` for LLM/code CLI connections only. This is **OmniRoute API quota**, not an upstream JustWatch/Notte allowance and not evidence of Stage D HTTP balance.
- Local `tracker.services.rate_limit` publishes daily/monthly limits for Streaming Availability and Watchmode only; there is no declared JustWatch plan limit. A missing declared local limit cannot establish a quota class, balance or permission.
- The offline prerequisites dossier (`offline-prerequisites-dossier-20261127.md` §2) is a static checklist; it repeats that the historical RapidAPI/Streaming Availability free tier is **not** a JustWatch quota.
- No configured dashboard, usage report or retained balance artifact showing current JustWatch `window_id`, `remaining` or reconciled `spent_at_review` was available under authorized read-only access. No live provider quota call was made.
- Actual future per-request cost is therefore **UNKNOWN**; the only measured cost in this task is the OpenCode identity probe at JSON cost 0.

**Result: `BLOCKED_ON_SPECIFIC_EVIDENCE`** — quota class, remaining balance, prior shared-grant spend and per-request cost are not established. See §10 owner inputs.

---

## 4. Local disposable PostgreSQL target and fingerprint (Task 4)

All database work used transient, disposable infrastructure only. No retained or production database was contacted, written or migrated.

| Property | Observed value(s) this task |
| --- | --- |
| Image | `postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` (PostgreSQL **16.15**) |
| Backend image | `wtdwi-stage-d-backend-test:latest` (Python 3.12 / Django 5.2), `backend/` and repo bind-mounted **read-only** |
| Network | Docker `--internal` network, zero published ports (`NetworkMode` confined, `PortBindings` length 0), loopback alias `db-test` |
| Data directory | tmpfs (`--tmpfs /var/lib/postgresql/data`) — destroyed after verification |
| Database / role | dedicated `wtdwi_test` / `wtdwi_test`; zero `tracker` databases; zero other client backends at check |
| Fingerprints (independent SQL) | `092bc50921129170cc34d333fbc4888a4e44fa7f940b1a87048c6a4934b6501c` (first cluster), `31e5950375450d862deaf60de585034c3f25291f5e8b50680f03675c7b33277d` (second cluster) |
| Fingerprint (runner `database_identity()` / preflight JSON) | matched the independent SQL value in each cluster; preflight emitted the same fingerprint |
| Migrations | `makemigrations --check --dry-run`: no changes; `migrate --check`: exit 0; all migrations applied incl. `0018`, `0019`, merge `0020` |
| Ledger triggers | `stage_d_ledger_immutable` enabled (`O`) on run and attempt-event tables (reviewed at base; exercised by the test suite) |
| Read-only preflight | `default_transaction_read_only=on`; preflight `readiness=blocked`; zero reservations/handoffs/transmissions/checkpoints; ledger counts `0|0|0|0` |
| Provisioning (gated offline writes) | `issue97_stage_d_identities --apply --confirm-database-identity <fp>` with `WTDWI_DISPOSABLE_TEST_DB=1` and `AVAILABILITY_STAGE_D_DISPOSABLE=1` provisioned exactly 12 typed rows; second read-only plan reported 0 missing on the retained state within that transient cluster |
| Preflight after provisioning | exactly one blocker: `current runtime source receipt must cover NL search only`; no `authorization_scope` emitted |

Both transient clusters were removed on trap exit. Any fingerprint recorded here is **not** a named retained execution target; a fresh cluster yields a different fingerprint. The retained execution target, its attested fingerprint and its ledger continuity remain owner-supplied inputs (§10).

---

## 5. Signed-scope and HMAC requirements (Task 5) — no grant issued

From `issue97_stage_d_policy.py` and the runbook at exact base, a valid execution requires:

1. **Signing key:** `AVAILABILITY_STAGE_D_AUTH_KEY` with ≥ 32 bytes. Token = unpadded Base64url JSON payload + `.` + hex HMAC-SHA256 of the encoded payload. Payload carries `expires_at` plus the exact `authorization_scope`.
2. **Scope equality:** `manifest_digest`, `database_identity`, `experiment_id`, `authorization_id` (stable run id derived from experiment + database), `phase`, `batch`, ordered `scope_ids`, `country`, `endpoint`, `method`, `operation`, `document_sha256`, `maximum_attempts`, `maximum_batch_attempts`, `maximum_retries`, `prior_checkpoints_sha256`, `evidence_sha256`, `runtime_receipt_sha256`. Any deviation is rejected before a send.
3. **Expiry bounds:** `now < expires_at <= evidence_expiry`, where evidence expiry is the earliest receipt/permission/quota/rate validity and the one-hour freshness deadline.
4. **Evidence environment:** `AVAILABILITY_STAGE_D_EVIDENCE` JSON with `access_permission`, `quota`, `rate_policy`, `database_identity`, `grant_reference`, `exclusive_grant_use: true`, optional `prior_receipt_idle_since`, all checked within one hour.
5. **Separate approval per batch:** Batch 2/3 tokens bind prior checkpoint digests and cumulative spend. No new database or new receipt reference may reset an unresolved reservation.

**No grant, key, token, signature or permission was created, issued or proposed for signing in this task.** The `evidence_sha256` and `runtime_receipt_sha256` values are unavailable, so no final signed-scope digest or token is prepared. Owner must supply the separately signed HMAC grant with exact scope and expiry (§10).

---

## 6. `GetSearchTitles` permission for Country NL under configured access conditions (Task 6)

**Not permitted under any configured access condition currently available.**

- The policy pins `GetSearchTitles` (document SHA-256 `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`) to POST `https://apis.justwatch.com/graphql` with `country: NL` — configuration is correct, but configuration is not permission.
- `review_evidence()` requires a current enabled JustWatch receipt with `countries == ["NL"]` and `operations == ["search"]`, plus independent `access_permission` with `operations == ["GetSearchTitles"]`, exact document digest, all 12 scope ids, `maximum_requests >= 12`, `maximum_retries == 0`, a valid window, reviewer, reference and exclusive use. **None exists** — preflight returns the single blocker `current runtime source receipt must cover NL search only`.
- `docs/source-permission/decision-2026-11-24.md` records the historical canary under owner risk acceptance with `justwatch_permission: **OPEN**`. It explicitly states it is not evidence of permission from JustWatch and grants no Stage D scope. The redefined function version did not create new permission evidence.
- No configured access condition, retained receipt or reviewed permission record authorizes `GetSearchTitles` for NL today. Marked `BLOCKED_ON_SPECIFIC_EVIDENCE` / permission.

---

## 7. Exact Batch 1 scope (Task 7)

| Field | Exact value |
| --- | --- |
| Phase / experiment | `titles-v1` / `issue97-stage-d` |
| Batch | 1 (first two ordered scopes) |
| Scope 1 | `movie-238-nl` — The Godfather, TMDb 238, movie, 1972, NL |
| Scope 2 | `series-1396-nl` — Breaking Bad, TMDb 1396, series, 2008, NL |
| Country | NL (single) |
| Operation | `GetSearchTitles` (one fixed one-page query document) |
| Endpoint / method | `https://apis.justwatch.com/graphql` / POST |
| Document SHA-256 | `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51` |
| First-send requests | maximum 2 (one per scope) |
| Retries | 0 (no second attempt; 503 is charged once) |
| Concurrency / burst / in-flight | 1 / 1 / 1 |
| Minimum interval | 5 s floor (max of manifest, receipt and reviewed upstream minima) |
| Fallback endpoints | none |
| Season queries (`GetTitleNode`) | none — denied; separate future authorization phase |
| Held identity | Coherence `movie-220289-nl` (2013 vs 2014) excluded; no expansion |
| Total experiment ceiling | 12 title attempts across batches 1–3; unused slots do not transfer |

### Manifest digest (computed and confirmed)

Computed from the exact base file `backend/availability/services/issue97_stage_d_titles.json` using the runner's canonical rule (`json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=True)` then SHA-256):

| Field | Digest |
| --- | --- |
| `manifest_digest` | `d7308681ae99363a6e02e63a6e801d7e9b6d749fb8727` (matches `MANIFEST_SHA256` and the offline/report references) |
| `document_sha256` | `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51` |
| Empty prior-checkpoint digest | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| Cohort CSV SHA-256 | `f573eff980127bb08943ea2c101d8354c6f18a6fd659e24ca727ae0576354e30` |
| Identity manifest SHA-256 | `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925` |

The full signed scope additionally binds `evidence_sha256`, `runtime_receipt_sha256`, `database_identity` and `expires_at`; those values are **UNAVAILABLE** here, so the final signed-scope digest is intentionally not produced.

---

## 8. Fail-closed rehearsal (Task 8) — mocked transport only, zero live calls

Executed at exact base `3e439e6` on transient PostgreSQL 16.15 with `requests.Session.post` mocked. No live `GetSearchTitles` call, no live Notte call.

| Case | Result |
| --- | --- |
| Stale / future-dated permission, quota or rate evidence | blocked (`test_evidence_freshness_and_effective_quota`) |
| Scope deviation (manifest, operation, retries, season document, scope ids) | rejected before send (`test_signed_scope_rejects_tampering_and_unauthorized_seasons`) |
| Wrong target fingerprint / missing disposable attestation | rejected (`test_runtime_guards_require_fingerprint_autocommit_and_expiry`) |
| 13th reservation | refused; committed sends stay at 12 (`test_twelve_committed_sends_and_no_thirteenth_reservation`) |
| Retry / 503 | zero retries; HTTP 503 charged once, no retry (`test_503_is_charged_without_retry`) |
| HMAC mismatch | unknown key, malformed token, corrupted tag/payload rejected; zero sends |
| Missing receipt / read-only command | blocked, zero rows created (`test_read_only_command_reports_missing_receipt_and_creates_no_rows`) |
| Wrong typed response / wrong market | stopped before observation (`test_wrong_typed_response_stops_before_observation`, `test_wrong_market_stops_before_observation`) |

Aggregate focused suite: **99 tests run — 98 passed, 1 skipped (opt-in restricted raw TMDb bytes), 0 failed.** Manifest suite: **7 run — 6 passed, 1 skipped, 0 failed.** Ledger counts on the read-only preflight clusters remained `0|0|0|0`. These results prove fail-closed behavior; they do **not** authorize execution.

---

## 9. Notte request-accounting template and populated rows (Task 9)

Template columns (one row per external call/probe): timestamp (UTC) | actor/route | function/version or model | title / country | operation | first-send count | retries | runtime duration | cost (USD) | response status | evidence digest | deviations.

| Timestamp (UTC) | Actor / route | Function / version | Title / country | Operation | First sends | Retries | Duration | Cost | Status | Evidence digest | Deviations |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| 2026-11-27 (this task) | native OpenCode identity probe | `opencode/hy4-flash-free` (CLI 1.19.6) | n/a (model identity) | `MODEL_IDENTITY_OK` probe | 0 (not a source call) | 0 | ~77 ms text part | 0 (`step_finish.cost`) | success | exact reply `MODEL_IDENTITY_OK opencode/hy4-flash-free` | Preferred route 503 ALL_TARGETS_SKIPPED; effort UNPINNED (HIGH not pinnable) |
| 2026-11-27 (this task) | OmniRoute read-only quota metadata | `omniroute_check_quota` (`read:quota`) | n/a | quota metadata | 0 | 0 | one call | 0 observed | success | `generatedAt 2026-11-27T21:30:48.637Z`, 79 providers | No JustWatch/Notte connection; `percentRemaining` null |
| 2026-11-27 (this task) | Stage D mocked rehearsal | runner at `3e439e6`, manifest `d7308681…8727` | Godfather NL / Breaking Bad NL (mock) | `GetSearchTitles` (mocked `Session.post`) | 0 live (mock sends only) | 0 | 15.7 s suite | 0 (no provider) | 98 pass / 1 skip / 0 fail | suite output; ledger `0|0|0|0` | No live transport handoff |
| — (historical, reviewed, **not this task**) | Notte canary JW-CANARY-v1b | `stage-a-notte-function` `v20261124_150434`, Notte CLI `v0.0.79` | Inception / Dune (US discovery, NL collection) | browser navigation + `GetUrlTitleDetails` | 3 collection + 1 probe (not Stage D) | 1 recorded retry | per-call ranges in custody | UNKNOWN | 3/6 collection, 1/3 probe | source spec `faf8b4ba…a23f`, show `e25f3721…219c` | Internal JustWatch HTTP count **UNKNOWN**; not an HTTP receipt |

**Live Stage D Batch 1 rows: none — zero live calls were made.** The template above is the required schema for populating any call made after exact owner authorization. Any future row must additionally record `evidence_sha256` / `runtime_receipt_sha256`, `authorization_id`, attempt ordinal and response/checkpoint digests, with raw payloads kept off-repository in approved custody.

---

## 10. Exact execution-authorization package and unresolved owner inputs

### Package handed to owner (nothing executable until all inputs arrive)

1. Exact base/head of this PR and manifest digest from §7.
2. Exact Batch 1 scope from §7; no season queries, no fallback, no retries.
3. Evidence matrix and HMAC scope requirements from §5; stop conditions from §12.
4. Accounting template and zero-live-call record from §9.
5. Disposable-target verification recipe and transient fingerprints from §4.
6. Gate assignment: `BLOCKED_ON_SPECIFIC_EVIDENCE`.

### Only unresolved owner inputs

| # | Unresolved input | Owner action |
| --- | --- | --- |
| 1 | **HMAC grant scope** — separately signed authorization binding exact Batch 1 `authorization_scope`, `evidence_sha256`, `runtime_receipt_sha256`, signing-key custody, `expires_at` within all evidence deadlines. | Issue the grant out of band; do not ask this package to generate it. |
| 2 | **Exact-batch confirmation** — written confirmation of exactly the two titles, NL, `GetSearchTitles`, ≤2 first sends, 0 retries, concurrency 1, no seasons, no fallback. | Confirm the exact batch; no scope expansion. |
| 3 | **Quota / cost evidence** — current JustWatch `window_id`, `remaining`, reconciled `spent_at_review`, quota class (metered or reviewed unmetered), rate/cooldown and actual request cost from an authorized source. | Supply reviewed evidence within the one-hour freshness window. |

Supporting evidence still required for readiness (already identified in reviewed reports and not re-derived here): current NL-search runtime receipt with reviewer/reference/valid window/finite ceiling/positive interval; independent access permission naming document `997e0b1f…3f51` and all 12 scope ids; exclusive-grant-use attestation across processes/DBs/schedulers/consumers; named retained disposable target with attested fingerprint, clean preflight and ledger continuity; approved restricted custody for raw captures. Coherence stays held pending a reviewed year decision and is outside Batch 1.

### Gate assignment

**Gate 97-D-NOTTE = `BLOCKED_ON_SPECIFIC_EVIDENCE`.** Not `READY_FOR_EXACT_APPROVAL` because the three owner inputs above (plus the supporting evidence) are absent; not `BLOCKED_ON_ACCESS` because authorized read-only access worked and produced evidence of absence; not `REQUEST_CHANGES` because runner behavior and fail-closed rehearsal passed; not `COMPLETE` because live execution is not authorized.

---

## 11. Forbidden-operation statement

This package and its author did **not**: execute a live `GetSearchTitles` call; execute a live Notte function invocation; issue, sign or request an HMAC grant/token/key; change quota, permission or rate configuration; expose credentials; commit raw evidence or payloads to Git; write to any production or retained database; expand the reviewed scope; close or clean up issue #97; or merge any branch. All database work was on transient disposable clusters with mocked transport and was destroyed after verification.

---

## 12. Explicit stop conditions (before any next send)

Stop and hold the run if any of the following occurs:

1. Permission, quota, rate evidence, receipt or HMAC is absent, stale (> 1 h), future-dated, changed, or expired before `expires_at`.
2. Budget exhaustion (2 first sends used for Batch 1 / 12 total), or any attempt to reserve a 13th slot.
3. Typed identity or market mismatch (TMDb id, media type, year, reviewed title/alias, NL), multiple or zero unambiguous native nodes, conflicting explicit territory.
4. Malformed or unexpected response, unknown mapping/parse outcome, or an unvalidated response without a checkpoint.
5. Unexpected transport: redirect, adapter retry, browser/Notte fallback, season lookup, or any destination other than the pinned GraphQL endpoint.
6. Lock or queue contention, overlapping run, unfinished reservation, or inconsistent ledger/artifact state.
7. HTTP 429 — record the charged response, enforce ≥ 10 min cooldown and any longer `Retry-After`; HTTP 401/403 or redirect — record and do not follow; challenge/access refusal — stop.
8. Transport timeout, reset, uncertain send, lost response or outcome record — charge the slot, mark `http_transmission_unknown`, and globally pause for reconciliation. No automatic refund or reissue.
9. Any instruction to expand beyond the exact two-title Batch 1 scope, add retries, enable seasons, or use a different target.

Resume only from the same durable target with a current matching token; a new database, receipt reference or batch cannot erase an unresolved reservation. A separate owner review is required after Batch 1 checkpoints before Batch 2.

---

## 13. Validation commands and results (exact base)

```sh
# Re-fetch and confirm exact base
git fetch origin master --quiet && git rev-parse origin/master
# 3e439e6a7cab7a72834e710352da7cbf0f0b739f

# Model identity / cost
opencode --version                                  # 1.19.6
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
# MODEL_IDENTITY_OK opencode/hy4-flash-free ; cost 0

# Manifest suite — no network, no DB (in stage-d backend image)
docker run --pull=never --rm --network none \
  --mount "type=bind,source=$PWD,target=/repo,readonly" \
  -e PYTHONPATH=/repo/backend -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint python wtdwi-stage-d-backend-test:latest \
  /repo/scripts/test_issue97_stage_d.py -v
# 6 passed, 1 skipped (restricted raw TMDb bytes), 0 failed

# Focused runner suite — disposable PostgreSQL 16.15, internal network,
# tmpfs, read-only backend mount, mocked transports; ISSUE97_STAGE_D_DOCS_ROOT=/repo
# manage.py test --noinput -v 1 \
#   availability.tests.test_issue97_stage_d_identities \
#   availability.tests.test_issue97_stage_d_runner \
#   availability.tests.test_issue97_stage_d_preflight \
#   availability.tests.test_experiment_runner \
#   availability.tests.test_justwatch_reviewed_transport \
#   availability.tests.test_issue97_collection
# 98 passed, 1 skipped, 0 failed (99 run) — no live provider calls

# Read-only preflight on transient target (fingerprint varies per cluster)
# manage.py makemigrations --check --dry-run   -> No changes detected
# manage.py migrate --check                     -> exit 0
# PGOPTIONS='-c default_transaction_read_only=on' manage.py issue97_stage_d --batch 1
# readiness=blocked; blockers: 12 identity rows + 'current runtime source receipt must cover NL search only'
# ledger counts 0|0|0|0 ; no authorization_scope emitted
```

Hosted CI conclusions for this draft PR are recorded in §14 and are limited to repository checks; CI passing does not grant execution permission.

---

## 14. PR head, CI link/job conclusions and handoff

- Branch: `docs/97-stage-d-notte-authorization-package-agent-b` (isolated, forward-only from exact base `3e439e6`). Draft PR [#240](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/240), base `3e439e6a7cab7a72834e710352da7cbf0f0b739f`, final reviewed head `c8c794c8c767d49be9aa7a8ca67c5ed4b5fcda66` (verified with `gh pr view` / `gh api`). An earlier doc-only commit at `cf9811c3f33ff212911c330c0cfe48f67b527b36` was advanced by a forward-only CI-record correction; PR metadata remains authoritative.
- CI: workflow `CI`, final-head run [`46071637588`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46071637588), overall conclusion **success**. All three jobs **success**: Backend Tests, Frontend Lint/Build/Test, Browser Extension Build/Typecheck/Tests. The earlier content head run [`46071125366`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46071125366) was also **success** on all three jobs. CI validates code only; it does not authorize live calls.
- Handoff: one comment on #97 with exact base/head, CI link and conclusions, and an explicit **ChatGPT Web independent review request**. No closure, no cleanup.
- Next action after owner supplies the three inputs: prepare the exact signed Batch 1 token out of band, re-run preflight on the named retained target, and only then consider a separately approved first send. Until then, stop.
