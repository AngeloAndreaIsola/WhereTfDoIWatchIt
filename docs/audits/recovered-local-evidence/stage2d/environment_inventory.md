# Stage 2D — Environment Inventory

Measured against the real local WhereTfDoIWatchIt application database.
No application data was modified; all checks were SELECT-only.

## Audit identity
- Repository: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt
- Verified master SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f` (HEAD, not on a branch; working tree clean)
- Observation timestamp (UTC): `2026-11-14T00:23:34Z`
- Audit command: `backend/availability/management/commands/audit_availability_accuracy.py` (merged in PR #77), run with `--offline`
- Command output (raw, isolated): `/tmp/wtdw-stage2d-audit/` (outside tracked repository files)
- Sanctioned report copies: `docs/audits/stage2d/`

## Docker Compose inventory (observed, not modified)
Multiple Compose projects exist; they do NOT share one database. The current worktree
(`mighty-lagoon`) is NOT among the running projects.

Running Compose projects (from `docker compose ls`):
- `b2-3a-mapping-evidence` (1 container)
- `b2-3b-negative-evidence` (5 containers)
- `mapping-correction-impact` (1 container)
- `openmaic` (2 containers)
- `pr47-review` (1 container)
- `wave2test` (5 containers; backend published on 127.0.0.1:8100, db on 127.0.0.1:5499)
- `wt-ag-d` (3 containers)
- `wt-ag-e` (1 restarting backend + 3 containers)
- `wt001-validation` (1 container)
- `wtdiwpr46` (1 container)

Not running: the `mighty-lagoon` worktree's own Compose stack is not deployed.
Its `docker-compose.yml` defines a PostgreSQL `db` service with a persistent `pgdata`
volume (backend on 127.0.0.1:8002), but no container from this project was running.

## Identified application database
The real, populated application database lives in the Docker volume
`wheretfdoiwatchit_pgdata` (237.8 MB). This volume matches the Compose `pgdata`
volume name pattern (`<project>_pgdata`) for the application project and is distinct
from every experiment worktree volume (`wt-ag-*_pgdata`, `wave2test_pgdata`,
`b2-*-pgdata`, `pr47-review_pgdata`, etc.).

A sibling volume, `wheretfdoiwatchit-watched_pgdata`, exists but is empty (4 KB).

No running container mounts `wheretfdoiwatchit_pgdata`. To inspect it without
modifying application state, a throwaway inspection container
(`stage2d-inspection-db`, image `postgres:16-alpine`) was started with that volume
mounted to `/var/lib/postgresql/data`, publishing only `127.0.0.1:15432`. The
container was stopped after inspection; the volume was not removed, recreated,
migrated, restored into, or replaced.

Connection used for inspection (read-only SELECT only):
- Database: `tracker`
- User: `tracker`
- Host: `host.docker.internal:15432` (mapped to container `5432`)
- No credentials or secrets are recorded here.

## Application vs database identity
The inspected `tracker` database schema matches the current application models and
migrations (`tracker_movie`, `tracker_provider`, `availability_source`,
`availability_offer`, `availability_contentmapping`, `availability_country`,
`availability_offerevidence`, `availability_syncrun`, …). It is therefore the same
database the running application would use, populated from a prior deployment, not
an offline developer database.

## What was NOT done (per constraints)
- No Streaming Availability / Watchmode API requests, no external HTTP from the audit.
- No live scraping, no source activation, no refresh workers, no collectors started.
- No migrations (`manage.py migrate` was not run; the container entrypoint was overridden).
- No database writes; the audit command issues `SET TRANSACTION READ ONLY`.
- No private watchlists, user accounts, subscriptions, or secrets inspected.
- Stage 2B and Stage 2C historical artifacts were not overwritten.
