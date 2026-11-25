# Agent E independent review — PR #198 (Issue #97 offer dedup)

## Verdict

**RECOMMEND_APPROVAL (conditional)** — no blocking findings in the PR #198 code at the reviewed head. Final merge remains gated on hosted CI clearing (billing block external to this review) and on ChatGPT Web's final review/merge decision; this review does not approve or merge anything.

## Scope

- Review branch: `review/97-pr198-independent-final`
- Reviewed head SHA: `85774f40ba31f283a148df625f80c0f1d4418188` (PR #198 head)
- Base: `master 54bd248060880523ce63701b46c031508f99338f`
- Diff reviewed: `master..85774f4` (full #198 diff, 68 files)
- Affected PR: #198 (draft). Cross-references: Issue #97 (open), Issue #103 (hold).
- Model/route: opencode / nemotron-3.5-super-vl-free, effort = UNPINNED
- Cost: COST_UNKNOWN
- Mode: read-only review; no production DB writes/migrations, no live provider calls, no permission changes, no CI approval/grant, no force-push/history rewrite.

## Method

1. Full diff review of `master..85774f4`.
2. Independent reproduction: focused backend suite then full backend suite on disposable PostgreSQL 16 (Docker tmpfs, internal network, zero published ports) with mocked transport and synthetic fixtures.
3. `makemigrations --check --dry-run` migration-drift check.
4. Migration `0018_offerprice_variants` round-trip / downgrade / upgrade verification via `test_offerprice_migration`.
5. Migration order cross-check with `0019_stage_d_run_ledger` / `0020_merge_stage_d_offerprice_variants` on `stage/97-bounded-runner`.
6. Public HTTP response and offline projection review for private identifiers, provider UUIDs, signed URLs, query strings, restricted evidence.
7. Hosted CI evidence review (in-repo documentation; live GitHub API not reachable from this environment — see CI section).

## Test results (independently reproduced)

| Check | Result |
|---|---|
| Focused backend suite (#198-relevant: issue97_collection, justwatch_integrity/regression/available_to/reviewed_transport, offer_prices, reconciliation, offerprice_migration, audit_provider_mapping; tracker contract/stale/season/leaving-soon/bulk_refresh/refresh_worker/title_level/guarded_migrate) | **345 passed**, 0 failures, 0 errors, 0 skips |
| Full backend suite (`manage.py test --noinput`) | **3,804 passed**, 0 failures, 0 errors, 0 skips |
| `makemigrations --check --dry-run` | **No changes detected** (no model drift) |
| `test_offerprice_migration` (0018 round-trip/downgrade/upgrade) | **Passed** (included in focused suite) |

The "justwatch exploded", "db connection lost", scraper "no HTML", and justwatch retry-on-429/500 log lines observed during runs are intentional mocked error-path assertions, not failures.

## Migration verification — `0018_offerprice_variants`

- Adds a `NOT NULL` JSONB `variants` column to `OfferPrice` with `default=list`; depends on `availability.0017_justwatch_request_budget`.
- Verified safe on existing rows: upgrade, downgrade to 0017, and re-upgrade each preserve IDs, timestamps, scalar price, offer/evidence/run/title-check provenance; existing rows receive `variants=[]`. A populated variant payload is lost on downgrade and restored as `[]` on re-upgrade (documented in runbook).
- Old-writer INSERT against 0018 is rejected (no DB default) → coordinated stop-the-writers cutover required; runbook in `docs/operations/issue97_migration_preservation_verification.md#release-runbook-for-availability-0018`.
- Django model drift: none.

## Migration order — 0018 / 0019 / 0020

Cross-checked against `stage/97-bounded-runner` (`origin/stage/97-bounded-runner`):
- `0018_offerprice_variants` (this PR): child of `0017`.
- `0019_stage_d_run_ledger` (draft #206 stage-d): child of `0017` — a **sibling leaf** of 0018, not a descendant.
- `0020_merge_stage_d_offerprice_variants`: merge migration depending on **both** `0018` and `0019`; no operations.

Conclusion: 0018 and 0019 are sibling leaves of 0017. A combined release must apply the 0020 merge migration (depending on both leaves) or repoint 0019 onto 0018 to linearize. The runbook's guidance (apply 0018 first, guarded runner, rehearse on disposable PostgreSQL) is consistent. No graph defect in #198's 0018.

## Public HTTP / offline projection privacy review

- `movie_availability` → `tracker/services/availability_reads.py` projects variants through `public_offer_variant()` (allowlisted display fields only: provider, country, offer class, quality, amount, currency) plus `_variant_display_url()`.
- `_variant_display_url` strips query strings and fragments, permits only http/https, omits embedded credentials and unsupported/unsafe schemes; backslashes rejected.
- Offline replay (`availability/services/issue97_collection.py`) uses the same `public_offer_variant` projection without variant links; audit publishes only an aggregate digest and count.
- No provider UUIDs, signed URLs, query strings, or raw private variant fields (package_node_id, element_count, reported_price_value, available_to, source-offer internals) reach public HTTP responses or the offline export. PostgreSQL retains complete variants for reconciliation only.

## Findings

No findings require a change to #198's code at head `85774f4`.

1. **Swarm P1 — cheapest-variant expiry suppresses still-open offer** (`refresh.py`, `reconciliation.py`). Addressed in current head: `window_open` is `True` if any same-source variant is open (`own_price.open_variants`), not only the representative's window; reconciliation overrides `provider_url`/`quality` from `selected_variant`. Verified by passing `test_justwatch_regression` / `test_reconciliation`. Severity: none (resolved).
2. **Swarm P1 — contradictory replay edge credited to reviewed title** (`issue97_collection.py`). Addressed in current head: `conflicting_label` check rejects any coalesced same-native edge with nonempty conflicting title/year. Severity: none (resolved).
3. **Swarm P2 — title/year fallback drops variants from partial edges** (`justwatch.py`). Addressed in current head: `_merge_title_matches` retains compatible same-native edges whose missing metadata does not contradict the anchor, while still rejecting distinct/typed-scope/conflicting-ID claims. Severity: none (resolved).
4. **P2, present on master — distinct TMDb season ID hides valid season observation** (`justwatch.py`, `refresh.py`, `reconciliation.py`). Pre-existing on `master`, not introduced by #198; same fixture fails on verified master and the PR. Severity: low. Affected: master + #198. Recommended correction: follow-up outside #198 — keep the native season ID and season slot, explicitly associate the offer mapping with its confirmed parent series, preserve the child TMDb ID as separate evidence; add regression coverage `test_child_tmdb_id_is_distinct_from_parent_key_used_by_season_read`.

## Hosted CI

- Hosted CI is **billing/spending-limit blocked**: all jobs stopped before their first step; no test step ran. Documented in `docs/audits/issue97/pr198-integration-verification.md`, `docs/audits/issue97/http-variant-projection-verification.md`, `docs/audits/issue97/swarm-reports/pr198-review-a2.md`, `docs/audits/issue97/swarm-reports/integration-final-review-a5.md`, and `docs/operations/issue97_migration_preservation_verification.md`, citing runs `45573972544`, `45569700694`, `45444414756`, `45471338314`.
- This is **not a test failure** and must not be characterized as one.
- Live GitHub API (`api.github.com/.../actions/runs/...`) returned 404 from this environment (repo not reachable / requires auth); run details could not be independently fetched. The consistent in-repo documentation across four run IDs is the available evidence.
- Per GLOBAL STOP: local PASS != hosted CI PASS; local results cover remediation only and do not substitute for a hosted pass.

## One-time local-test CI exception — evaluation only (recommend; do NOT approve or grant)

Proposal: accept the locally reproduced passing results (345 focused / 3,804 full, disposable PostgreSQL, mocked transport) as a one-time substitute for the billing-blocked hosted CI so #198 can proceed to review.

Evaluation: the local runs use the same Docker/disposable-PostgreSQL/mocked-transport seams as CI and provide real signal, but they run outside the hosted environment (different config/secrets, no CI-specific surface) and GLOBAL STOP explicitly states local PASS != hosted CI PASS. Accepting them as a permanent or broad substitute would weaken the CI gate and set precedent.

Recommendation (non-binding, non-approval): at most a **narrow, time-boxed, owner-documented one-time exception** allowing *review to proceed*, explicitly conditional on hosted CI being re-run and passing once billing clears, before any merge; not a waiver for this or future PRs, not granted by this review, and recorded in the PR discussion. Final approval/grant belongs to the release/merge owner, not this review.

## Blockers

- Hosted CI billing/spending limit (external): release owner must clear before hosted checks run and final merge sign-off.
- This environment cannot reach GitHub (API 404), so the required GitHub comment on PR #198 could not be posted from here; it should be posted by the coordinator/release owner with the contents below.

## Recommended correction

None required for #198 code. Merge is recommended conditional on: (a) hosted CI clearing after billing is restored, (b) the coordinated writer cutover for 0018 per the runbook, (c) the 0018→0019→0020 graph/ordering for any combined release with draft #206, (d) tracking finding #4 (season ID mismatch) as a master-level follow-up, and (e) ChatGPT Web's final review and merge decision (#198 remains OPEN/DRAFT; ChatGPT Web is sole final reviewer/merger).

## Evidence references (sanitized, already public in this repo)

- `docs/audits/issue97/pr198-integration-verification.md`
- `docs/audits/issue97/http-variant-projection-verification.md`
- `docs/audits/issue97/evidence-custody-verification.md`
- `docs/audits/issue97/swarm-reports/pr198-review-a2.md`
- `docs/audits/issue97/swarm-reports/integration-final-review-a5.md`
- `docs/operations/issue97_migration_preservation_verification.md`
- `docs/source-permission/decision-2026-11-24.md` (`justwatch_permission: OPEN`)
- Cited hosted-CI runs: `45573972544`, `45569700694`, `45444414756`, `45471338314`

No restricted payload, credential, signed URL, provider UUID, or raw canary evidence is reproduced in this report; all restricted originals remain in custody outside Git per `evidence-custody-verification.md` (20/20 SHA-256 and byte-count match preserved).
