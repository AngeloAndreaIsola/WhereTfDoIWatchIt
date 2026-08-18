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

Daily availability background updates are capped to preserve API limits.

To run the refresh command automatically via host `crontab`:

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
   17 3 * * * cd /opt/streaming-tracker && docker compose exec -T backend python manage.py refresh_availability --budget 100 >> ./logs/refresh.log 2>&1
   ```

Refer to [`docs/CRON.md`](CRON.md) for further background task details.

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
- **Quota Protection**: Never trigger live API fetches for entire watchlists simultaneously. Use the daily `--budget 100` cron refresh or on-demand per-movie lookups.
