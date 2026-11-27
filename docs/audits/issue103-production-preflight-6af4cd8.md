# Gate 103-DEPLOY — Production preflight (Agent A, #103)

**Gate verdict: `BLOCKED_ON_ACCESS`.** Production deployment, cutover, migrations, live provider calls and configuration changes remain **NOT AUTHORIZED**. This is a documentation-only preflight; it grants no approval and activates nothing.

**Issue:** #103 (subscription plan tiers, channel add-ons, offer entitlement verdicts).
**Base used (exact, re-fetched):** `6af4cd8421ec9c85e4fff724fded60d5cce86c85` — current `origin/master` at dispatch; equals the PR #233 merge commit. Coordinator dispatch base `6af4cd8421ec9c85e4fff724fded60d5cce86c85` matched `git ls-remote origin refs/heads/master`.
**Branch:** `audit/issue103-production-preflight-6af4cd8` (isolated, forward-only, created from the exact base; no cleanup or merge requested).

## 0. Forbidden operations and route record

No deployment, production migration, production DB write, live provider/TMDb/first-party/playback request, HMAC grant, quota or permission change, Activation B mapping activation, branch/PR/worktree cleanup, or issue closure occurred. No credentials, DSNs, secrets or raw production rows were read into or written to Git, issue comments, or this document. No `docker exec` into, connection to, or mutation of any production database was attempted.

### Route / model / provider / effort / cost (fresh in-session probe)

| Field | Value |
| --- | --- |
| Preferred route | `omniroute/ag-gemini-3.8-flash-tiered` — **not used**: fresh coordinator probe failed repeatedly with 503 `ALL_TARGETS_SKIPPED`; verified fallback authorized |
| Requested route (this session) | `opencode/hy4-flash-free` (native OpenCode CLI) |
| Actual model (probe) | `opencode/hy4-flash-free` |
| Probe command | `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` |
| Probe text | `MODEL_IDENTITY_OK opencode/hy4-flash-free` — requested = actual, no substitution |
| Provider | OpenCode (local CLI) |
| Effort | **UNPINNED** — OpenCode has no reliable effort flag; no HIGH/XHIGH asserted |
| Cost | JSON telemetry `cost: 0` |
| Forbidden routes | No Sol, no Astra, no paid escalation, no silent substitution |

## 1. Inputs read (merged deployment-approval and dossier documents)

- PR #232 (merged `2559022806100e746c35396d5bda9dc14dec8fea`, 2026-11-27T19:46:45Z): `docs/audits/issue103-production-deployment-approval-package.md`. Status DRAFT/REQUEST_CHANGES corrections applied; explicitly **not** deployment authorization. §3 requires `to_regclass` table validation and exact `$BACKUP_PATH` restore verification; §4 mandates coordinated stop-writers and rejects rolling/old-writer compatibility; §5 labels numeric downtime UNKNOWN; §7 approves Activation A wording only, Activation B disabled.
- PR #233 (merged `6af4cd8421ec9c85e4fff724fded60d5cce86c85`, 2026-11-27T19:46:54Z): `docs/audits/issue97/offline-prerequisites-dossier-20261127.md`. Offline-only dossier; raw evidence stays in private off-repo custody.
- PR #231 (merged `331dbe2f7731078c93e5e93eb4620c6e7d07e507`): `docs/audits/issue103-production-readiness-recovery-b.md` — stop-writers sequence, backup/restore arrangements, migration verification on disposable PostgreSQL.
- Hosted CI for base `6af4cd8`: PR #232 status rollup recorded three SUCCESS jobs (Backend Tests; Frontend Lint, Build & Test; Browser Extension Build, Typecheck & Tests). Historical exact-head runs: `46042534871` on `331dbe2`, `46055139709` on `7f2eec5`.

## 2. Actual deployment target — BLOCKED_ON_ACCESS

**Conclusion: no authorized production deployment target is identified. Gate is BLOCKED_ON_ACCESS.** The repository and all authorized read-only sources explicitly disclaim production deployment:

- `docs/DEPLOYMENT.md:1-14`: "does **not** contain evidence of a deployed VPS, reverse proxy, authentication service, or production database"; ports bind to `127.0.0.1`; publishing outside localhost is a separate design needing auth boundary, TLS, host allow-list, non-placeholder `DJANGO_SECRET_KEY` and `DJANGO_DEBUG=0`, none supplied here.
- `docker-compose.yml`: all published ports are `127.0.0.1` (`${BACKEND_PORT:-8002}:8000`, `3001:3000`); `postgres:16-alpine` local `db` service with named volume `pgdata`.
- `ops/deployment/compose.release.json`: requires operator-supplied `WTDW_IMAGE`, `WTDW_BACKEND_ENV`, `WTDW_PORT`, `WTDW_NETWORK`; no concrete values are committed.
- Local Docker inspection (read-only, `docker ps` / `docker inspect`) found only loopback dev/test stacks: projects `new-sources`, `wheretfdoiwatchit`, `wtdwi-full`, `openmaic`. Both `new-sources-backend-1` and `wheretfdoiwatchit-backend-1` report `DJANGO_DEBUG=1`, `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,backend`, `POSTGRES_HOST=db`, `POSTGRES_DB=tracker`. These are **not** production: debug enabled, loopback-only hosts, compose-internal DB host. No production host, VPS, reverse proxy or public endpoint is evidenced. No host was probed or guessed.

**Missing facts:** exact production host/VPS identity and Compose project; exact database host, port, name, role; whether a reverse proxy/TLS terminator exists; exact release image/tag and application revision currently deployed. The only concrete historical DB observation is a **historical** local observation in `docs/audits/MIGRATION_0033_INCIDENT_2026-11-17.md`: Compose project `new-sources`, service `db`, `postgres:16-alpine`, PostgreSQL **16.15**, database `tracker`, user `tracker`, host `db`, volume `new-sources_pgdata` — recorded at revision `7617f73`, not current production.

## 3. Production configuration inspection

Only repository configuration was inspected; no production configuration was reachable or changed. `backend/config/settings.py` enforces: `validate_security_settings()` rejects placeholder secret when `DJANGO_DEBUG=0`; `ALLOWED_HOSTS` from `DJANGO_ALLOWED_HOSTS` (default `localhost,127.0.0.1,backend`); Postgres settings from `POSTGRES_*` env (defaults `tracker`/`db`/`5432`). Migration writes are gated by `backend/tracker/management/commands/guarded_migrate.py`, which requires exact `--expected-database`, `--expected-host` and `--confirm-target=<name>@<host>`. Runtime startup only runs `migrate --check`; the `migrate` service is profile-gated. `ops/deployment/health.py` is a read-only `/api/providers/` 200 probe.

## 4. Exact DB/version/migration/revision verification

| Fact | Status |
| --- | --- |
| Exact DB host | **UNKNOWN** — no authorized production evidence; historical local `db` only |
| Exact DB name | **UNKNOWN** for production; historical local `tracker` |
| DB version | **UNKNOWN** for production; historical local PostgreSQL 16.15 (`postgres:16-alpine`) |
| Current migration state | **UNKNOWN** for production. Documented local/disposable targets: tracker `0064_market_entitlements`, availability `0020_merge_stage_d_offerprice_variants`; historical incident observation through tracker `0053` / 84 migrations total |
| Deployed application revision | **UNKNOWN** — no deployed image or SHA evidenced; repo pins only historical SHAs (`331dbe2` review baseline, `a79b926` earlier baseline) |

`ops/deployment/integrity.py` asserts a **historical 0056-baseline** snapshot (`discoveries=5095`, `legacy_mappings=3300`, `canonical_associations=4769`, `unresolved=326`, `offers=0`) in a `REPEATABLE READ, READ ONLY` transaction with `transaction_read_only=on`. It is not a current production measurement and will fail closed if production shape differs; re-review before reliance.

## 5. Writers, workers, schedulers and replicas to stop

Identified from authorized compose/config and docs as **must-stop before coordinated cutover** (exact live replica counts UNKNOWN):

- `backend` — all replicas (Gunicorn workers 3, threads 2; mutation/admin endpoints).
- `worker` — title lane (`refresh_worker --lane title`).
- `catalogue-worker` — catalogue lane (`refresh_worker --lane catalogue`).
- Host `crontab` schedules from `docs/DEPLOYMENT.md:5`: `refresh_availability --budget 90` (daily 03:17 UTC); `refresh_provider_catalogue` (every 6h) and per-source slots (bbc_iplayer, tubi, prime_video, rai_play, pluto_tv, abc_iview).
- Manual/one-off writers: Django admin edits, `POST /api/watchlist/refresh/`, `manage.py` write commands, and any standalone/one-off Compose clients of the same DB.
- Ingress: remove public ingress before quiesce. `ops/deployment/release.py` Gate B requires exclusive token `<gate>:<project>:<database>@<host>` plus `--quiescent <project>:<database>@<host>`; Gate E is review-only (no schedule activation implemented).

Verify quiescence (no DB sessions from app roles) before migration. Exact replica counts and in-flight job durations remain **UNKNOWN**.

## 6. TLS, authentication, allowed hosts, secrets — configuration shape (no values)

- **TLS:** not evidenced in repo. `docs/DEPLOYMENT.md` states reverse proxy/TLS is not supplied; must be owner-supplied. Production TLS status **UNKNOWN**.
- **Authentication:** v1 intentionally has no auth boundary; repo explicitly requires an auth/authorization boundary for mutation and quota-consuming endpoints before publishing outside localhost. Not evidenced; **UNKNOWN**.
- **Allowed hosts:** enforced via `DJANGO_ALLOWED_HOSTS` (defaults loopback/`backend`); production value **UNKNOWN**. Local dev stacks observed as `localhost,127.0.0.1,backend`.
- **Secret configuration shape:** `DJANGO_SECRET_KEY` supplied by env and validated non-placeholder when `DJANGO_DEBUG=0`; `DJANGO_DEBUG=0` required; DB creds/API keys via `POSTGRES_*`, `TMDB_API_KEY`, `RAPIDAPI_KEY`, `WATCHMODE_API_KEY`, all env-injected (release overlay uses frozen `env_file`). No values read or exposed; actual production secret hygiene **UNKNOWN**.

## 7. Backup and recovery — documented vs. verified

**Documented arrangements:**
- Manual (`docs/DEPLOYMENT.md:6`): `docker compose exec db pg_dump -U tracker tracker > ./backups/tracker_backup_<ts>.sql`; restore stops `backend frontend worker`, `psql` restore, restart; test in staging first; `chmod 700 ./backups`.
- Hardened (`ops/deployment/release.py`, `rehearse.py`): Gate B verifies baseline, stops backend, dumps `before-migrations.dump` with SHA-256, verifies via `verify_backup`, `check_plan.py` rejects unreviewed migrations.
- Recovery (`ops/deployment/recover_database.py`): exact approval token `restore:<project>:<database>@<host>:accept-post-backup-data-loss` plus `--quiescent` token; re-verifies checksum; restores into `<database>_restore_<ns>`; renames original to `<database>_failed_<ns>` (retained, never dropped); promotes restored DB; redeploys old image; resumable stages. Accepts post-backup data loss.
- Application rollback: old pinned image retained, additive schema kept, integrity snapshot asserted unchanged; no migration revert.

**Verified restore feasibility (historical, disposable only):** `docs/audits/MIGRATION_0033_INCIDENT_2026-11-17.md` records a checksum-verified custom dump (SHA-256 `fd04b867…703e`) restored successfully to disposable DB `wtdwi_live_restore2`, matching live counts/IDs/hashes/history through `0053`; another dump `…7617f73.dump` (mode 0600, ≈1.2 MB, SHA-256 `9417b89c…`) passed `pg_restore --list` and two isolated restores. These prove a restore procedure worked for a **historical local database**, not for current production. The tracked `backups/tracker_backup_20260926_140128.sql` is divergent (0033 lineage, zero mappings, four movies). **Never identify backups by filename alone.**

**Gap:** no current production backup exists in authorized evidence, and no production restore has been rehearsed. Exact-target pre-cutover dump plus `pg_restore --list` and disposable restore verification remain mandatory preconditions.

## 8. DB size and backup throughput

**UNKNOWN.** No safe read-only telemetry for the production target exists in authorized sources. Historical local dump size ≈1.2 MB is not a production size measurement and must not be used as one. Needed evidence: `pg_total_relation_size` / table row counts on the exact target via read-only preflight; measured `pg_dump` elapsed time and output bytes on the exact target; replica count and in-flight job duration. Without these, no numeric downtime can be defended.

## 9. Exact coordinated stop-writers sequence (plan only — not executed)

Rolling deployment and old/new-writer compatibility are **explicitly rejected**. `availability.0018_offerprice_variants` drops its temporary DB default; a pre-0018 writer omitting `variants` has inserts rejected by PostgreSQL. No independent proof of rolling safety exists; do not claim it. Sequence (every step owner-gated):

1. Record exact base/head SHAs and CI for the release; confirm exact Compose project, DB name/host and pinned images staged locally; verify auth/TLS/allow-list/secret/debug shape is owner-confirmed; stage old and new artifacts (build never on the production DB).
2. Validate current table names with `SELECT to_regclass('public.<table>')` for every offline-derived table; fail closed on any null/mismatch. Record counts and deterministic content hashes into private off-repo custody.
3. Take the exact-target pre-cutover custom-format dump (`pg_dump -Fc ... --file "$BACKUP_PATH"`); record SHA-256 off-repo; `pg_restore --list "$BACKUP_PATH"`; restore to disposable PostgreSQL and verify counts/hashes and `migrate --check`. Use only the exact `$BACKUP_PATH` — no wildcard.
4. Remove ingress; pause both `refresh_availability` and all `refresh_provider_catalogue` cron slots; stop all replicas of `backend`, `worker`, `catalogue-worker`; stop manual/one-off jobs; drain in-flight work; verify no app-role DB sessions remain (quiescence).
5. Review `migrate --plan`; apply only via the confirmed `migrate` service with exact `EXPECTED_DATABASE_NAME`, `EXPECTED_DATABASE_HOST`, `CONFIRM_MIGRATION_TARGET=<name>@<host>`; verify `showmigrations` and `migrate --check` on the release image before starting runtime services.
6. Recreate backend from staged image with ingress still paused; verify runtime guard entrypoint mount is read-only (`/bin/sh /runtime-guard-entrypoint.sh`); a missing guard mount leaves backend stopped.
7. Read/write compatibility checks: `migrate --check` exit 0; read-only health/smoke; old-shaped insert smoke on a disposable DB with mocked transport; confirm entitlement tables empty and verdicts `UNKNOWN` (Activation A); no live provider call.
8. Health: `/api/health/` 200 `{"status":"ok","db":"ok"}`; `/api/providers/` 200; inspect backend/worker/catalogue logs for insert failures.
9. Explicit owner rollback decision point; keep the protected backup until the window closes.
10. Resume writers one lane at a time only after checks pass, then restore cron/schedules under separate approval; monitor one refresh cycle.

Rollback: prefer application rollback retaining additive schema; do **not** present isolated `availability 0019` downgrade as verified safe (`0020` depends on 0018/0019; reversing 0018 can lose populated variants). Full schema/data rollback only via separately approved restore from the exact backup with explicit post-backup data-loss acceptance.

## 10. Maintenance window

**UNKNOWN — owner-measured/recorded evidence required before sign-off.** No numeric duration is invented. Known cost drivers are quiesce detection, dump/checksum/disposable restore verification, additive DDL (tracker 0064: 6 tables, 3 constraints, 1 index; availability through 0020), force-recreate with ingress paused, health checks, and one-lane-at-a-time resume. Recorded historical evidence in this repo concerns disposable/local databases only and does not measure production downtime.

## 11. Missing credentials, permissions and operational facts

1. Exact production Compose project, host/VPS, database host/port/name/role, and currently deployed image/revision.
2. Read-only preflight access to the exact production DB (or owner-supplied, recorded read-only telemetry) for schema, counts, hashes, size and migration state.
3. Owner-confirmed TLS terminator/auth boundary and production `DJANGO_ALLOWED_HOSTS`, `DJANGO_DEBUG=0`, non-placeholder secret shape (values never in Git).
4. Exact pre-cutover backup path, checksum, and a verified disposable restore of that exact backup.
5. Measured production DB size, dump throughput, replica count and in-flight job duration for a defensible window.
6. Exact stop-writers authority (ingress, cron, workers, replicas) and quiescence verification.
7. Fresh pinned release SHA for sign-off; historical `331dbe2`/`a79b926` baselines are not the deployment revision.

## 12. Deployment-ready checklist (all unchecked until evidence supplied)

- [ ] Exact target confirmed: project/host/db name/host/port/role; release SHA pinned; images staged.
- [ ] `to_regclass` table validation passed; counts/hashes in private custody.
- [ ] Exact `$BACKUP_PATH` dumped, SHA-256 recorded, `pg_restore --list` and disposable restore verified.
- [ ] All writers/wokers/schedulers/replicas stopped; ingress removed; quiescence verified.
- [ ] `migrate --plan` reviewed; guarded migration applied with exact confirmation; `migrate --check` green on release image.
- [ ] Runtime guard mount verified read-only; backend recreated with ingress paused.
- [ ] Health/smoke green; entitlement tables empty; verdicts `UNKNOWN`; no live provider call.
- [ ] Explicit owner rollback decision; protected backup retained until window closes.
- [ ] Writers resumed one lane at a time under separate approval; one refresh cycle monitored.
- [ ] Owner sign-off on exact activation scope: Activation A only; Activation B stays disabled.
- [ ] Numeric downtime window supplied from owner-measured evidence (currently UNKNOWN).

## 13. Activation A/B separation and handoff

Activation A (published user-facing wording) was approved by owner on 2026-11-27 (issue #103 decision comment, referenced as 7224557483). Activation B real-world provider/package mappings remain **disabled/UNKNOWN**; this preflight does not activate them. #97 Stage D/E live gates unchanged. #103 remains OPEN; no issue closure.

**ChatGPT Web independent review is explicitly requested** for this draft PR, including whether Gate `BLOCKED_ON_ACCESS` is correctly assigned and whether any listed UNKNOWN can be closed with already-authorized evidence.
