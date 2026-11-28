# #103 MacBook Local Deployment Plan — Agent A (exact base 3e439e6)

Status gate: **READY_FOR_EXACT_APPROVAL**

Owner decision recorded by the coordinator: the application runs locally on the owner's MacBook for now. No VPS, public hostname, reverse proxy, or external production database is required. The existing MacBook application database is retained and all existing data is preserved. No migration or write against the retained DB was performed in this task.

## 0. Identity / provider / effort / cost probe (requested vs actual) — route divergence disclosed

**Route divergence (corrected disclosure):** this agent did **not** run on the specified and coordinator-verified native route `opencode/hy4-flash-free`. The actual probe and session used `tencent/hy4-flash:free` via the `omniroute_route_request` wrapper, whose routing response reported `provider: unknown`. The independent reviewer's native probe confirmed `opencode/hy4-flash-free`; that result belongs to the reviewer and is **not** claimed here as this agent's route. This is recorded as an **observed route divergence**, not a silent substitution claim and not a rewrite of history.

| Field | Value as observed |
| --- | --- |
| Requested route (coordinator dispatch) | `opencode/hy4-flash-free` (native OpenCode, coordinator-verified exact `MODEL_IDENTITY_OK opencode/hy4-flash-free`) |
| Actual route used by this agent | `tencent/hy4-flash:free`, invoked through `omniroute_route_request` |
| Provider reported by wrapper | `unknown` in the routing response (`fallbacksTriggered: 0`) |
| Model self-identification | "Hunyuan, a large model developed by Tencent" |
| Literal probe phrase | The model declined to emit the verbatim string `MODEL_IDENTITY_OK opencode/hy4-flash-free`, stating it did not match its identity. The refusal was recorded rather than manufactured; no native OpenCode identity string is claimed by this agent. |
| Independent reviewer's native probe | Confirmed `opencode/hy4-flash-free` — reviewer's evidence, not this agent's route. |
| Effort | UNPINNED (owner requested MEDIUM; OpenCode effort is not reliably pinnable). Not claimed as MEDIUM. |
| Cost of identity probe | `0` USD (`routing.cost: 0`) |
| Fallbacks / escalation | None observed. No Sol, no Astra, no paid escalation, no Activation B, no HMAC grant. |
| Base re-fetch | `git fetch origin master` → `origin/master = 3e439e6a7cab7a72834e710352da7cbf0f0b739f` (matches coordinator-supplied base) |
| Local checkout at start | `6af4cd8421ec9c85e4fff724fded60d5cce86c85`, confirmed ancestor of `origin/master`; 20 commits behind |

**Environment-probe incident (sanitized).** An early environment-discovery command echoed process environment variable names and values into a local tool transcript. No value is reproduced in this document, Git, any issue, or any PR, and no value was reused in any command. Custody/status as observed: the echo occurred in this agent's local structured-worker shell transcript under Orca's chat-scratch session storage (`ORCA_CHAT_SCRATCH_DIR`, a local `0700` directory); it was not committed to Git and was not posted to GitHub. The transcript was **not** affirmatively destroyed by this agent, so its retention status is **UNVERIFIED** rather than claimed deleted.

**Precise remaining owner input required:**
1. Whether any of the echoed environment values were sensitive (credential, token, key, or otherwise protected).
2. Whether that local transcript is to be retained or destroyed under the owner's policy.

No raw secrets are included in this document. No credential was copied into any file, Git object, issue, PR, or report, and the values were not reused.

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
| `wheretfdoiwatchit-backend-1` | `wheretfdoiwatchit-backend` | **Restart-loop: Restarting, exit 1** (observed restart counts: 40 first inspection, 52 at 09:00:39Z Agent C, 66 prior recheck, 59 this repair recheck — counter grows as it retries) | `127.0.0.1:8002->8000/tcp` (binding inactive while restarting) | App backend, guarded entrypoint |
| `wheretfdoiwatchit-frontend-1` | `wheretfdoiwatchit-frontend` | Up | `127.0.0.1:3001->3000/tcp` | App frontend, HTTP 200 |
| `wheretfdoiwatchit-worker-1` | `wheretfdoiwatchit-worker` | Up | `8000/tcp` internal | Background worker / potential writer |
| `wheretfdoiwatchit-catalogue-worker-1` | `wheretfdoiwatchit-catalogue-worker` | Up | `8000/tcp` internal | Catalogue worker / potential writer |
| `wtdwi-full-db-test-1` | `postgres:16-alpine` | Up (healthy), 22h | `5432/tcp` internal | **Disposable test DB** (no host port) |

### 2.3 Ports and binding evidence

- `127.0.0.1:3001->3000/tcp` — app frontend, loopback only.
- `127.0.0.1:8002->8000/tcp` — app backend, loopback only; **down in a restart loop at inspection**. Health probe `http://127.0.0.1:8002/api/health/` returned connection failure (HTTP `000`). **Correction:** `docker logs` is **not** empty at fresh inspection — it returned **5,576 bytes** containing a Django `OperationalError` (`connection to server at "172.20.0.3", port 5432 failed: FATAL: the database system is starting up`) raised through `migrate --check` → `recorder.applied_migrations()`. Earlier zero-byte reads were time-dependent (log rotation/restart timing), not proof that no traceback exists. There is **no clean backend local health check** to claim while this remains unresolved; only the frontend at `127.0.0.1:3001` returned HTTP 200.
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

Migration state (from `django_migrations`): tracker through `0058_baseline_completed_catalogue`; availability through `0016_contentmapping_match_evidence`. Source at both the live checkout and base `3e439e6` contains tracker through `0064_market_entitlements` and availability through `0020_merge_stage_d_offerprice_variants`. **Ten migrations are pending** — a real, independently verified divergence.

**Two distinct causes must not be conflated (corrected):**
1. **DB-startup race (has a captured traceback).** Fresh `docker logs` (5,576 bytes) shows `django.db.utils.OperationalError: connection failed: connection to server at "172.20.0.3", port 5432 failed: FATAL: the database system is starting up`, raised via `migrate --check` → `MigrationLoader.build_graph()` → `recorder.applied_migrations()` → `has_table()`. At Agent C's observation, the container was `Restarting (1)` with restart count **52 at 09:00:39Z**; at this repair cycle's recheck it was `Restarting (1)` with restart count 59 and last start `2026-11-28T09:06:50Z`. This is a readiness race, not evidence about migration state.
2. **Pending-migration divergence (separate, verified read-only).** `showmigrations` on the restored copy showed 10 unapplied migrations and `migrate --noinput` applied them all cleanly. This remains a real condition that the guarded `backend/entrypoint.sh` (`migrate --check`) will reject once the DB is accepting connections.

**MacBook-restart observation:** the retained DB's postmaster restarted at `2026-11-28 08:16:08.692617+00` (read-only `SELECT pg_postmaster_start_time()`), consistent with a MacBook restart; **retained data survived** — rechecked at this repair cycle: `tracker_movie=829`, `tracker_availability=1541`, `django_migrations=94`.

Activation must therefore include explicit DB-ready/health gating (wait for `db` service healthy / `pg_isready` and a successful Django DB connection) before any migration or writer restart. See Section 10; numeric downtime is **UNKNOWN** (owner-measured only).

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
| Row-content checksum (exact expression pinned below) | `05ba734ad48666cd91a7293724ccf56c` | `05ba734ad48666cd91a7293724ccf56c` |
| Server version | 16.15 | 16.15 |

**Pinned checksum expression (reproducibility corrected).** The value `05ba734ad48666cd91a7293724ccf56c` is reproducible from the retained DB with this exact SQL, including the composite-type cast, `chr(10)` separator, and ordering:

```sql
SELECT md5(string_agg(t::text, chr(10) ORDER BY t::text))
FROM tracker_movie t;
```

This was re-executed read-only against the retained DB during this repair cycle and returned `05ba734ad48666cd91a7293724ccf56c`. A different expression (`md5(string_agg(t.id::text, chr(10) ORDER BY t.id))`) returns `293e8f7132ea9df3f126a5130aa78168`, confirming the expression must be pinned exactly. The restored-copy value was computed with the same expression at verification time; counts and checksum matched then. The post-migration restored-copy checksum `ebc18ee8421b37efc352ae108800d70c` in Section 6.3 is **UNVERIFIED against current primary evidence** — that disposable instance was removed after the rehearsal and this repair cycle did not repeat restore/migration work; treat that specific value as historical record only, not a reproducible claim.

### 6.3 Migration rehearsal (restored copy only)

Runner: container `agent-a-migrate-3e439e6`, image `wheretfdoiwatchit-backend:latest`, source mounted read-only from this worktree at `/app`, connected to `POSTGRES_HOST=agent-a-restore`, `POSTGRES_DB=tracker_restore`. A disposable, clearly non-production `DJANGO_SECRET_KEY` was supplied in the container environment only.

- Before: `showmigrations tracker availability` → 10 unapplied migrations; tracker at 0058, availability at 0016.
- `python manage.py migrate --noinput` applied, in order: availability `0017`, `0019`, `0018`, `0020`; tracker `0059`, `0060`, `0061`, `0062`, `0063`, `0064`. All `OK`.
- After: 0 unapplied migrations; `django_migrations = 104`.
- Data preserved: 829 movies, 1541 availability rows, 1 preference. The historical post-migration movie row checksum recorded at the time was `ebc18ee8…` (attributed to migration 0059 altering movie title/canonical-title columns); this specific value is **UNVERIFIED against current primary evidence** because the disposable instance was removed and this repair cycle did not repeat restore/migration work. Five new entitlement/title-check tables exist.
- `python manage.py check` → `System check identified no issues (0 silenced).`
- `python manage.py check --deploy` → 4 expected local-only warnings (`W004` HSTS, `W008` SSL redirect, `W012` session cookie secure, `W016` CSRF cookie secure). No TLS/reverse proxy is in scope for local use; these are recorded, not silently waived for production.
- `python manage.py collectstatic --noinput --dry-run` → 163 static files reported; dry run only.
- Retained DB rechecked **after** rehearsal: counts `829:1541:94` and hash `05ba734a…` unchanged.

Note: `python manage.py migrate --check` returned exit 0 with empty output both before and after migration in the runner, so it is not a reliable standalone indicator of pending migrations in this setup. The pending-migration evidence is from `showmigrations` and `migrate --plan`. The running backend's failure traceback **is** available in fresh logs (5,576 bytes) and shows a DB-startup race in `recorder.applied_migrations()`, not a pending-migration error (Section 10); earlier zero-byte reads were timing-dependent.

## 7. Local deployment package (exact, loopback-only)

Implement only after explicit exact-target approval. All commands run from the live app checkout `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt` (currently revision `6af4cd8`), with its own `.env` providing `POSTGRES_*` values. No files in this PR change runtime configuration.

1. Pre-flight (read-only): re-run identity/count/hash checks from Section 3; confirm backup SHA-256 from Section 5; confirm loopback bindings and no public listeners.
2. Stop writers per Section 7 step 2 (worker, catalogue-worker, backend). Keep frontend up if desired; it is not a DB writer.
3. Apply migrations via the confirmed service only. The bare form
   `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm migrate`
   is **incomplete and must not be used**: it omits the `ops` profile and the three fail-closed confirmation variables. Observed effect of a bare-form attempt: a transient auto-removed container was created (no surviving container), and the retained DB was left at **94 migrations / 829 movies** — no schema change occurred.

   The real `migrate` service is declared under `profiles: ["ops"]` with `entrypoint: ["/guarded-migrate-entrypoint.sh"]`. That entrypoint (`backend/guarded-migrate-entrypoint.sh`, SHA-256 `f09c15c658224302dd736e7fe161bf190b018530cfbe53e797e237d984ffeb5a`, identical in the live checkout and this worktree) fails closed unless `EXPECTED_DATABASE_NAME`, `EXPECTED_DATABASE_HOST`, and `CONFIRM_MIGRATION_TARGET` are set, then runs `manage.py guarded_migrate --expected-database ... --expected-host ... --confirm-target ...`.

   Exact fail-closed local command, following `docs/DEPLOYMENT.md` lines 88–100 and verified read-only from the live `.env` (`POSTGRES_DB=tracker`, `POSTGRES_HOST=db`, DB Compose service name `db`; the three confirmation variables are **unset** in `.env` and must be supplied at `run` time):

   ```bash
   docker compose --profile ops run --rm --no-deps \
     -e EXPECTED_DATABASE_NAME=tracker \
     -e EXPECTED_DATABASE_HOST=db \
     -e CONFIRM_MIGRATION_TARGET=tracker@db \
     migrate
   ```

   Owner-confirmed-only values still required before execution: the exact target values above are *documented defaults* from `DEPLOYMENT.md` and verified against the live compose config, but **the owner's explicit exact-target approval to apply them has not been given**. Do not substitute values; do not run migrations through `exec`, `run` on `backend`, or the runtime entrypoint.
4. Verify: `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py showmigrations tracker availability` shows 0 unapplied; recheck `829:1541:1:104` and loopback health. Gate DB readiness first (Section 10): wait for the `db` service healthy / `pg_isready` and confirm the backend can connect before treating any health result as valid.
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
## 10. Blockers (explicit)

These are blockers to **execution**, not to the `READY_FOR_EXACT_APPROVAL` documentation gate:

1. **Backend restart-loop unresolved, with a captured cause — corrected.** `wheretfdoiwatchit-backend-1` is `Restarting`, exit 1; restart count **52 at 09:00:39Z** (Agent C evidence), 40 at first inspection, 66 at prior correction recheck, 59 at this repair cycle's recheck. `docker logs` is **5,576 bytes**, not empty: it contains a Django `OperationalError` — `connection to server at "172.20.0.3", port 5432 failed: FATAL: the database system is starting up` — raised in `migrate --check` / `recorder.applied_migrations()`. Earlier zero-byte reads were timing-dependent and must not be cited as "no traceback". Backend health at `127.0.0.1:8002/api/health/` is a connection failure (HTTP `000`). No clean local backend health check is claimed.
2. **DB-startup race is distinct from pending migrations.** The captured traceback proves a readiness race; the 10 pending migrations are separately verified via `showmigrations`/rehearsal. Both must be handled: require DB-ready gating (`db` service healthy / `pg_isready` / successful Django DB connection) before activation or writer restart. Retained postmaster restarted at `2026-11-28 08:16:08.692617+00` (MacBook restart) and data survived (`829:1541:94`).
3. **Numeric downtime UNKNOWN** — owner-measured during the approved window; no estimate asserted.
4. **Owner input on environment-probe incident** — whether the echoed values were sensitive and whether the local transcript is to be retained or destroyed (Section 0).
5. **Exact-target approval** — not yet given; no retained-DB change authorized. The confirmation values for the fail-closed `migrate` command are documented defaults, not owner-approved execution permission.
6. **Checksum value `ebc18ee8…`** — historical/unverified against current primary evidence (disposable copy removed; no restore/migration repeated in this repair cycle). The `05ba734a…` expression is pinned and reproduced.

No old-writer/rolling compatibility is claimed.

- Left open: backend readiness gating and restart-loop resolution, owner-measured downtime, owner incident-policy input, owner's exact-target approval, and re-verification of the historical post-migration checksum if needed. These are recorded, not guessed.
- Explicit request: ChatGPT Web re-review of this plan and the correction handoff on #103.
