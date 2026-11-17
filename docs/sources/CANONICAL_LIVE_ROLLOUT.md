# First canonical live rollout: exact authorization procedure

This procedure is prepared, **not executed on live**. It targets only Compose
project `new-sources`, container `new-sources-db-1`, database `tracker`, host `db`,
volume `new-sources_pgdata`. Do not substitute the other running project's DB.
The approved candidate is the committed **387-association first manifest**.
`Blue` (1968), record 1771/movie 169869, the second 475 and later reconciliation
results are excluded from this rollout.

Separate explicit authorization must cover each live-write operation below:

| ID | Operation | Authorization scope |
|---|---|---|
| W0 | Stop/resume the existing application writers | Maintenance window; do not enable additional workers or schedules |
| M1 | Four guarded migrations | availability `0016`; tracker `0054`, `0055`, `0056` |
| C1 | First manifest import | Exactly 387 associations and its reviewed checksum |
| C2 | Replay | Same manifest; must produce zero changes |
| R1 | Rollback, if required | Replace `tracker` from the verified pre-write backup; destructive database restoration |

Do not proceed from a failed precondition or failed verification. Keep writers
stopped and inspect the saved receipts. R1 requires authorization covering
rollback; failure is not implicit permission to replace the live database.
Do not fake history, drop individual tables/constraints, or reverse migrations
to force a partially applied rollout through.

## 1. Read-only preflight and preparation

Use Bash from the `new-sources` checkout. Review `git status`, the delivered
commit and the manifest before invoking any write section. Build current code
so that both one-shot services contain the reviewed management commands and
migration files; image building itself does not touch the DB.

```bash
set -euo pipefail

dc() {
  docker compose --project-name new-sources \
    -f docker-compose.yml -f docker-compose.dev.yml --profile ops "$@"
}

export ROLLOUT_DIR="/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/canonical-live-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$ROLLOUT_DIR"
export FIRST_SHA=eec5c6717a0e7bf185cf29c7c3f213eeff5b4382ff6d0d1277f140322518df8a
export PRE_LIVE_BACKUP="$ROLLOUT_DIR/tracker-before.dump"
cp docs/audits/canonical_first_live_20261117.manifest.json "$ROLLOUT_DIR/first-live.json"
printf '%s  %s\n' "$FIRST_SHA" "$ROLLOUT_DIR/first-live.json" | shasum -a 256 -c -

# Set APPROVED_COMMIT to the full delivered commit SHA from the reviewed result.
: "${APPROVED_COMMIT:?Set APPROVED_COMMIT to the reviewed delivered commit, not HEAD}"
test "$(git branch --show-current)" = new-sources
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = "$APPROVED_COMMIT"
dc build backend migrate

audit_live() {
  dc run --rm --no-deps --entrypoint python \
    -e PGOPTIONS='-c default_transaction_read_only=on' \
    -v "$ROLLOUT_DIR:/rollout" backend manage.py audit_canonical_rollout \
    --expected-database tracker --expected-host db "$@"
}

TARGET_IDENTITY="$(docker inspect new-sources-db-1 --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}')"
test "$TARGET_IDENTITY" = 'new-sources|db|new-sources_pgdata'
docker exec new-sources-db-1 psql -X -U tracker -d tracker -Atc \
  "BEGIN READ ONLY; SELECT current_database(),current_user; SELECT name FROM django_migrations WHERE app='tracker' ORDER BY name DESC LIMIT 1; COMMIT;"
audit_live snapshot --output /rollout/live-preflight.json

python3 - <<'PY'
import os,json
from pathlib import Path
p=Path(os.environ['ROLLOUT_DIR'])
s=json.loads((p/'live-preflight.json').read_text())
expected=['availability.0016_contentmapping_match_evidence',
          'tracker.0054_discovered_catalogue_last_seen',
          'tracker.0055_cataloguediscoverystate_heartbeat_at_and_more',
          'tracker.0056_movie_tmdb_content_type_identity']
assert s['schema']['pending_migrations']==expected
for table,count in {'tracker_discoveredcataloguerecord':5095,
    'tracker_scrapedcontentmapping':3300,'tracker_movie':0,
    'availability_providercontent':0,'availability_contentmapping':0,
    'availability_offer':0}.items():
    assert s['tables'][table]['count']==count, table
assert 'tracker_canonicalbootstrapevidence' not in s['tables']
m=json.loads((p/'first-live.json').read_text())
assert len(m['associations'])==387
assert 1771 not in {r['source_record_id'] for r in m['associations']}
PY
```

Require the inspected project/service/volume to be exactly `new-sources db
new-sources_pgdata`. If counts, pending migration plan, settings, checkpoints,
source observations, code or checksum differ, stop and repeat the clone review.
The importer also rejects expired proof: the earliest first-manifest proof
expires **2026-12-17 19:03:32 UTC**. Do not refresh ambiguous records or replace
manifest evidence automatically. Do not use `migrate --check` as a shell command
that must succeed before M1: exit 1 is expected while these migrations are pending.

## 2. W0: authorized maintenance window and verified backup

This section requires maintenance-window authorization. Record which services
were running, then stop the project's existing writers. Do not touch other
Compose projects or start workers that were previously absent.

```bash
dc ps --services --status running > "$ROLLOUT_DIR/previously-running-services.txt"
dc stop backend worker catalogue-worker

audit_live snapshot --output /rollout/live-before.json
# This second snapshot must match the preflight exactly.
audit_live unchanged --before /rollout/live-preflight.json \
  --output /rollout/preflight-still-current.json

docker exec new-sources-db-1 sh -c \
  'pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$PRE_LIVE_BACKUP"
shasum -a 256 "$PRE_LIVE_BACKUP" > "$ROLLOUT_DIR/backup.sha256"
shasum -a 256 -c "$ROLLOUT_DIR/backup.sha256"
```

Restore-test this exact dump before M1. The following creates a disposable,
unpublished PostgreSQL instance on the existing network. Its public test-only
password is for the clone, not the application DB.

```bash
docker run -d --name canonical-live-rehearsal-db \
  --network new-sources_default --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=canonical_rehearsal -e POSTGRES_PASSWORD=clone_only_test \
  -e POSTGRES_DB=tracker postgres:16-alpine

ready=0
for attempt in $(seq 1 30); do
  if docker exec canonical-live-rehearsal-db pg_isready -U canonical_rehearsal -d tracker; then
    ready=1; break
  fi
  sleep 1
done
test "$ready" -eq 1

docker exec -i canonical-live-rehearsal-db pg_restore --list \
  < "$PRE_LIVE_BACKUP" > "$ROLLOUT_DIR/backup.list"
rg '^;[[:space:]]+dbname: tracker$' "$ROLLOUT_DIR/backup.list"
docker exec -i canonical-live-rehearsal-db pg_restore --exit-on-error \
  --no-owner --no-privileges -U canonical_rehearsal -d tracker < "$PRE_LIVE_BACKUP"

clone_cmd() {
  dc run --rm --no-deps --entrypoint python \
    -e POSTGRES_HOST=canonical-live-rehearsal-db -e POSTGRES_DB=tracker \
    -e POSTGRES_USER=canonical_rehearsal -e POSTGRES_PASSWORD=clone_only_test \
    -v "$ROLLOUT_DIR:/rollout" backend manage.py "$@"
}
clone_audit() {
  clone_cmd audit_canonical_rollout --expected-database tracker \
    --expected-host canonical-live-rehearsal-db "$@"
}
clone_audit unchanged --before /rollout/live-before.json \
  --output /rollout/restore-matches-live.json
clone_audit snapshot --output /rollout/clone-before.json
clone_cmd guarded_migrate --expected-database tracker \
  --expected-host canonical-live-rehearsal-db --confirm-target tracker@canonical-live-rehearsal-db
clone_audit migrations --before /rollout/clone-before.json \
  --output /rollout/clone-migrations-verified.json
clone_audit snapshot --output /rollout/clone-migrated.json
clone_cmd apply_canonical_manifest --manifest /rollout/first-live.json --apply \
  --expected-database tracker --expected-host canonical-live-rehearsal-db \
  --confirm-target tracker@canonical-live-rehearsal-db --confirm-manifest-sha256 "$FIRST_SHA" \
  > "$ROLLOUT_DIR/clone-import.json"
clone_audit snapshot --output /rollout/clone-imported.json
clone_cmd apply_canonical_manifest --manifest /rollout/first-live.json --apply \
  --expected-database tracker --expected-host canonical-live-rehearsal-db \
  --confirm-target tracker@canonical-live-rehearsal-db --confirm-manifest-sha256 "$FIRST_SHA" \
  > "$ROLLOUT_DIR/clone-replay.json"
clone_audit import --before /rollout/clone-migrated.json --imported /rollout/clone-imported.json \
  --manifest /rollout/first-live.json --output /rollout/clone-import-verified.json

# Also rehearse the exact rollback after schema and canonical objects exist.
docker exec -i canonical-live-rehearsal-db pg_restore --clean --if-exists --create \
  --exit-on-error --no-owner --no-privileges -U canonical_rehearsal -d postgres \
  < "$PRE_LIVE_BACKUP"
clone_audit unchanged --before /rollout/clone-before.json \
  --output /rollout/clone-rollback-verified.json

audit_live unchanged --before /rollout/live-before.json \
  --output /rollout/prewrite-live-still-unchanged.json
```

Every verification command exits nonzero on failure and saves its receipt. The
migration verifier permits only the expected added columns, evidence table,
availability JSON field, four history rows, one content type and its four
permissions. All existing IDs/fields, settings, checkpoint values and mapping
hashes must remain intact. `last_seen_at` must equal each original observation's
`updated_at` (or `discovered_at`). The exact canonical namespace constraint must
exist and the old global numeric-ID uniqueness must be absent.

## 3. M1: authorized guarded live migrations

Require explicit authorization for **all four** migrations. Applying only
`tracker 0056` omits the independent availability dependency. With reviewed code
and the exact four-item plan checked above, the guarded service applies both
apps; it performs no reconciliation or availability refresh.

```bash
dc run --rm --no-deps \
  -e EXPECTED_DATABASE_NAME=tracker -e EXPECTED_DATABASE_HOST=db \
  -e CONFIRM_MIGRATION_TARGET=tracker@db migrate \
  > "$ROLLOUT_DIR/live-migrations.log"
audit_live migrations --before /rollout/live-before.json \
  --output /rollout/live-migrations-verified.json
audit_live snapshot --output /rollout/live-migrated.json

dc run --rm --no-deps --entrypoint python \
  -e PGOPTIONS='-c default_transaction_read_only=on' backend manage.py migrate --check
dc run --rm --no-deps --entrypoint python \
  -e PGOPTIONS='-c default_transaction_read_only=on' backend manage.py makemigrations --check --dry-run
```

Stop on a nonzero exit or failed verification. Current task rehearsals prove
that all 5,095 discoveries and 3,300 legacy mappings survive these operations;
no data repair, fake migration or manually altered constraint is required.

## 4. C1 and C2: separately authorized 387-only import and replay

Do not proceed merely because schema authorization was granted. First validate
the exact manifest offline, then execute only the authorized import and replay.

```bash
dc run --rm --no-deps --entrypoint python \
  -v "$ROLLOUT_DIR:/rollout:ro" backend manage.py apply_canonical_manifest \
  --manifest /rollout/first-live.json > "$ROLLOUT_DIR/live-import-dry-run.json"

# C1: LIVE WRITE — exactly the first 387.
dc run --rm --no-deps --entrypoint python \
  -v "$ROLLOUT_DIR:/rollout:ro" backend manage.py apply_canonical_manifest \
  --manifest /rollout/first-live.json --apply --expected-database tracker --expected-host db \
  --confirm-target tracker@db --confirm-manifest-sha256 "$FIRST_SHA" \
  > "$ROLLOUT_DIR/live-import.json"
audit_live snapshot --output /rollout/live-imported.json

# C2: WRITE-CAPABLE replay — must change no rows or timestamps.
dc run --rm --no-deps --entrypoint python \
  -v "$ROLLOUT_DIR:/rollout:ro" backend manage.py apply_canonical_manifest \
  --manifest /rollout/first-live.json --apply --expected-database tracker --expected-host db \
  --confirm-target tracker@db --confirm-manifest-sha256 "$FIRST_SHA" \
  > "$ROLLOUT_DIR/live-replay.json"
audit_live import --before /rollout/live-migrated.json --imported /rollout/live-imported.json \
  --manifest /rollout/first-live.json --output /rollout/live-import-verified.json
```

Require 387 new canonical titles/evidence/mappings/provider-content rows and one
disabled identity-only HydraHD Source; zero offers; every typed canonical and
provider-content relationship correct; all 4,708 unselected source rows and
unrelated row hashes unchanged; replay changes all zero. The command is atomic,
locks identity writes and rejects changed sources, expired/ambiguous evidence,
provider identity collisions or existing operator mapping conflicts. It has no
HTTP or cache-write path. The read-only post-import verifier checks every
relationship rather than trusting command counters.

After every check passes, W0 authorization may resume the previously existing
backend. Confirm `/api/providers/` returns 200 before closing maintenance:

```bash
# W0: only after authorization and every verification passes.
dc start backend
BACKEND_BIND="$(dc port backend 8000)"
curl --fail --silent --show-error "http://$BACKEND_BIND/api/providers/" \
  > "$ROLLOUT_DIR/providers-after.json"
``` Do not start previously absent workers or enable schedules.
Preserve the backup, checksums, manifests and all receipts outside Git.

## 5. R1: authorized rollback through the verified backup

Keep writers stopped. Recheck project, container, database, role, archive name,
checksum and the successful clone rollback receipt before this operation. Stop
if another connection is attached to `tracker`; do not automatically terminate
unidentified clients. `tracker` must have permission to recreate its database.

```bash
shasum -a 256 -c "$ROLLOUT_DIR/backup.sha256"
rg '^;[[:space:]]+dbname: tracker$' "$ROLLOUT_DIR/backup.list"
docker exec new-sources-db-1 psql -X -U tracker -d postgres -Atc \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='tracker';" \
  > "$ROLLOUT_DIR/rollback-connections.txt"
test "$(tr -d '[:space:]' < "$ROLLOUT_DIR/rollback-connections.txt")" = 0
docker exec new-sources-db-1 psql -X -U tracker -d postgres -Atc \
  "SELECT (rolsuper OR rolcreatedb)::int FROM pg_roles WHERE rolname=current_user;" \
  > "$ROLLOUT_DIR/rollback-role.txt"
test "$(tr -d '[:space:]' < "$ROLLOUT_DIR/rollback-role.txt")" = 1

# R1: DESTRUCTIVE LIVE RESTORE — requires explicit rollback authorization.
# --create uses the archive's checked database name, tracker. postgres is only
# the maintenance connection. It replaces the database, including new objects.
docker exec -i new-sources-db-1 pg_restore --clean --if-exists --create \
  --exit-on-error --no-owner --no-privileges -U tracker -d postgres < "$PRE_LIVE_BACKUP"
audit_live unchanged --before /rollout/live-before.json \
  --output /rollout/live-rollback-verified.json
```

A plain object-level `--clean` restore is insufficient: objects introduced after
the dump, including the evidence table and its foreign keys, are not in the old
archive's drop list. Database replacement was rehearsed after all four migrations
and the 387 import and restored exact rows, migration history and inspected
schema. PostgreSQL documents that `--create --clean` replaces the database named
in the archive ([pg_restore documentation](https://www.postgresql.org/docs/current/app-pgrestore.html)).

Rollback restores the original `0053`/availability `0015` state and zero canonical
associations. That also restores the pre-existing code/schema mismatch. Keep
current application code stopped until an operator chooses compatible code or
an authorized corrected migration attempt; do not claim rollback makes the
current backend healthy.
