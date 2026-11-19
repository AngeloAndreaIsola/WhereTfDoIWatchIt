# Incremental reconciliation deployment runbook

Status: prepared for separately authorized execution. Nothing in this runbook
grants permission to operate the live project. Run each gate only after its own
approval. A failed command or receipt stops progression. Never merge or push as
part of these commands.

## Architecture and inspected target

Use a backend-only, standalone Compose configuration, not another overlay on the
development stack. `ops/deployment/compose.release.json` has only `backend` and a
profile-gated one-off `migrate` service. It has no database, frontend, worker,
volume declaration, application bind mount, development command, or build action.
It attaches to the existing database network. Gate commands target only backend
with `--no-deps --no-build --pull never`; they never call `down` or remove orphans.
This preserves the current database volume and frontend, including its override.
Docker documents these [Compose service operations](https://docs.docker.com/reference/cli/docker/compose/up/).

The image uses archived Git application source, an inspected local dependency
image, and a baked migration guard. It runs Gunicorn without reload, a read-only
root filesystem, and temporary static-file/tmp directories. SHA labels identify
both application source and runtime utility content. Actual deployment references
the immutable image ID, never just a mutable tag. A saved image archive supports
offline recovery. Git/worktree edits have no path into its filesystem.

Frozen environment files use Compose's
[raw env_file format](https://docs.docker.com/reference/compose-file/services/#env_file)
to preserve literal dollars/quotes in existing settings. Compose 2.30+ is required
for this format; the inspected version is 5.7.0. `UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES`
is explicitly empty in both services regardless of captured environment.

Inspected live identifiers:

| Item | Exact value |
| --- | --- |
| Project | `new-sources` |
| Backend | `new-sources-backend-1` |
| Database container | `new-sources-db-1` |
| Database / host | `tracker` / `db` |
| Existing network | `new-sources_default` |
| Existing data volume | `new-sources_pgdata` |
| Existing backend publication | `127.0.0.1:8000` → `8000` |
| Old application revision | `a7ce47f5a15107ea8df1c39c740e8699f0f6cd68` |
| Feature application revision | `11cc7416503c300b701945c8d39a7a2eaf6f943d` |
| Old application image | `sha256:3417517486d65b9e2fea71ce8d8cf29db2e2a5364706fc167da09d181b5efa36` |
| Feature application image | `sha256:3e1bfcaf1b944cdde14c03ff6b463c4a2bb3b3646e6d5e03719030e3f695e1db` |
| Dependency image | `sha256:4a5a7aa75a4222f57a76722a2249826684ad6a6f6f73e8b3e3ce3e0a8d9d1fd7` |

The live backend currently runs `runserver` with autoreload and mounts
`.worktrees/new-sources/backend` at `/app`, plus that checkout's entrypoint at
`/runtime-guard-entrypoint.sh`. That directory is already on the feature branch;
its name does not identify its branch or loaded modules. Live tracker is 0056,
availability 0016. Do not change that directory. The frontend also records
`/Users/angeloandreaisola/.hermes/cache/scratch/new-sources-ports.yml`, publishing
3003; backend and database record the base/dev files without that override.
The frozen deployment does not manage frontend or change these files.

Reinspection at the start of the final preparation found the original backend
ALREADY restarting with exit 1 after a Docker-host restart. Its container ID and
source mounts were unchanged, while tracker still recorded 0056. This matches the
pending-migration startup refusal reproduced on a clone; it is not a compatible
feature deployment. Preparation did not stop, restart or repair that service.
Gate A therefore also recovers the backend to the verified 0056-compatible image,
and still requires separate approval. Reinspect current state before execution.

## Preparation and preflight (no live changes)

Run from the isolated preparation checkout. Private evidence is ignored by Git:

```sh
set -eu
cd /Users/angeloandreaisola/orca/workspaces/WhereTfDoIWatchIt/incremental-integration-safety
git status --short --branch
git worktree list --porcelain
git rev-parse new-sources origin/new-sources feature/incremental-catalogue-reconciliation
docker inspect new-sources-backend-1 new-sources-db-1 --format '{{.Name}} {{.Id}} {{.Image}} {{json .Mounts}}'
docker port new-sources-backend-1
python3 -m unittest discover -s ops/deployment -p test_release.py -v
```

Expected target refs: a7ce47f5a15107ea8df1c39c740e8699f0f6cd68; feature ref: 11cc7416503c300b701945c8d39a7a2eaf6f943d.
Stop if the target, network, volume, source checkout, container identities or
scheduler state differ from the prepared manifest. No workers may be running
in this project. Development worktrees remain usable throughout.

The pinned image manifests are under `.catalogue-staging/deployment-preparation/`.
The final reviewed bundle, image archive and fresh verification evidence are under
`.catalogue-staging/deployment-final-20261118T101748Z/`. Preparation captured the existing
backend environment without printing credentials and ran only a direct,
database-enforced read-only integrity inspection. It did not start an application
or change a live service. Keep this directory private: raw environment and
resolved Compose files contain secrets.

To reproduce images offline into NEW filenames:

```sh
set -eu
python3 ops/deployment/release.py build --revision a7ce47f5a15107ea8df1c39c740e8699f0f6cd68 --dependency-image sha256:4a5a7aa75a4222f57a76722a2249826684ad6a6f6f73e8b3e3ce3e0a8d9d1fd7 --output .catalogue-staging/old-image-rebuilt.json
python3 ops/deployment/release.py build --revision 11cc7416503c300b701945c8d39a7a2eaf6f943d --dependency-image sha256:4a5a7aa75a4222f57a76722a2249826684ad6a6f6f73e8b3e3ce3e0a8d9d1fd7 --output .catalogue-staging/feature-image-rebuilt.json
```

The local dependency image must exist; build uses no pip/install/network step.
No moving registry tag is used at execution. The previously unfinished private
`deployment-preparation/release-images-r3.tar.gz` failed CRC validation and MUST
NOT be used. It was preserved. The new archive includes both pinned release
images and their exact dependency image; gzip/tar validation and an actual
`docker image load` both passed. Do not prune these images.

To reproduce a verified archive into a NEW path (no live containers are touched):

```sh
set -eu
python3 ops/deployment/release.py archive --old-image .catalogue-staging/deployment-preparation/old-image-r3.json --feature-image .catalogue-staging/deployment-preparation/feature-image-r3.json --output .catalogue-staging/release-images-repeat.tar.gz
```

For the already prepared archive, verify its checksum and compression before
loading it if recovery needs the images. A failed command stops recovery:

```sh
set -eu
python3 - <<'PY'
import sys
sys.path.insert(0, 'ops/deployment')
import release
release.verify_image_archive(
    '.catalogue-staging/deployment-final-20261118T101748Z/release-images.tar.gz',
    '2287fa4fe0698299ba2edd5e6591d7c60fbecb71240f39d97162bf0446ee5194',
    ['sha256:3417517486d65b9e2fea71ce8d8cf29db2e2a5364706fc167da09d181b5efa36',
     'sha256:3e1bfcaf1b944cdde14c03ff6b463c4a2bb3b3646e6d5e03719030e3f695e1db',
     'sha256:4a5a7aa75a4222f57a76722a2249826684ad6a6f6f73e8b3e3ce3e0a8d9d1fd7'])
PY
docker image load --input .catalogue-staging/deployment-final-20261118T101748Z/release-images.tar.gz
```

The adjacent `release-images.tar.gz.json` retains the archive size, image IDs,
source manifests and load verification. Preserve this manifest alongside the
archive outside the worktree when preparing Gate A.

If the captured baseline or environment is stale, prepare a NEW bundle; never
edit a bundle to bypass drift checks:

```sh
set -eu
python3 ops/deployment/release.py prepare --project new-sources --backend new-sources-backend-1 --db-container new-sources-db-1 --database tracker --host db --network new-sources_default --port 8000 --old-image .catalogue-staging/deployment-preparation/old-image-r3.json --feature-image .catalogue-staging/deployment-preparation/feature-image-r3.json --output .catalogue-staging/live-bundle-refreshed
```

For offline verification, use the existing backup SHA in the evidence directory:

```sh
set -eu
python3 ops/deployment/test_backend.py --image-manifest .catalogue-staging/deployment-preparation/feature-image-r3.json --output .catalogue-staging/backend-verification-repeat
python3 ops/deployment/rehearse.py --backup /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-IntegrationSafety-20261118T065108Z/live-readonly.dump --backup-sha256 3b21f6dae0234f9adba496f1b79a1b75ca9dfdd651c68f75a51cfaecc37af9ce --old-image .catalogue-staging/deployment-preparation/old-image-r3.json --feature-image .catalogue-staging/deployment-preparation/feature-image-r3.json --output .catalogue-staging/deployment-rehearsal-repeat
```

These helpers create unique internal-only networks and disposable databases;
they do not connect fault tests to live. Each output path must be new.
On this Docker host an internal-only network leaves runtime published-port
metadata empty; the rehearsal checks the configured `HostConfig.PortBindings`
and tests HTTP inside the network. It does not claim to test live host routing.
The live binding is separately inspected as 127.0.0.1:8000 and retained by config.

## Gate A — separately approve source isolation and backend restart

Approval covers moving the prepared bundle/controller into an independent release
directory and replacing ONLY the backend with the schema-0056-compatible old
application image. It necessarily interrupts current backend connections. Do not
run this gate under the preparation authorization. Arrange an exclusive local
maintenance window; prevent frontend/API clients from submitting writes for the
duration of A–C. This is a single-user local deployment, not a rolling deployment.

After approval, copy the reviewed controller and bundle outside every Git
worktree. Do not overwrite an existing release directory:

```sh
set -eu
cd /Users/angeloandreaisola/orca/workspaces/WhereTfDoIWatchIt/incremental-integration-safety
test ! -e /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118
mkdir -p -m 700 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118
cp -R ops/deployment /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller
cp -R .catalogue-staging/deployment-final-20261118T101748Z/live-bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle
cp .catalogue-staging/deployment-final-20261118T101748Z/release-images.tar.gz .catalogue-staging/deployment-final-20261118T101748Z/release-images.tar.gz.json /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py execute --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --gate A --approve A:new-sources:tracker@db
```

Expected: `GATE A PASSED new-sources tracker@db`. The controller checks current
catalogue digests before replacement, pins image/config/environment, retains the
migration guard, waits for health and verifies no bind mounts or autoreload.
It also verifies the original database container and mounts are unchanged.
Do not progress without a PASSED A receipt and successful verification:

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py verify --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --version old
docker inspect new-sources-backend-1 --format '{{.Image}} {{json .Config.Cmd}} {{json .Mounts}} {{.State.Health.Status}}'
```

Expected: old image ID above, Gunicorn, only tmpfs mounts, `healthy`, no schema
advance. Git operations can now affect development checkouts without reloading
the release. Do not later use the original base/dev `compose up` against this
project: it would reintroduce the mutable mount. Use this controller exclusively.

## Gate B — separately approve backup and tracker 0057/0058

Approval covers an exclusive write-free maintenance window, stopping the backend,
the fresh live backup, its disposable restore test, the two live migrations, and
restarting the old pinned application against the expanded schema. The explicit
`--quiescent` token attests that no other client or process is writing this DB.
The controller additionally refuses running workers in the project.

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py execute --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --gate B --approve B:new-sources:tracker@db --quiescent new-sources:tracker@db
```

The controller stops only backend, captures `before-migrations.dump` with
`default_transaction_read_only=on`, records SHA-256, and restores into a new
internal-only PostgreSQL container. All 48 table digests and migration history
must match the captured baseline before any live schema write. Failed or
interrupted backup files are preserved; never promote an `.incomplete` file.
If a dump exists, its checksum and fresh restore test must pass; it is not
overwritten. Backups, private environment, evidence and images must be retained
outside Git and copied to separately protected storage before proceeding.

Migration plan must be precisely 0057 then 0058, a remaining suffix after partial
completion, or empty for an already completed repeat. The guarded migrator checks
database `tracker`, host `db`, and confirmation `tracker@db`, and targets exactly
tracker `0058_baseline_completed_catalogue`. No startup migration is implicit.
0058 writes new reconciliation baseline rows, not existing catalogue/mappings.

Expected: `GATE B PASSED new-sources tracker@db`; old application healthy on
0058; exactly 4,769 ASSOCIATED and 326 BASELINED rows, zero attempts/retry dates;
all existing application data unchanged. If B fails the backend may intentionally
remain stopped. C is refused until B passes. Use the recovery section; never fake
a receipt. Retrying B reuses a verified backup and checks the remaining plan.

## Gate C — separately approve feature release and backend replacement

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py execute --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --gate C --approve C:new-sources:tracker@db
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py verify --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --version feature
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8000/api/providers/
```

Expected: `GATE C PASSED new-sources tracker@db`, feature image ID, guard success,
HTTP 200 from `/api/providers/`, healthy status, unchanged original table digests,
zero offers, all schedules empty. Repeat C is idempotent and must not recreate an
already identical healthy backend. No reconciliation is part of A, B or C.
The curl command checks the inspected host publication for project `new-sources`,
container `new-sources-backend-1`, database `tracker@db`; expected output is `200`.
It is an authorized-release health check, not a preparation-time live request.
Any new C attempt invalidates an earlier D receipt. No branch merge/push is
performed by the controller; code publication requires its own authorization.

## Gate D — separately approve one offline, bounded reconciliation canary

The first canary confirms unchanged inventory stays deferred. It is one HydraHD
cycle, `limit=1`, request budget 1, retries 0, interval 1, `--apply`, with no
`--allow-network`, `--review` or explicit deferred record selection. No crawl or
bulk import occurs. It may write durable run bookkeeping. It must select zero
records and attempt zero HTTP requests against this exact baseline.

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py execute --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --gate D --approve D:new-sources:tracker@db
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py verify --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --version feature
```

Expected `GATE D PASSED`; `bundle/canary.json` shows selected 0 / attempted 0;
unchanged original 48 table digests and no offers. New run-state timestamps are
expected in the new reconciliation-run table. A canary spending network requests,
reviewing deferred IDs or processing new observations requires a different,
explicit provider/record/request authorization and a new approved baseline.

## Gate E — scheduler readiness review only

After D passes, and only when considering separately authorized activation:

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py scheduler-readiness --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle
```

Expected `GATE E REVIEW ONLY` and all modes disabled. This preparation deliberately
has no recurring worker or scheduler activation command. Activation needs a new
reviewed provider/mode-specific configuration, credentials, intervals, hard
budgets, network authorization, cooldown policy, monitoring and disable/rollback
plan. Do not run `refresh_worker`, copy the canary template into the environment,
or remove the forced-empty schedule setting as part of A–D. D never authorizes E.

## Failure conditions, application rollback and full restore

Stop for a failed guard, unexpected migration plan/table, drifted digest or env,
active worker, failed backup/restore, missing receipt, unhealthy API, changed
database container/volume, nonzero HTTP/selection in the initial canary, or any
unexpected offer. RUNNING/FAILED receipts never grant the next gate. An unfinished
or failed database restore blocks all forward gates until separately approved
restore recovery completes; an explicitly approved application rollback can still
be attempted when the current database passes preservation and guard checks. A host-wide
project lock prevents concurrent controllers; process death releases it. Preserve
`last-failure.json`, receipts and logs; do not delete lock files to bypass a live lock.
An unfinished Compose migration/inspection runner also blocks new operations.
After interruption, inspect the exact one-off container and wait for it to exit;
do not assume killing the host CLI stopped the database operation. Reinspect the
remaining plan and integrity before resuming. Never launch a competing migrator.

For startup, health, interruption or feature failure, separately approve this
application-only rollback. It deploys the old immutable image on the CURRENT
schema and retains all new evidence/checkpoints. The old application was tested
on 0056, partial 0057 and complete 0058, including API health and exact digests.

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py execute --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --gate rollback --approve rollback:new-sources:tracker@db
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/release.py verify --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --version old
```

Do NOT reverse 0057 in live recovery: it drops the new state tables and can destroy
post-release leases, cooldowns, outcomes and checkpoints. 0058's reverse is a
no-op, so reversing it alone cannot restore the original database. Reverse/reapply
is tested only on an unused clone. Retaining the expanded schema is the preferred
code rollback. A partial migration can be resumed only after the exact remaining
plan, backup and original-data checks pass again.

If original data or physical schema is damaged, obtain separate restore approval,
including acceptance of losing changes since the verified backup and an exclusive
write-free maintenance window. The following recovery is destructive to the
CURRENT logical deployment, but it does not drop a database or delete a volume:

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/recover_database.py --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --approve restore:new-sources:tracker@db:accept-post-backup-data-loss --quiescent new-sources:tracker@db
```

Recovery restores the verified dump into `tracker_restore_<timestamp>` in the
exact original database container, validates all data/history and the old guard,
stops backend, terminates connections only to tracker, retains the failed DB as
`tracker_failed_<timestamp>`, renames the verified restore to tracker, and starts
the old pinned image. It invalidates B/C/D receipts. No database/volume is dropped.
`restore-progress.json` identifies every stage and the exact retained names.

If recovery stops at `ORIGINAL_RENAMED`, leave backend stopped. After inspecting
the exact progress file, separately authorize resuming the verified restore:

```sh
set -eu
python3 /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/controller/recover_database.py --bundle /Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/incremental-20261118/bundle --approve restore:new-sources:tracker@db:accept-post-backup-data-loss --quiescent new-sources:tracker@db --resume
```

Resume accepts RESTORE_VERIFIED, ORIGINAL_RENAMED or RESTORED_PROMOTED, revalidates the saved
names, retained failed database, restored rows/history and guard, and reconciles
a rename that committed just before interruption. RESTORE_VERIFIED is accepted
only when the exact failed-database name already exists, proving the first rename
committed before its journal update. Ambiguous names fail closed. Gate and
recovery journal updates sync both the file and its directory before progression.
Do not invent names, delete either DB, or run a broad Compose command.
PostgreSQL database renames are not one atomic
multi-database transaction; the progress file is the recovery journal. A restore
is NOT lossless if any real user writes occurred after the backup. Retain the
failed DB so those changes can be assessed and recovered separately.

## Monitoring and cleanup

After each successful gate, retain its receipt, API result, baseline comparison,
image IDs, environment/Compose hashes, backup SHA and restore proof. Review:

```sh
set -eu
docker inspect new-sources-backend-1 --format '{{.State.Health.Status}} {{.RestartCount}} {{.Image}}'
docker logs --since 10m new-sources-backend-1
docker inspect new-sources-db-1 --format '{{.Id}} {{json .Mounts}}'
```

`release.py verify` supplies the periodic read-only catalogue/migration check.
Health checks make only local `/api/providers/` reads, never refresh/mutation
requests. Keep an exclusive window through C; do not confuse normal user writes
after release with preparation-baseline drift. Before returning to normal use,
record a new approved operational baseline if user activity resumes. All three
scheduled modes remain disabled until a later Gate E plan is approved.

No existing worktrees, stashes, backup refs, volumes or backups are cleanup
candidates. Rehearsal helpers remove only their newly created exact container IDs
and networks. Retain private dumps, image archives, controller, manifests, receipts,
failed recovery DBs and logs until the operator explicitly approves their
retention/cleanup policy. Never run `docker compose down -v`, image/volume prune,
or automatic backup deletion from this procedure.
