# Scheduled Refresh (Cron Setup)

This document describes how to configure cron for automatic daily availability background refreshes.
For full server setup and deployment instructions, see [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Recommended Crontab Entry

Run the availability refresh command daily at 03:17 UTC with a budget cap of 90 stalest items. The external free tier allows 100 requests/day; keeping the scheduled job at 90 preserves a small reserve for ad-hoc lookups.

```cron
17 3 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_availability --budget 90 >> ./logs/refresh.log 2>&1
```

Or if running directly on the host / server environment:

```cron
17 3 * * * cd /path/to/streaming-tracker/backend && python manage.py refresh_availability --budget 90 >> ../logs/refresh.log 2>&1
```

## Options and Configuration

- **Schedule (`17 3 * * *`)**: Daily at 03:17 UTC to avoid peak API traffic.
- **Budget Cap (`--budget 90`)**: Caps the scheduled refresh to 90 stale availability requests per run, leaving roughly 10 calls/day available for manual or on-demand lookups under the 100 request/day free-tier cap.
- **Log Path (`./logs/refresh.log`)**: Output is appended to `./logs/refresh.log` within the project tree (matching `.gitignore` log rules).
- **Audit Logging**: Each invocation automatically creates a `RefreshRun` audit record in PostgreSQL, accessible via the Django Admin interface under `RefreshRun`.
