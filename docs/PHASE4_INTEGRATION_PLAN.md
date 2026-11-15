# Phase 4 Integration Plan (WhereTfDoIWatchIt)

## Current baseline

- Integration branch: `feature/scrapres-improvedment`
- HEAD: `0044ce18dfb6b5be8f7e124effa7d9ce4e041db0`
- Audit commit (unpublished): `4210ee8` + `7affa8b` (worktree `feature/audit-mapping`)

## Delegated work (Wave 1)

- Beam verification (sa-0-91aab470): audit_provider_mapping correction + tests. Branch/worktree TBD by agent. Status: IN PROGRESS.
- Step fixture pack (sa-1-bf164b7f): synthetic TMDb matching fixtures. Status: IN PROGRESS.
- Jules A/B/C: managed by separate session. Status: not accessible from this invocation.

## PR merge sequence (recommended)

1. Audit/fixture improvements (independent) — Beam + Step, if non-overlapping.
2. Jules C — correction impact planner (read-only).
3. Jules A — TMDb candidate discovery.
4. Jules B — RaiPlay targeted evidence.

Adjust based on actual dependencies and conflicts.

## Database migrations / runtime

- Audit changes: no schema changes expected (read-only command + tests).
- Jules A/B may introduce models/migrations — verify with `makemigrations --check` and `migrate --plan` (no apply to existing local DB per constraints).
- Runtime rebuilds: backend image rebuild only if dependencies change; no container recreation.

## Rollback

- Revert the merge commit on the integration branch; worktree branches remain for rework.
- No data mutation performed in Wave 1 (read-only constraints).

## Wave 2 dependent task specs (do NOT implement until Wave 1 contracts reviewed)

### A. Verified mapping acceptance
- Validate TMDb details/identity/type.
- Safe local Movie creation/enrichment.
- Explicit review acceptance/rejection.
- ContentMapping revision handling.
- No unsafe overwrite of manual mappings.
- Audited correction/invalidation semantics.
- Enqueue recovery jobs without creating Offers.

### B. Offer recovery orchestration
- Durable idempotent jobs.
- Bounded targeted provider fetches.
- Canonical availability service persistence.
- Correct evidence and price lifecycle.
- No false watchability or unrelated deactivation.
- No full-catalogue checkpoint interference.

### C. Canonical mapping review UI
- Inspect candidates and evidence.
- Accept/reject with reason.
- Review ambiguity and confidence.
- Show mapping vs Offer status separately.

## Blockers

- Jules PRs not accessible from this environment; review deferred.
- Live TMDb/RaiPlay requests prohibited; all verification is offline/synthetic.
- No migrations applied to existing local DB.
