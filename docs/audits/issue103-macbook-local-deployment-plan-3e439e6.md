# #103 MacBook Local Deployment Plan — Agent A (exact base 3e439e6)

Status gate: **READY_FOR_EXACT_APPROVAL**

Owner decision recorded by the coordinator: the application runs locally on the owner's MacBook for now. No VPS, public hostname, reverse proxy, or external production database is required. The existing MacBook application database is retained and all existing data is preserved. No migration or write against the retained DB was performed in this task.

## 0. Identity / provider / effort / cost probe (requested vs actual)

| Field | Value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual model reported by probe | `tencent/hy4-flash:free` (self-identifies as "Hunyuan", consistent with the requested `hy4-flash:free` family) |
| Literal probe phrase | The model refused to emit the verbatim string `MODEL_IDENTITY_OK opencode/hy4-flash-free`, stating it does not match its identity; it reported its identity as Hunyuan / `tencent/hy4-flash:free` instead. No silent substitution was accepted: the refusal is recorded here rather than manufactured. |
| Provider | OmniRoute route_request, `provider: unknown` in the routing response; no fallback triggered (`fallbacksTriggered: 0`) |
| Effort | UNPINNED (owner requested MEDIUM; OpenCode effort is not reliably pinnable). Not claimed as MEDIUM. |
| Cost of identity probe | `0` USD (`routing.cost: 0`) |
| Fallbacks / escalation | None. No Sol, no Astra, no paid escalation, no Activation B, no HMAC grant. |
| Base re-fetch | `git fetch origin master` → `origin/master = 3e439e6a7cab7a72834e710352da7cbf0f0b739f` (matches coordinator-supplied base) |
| Local checkout at start | `6af4cd8421ec9c85e4fff724fded60d5cce86c85`, confirmed ancestor of `origin/master`; 20 commits behind |

No raw secrets are included in this document. An early environment probe incidentally echoed process environment variable names and values into a local tool transcript; no credential was copied into any file, Git object, issue, PR, or report, and the values were not reused.

## 1. Forbidden operations statement

During this task the following were **not** performed against the retained database or any external service:

- No migration, schema change, write, or config change against the retained DB (`tracker` in `wheretfdoiwatchit-db-1`).
- No public exposure, no host binding other than `127.0.0.1`, no reverse proxy, no VPS, no public hostname.
- No live provider, TMDb, first-party, or playback request.
- No credential or raw row data written to Git, issues, PRs, or this document.
- No Activation B, HMAC grant, quota/permission change, issue closure, or cleanup.
- No replacement of the retained DB with the restored copy; the restored copy is isolated and disposable.

Exact-target confirmation before any change to the retained DB is explicitly required and has **not** been given in this task. Gate `103-DEPLOY-LOCAL` is `READY_FOR_EXACT_APPROVAL` — it is not permission to execute.

## 2. Local environment inventory (read-only)

Commands used (all read-only): `docker compose ls`, `docker ps -a`, `docker volume ls`, `docker inspect <container>`, `docker logs <container>`, `lsof -nP -iTCP -sTCP:LISTEN`.

### 2.1 Compose projects

| Project | Status | Source |
| --- | --- | --- |
| `wheretfdoiwatchit` | running(5) | `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/docker-compose.yml` |
| `wtdwi-full` | running(1) | `/Users/angeloandreaisola/orca/workspaces/WhereTfDoIWatchIt/wtdw103-backend-followup/docker-compose.yml` + `docker-compose.test.yml` |
| `new-sources` | restarting(1), running(2) | `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/.worktrees/new-sources/...` (unrelated app; owns host port 8000) |
| `openmaic` | running(2) | unrelated project (owns host port 3000) |

### 2.2 Containers of interest

| Container | Image | Status | Ports | Role |
| --- | --- | --- | --- | --- |
| `wheretfdoiwatchit-db-1` | `postgres:16-alpine` | Up (healthy) | `5432/tcp` internal only | **Retained application DB** |
| `wheretfdoiwatchit-backend-1` | `wheretfdoiwatchit-backend` | Restarting (exit 1, restart count 40 at inspection) | `127.0.0.1:8002->8000/tcp` (binding inactive while restarting) | App backend, guarded entrypoint |
| `wheretfdoiwatchit-frontend-1` | `wheretfdoiwatchit-frontend` | Up | `127.0.0.1:3001->3000/tcp` | App frontend, HTTP 200 |
| `wheretfdoiwatchit-worker-1` | `wheretfdoiwatchit-worker` | Up | `8000/tcp` internal | Background worker / potential writer |
| `wheretfdoiwatchit-catalogue-worker-1` | `wheretfdoiwatchit-catalogue-worker` | Up | `8000/tcp` internal | Catalogue worker / potential writer |
| `wtdwi-full-db-test-1` | `postgres:16-alpine` | Up (healthy), 22h | `5432/tcp` internal | **Disposable test DB** (no host port) |

### 2.3 Ports and binding evidence

- `127.0.0.1:3001->3000/tcp` — app frontend, loopback only.
- `127.0.0.1:8002->8000/tcp` — app backend, loopback only; currently down due to crash loop.
- `127.0.0.1:8000` is **not** this app: it belongs to `new-sources-backend-1`; its `/api/health/` returned `{"status":"ok","db":"ok"}` but is irrelevant evidence for this deployment.
- `127.0.0.1:3000` belongs to `openmaic-openmaic-1` (unrelated).
- Both retained and test DB containers publish **no** host port; DB access is internal to `wheretfdoiwatchit_default` / `wtdwi-full_default`.
- A native PostgreSQL 18 process listens on `127.0.0.1:5432` and `[::1]:5432` only (loopback). It is a separate local instance, not the retained app DB.
- Host `lsof` showed listeners only on loopback addresses for 5432, 8000, 3000, 3001. No `0.0.0.0` or public binding for this stack.

### 2.4 Volumes and ownership

- `wheretfdoiwatchit_pgdata` — `local` driver, labels `com.docker.compose.project=wheretfdoiwatchit`, `com.docker.compose.volume=pgdata`; mounted `rw` at `/var/lib/postgresql/data` in `wheretfdoiwatchit-db-1`. **This is the retained data volume.**
- `wtdwi-full-db-test-1` has **no volume mount** (`docker inspect .Mounts` → `[]`): its data is container-ephemeral, consistent with disposable test use.
- Other volumes exist from unrelated or historical projects and were not touched.

### 2.5 Retained vs disposable data — evidence

| Evidence | Retained (`wheretfdoiwatchit-db-1`) | Disposable (`wtdwi-full-db-test-1`) |
| --- | --- | --- |
| Project | `wheretfdoiwatchit` (the live app) | `wtdwi-full` (follow-up worktree) |
| Volume | named persistent `wheretfdoiwatchit_pgdata` | none (ephemeral) |
| `django_migrations` table | present, 94 rows | absent (`relation does not exist`) |
| `pg_stat_user_tables` | many app tables | `0` user tables |
| Data | 829 movies, 1541 availability rows, 1 preference | empty |
| Conclusion | Real development data — preserve | Disposable test DB — safe to recreate |

## 3. Exact target identity (no credentials)

All values obtained with in-container `psql` using the container's own environment; no password printed.

| Field | Value |
| --- | --- |
| Host / container | `wheretfdoiwatchit-db-1` on Docker network `wheretfdoiwatchit_default` |
| Host port | none published (internal `5432/tcp` only) |
| Database name | `tracker` |
| Role | `tracker` |
| Server version | PostgreSQL 16.15 |
| Database OID (identity component) | `16384` |
| Postmaster start (fingerprint component) | `2026-11-27 20:57:52.100532+00` |
| Connection identity | in-container Unix socket (`inet_server_addr()` and `inet_server_port()` empty via socket) |
| App source revision currently running | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` at `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt` (20 commits behind master) |

Table counts (exact `count(*)`, not stale `n_live_tup`): `tracker_movie = 829`, `tracker_availability = 1541`, `tracker_preference = 1`, `django_migrations = 94`.

Migration state (from `django_migrations`): tracker through `0058_baseline_completed_catalogue`; availability through `0016_contentmapping_match_evidence`. Source at both the live checkout and base `3e439e6` contains tracker through `0064_market_entitlements` and availability through `0020_merge_stage_d_offerprice_variants`. **Ten migrations are pending** — this is why the guarded backend entrypoint (`backend/entrypoint.sh`: `python manage.py migrate --check`) exits 1 and the backend is in a crash loop. The exact crash cause was not printed in `docker logs` (empty); the pending-migration explanation is inferred from source/DB divergence and the guard code, not from a captured traceback. Numeric downtime is **UNKNOWN** (owner-measured only).

## 4. Writer inventory and coordinated stop/start sequence

At inspection time the only active connection to `tracker` was the auditor's own `psql` session. However, three long-running containers share the app network and are capable of writing: `wheretfdoiwatchit-worker-1`, `wheretfdoiwatchit-catalogue-worker-1`, and the restarting `wheretfdoiwatchit-backend-1`. No rolling or old-writer compatibility is claimed; no evidence proves old writers are safe against a migrated schema.

Mandatory coordinated sequence **for the future approved activation only** (not executed here):

1. Record exact target identity and counts again immediately before the window; confirm no unexpected connections in `pg_stat_activity`.
2. Stop writers in this order: `docker compose -f docker-compose.yml -f docker-compose.dev.yml stop worker catalogue-worker backend` (frontend may stay up; it is not a DB writer). Confirm all three are stopped and `pg_stat_activity` shows no app connections.
3. Only then, with explicit exact-target approval, apply migrations via the confirmed `migrate` service and verify.
4. Restart writers: `docker compose -f docker-compose.yml -f docker-compose.dev.yml start backend worker catalogue-worker`; wait for `backend` to leave the restarting state.
5. Verify health at loopback URLs and re-check counts.
6. If any step fails, execute Section 8 rollback before restarting writers against a mismatched schema.

## 5. Protected backup

- Created from the retained DB only, via `pg_dump -Fc` executed **inside** the retained DB container, then `docker cp` to off-repository custody. No existing file was overwritten (`test ! -e "$OUT"` guard).
- Custody directory: `/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-Releases/agent-a-local-backups`, mode `0700`.
- File: `tracker-retained-20261127T213125Z.sql`, mode `0600`. The `.sql` suffix is historical naming; the actual format is **PostgreSQL custom** (`file` reports `PostgreSQL custom database dump - v1.15-0`), gzip-compressed.
- Size: `5,797,179` bytes (5.8 MB) versus 86 MB live database size (compression).
- SHA-256: `1292f6ed058eb211ed2fd04a68de7301f2dac4efad287bc5d7627942e557bb12` (verified twice).
- `pg_restore -l`: 579 TOC entries, 590 output lines, dump version 1.15-0, source database version 16.15.
- The in-container temporary dump was removed after copy. The pre-existing repo backup `backups/tracker_backup_20260926_140128.sql` was not touched.

## 6. Restore verification and migration rehearsal (isolated disposable instance only)

### 6.1 Isolated instance

- Container `agent-a-restore-3e439e6`, image `postgres:16-alpine`, bound `127.0.0.1:55432->5432/tcp` (nonconflicting port; host `lsof` showed 55432 and 55433 free before creation).
- Database `tracker_restore`, role `restoreuser`, disposable credentials held only in container environment; never written to files or Git.
- Attached to `wheretfdoiwatchit_default` with alias `agent-a-restore` so the disposable Django runner could reach it without touching the retained DB.
- Restore command: `docker cp <backup> agent-a-restore-3e439e6:/tmp/backup.dump` then `docker exec agent-a-restore-3e439e6 sh -c 'pg_restore -U restoreuser -d tracker_restore --no-owner --no-privileges /tmp/backup.dump'` → exit 0, no errors.

### 6.2 Independent restore verification

| Check | Retained | Restored |
| --- | --- | --- |
| `tracker_movie` | 829 | 829 |
| `tracker_availability` | 1541 | 1541 |
| `tracker_preference` | 1 | 1 |
| `django_migrations` | 94 | 94 |
| `md5(string_agg(movie row text ORDER BY row text))` | `05ba734ad48666cd91a7293724ccf56c` | `05ba734ad48666cd91a7293724ccf56c` |
| Server version | 16.15 | 16.15 |

Counts and row-content checksum match exactly; restore is verified independently of the dump listing.

### 6.3 Migration rehearsal (restored copy only)

Runner: container `agent-a-migrate-3e439e6`, image `wheretfdoiwatchit-backend:latest`, source mounted read-only from this worktree at `/app`, connected to `POSTGRES_HOST=agent-a-restore`, `POSTGRES_DB=tracker_restore`. A disposable, clearly non-production `DJANGO_SECRET_KEY` was supplied in the container environment only.

- Before: `showmigrations tracker availability` → 10 unapplied migrations; tracker at 0058, availability at 0016.
- `python manage.py migrate --noinput` applied, in order: availability `0017`, `0019`, `0018`, `0020`; tracker `0059`, `0060`, `0061`, `0062`, `0063`, `0064`. All `OK`.
- After: 0 unapplied migrations; `django_migrations = 104`.
- Data preserved: 829 movies, 1541 availability rows, 1 preference. Movie row checksum changed from `05ba734a…` to `ebc18ee8…` as expected because migration 0059 alters movie title/canonical-title columns. Five new entitlement/title-check tables exist.
- `python manage.py check` → `System check identified no issues (0 silenced).`
- `python manage.py check --deploy` → 4 expected local-only warnings (`W004` HSTS, `W008` SSL redirect, `W012` session cookie secure, `W016` CSRF cookie secure). No TLS/reverse proxy is in scope for local use; these are recorded, not silently waived for production.
- `python manage.py collectstatic --noinput --dry-run` → 163 static files reported; dry run only.
- Retained DB rechecked **after** rehearsal: counts `829:1541:94` and hash `05ba734a…` unchanged.

Note: `python manage.py migrate --check` returned exit 0 with empty output both before and after migration in the runner, so it is not a reliable standalone indicator of pending migrations in this setup. The pending-migration evidence is from `showmigrations` and `migrate --plan`; the authoritative guard is the entrypoint in the running image, whose actual failure traceback was not available in logs (recorded as UNKNOWN above).

## 7. Local deployment package (exact, loopback-only)

Implement only after explicit exact-target approval. All commands run from the live app checkout `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt` (currently revision `6af4cd8`), with its own `.env` providing `POSTGRES_*` values. No files in this PR change runtime configuration.

1. Pre-flight (read-only): re-run identity/count/hash checks from Section 3; confirm backup SHA-256 from Section 5; confirm loopback bindings and no public listeners.
2. Stop writers per Section 7 step 2 (worker, catalogue-worker, backend). Keep frontend up if desired; it is not a DB writer.
3. Apply migrations via the confirmed service only:
   `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm migrate`
   (the `migrate` service is the explicitly confirmed migration path in `docs/DEPLOYMENT.md`; do not add migrations to the backend entrypoint).
4. Verify: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py showmigrations tracker availability` shows 0 unapplied; recheck `829:1541:1:104` and loopback health.
5. Start writers per Section 7 step 4; confirm backend stays up.
6. Access URLs (all loopback): frontend `http://127.0.0.1:3001/`, backend health `http://127.0.0.1:8002/api/health/`. Note that `http://127.0.0.1:8000/api/health/` belongs to the unrelated `new-sources` project and must not be used as evidence for this stack.
7. No VPS, no public hostname, no reverse proxy, no external DB, no provider/TMDb/playback live call during activation.

## 8. Rollback and data-recovery plan

- **Before migrations:** if any pre-flight check fails, do nothing; retained DB is untouched. No rollback needed.
- **During/after migrations, before writers restart:** stop the `migrate` operation; the protected logical backup from Section 5 is the recovery source. Restoration target must be a **new disposable instance**, never the retained DB, until exact-target approval for a real restore is obtained explicitly.
- **Writer restart fails:** stop writers again; do not point old code at a migrated schema (no rolling/old-writer compatibility claim). Diagnose with `docker logs`; re-run `showmigrations`; if schema is inconsistent, use the backup into a disposable instance and re-rehearse.
- **Data loss suspected:** the retained volume `wheretfdoiwatchit_pgdata` is not to be deleted or overwritten. Preserve it, stop all writers, and recover from the SHA-256-verified backup into a new isolated database first; compare counts/hash before any promotion.
- **Downtime:** numeric downtime is **UNKNOWN** — it must be measured by the owner during the approved window; no estimate is asserted here.
- **Abort condition:** any unexpected connection to the retained DB, any non-loopback binding, any credential appearing in logs/Git, or any deviation from the 10-migration rehearsal result requires abort and escalation.

## 9. Deliverable scope and leftover items

- This PR adds only this document on isolated forward-only branch `agent-a/macbook-local-deployment-3e439e6` at exact base `3e439e6`. No runtime, schema, config, or DB change is included.
- Gate `103-DEPLOY-LOCAL`: **READY_FOR_EXACT_APPROVAL** — evidence is sufficient for the owner to grant exact-target approval; execution is gated on that approval.
- Left open: captured backend crash-loop traceback (logs empty), owner-measured downtime, and owner's exact-target approval. These are recorded, not guessed.
- Explicit request: ChatGPT Web review of this plan and handoff on #103.
