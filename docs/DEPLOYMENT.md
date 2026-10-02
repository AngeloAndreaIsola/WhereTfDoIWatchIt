# Production Deployment Guide

This guide describes how to deploy, manage, and maintain **WhereTfDoIWatchIt** on a self-hosted Linux VPS using Docker Compose.

---

## 1. System Prerequisites

Before starting, ensure your VPS meets the following minimum requirements:

- **OS**: Linux (Ubuntu 22.04 LTS or Debian 12 recommended)
- **CPU**: 1 vCPU
- **RAM**: 1 GB minimum (2 GB recommended)
- **Disk**: 10 GB SSD space
- **Software**:
  - Docker (v24.0+)
  - Docker Compose (v2.20+)
  - Git

---

## 2. Environment Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/your-org/streaming-tracker.git /opt/streaming-tracker
   cd /opt/streaming-tracker
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

   Edit `.env` and configure your production credentials:
   ```env
   # PostgreSQL settings
   POSTGRES_USER=tracker
   POSTGRES_PASSWORD=generate_a_secure_password_here
   POSTGRES_DB=tracker
   POSTGRES_HOST=db
   POSTGRES_PORT=5432

   # Django settings
   DJANGO_SECRET_KEY=generate_a_strong_50_char_secret_key
   DJANGO_DEBUG=0
   DJANGO_ALLOWED_HOSTS=yourdomain.com,localhost,127.0.0.1,backend

   # External API keys
   TMDB_API_KEY=your_tmdb_api_key
   RAPIDAPI_KEY=your_rapidapi_key

   # Frontend
   BACKEND_URL=http://backend:8000
   ```

   > **Security Note**: Never expose or commit real secrets to source control. Ensure `.env` permissions are restricted (`chmod 600 .env`).

---

## 3. Building and Starting Services

Run Docker Compose to build images and launch the stack in detached mode:

```bash
docker compose up -d --build
```

### Automatic Initializations
The `backend` entrypoint script (`entrypoint.sh`) will automatically execute:
1. `python manage.py migrate` — Applies all PostgreSQL database migrations.
2. `python manage.py collectstatic --noinput` — Collects Django Admin static files for WhiteNoise static file serving.

### One-off legacy scraper backfill (first deploy after the read-path migration)
Databases that predate the canonical read path store scraper rows only in `tracker.ScrapedContentMapping`. Project them once so the unified catalogue serves them:

```bash
docker compose exec -T backend python manage.py ingest_legacy_scraper_catalogue
```

The command is idempotent (re-running upserts the same `legacy-<pk>` identities) and local-only — no network, no TMDb, no offers written. New scraper rows are bridged automatically on save, so this never needs re-running.

### Event-driven availability refresh (Phase B)
No additional service, worker or cron entry is required: watchlist adds, imports and detail views only queue `tracker.RefreshRequest` rows (migration `tracker.0029_refreshrequest`, applied automatically by the entrypoint above), and the existing `refresh_availability` cron plus `POST /api/watchlist/refresh/` drain that queue first, within the same daily budget. The pending backlog and any `failed` requests are inspectable read-only in the Django admin under **Tracker → Refresh requests**.

---

## 4. Health Checks and Logs

### Monitoring Service Status
Check container health and running state:
```bash
docker compose ps
```

Verify service endpoints:
- **Backend Health Check**:
  ```bash
  curl -I http://localhost:8000/api/health/
  ```
  Should return `HTTP/1.1 200 OK`.

- **Frontend Access**:
  ```bash
  curl -I http://localhost:3001
  ```

### Inspecting Logs
View aggregate logs or stream specific container logs:
```bash
# View all container logs
docker compose logs -f

# View backend logs only
docker compose logs -f backend

# View frontend logs only
docker compose logs -f frontend
```

---

## 5. Scheduled Background Tasks (Cron)

Daily availability background updates are capped to preserve API limits; provider catalogue refreshes (CBC Gem, BBC iPlayer, Tubi, Prime Video, RaiPlay, Pluto TV, PBS, and ABC iview) run uncapped because they use public catalogue endpoints.

To run the refresh commands automatically via host `crontab`:

1. Create a `logs` directory in the repository root:
   ```bash
   mkdir -p /opt/streaming-tracker/logs
   ```

2. Open crontab editor:
   ```bash
   crontab -e
   ```

3. Add the following entry (runs daily at 03:17 UTC):
   ```cron
   17 3 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_availability --budget 90 >> ./logs/refresh.log 2>&1
   ```

4. Add the provider catalogue refresh (every 6 hours; no budget flag):
   ```cron
   17 */6 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue >> ./logs/catalogue.log 2>&1
   ```

   The catalogue command exits `0` on success, `1` when every attempted source failed, and `2` on partial failure; overlapping runs of the same source skip cleanly under a per-source lock.

5. Optionally move the heavy catalogue sources onto their own slots (BBC walks thousands of pages; Tubi and Prime Video each walk up to 500 title pages per run; RaiPlay walks up to 400 programme+video pages per run; ABC iview reads one detail page per series — ~1613 requests paced 0.18 s apart, ≈5 minutes per run):
   ```cron
   17 4 * * *  cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source bbc_iplayer >> ./logs/catalogue.log 2>&1
   17 1 * * *  cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source tubi >> ./logs/catalogue.log 2>&1
   17 7 * * *  cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source prime_video >> ./logs/catalogue.log 2>&1
   17 10 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source rai_play >> ./logs/catalogue.log 2>&1
   17 13 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source pluto_tv >> ./logs/catalogue.log 2>&1
   17 19 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source abc_iview >> ./logs/catalogue.log 2>&1
   ```
   PBS is cheap (one index page plus ~20 batch calls) and can stay on the bare every-6-hours command.

   **Region requirement (Tubi)**: Tubi only serves the United States, so this deployment must egress from a US IP for `--source tubi` to succeed. On a geo-blocked egress the Tubi fetch raises `SourceFetchError` and the run exits `1`/`2` with nothing written — a loud failure, never a silently empty catalogue. If the host cannot egress from the US, disable the source (`availability.Source → enabled = false` in the Django admin at `/admin/`) so scheduled runs stop attempting it; re-enable it when a US egress path exists.

   **Region requirement (Prime Video)**: the Prime Video source is hard-bound to the one territory it was researched against — the Netherlands (`COUNTRY == "NL"` in `backend/availability/sources/prime_video.py`). Prime selects its catalogue by the caller's IP, not by the URL path, so this deployment must egress from an NL IP (or another path that makes Prime answer `currentTerritory == "NL"`). Every fetched page — the `/categories` seed included — carries that territory, and a mismatch raises `SourceFetchError` → run exits `1`/`2` with nothing written, rather than silently persisting another country's catalogue. If the host cannot egress from the Netherlands, disable the source (`availability.Source → enabled = false` in the Django admin at `/admin/`) so scheduled runs stop attempting it; re-enable it when an NL egress path exists. Offers are only written for `Country` rows that exist and are enabled — `NL` is seeded by migration `availability.0004_seed_nl_country`, and a disabled `NL` row skips Prime offers without failing the run (logged per offer).

**Region requirement (RaiPlay)**: none — unlike Tubi and Prime Video, RaiPlay needs no special egress. The source declares the one country the catalogue is published for (`COUNTRY == "IT"` in `backend/availability/sources/rai_play.py`), but that country is read from each video's access specification (`eligibleRegion.name`), not from the caller's IP: the catalogue pages and their specs are readable from any egress (verified from a non-IT host on 2026-09-30), a spec naming another country skips that offer (logged, `reason=region_mismatch`) instead of failing the run, and a spec that states no region skips it as unobservable (`reason=region_unobserved`). A bounded run makes ≈1.2k requests (≈8 minutes) against RaiPlay's public site — no API key, no quota, no effect on the Streaming Availability budget. If RaiPlay stops answering from this host the run fails loudly (`SourceFetchError` → exit `1`/`2`, nothing written); disable the source (`availability.Source → enabled = false`) to stop scheduled attempts.

**Region requirement (Phase E sources)**: `pbs`, `abc_iview` and `mediaset_infinity` need no special egress — the PBS content API serves the same US catalogue from any IP, the iview catalogue endpoints answer from any egress (verified from a non-AU host; only playback is geo-blocked, and playback is never fetched), and Mediaset's gateway exposes no egress probe. `youtube` pins its own region (default `US`; offers are written only for its five `FREE_PROGRAM_COUNTRIES`). `pluto_tv` is IP-sensitive: Pluto derives `collection.region` server-side from the egress IP and cannot be overridden, so an IP Pluto serves only its international landing to is a successful observation of nothing (`pluto_tv_unsupported_region`, exit `0`, no offers) — no failure and no config, but offers only appear when the egress is one Pluto serves as a real market. All five are 24h-fresh sources (`youtube`, `pluto_tv`, `mediaset_infinity`, `pbs`, `abc_iview` in `availability.freshness`); `youtube` and `mediaset_infinity` are title-targeted and run inside the budgeted daily `refresh_availability` sweep, never under `refresh_provider_catalogue`.

Refer to [`docs/CRON.md`](CRON.md) for further background task details, including the full scheduling semantics (discovery, locking, country scope, failure isolation).

---

## 6. PostgreSQL Backup and Restore

### Creating a Database Backup
To perform an on-demand SQL dump of your PostgreSQL database:

```bash
mkdir -p ./backups
docker compose exec db pg_dump -U tracker tracker > ./backups/tracker_backup_$(date +%Y%m%d_%H%M%S).sql
```

Compress old backups to save disk space:
```bash
gzip ./backups/*.sql
```

### Restoring from a Backup
To restore a SQL dump into the database:

1. Stop application containers to prevent write conflicts:
   ```bash
   docker compose stop backend frontend
   ```

2. Drop and recreate database or restore directly:
   ```bash
   # Gunzip if compressed
   gunzip ./backups/tracker_backup_YYYYMMDD_HHMMSS.sql.gz

   # Drop existing database tables and restore
   docker compose exec -T db psql -U tracker -d tracker < ./backups/tracker_backup_YYYYMMDD_HHMMSS.sql
   ```

3. Restart application services:
   ```bash
   docker compose start backend frontend
   ```

> **Warning**:
> - Always test backup restorations in a staging environment before relying on them.
> - SQL dumps contain sensitive application data; ensure `./backups` directory permissions are secure (`chmod 700 ./backups`).

---

## 7. Updating and Redeploying

To deploy new code updates:

1. Pull latest changes:
   ```bash
   git pull origin master
   ```

2. Rebuild and restart containers:
   ```bash
   docker compose up -d --build
   ```

3. Clean up unused images and build caches:
   ```bash
   docker system prune -f
   ```

---

## 8. API Quota Operational Notes

- **Streaming Availability API Limit**: The free tier allows up to **100 requests per day**.
- **Cache-First Architecture**: All movie availability lookups query PostgreSQL cache first (`Availability` model).
- **Quota Protection**: Never trigger live API fetches for entire watchlists simultaneously. Use the daily `--budget 90` cron refresh so the scheduled job stays below the 100 request/day free-tier cap and leaves a small reserve for on-demand per-movie lookups.
