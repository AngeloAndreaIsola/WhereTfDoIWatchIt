# Evidence preservation record — 2026-11-19 consolidation

The three reports added in this PR existed only as **untracked** files in the
`.worktrees/new-sources` worktree (branch `hardening/discovery-request-controls`,
HEAD `7b51710`, fully contained in `master` via PR #130). They would not have
survived a worktree prune or fresh clone. They are committed here, unmodified,
after a credential pattern scan found no secrets.

## Reports

| File | Outcome token | Meaning |
|---|---|---|
| `bulk_canonical_final_20261117.md` | `BULK_CANONICAL_AUDIT_PASSED` | Offline audit of the bulk canonical release (code `a7ce47f5`); 227 focused + 3,342 backend tests on ephemeral PostgreSQL; no live provider requests. |
| `bulk_canonical_live_20261118.md` | `LIVE_IMPORT_COMPLETE` | Explicitly authorized guarded import into Compose `new-sources` `tracker@db`; 4,769 canonical associations (4,382 inserted / 387 reused); backup + restore rehearsal verified. |
| `live_discovery_reconciliation_canary_20261118.md` | `END_TO_END_CANARY_BLOCKED` | First live canary attempt stopped at preflight: no safe executable discovery command and no current source-permission evidence. No discovery run, no external HTTP requests. |

These records are evidence for issues #122–#126 (catalogue bridge gating) and do
not authorize any live collector, deployment, or schedule activation.

## Bulk evidence outside git

The ignored `.catalogue-staging/` tree from the same worktree (119 MB, 2,111
files: import tooling, canary attempt records, hardening source snapshots,
manifests and checkpoints) was copied to a durable location **outside** any
worktree before cleanup:

- Backup: `~/Documents/GitHub/wtdw-evidence-preservation-20261119/`
- Integrity: per-file SHA-256 manifest (`SHA256MANIFEST.json`, `SHA256SUMS.txt`);
  2,114/2,114 files re-hashed after copy with 0 mismatches.
- The backup is deliberately not committed: it contains runtime dumps and
  test-fixture source snapshots (dummy test tokens only; no real credentials).

## Stashes and recovery refs

Five stashes (`59062ab`, `2829461`, `a37fb65`, `f626daa`, `f950365`) and the
preservation branches `preserve/stage2d-audit-mighty-lagoon`,
`preserve/worktree-cleanup-report`, `wip/runtime-recovery-evidence-wtdw` are
recorded in the backup README and must not be deleted without separate
authorization.
