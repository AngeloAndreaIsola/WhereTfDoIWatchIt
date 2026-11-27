# Issue #97 Stage D identity provisioning and runtime-receipt inventory — Agent C, Stage D

**Status: PARTIAL UNBLOCK / STILL BLOCKED.** The 12 missing typed title identities are explained and reproducibly provisionable offline from retained, SHA-256-pinned evidence. One current JustWatch NL-search runtime receipt plus current quota/balance and signed scope remain unavailable; no live collection was performed.

## Route / model identity / effort / cost

| Field | Value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual model (fresh in-session probe) | `opencode/hy4-flash-free` — reply `MODEL_IDENTITY_OK opencode/hy4-flash-free` |
| Provider | `opencode` (native OpenCode CLI `1.19.6`) |
| Effort | **unpinned** — OpenCode exposes no reliable effort flag; coordinator dispatch labelled MEDIUM/HIGH, no override set; recorded honestly as unpinned |
| Cost | JSON probe `step_finish.cost: 0` (USD); textual self-report "COST unknown" for upstream work; no telemetry tool in this session |
| Substitution | none — requested = actual; no Sol/Astra/XHIGH |

Fresh probe command and output retained in this session:

```sh
opencode --version                                    # 1.19.6
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
# text: MODEL_IDENTITY_OK opencode/hy4-flash-free ; step_finish.cost: 0
```

## Exact bases and SHAs

- Worktree base: `master` @ `a79b9268ceb48e15fd75a6ec196142b172d583b3` (merge of PR #228).
- PR #228 reviewed: head `84a4625002708cf8564d09ba250d97915b2ce8de`, base `488654a22b1837acdd0acbcd810fc495449fc66d`.
- Pinned runner manifest `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`; GraphQL document `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`.
- Cohort CSV `f573eff980127bb08943ea2c101d8354c6f18a6fd659e24ca727ae0576354e30`; identity manifest `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925`.
- Sealed archive `6aaa66279d95c54d4b2bddbfe8ff677ec960182a79f71ddcfb7b95b9264147ad` (verified present and hash-matching on this host, read-only). All 15 raw TMDb payloads and the TMDb manifest hash-match their pinned digests.

## Before / after / evidence / remaining-blocker table

| Area | Before (retained reports / fresh preflight) | After this work | Evidence | Remaining blocker |
| --- | --- | --- | --- | --- |
| 12 typed identities on clean disposable target | 12 blockers — "requires the exact typed Movie title and year" | 0 identity blockers after offline provisioning; plan derived from pinned evidence, no invented identity | `_identity_blockers` at `issue97_stage_d_runner.py:104`; fresh read-only preflight on migrated disposable DB (details below) | None for identity; provisioning is opt-in and must be run on the retained target with its own attestation |
| Runtime NL-search receipt | 1 blocker — "current runtime source receipt must cover NL search only" | Still blocked; no enabled JustWatch receipt configured in any inspected DB, no evidence env vars | `review_evidence()` at `issue97_stage_d_policy.py:64`; `reviewed_permission()` at `source_permissions.py:19`; read-only SELECT on configured DBs | Current enabled JustWatch receipt: `countries:["NL"]`, `operations:["search"]`, endpoint/expiry/reviewer/reference/finite ceiling/interval |
| Independent access permission (`AVAILABILITY_STAGE_D_EVIDENCE`) | Unverified / absent | Still unavailable — no env var configured in any running backend/worker | `issue97_stage_d_policy.py:64-120`; env inventory this session | Reviewed `access_permission` record matching document SHA, all 12 scope IDs, exact target, exclusive use |
| Quota / balance / prior spend | Unverified | Still unavailable — no quota evidence file or env; canary budget 6/3 is a historical Notte function budget, not an HTTP allowance | `issue97_stage_d_policy.py:104-108`; canary `manifest.json` (restricted, read-only) | Current upstream quota class, `window_id`, `remaining`, reconciled `spent_at_review`, reviewed rate/cooldown |
| Rate / cooldown | Unverified | Still unavailable | `issue97_stage_d_policy.py:95-101` | Reviewed `maximum_in_flight`, `max_burst`, `minimum_interval_seconds`, active cooldown |
| Exclusive grant use | Unverified | Still unavailable — requires operator attestation outside PostgreSQL | runbook §4 | Cross-process/DB/scheduler/consumer attestation |
| Disposable target fingerprint | Rehearsal-only fingerprints | Rehearsal fingerprint `565b0e4c…197075` (this session, temporary); not a retained target | `database_identity()` | Named retained disposable target with attested fingerprint and migrations through 0020 |
| Signed Batch 1 token | Not issued | Not issued — prepared only the inputs; evidence/receipt/expiry are UNAVAILABLE | `check_authorization()` at `issue97_stage_d_policy.py:147` | Owner-signed HMAC token over exact scope with valid expiry and the missing evidence digests |
| Coherence year mismatch (2013 vs 2014) | Held | **Held unchanged** — excluded from the provisioning plan and from all 12 runner scopes | `stage-d-remaining-cohort.csv`, `issue97-identity-decisions.md:10` | Reviewed year decision before any admission |
| Live collection | Never executed | Never executed — zero provider/TMDb/first-party/playback calls; zero HMAC grants; no production writes/permission/quota/merge/closure | all commands this session offline/mocked | Owner authorization required |

## Why the 12 identities were missing (root cause)

The runner's identity gate (`_identity_blockers`, `issue97_stage_d_runner.py:104-114`) requires an exact `Movie` row for each scope keyed on `(tmdb_id, content_type, title, year)`. A freshly migrated disposable database has zero `Movie` rows: no provisioning step existed between migration and preflight. The retained cohort (`stage-d-remaining-cohort.csv`) and identity manifest (`identity_manifest_20261122.csv`) contain all 12 exact identities, and the sealed archive contains the 15 hash-matching raw TMDb payloads. The data was retained but never loaded into the target. Synthetic test `Movie` rows cannot substitute for this (as prior reports correctly note), which is why every clean target reported exactly 12 identity blockers.

## Offline provisioning process (reproducible, validated)

Added `backend/availability/services/issue97_stage_d_identity_provisioning.py` and management command `issue97_stage_d_identities`:

- Derives the exact 12 typed rows from the SHA-256-pinned cohort and identity manifest via `audit_identity.load_identity_manifest`; refuses any pin mismatch, altered data, unknown scope, or non-`VERIFIED_*` status.
- Hard-excludes the held Coherence row (`movie-220289-nl`, `BLOCKED_YEAR_MISMATCH`) and verifies it remains held.
- Optional `--tmdb-evidence-dir` validates every raw TMDb payload against its pinned digest (15/15 verified from the sealed archive this session).
- Read-only by default. `--apply` requires `AVAILABILITY_STAGE_D_DISPOSABLE=1` and an exact `--confirm-database-identity` fingerprint; creates only missing rows in one durable transaction, refuses to overwrite conflicting rows, and is idempotent.
- Touches no network, no grants, no quota, no runtime configuration, no production DB.

Reproduction (offline, disposable PostgreSQL, read-only code mount):

```sh
python manage.py issue97_stage_d_identities            # read-only plan report
AVAILABILITY_STAGE_D_DISPOSABLE=1 \
  python manage.py issue97_stage_d_identities --apply \
    --confirm-database-identity <fingerprint>
```

## Disposable-target preflight re-run (offline)

On a freshly migrated, internal-network, tmpfs PostgreSQL target with read-only preflight transaction:

```
BEFORE: readiness=blocked, blockers=13 (12 identity + 1 runtime receipt), scope withheld
provision: provisioned=12, ready=True
AFTER:  readiness=blocked, blockers=1 ("current runtime source receipt must cover NL search only"), scope withheld
```

`makemigrations --check --dry-run` and `migrate --check` both exit 0. Stage D ledger tables/budgets/attempts/events remained empty; zero HTTP clients opened. This proves identity provisioning resolves exactly the 12 identity blockers and nothing else.

## Tests

New module `backend/availability/tests/test_issue97_stage_d_identities.py` (7 tests) run against disposable PostgreSQL with the retained image `wtdwi-stage-d-backend-test:latest` and a read-only repo mount:

- 6 passed, 1 skipped when the restricted archive is not mounted; with `AVAILABILITY_STAGE_D_EVIDENCE_ARCHIVE` set to the sealed archive, all 3 plan tests pass including raw-payload hash validation.
- Verified: 12 exact rows, held Coherence exclusion, ordered Batch 1 scope IDs, reviewed alias `The Office`, read-only report shows 12 missing without writes, apply requires attestation + exact fingerprint, conflicting row refused, apply idempotent.

No migrations added; `git diff --check` run before push.

## Signed-scope manifest inputs for proposed Batch 1 (inputs only, NOT authorization)

Prepared from the pinned policy on a rehearsal target (fingerprint is that temporary target, not the retained target):

| Field | Value |
| --- | --- |
| experiment_id / phase / batch | `issue97-stage-d` / `titles-v1` / `1` |
| scope_ids | `movie-238-nl`, `series-1396-nl` |
| manifest_sha256 | `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727` |
| rehearsal database_identity | `565b0e4c4ea6dff3c3f938cd9d020a341f964caada0c55400a022fdf20197075` (retained target TBD) |
| authorization_id | `e28e54fb-6d1b-5095-9f52-0d9d699e8be5` (recomputed from actual target at signing) |
| endpoint / operation / method | `https://apis.justwatch.com/graphql` / `GetSearchTitles` / `POST` |
| document_sha256 | `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51` |
| maximum_attempts / batch / retries | `12` / `2` / `0` |
| prior_checkpoints_sha256 | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` (empty prior) |
| identity evidence | `movie-238-nl` `f81b13ca…8ee2b`; `series-1396-nl` `7685411f…c1f14` |
| evidence_sha256 / runtime_receipt_sha256 / expires_at | **UNAVAILABLE** — require current reviewed evidence, receipt and owner signature |

The proposed batch (The Godfather 1972, Breaking Bad 2008, NL, GetSearchTitles, ≤2 first-sends, 0 retries, concurrency 1, no season queries, no fallback endpoints) is **not authorized** by this document.

## Forbidden operations — none performed

No live provider/TMDb/first-party/playback request; no HMAC grant or signing key; no quota/scope expansion; no runtime permission change; no production write or deployment; no PR merge/closure; no issue closure; no cleanup; no permission change. Issue #97 and PR #221 remain open. The Coherence hold is preserved.
