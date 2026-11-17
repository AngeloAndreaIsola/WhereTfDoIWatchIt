# Incremental unofficial catalogue reconciliation — 2026-11-18

Branch: `feature/incremental-catalogue-reconciliation`, based on `a7ce47f`.
No merge, live migration, live catalogue write or unattended schedule activation.

## 1. New-record detection

Discoveries without an outcome are eligible. Matching-input fingerprint changes
also qualify older IDs; observation timestamps, page changes and feed build
times do not. Associated records with a missing mapping qualify for repair.
Unresolved records need fresh relevant verification evidence, a due retry, or
bounded explicit review. The selector reads eligibility metadata across the
inventory; only selected records enter matching.

The baseline migration acknowledges the completed inventory through ID 5,095
and update time `2026-11-17T23:09:14.771940Z`. Later discoveries and edits are
excluded from that acknowledgement and remain eligible on deployment. The
baseline records 4,769 existing associations and 326 deferred discoveries,
with zero immediate matching work. It does not repeat bulk reconciliation.

## 2. Incremental matching

The existing bootstrap evaluator is extracted into the shared
`CanonicalVerifier`; the bootstrap command and guarded manifest importer still
use it. Incremental orchestration reuses the TMDb candidate gateway and existing
classification/association writer. Existing canonical identities and fresh
details/alternative-title caches can be reused without HTTP. New lookups require
explicit network authorization and an enforced finite budget.

Movie/series IDs remain separate. Exact title, year, details, trusted-ID
provenance, duplicate source-release and search-completeness guards remain.
Fuzzy proposals are not sufficient for association. Missing years/types stay
unknown. Expired verification cannot authorize a new association. Conflicting
edits preserve the prior association and proof. Same-identity proof refresh is
permitted after fresh verification. Ambiguous, unmatched, conflicting, blocked,
cache-miss and error outcomes are durable.

## 3. Idempotency

On the restored live snapshot, one new HydraHD observation became one provider
identity, one canonical association and one record-specific proof. Replaying
selected zero records and created no duplicates. RSS GUID replay retained one
discovery; missing-year RSS classification produced no canonical match or HTTP.
The existing guarded bulk-manifest replay continues to preserve every row.

## 4. Checkpoint and crash recovery

Proof, association, outcome and successful cursor commit in one transaction.
Interruption retained the first record's commit and resumed only the remaining
record. A crash inside association creation rolled back partial proof/outcome;
resume reused fresh cached responses. Expired leases recovered; live/replaced
leases rejected writes. An independent PostgreSQL session was excluded by the
existing advisory lock. Reporting failures still release the lock.

The last-completed ID is diagnostic, not a high-water eligibility filter.
Historical and incremental discovery cursors remain independent and unchanged.
Legacy bootstrap file fingerprints retain their checkpoint format.

## 5. API budgets

The new HydraHD match used two intercepted HTTP attempts under a budget of two.
A budget of one stopped between search and details; resume used the cached
search and exactly one details request. Retries share the same hard budget.
HTTP 429 stopped after one attempt and persisted its 900-second cooldown.
Missing metadata and fresh cached identity reuse spent zero HTTP requests.

CLI/service limits: 1–250 records, 1–500 HTTP attempts, 0–2 retries.
Scheduled budgets remain capped at 40 HTTP attempts per provider/mode.
Known-year unmatched records defer 30 days; transport errors back off from five
minutes to one day. Ambiguous/conflicting/missing-metadata/cache-only outcomes
wait for changed inputs, fresh evidence or explicit review.

## 6. Scheduler readiness and expansion

The existing catalogue-worker scheduler now accepts independent `historical`,
`incremental` and `reconciliation` configuration, including enable switches,
provider limits, intervals, budgets, cooldowns and error reports. Compose passes
its existing opt-in JSON setting to the catalogue worker. All schedules remain
disabled by default. No scheduling framework or second matching engine added.

Read-only live verification confirmed HydraHD historical pages **132 movies /
77 TV**, and AnimeWorld's permitted RSS snapshot at page 1. Existing HydraHD
and RSS collectors are preserved. No restricted archive or unverified-domain
discovery was attempted. New batches need no bulk matching operation.

## 7. Validation

- **142 focused PostgreSQL tests passed**, covering discovery, identity,
  bootstrap, reconciliation, incremental scheduling, gateway budgets and guarded
  manifest replay.
- **3,377 complete isolated PostgreSQL backend tests passed**.
- `makemigrations --check --dry-run`: no changes detected.
- `migrate --check`, Django system checks, Compose configuration validation and
  `git diff --check`: passed.
- Fresh live dump restored into the separate `incremental_catalogue` database;
  migrations `0057`/`0058` applied and reverse/reapply exercised there only.
- Fixture-driven rehearsal passed all scenarios: new HydraHD discovery, stable
  RSS GUID/missing year, conflicting metadata, interrupted/atomic commits,
  expired leases/evidence, concurrent jobs, unresolved candidates, hard budgets
  and rate limits. All HTTP in this rehearsal was intercepted.
- All **50** migrated application tables matched before/after rehearsal rollback.
- A PostgreSQL-enforced read-only live audit matched all **48** original
  application tables to the restored clone. Live remains on tracker `0056` and
  availability `0016`; clone alone is on tracker `0058`.
- **Zero playback offers**. Discovery and association never infer availability.

The initial full-suite run found a replay timestamp regression during verifier
extraction. Evidence refresh is now limited to incremental same-identity
verification; the guarded importer retains unchanged replay behavior.

## 8. Remaining blockers

No implementation blocker. Deployment still requires separately authorized
live migrations and, if desired, explicit schedule/network configuration.
The existing 326 unresolved discoveries remain deferred; ambiguous/missing-year
records still require stronger evidence. Synthetic HTTP responses validate the
workflow, not the real-world identity of a newly released title. Language
enrichment remains out of scope.

## 9. Commit and reproduction

The verified implementation and this report are committed on the feature branch;
the final task response records the commit SHA. The corresponding JSON report
contains the acceptance results and clone evidence.

See [operational runbook](../sources/INCREMENTAL_RECONCILIATION.md) and
`scripts/validate_incremental_catalogue.py`. Complete isolated test command:

```sh
./scripts/test-backend-isolated.sh tracker availability -v 1
```
