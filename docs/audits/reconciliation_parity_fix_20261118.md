# Canonical reconciliation dry-run/apply parity — 2026-11-18

**RECONCILIATION_PARITY_FIXED**

The Gate D conflict case now returns CONFLICTING in both dry-run and apply. Existing mappings, canonical metadata and record-specific proofs remain intact. Work was performed on `integration/reconciliation-parity`, based on the existing integration safety branch at `c5e692e`, in a separate Orca worktree. No merge, push, deployment or live reconciliation occurred.

## Root cause and implementation

The [root-cause investigation](reconciliation_parity_root_cause_20261118.md) was recorded before implementation edits. Both modes use the same exact candidate verifier, but preview returned VERIFIED before apply's mapping/proof conflict checks in `_commit_record` / `persist_verified`. The discrepancy existed with equivalent database state and did not require a race. Existing HTTP attempt accounting already included requests spent on rejected proposals.

Added `canonical_association_validation.py`, a shared validator that creates or updates no rows. It checks typed mapping targets, the mapping's provider-content link, trusted identifiers, stored proof identity/internal canonical evidence and canonical year. Both modes validate verified candidates and reused proofs. Apply repeats validation against fresh rows with locks inside its atomic commit. Existing lease/fingerprint/expiry checks, final writer conflict checks and uniqueness constraints remain in place for concurrent edits and absent-row races.

Rejected associations retain request counts, candidates and rejection evidence. Apply records its normal outcome/run/checkpoint bookkeeping while skipping candidate/proof/mapping/classification writes for association conflicts. Same-identity proof refresh remains supported; movie/TV identities, matching thresholds, budgets, cooldowns, leases and checkpoint behavior are preserved. No migrations were added.

## Changed files

- `backend/tracker/services/canonical_association_validation.py`: shared read-only conflict decisions and optional transactional row locking.
- `backend/tracker/services/incremental_catalogue_reconciliation.py`: shared preview validation and locked commit revalidation; rejected-association bookkeeping.
- `backend/tracker/tests/test_reconciliation_parity.py`: 15 deterministic regressions.
- `docs/audits/reconciliation_parity_root_cause_20261118.md`: pre-edit investigation.
- `docs/audits/reconciliation_parity_fix_20261118.md`: this validation report.

## Regression and full-suite results

| Validation | Result |
| --- | --- |
| Focused PostgreSQL tests | **154 passed**, 6.144s |
| Complete backend suite | **3397 passed**, 108.785s |
| Original isolated rehearsal | **18/18 passed**, no defects |
| Original movie/TV conflict case | Dry-run CONFLICTING / apply CONFLICTING |
| Rehearsal HTTP fixture attempts | 37; zero external HTTP requests |
| Django system checks | Passed |
| Migration consistency / pending migration checks | Passed, no changes |
| Git whitespace / changed Python parsing | Passed |

The 15 added regressions cover same-identity association reuse, conflicting canonical targets, numeric movie/TV separation, existing wrong-type mappings, contradictory provider-content links, proof rebinding, internally contradictory proof evidence, trusted-ID changes, ambiguous and unmatched outcomes, idempotent replay, rejected-proposal request accounting, proof changes after validation, canonical year changes after verification and a mapping update from an independent PostgreSQL connection between validation and commit. Preview tests compare all modeled application rows before and after to prove no writes.

The first complete-suite attempt ran 3,397 tests with two harness failures: the strict DNS audit raised AssertionError when admin security tests resolved optional internal host `frontend`. No DNS/API packet was sent. A deterministic absent-host `socket.gaierror` fixture restored the expected security-test environment while all actual DNS and external HTTP remained blocked. The rerun passed all 3,397 tests; no production code changed to accommodate the harness.

## Isolated clone rehearsal

Restored the checksum-validated Gate B catalogue backup (SHA-256 `5c03a62843269579989b64cf95545398afb41a0dbb897d9d9ce21661bbabaa13`) into a dedicated PostgreSQL container. Before writes, verified the exact `incremental_gate_d_parity_123948` database, `clone_rehearsal` role, Unix socket and server identity. Both database and runners used network `none`; runners received no live credentials. Read-only mounts overlaid only the three changed Python files onto the deployed dependency image for testing. No corrected application image was built.

The historical backup required tracker 0057/0058 migrations only on the clone. All 48 original application tables survived migration unchanged; the resulting 60-table / 50-application-table baseline had 4,769 associations, 326 deferred records and zero offers. Every original scenario was repeated: new movie and series, observation noise/meaningful edits, identity reuse, ambiguity/unmatched/deferred evidence, typed mapping conflict, shared budgets/retries, 429/Retry-After/cooldown, interruption and checkpoints, expired/fresh leases, concurrent exclusion, atomic rollback, record limits/restrictions, two-second pacing, expired/mismatching proof, and independently observed durable commit/resume.

Each scenario checked all restored baseline rows, table deltas, association consistency and absence of offers. The 326 original deferred records and all original mappings, canonical metadata, provider settings and discovery checkpoints remained intact. Replay selected zero completed records. A second PostgreSQL connection observed committed proof/mapping/checkpoint after interruption; resume processed only the unfinished record from cache.

Transactional fixtures rolled back; committed recovery fixtures were explicitly removed. The final full clone snapshot matched every baseline table, schema, constraint, index, owner and framework row. Expected clone sequence advances from fixture inserts are enumerated in the evidence. The disposable database container and both task-owned volumes were removed.

## Live preservation and limits

Read-only before/after verification matched all **60 public / 50 application table digests**, schema and sequences exactly. All **4,769 associations** and **326 deferred discoveries** are preserved, with no live reconciliation runs or record attempts. Backend image `sha256:3e1bfcaf1b944cdde14c03ff6b463c4a2bb3b3646e6d5e03719030e3f695e1db`, revision `11cc7416503c300b701945c8d39a7a2eaf6f943d`, migration state 0058/0016, backend/container identities and zero restart count remain unchanged. Backend healthy; historical, incremental and reconciliation schedulers disabled; offers remain zero.

Preview remains a point-in-time decision. Apply explicitly detects changes under its transaction; it cannot reserve future state for an earlier preview. Real TMDb credentials, real provider-origin matching evidence and network behavior were not exercised. A corrected immutable image and controlled patch deployment require separate authorization.

Private evidence directory: `/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/parity-fix-20261118T123638Z`. `RESULT.json`, `REHEARSAL_RESULTS.json`, complete focused/full test stdout/stderr, `PRESERVATION.json`, full pre/post snapshots, source SHA-256 values, commands and test/fixture source are retained there. The external result/commit receipt records the final local commit SHA, avoiding a self-referential hash in this committed report.
