# Incremental reconciliation deployment preparation — 2026-11-18

**DEPLOYMENT_PREPARATION_COMPLETE** — ready for separately authorized execution;
no feature deployment, live gate, merge or push was performed.

## Architecture and deliverables

The reviewed standalone backend-only Compose release pins exact image IDs, runs
Gunicorn without reload, mounts no application source, uses a read-only root with
temporary static/tmp files, and retains the baked migration guard. It connects to
the existing `new-sources_default` network without managing the database volume,
frontend or workers. A frozen raw environment preserves provider/API settings and
forces all three scheduler modes off. The controller and bundle are copied outside
Git worktrees only under Gate A approval; subsequent Git edits cannot reach code
inside the release container.

The existing untracked deployment drafts were preserved in a private archive,
reviewed and completed in the isolated integration worktree. Application source
at the validated feature revision is unchanged. The executable procedure,
including every exact gate command and recovery operation, is in
[the runbook](../sources/INCREMENTAL_DEPLOYMENT.md).

| Artifact | Value |
| --- | --- |
| Isolated branch | `integration/incremental-catalogue-safety` |
| Target local/origin branch | `new-sources`, unchanged at `a7ce47f5a15107ea8df1c39c740e8699f0f6cd68` |
| Feature application SHA | `11cc7416503c300b701945c8d39a7a2eaf6f943d` |
| Old application image | `sha256:3417517486d65b9e2fea71ce8d8cf29db2e2a5364706fc167da09d181b5efa36` |
| Feature application image | `sha256:3e1bfcaf1b944cdde14c03ff6b463c4a2bb3b3646e6d5e03719030e3f695e1db` |
| Dependency image | `sha256:4a5a7aa75a4222f57a76722a2249826684ad6a6f6f73e8b3e3ce3e0a8d9d1fd7` |

Both offline rebuilds reproduced the exact pinned image IDs. The new 69,536,522-byte
recovery archive passed checksum, gzip/tar inspection and a real Docker image load.
Its SHA-256 is `2287fa4fe0698299ba2edd5e6591d7c60fbecb71240f39d97162bf0446ee5194`.
The earlier incomplete `release-images-r3.tar.gz` failed CRC validation; it was
preserved and is explicitly excluded from recovery instructions.

## Verification

| Check | Fresh result |
| --- | --- |
| Expanded focused feature baseline | 160 passed |
| Focused integration baseline | 112 passed |
| Complete backend suite | 3,382 passed |
| Deployment controller safeguards | 21 passed |
| Full deployment/recovery rehearsal | 20 scenarios passed |
| Migration consistency / Django system checks | Passed |
| Effective base/dev/frontend overrides and frozen release Compose | Validated |
| Same source and dependency rebuild | Exact image IDs reproduced |
| Recovery image archive | Validated and loaded |
| Peer `/api/providers/` through service DNS | HTTP 200 on disposable internal network |

The focused groups overlap; they are not additional unique backend test counts.
The 160-test selection reproduces the original review's expanded module list,
while the 112-test group reproduces the later integration investigation's narrower
selection. Neither baseline was reduced or relaxed.

The rehearsal starts the old mutable application at tracker 0056, applies source
isolation, edits the former source directory and confirms no reload, applies 0057
then 0058, verifies the old application against the expanded schema, deploys the
feature and checks API/guard health. It checks repeated deployment without container
recreation, zero-selection/zero-request replay and one offline bounded canary.

Fault coverage includes startup failure, failed health checks, SIGKILL during a
release, migration failure after 0057, refusal of C after B fails, old-application
rollback on partial 0057, resumed migration, repeated migration, restore-based
recovery, and recovery when a database rename commits before its progress journal.
Guard and restore journals sync files and directory entries before advancing.
Incomplete or failed restore operations block forward gates. A refused or partial
canary cannot be marked passed.

The original strict migration reverse/reapply and fixture-only reconciliation
validators also passed. Original application data remained identical, the 326
unresolved outcomes stayed BASELINED with zero attempts/retry dates, all offers
remained absent, and all 50 migrated application tables matched after fixture
rollback. The complete rehearsal ended with the old backend healthy and the exact
original 0056 data/schema restored. Only newly created clone containers/networks
were removed.

## Live preservation and actual runtime state

Initial inspection found the live backend ALREADY restarting with exit 1 after a
Docker-host restart. Its original source mount points at the feature checkout,
while live schema is tracker 0056 / availability 0016. This is consistent with the
pending-migration guard refusal reproduced on a clone. Preparation did not restart,
stop, repair or recreate that backend; its identity/image/mounts/config remain the
same. The existing restart loop continued independently. Main and other worktrees
had advanced since the previous report; their inspected refs were preserved.

Read-only snapshots before/after preparation matched all 48 application tables and
migration history. No live migrations, application writes, reconciliations, crawls,
canaries or schedule changes occurred. Existing stashes and refs were identical
before committing this preparation; only the isolated branch is advanced by the
preparation commit. The original checkout, its unrelated audit files, existing
backup ref and all existing worktrees/backups remain preserved.

| Live invariant | Verified result |
| --- | --- |
| Discoveries | 5,095 unchanged |
| Legacy mappings | 3,300 unchanged |
| Canonical associations | 4,769 unchanged |
| Unresolved discoveries | 326 unchanged |
| Playback offers | 0 |
| Canonical and provider-native identities | Unique/preserved |
| Existing proofs, evidence, mappings and provider settings | Exact digests unchanged |
| Historical/incremental discovery checkpoints | Exact digest unchanged |
| Scheduler modes | Historical, incremental and reconciliation disabled |
| Live schema | Tracker 0056 / availability 0016 |

## Approvals, recovery and remaining risks

Gate A separately approves source isolation and backend replacement/restart. Gate B
separately approves exclusive maintenance, a fresh restore-tested backup, and live
0057/0058. Gate C separately approves the feature release and its live health check.
Gate D separately approves one offline, bounded reconciliation cycle. Gate E is
readiness review only; no scheduler activation tooling is included. Activation
requires its own provider-specific configuration, budgets, network authorization,
monitoring and disable plan. A failed gate prevents progression.

The old application was verified on 0056, partial 0057 and complete 0058. Preferred
application rollback keeps the expanded schema and all reconciliation state. Do
not reverse 0057 in live recovery: dropping the new tables can erase cooldowns,
leases, outcomes and checkpoints. 0058's reverse is a no-op. Destructive logical
recovery therefore restores the verified backup to a new database, validates it,
retains the failed database under a separate name, and promotes the restore only
under explicit acceptance of post-backup data loss and exclusive maintenance.

There are no remaining preparation blockers. Live execution approvals are still
required, the current live restart loop is not repaired, live host-port routing
will be checked only during authorized execution, and the strict fixed-baseline
checks must be refreshed if legitimate user writes occur before deployment.
Private environment/config snapshots, backup/image archives and raw logs must be
retained securely; they are ignored by Git.

Private fresh evidence:
`.catalogue-staging/deployment-final-20261118T101748Z/` — `backend-verification/`,
`full-rehearsal/`, `controller-tests.log`, `live-bundle/`, live preservation hashes,
reproduced image manifests, and the verified archive/manifest. Sanitized test,
image and integrity results are also retained in the adjacent JSON audit.
