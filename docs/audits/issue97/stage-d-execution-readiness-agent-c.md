# Issue #97 — Stage D execution-readiness report (Agent C, exact-scope Batch 1)

**Gate 97-D: `BLOCKED_ON_SPECIFIC_EVIDENCE`.** Offline execution readiness is verified against a disposable target. Live Batch 1 remains strictly NOT AUTHORIZED. The current NL-search runtime receipt, reconciled quota/balance, independent access permission, effective rate/cooldown evidence, exclusive-grant attestation and a named retained execution target are all absent from every retained, read-only-authorized source inspected here. This report prepares exact inputs and rehearses fail-closed behavior; it grants no permission, issues no token, and sends no request.

- Base (exact, re-fetched): `6af4cd8421ec9c85e4fff724fded60d5cce86c85` — `Merge PR #233: #97 offline prerequisites dossier` — now `origin/master`.
- Issue: [#97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97). No issue closure, no grant, no quota/permission change, no production write or migration, no merge.
- Scope: Stage D Batch 1 only — The Godfather (1972) TMDb 238, Breaking Bad (2008) TMDb 1396, country NL, operation `GetSearchTitles`, at most two first-send requests, zero retries, concurrency one, no fallback endpoints, no season queries. No expansion.

## 1. Route / model / provider / effort / cost

| Field | Recorded value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual model (fresh in-session probe) | `opencode/hy4-flash-free` — exact text `MODEL_IDENTITY_OK opencode/hy4-flash-free` |
| Provider | native OpenCode CLI `1.19.6` (`opencode`) |
| Effort | **UNPINNED** (OpenCode exposes no reliable effort flag; no override set) |
| Cost | JSON `step_finish.cost: 0` (USD) for the identity probe; no paid escalation |
| Substitution | none — no Sol, Astra, XHIGH or silent substitution |

Probe command and result:

```sh
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
# text part: MODEL_IDENTITY_OK opencode/hy4-flash-free ; cost: 0
```

The first probe attempt returned a transient `UnknownError`/`Unexpected server error`; an immediate retry returned the identity above. The successful probe is the recorded one.

## 2. Twelve typed title identities — VERIFIED (offline, disposable)

All twelve admitted typed identities were verified on a freshly migrated disposable PostgreSQL 16.15 target using SHA-256-pinned retained CSV evidence only. No live TMDb call was made.

- Cohort CSV `f573eff980127bb08943ea2c101d8354c6f18a6fd659e24ca727ae0576354e30`; identity manifest `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925`; runner manifest `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`.
- Read-only plan (clean target): 12 missing rows, 0 present, 0 conflicts, `ready_for_preflight=false` — root cause is no provisioning step between migration and preflight.
- Gated `--apply` on the disposable target (with `WTDWI_DISPOSABLE_TEST_DB=1`, `AVAILABILITY_STAGE_D_DISPOSABLE=1`, exact `--confirm-database-identity`) provisioned exactly 12 rows; a second apply was idempotent (0 new rows, 12 present).
- Coherence (`movie-220289-nl`, pilot 2013 vs TMDb 2014, `BLOCKED_YEAR_MISMATCH`) remained held and excluded.
- After provisioning, the default read-only preflight reported 0 identity blockers and exactly 1 blocker: `current runtime source receipt must cover NL search only`.

Twelve admitted scopes: `movie-238-nl`, `series-1396-nl`, `movie-496243-nl`, `movie-603-nl`, `movie-619264-nl`, `movie-840430-nl`, `movie-565310-nl`, `movie-116149-nl`, `series-70523-nl`, `series-66732-nl`, `series-2316-nl` (reviewed alias The Office), `series-1438-nl`.

## 3. NL-search runtime receipt — BLOCKED_ON_SPECIFIC_EVIDENCE

No current receipt was found in any retained or read-only-authorized source. Preflight on the disposable target confirms the single blocker. The validator (`source_permissions.py:19`, `issue97_stage_d_policy.py:64`) requires, and the evidence is absent for:

| Requirement | Finding |
| --- | --- |
| Source identity | JustWatch row exists in synthetic tests only; no configured production/retained source receipt. |
| Operation | Required `search` / `GetSearchTitles`; no enabled receipt names it. |
| Scope | Required `countries:["NL"]`, `operations:["search"]`; absent. |
| Endpoint | `https://apis.justwatch.com/graphql` pinned in manifest; no receipt attests it. |
| Reviewer provenance | `reviewed_by` and `evidence_reference` required; no current dated record. |
| Validity window | `valid_from <= now < valid_until`; no current window supplied. |
| Request ceiling / interval | finite positive `maximum_requests` and `minimum_interval_seconds`; absent. |

The historical Notte canary (JW-CANARY-v1b) has an internal JustWatch HTTP count of **UNKNOWN**, 3/6 collection invocations and 1/3 validation probes — a historical function budget, not an HTTP receipt. Its owner risk acceptance leaves `justwatch_permission` open and grants no Stage D scope.

## 4. Quota / balance evidence — BLOCKED_ON_ACCESS/EVIDENCE

Only static checklists and historical readiness reports exist (e.g. `offline-prerequisites-dossier-20261127.md` §2, `stage-d-runtime-authorization-verification.md` finding 4, `stage2r/readiness_reassessment.md`). No configured dashboard, usage report or retained balance artifact showing current JustWatch remaining quota, `window_id`, or reconciled `spent_at_review` was available for read-only inspection. No live provider quota call or scraping was performed. Current quota class and balance remain **UNKNOWN**. On the disposable target the budget/attempt/event/run ledger counts were all zero after preflight and after all mocked tests.

## 5. Exclusive grant ownership and rate limits — BLOCKED_ON_ACCESS/EVIDENCE

- Owner decision tracker (issue #97 comment `7224642545`) states Stage D/E live execution is **NOT AUTHORIZED**, with no live requests, HMAC grants, quota changes or runtime-permission changes. The separate written upstream grant was waived as an issue-level artifact, but no runtime grant attestation replaced it.
- No `exclusive_grant_use` attestation, no cross-process/DB/scheduler/consumer inventory, and no effective `max_burst`/`minimum_interval_seconds`/active-cool-down evidence was found in retained sources.
- Validator requires `exclusive_grant_use: true`, `max_burst >= 1`, `maximum_in_flight >= 1` and a finite interval; only synthetic test fixtures satisfy these. They are test-only and not operational evidence.
- Disposable isolation checks: internal Docker network, zero published ports, dedicated `wtdwi_test` role/database, zero `tracker` databases, zero other client backends — verified on the rehearsal target, not a retained execution target.

## 6. Disposable database fingerprint — VERIFIED (exact)

| Field | Value |
| --- | --- |
| PostgreSQL | 16.15 (aarch64-unknown-linux-musl) |
| Image | `postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` |
| Backend image | `wtdwi-stage-d-backend-test:latest` (Python 3.12.16, Django 5.2.19), current `backend/` bind-mounted read-only |
| Database / role | `wtdwi_test` / `wtdwi_test`, tmpfs data, internal network, 0 published ports |
| Migrations | 104 records; `0018`, `0019`, merge `0020` applied; `makemigrations --check --dry-run`, `migrate --check` exit 0 |
| Ledger triggers | `stage_d_ledger_immutable` enabled (`O`) on `availability_availabilitystagedrun` and `availability_availabilitystagedattemptevent` |
| Fingerprint (independent SQL) | `ee878b462b06960d70de9e7cbd215656475743e738c36048c9a13f3a17522b15` |
| Fingerprint (runner `database_identity()`) | `ee878b462b06960d70de9e7cbd215656475743e738c36048c9a13f3a17522b15` — exact match |

Both independent SQL and runner-side hashes matched. This fingerprint belongs to a transient rehearsal cluster that was destroyed after verification; it is **not** a named retained execution target. A fresh cluster produces a different fingerprint.

## 7. HMAC validation boundaries — VERIFIED with synthetic HMAC + mocked transport

HMAC-SHA256 is computed over the canonical encoded authorization payload with a key of at least 32 bytes. `check_authorization` (`issue97_stage_d_policy.py:147`) verifies the signature, pops `expires_at`, compares the canonical payload digest to the expected scope, and enforces `now < expiry <= evidence_expiry`. The scope binds: database identity, stable run id, manifest digest, phase, batch, ordered scope ids, country, endpoint, method, operation, document digest, total and batch attempt ceilings, retries, prior-checkpoint digest, evidence digest and runtime-receipt digest. Reservation and handoff revalidate. Synthetic fixtures prove:

- Tampering with `manifest_digest`, `database_identity`, `evidence_sha256`, `runtime_receipt_sha256`, `prior_checkpoints_sha256`, `scope_ids`, `maximum_attempts`, `maximum_retries`, `operation` or `document_sha256` is rejected before any send.
- Unknown signing key, malformed token, corrupted tag and corrupted payload are rejected.
- Expired or outliving-authorization tokens are rejected.
- HTTP `requests.Session.post` is mocked; `session.trust_env` is false, adapter `max_retries.total == 0`, `allow_redirects` false, single destination, exact NL variables and document digest. No real transport handoff occurred.

## 8. Exact Stage D Batch 1 execution manifest digest

| Field | Exact value |
| --- | --- |
| `manifest_digest` (canonical policy SHA-256) | `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727` |
| `document_sha256` (GetSearchTitles query) | `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51` |
| `operation` / endpoint / method | `GetSearchTitles` / `https://apis.justwatch.com/graphql` / `POST` |
| Batch 1 ordered `scope_ids` | `movie-238-nl`, `series-1396-nl` |
| Typed identities | movie 238, 1972, NL; series 1396, 2008, NL |
| `maximum_attempts` / `maximum_batch_attempts` | 12 / 2 |
| `maximum_retries` | 0 |
| `maximum_in_flight` / `burst` / interval | 1 / 1 / 5 s floor |
| Season queries / fallback endpoints | none / none |
| Prior-checkpoint digest (empty prior) | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |

The full signed authorization scope also carries `evidence_sha256` and `runtime_receipt_sha256`; both are **UNAVAILABLE** here, so no final signed-scope digest or HMAC token is prepared. On the transient rehearsal fingerprint the computed `authorization_id` was `04086cbd-8ae8-597d-9d47-f0bfc8d80bcb`; this is not transferable to a retained target.

## 9. Fail-closed rehearsal — VERIFIED with mocked transport

All eight targeted cases passed on disposable PostgreSQL with mocked transport (8 tests, 0 failures):

| Case | Result |
| --- | --- |
| Stale / future-dated evidence | blocked (`test_evidence_freshness_and_effective_quota`) |
| Scope deviation (manifest, operation, retry, season document, scope ids) | rejected before send (`test_signed_scope_rejects_tampering_and_unauthorized_seasons`) |
| Wrong target fingerprint / missing disposable attestation | rejected (`test_runtime_guards_require_fingerprint_autocommit_and_expiry`) |
| Extra request (13th reservation) | refused; send count stays at 12 (`test_twelve_committed_sends_and_no_thirteenth_reservation`) |
| Retry | zero retries; HTTP 503 charged once with no retry (`test_503_is_charged_without_retry`) |
| HMAC mismatch | unknown key, malformed, corrupted tag/payload rejected; zero sends |
| Missing receipt / read-only command | blocked, zero rows created (`test_read_only_command_reports_missing_receipt_and_creates_no_rows`) |
| Wrong typed response / wrong market | stopped before observation (`test_wrong_typed_response_stops_before_observation`, `test_wrong_market_stops_before_observation`) |

The full focused suite ran 99 tests: 98 passed, 1 skipped (opt-in restricted raw TMDb bytes), 0 failures. The standalone manifest suite ran 7 tests: 6 passed, 1 skipped. Ledger counts on the rehearsal DB remained `0|0|0|0`.

## 10. Validation commands and results

```sh
# Identity probe
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
# MODEL_IDENTITY_OK opencode/hy4-flash-free ; cost 0

# Manifest suite (no network, no DB)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v
# 6 passed, 1 skipped, 0 failed

# Disposable PostgreSQL 16.15, internal network, tmpfs, read-only code mount
app() { docker run --pull=never --rm --network "$net" \
  --mount "type=bind,source=$PWD/backend,target=/app,readonly" \
  --mount "type=bind,source=$PWD,target=/repo,readonly" \
  -e DJANGO_SETTINGS_MODULE=config.settings -e DJANGO_DEBUG=1 \
  -e DJANGO_SECRET_KEY=wtdwi-test-only \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD=wtdwi_test -e POSTGRES_HOST=db-test \
  -e WTDWI_DISPOSABLE_TEST_DB=1 -e AVAILABILITY_STAGE_D_DISPOSABLE=1 \
  -e ISSUE97_STAGE_D_DOCS_ROOT=/repo -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint python wtdwi-stage-d-backend-test:latest manage.py "$@"; }
app makemigrations --check --dry-run     # No changes detected
app migrate --noinput                     # applied through 0064/0020
app migrate --check                       # exit 0
app issue97_stage_d_identities            # 12 missing, 0 conflicts (read-only)
app issue97_stage_d_identities --apply \
  --confirm-database-identity ee878b46...b15   # provisioned 12; second apply 0 new
app issue97_stage_d --batch 1             # readiness=blocked; 1 receipt blocker; 0 counters
app test --noinput -v 1 \
  availability.tests.test_issue97_stage_d_identities \
  availability.tests.test_issue97_stage_d_runner \
  availability.tests.test_issue97_stage_d_preflight \
  availability.tests.test_experiment_runner \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_issue97_collection
# 98 passed, 1 skipped, 0 failed
```

Independent fingerprint SQL:

```sql
SELECT encode(sha256(convert_to(
  format('{"actual":[%s,%s],"backend":"postgresql"}', d.oid, c.system_identifier),'UTF8')),'hex')
FROM pg_database d CROSS JOIN pg_control_system() c
WHERE d.datname=current_database();
-- ee878b462b06960d70de9e7cbd215656475743e738c36048c9a13f3a17522b15
```

## 11. Remaining blockers (precise)

1. **Current NL-search runtime receipt** — no enabled JustWatch receipt with `countries:["NL"]`, `operations:["search"]`, valid window, reviewer/reference, endpoint, finite ceiling and positive interval in any retained source. (`BLOCKED_ON_SPECIFIC_EVIDENCE`)
2. **Independent access permission** — no dated reviewed record naming document `997e0b1f…3f51`, endpoint, all 12 scope ids, exact target, zero retries, exclusive use. (`BLOCKED_ON_SPECIFIC_EVIDENCE`)
3. **Quota / balance / prior spend** — no configured dashboard or retained report; `window_id`, `remaining`, `spent_at_review` unknown. (`BLOCKED_ON_ACCESS/EVIDENCE`)
4. **Rate / cooldown** — no reviewed `max_burst`, `minimum_interval_seconds`, `maximum_in_flight` or active cooldown evidence. (`BLOCKED_ON_ACCESS/EVIDENCE`)
5. **Exclusive grant use** — no cross-process/DB/scheduler/consumer attestation. (`BLOCKED_ON_ACCESS/EVIDENCE`)
6. **Retained execution target** — only transient rehearsal fingerprints exist; no named retained disposable target with attested fingerprint, clean preflight and ledger continuity. (`BLOCKED_ON_SPECIFIC_EVIDENCE`)
7. **Owner-signed Batch 1 HMAC** — not issued; `evidence_sha256`, `runtime_receipt_sha256`, expiry and signing key custody unavailable. (`BLOCKED_ON_SPECIFIC_EVIDENCE`)
8. **Restricted custody approval** — raw payloads/tokens must remain off-repository under approved custody; not established for a fresh capture. (`BLOCKED_ON_SPECIFIC_EVIDENCE`)

Coherence remains held for a reviewed year decision; it is outside Batch 1 and this report does not expand scope.

## 12. Gate and handoff

**Gate 97-D = `BLOCKED_ON_SPECIFIC_EVIDENCE`.** Offline execution readiness and fail-closed behavior are verified; every live-authorization input is missing from retained evidence. ChatGPT Web is requested to review this draft independently. Live Batch 1 stays blocked until exact owner confirmation. No merge, no issue closure, no cleanup.
