# Scheduled Refresh (Cron Setup)

This document describes how to configure cron for automatic daily availability background refreshes.

## Recommended Crontab Entry

Run the availability refresh command daily at 03:17 UTC with a maximum budget cap of 100 stalest items:

```cron
17 3 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_availability --budget 100 >> ./logs/refresh.log 2>&1
```

Or if running directly on the host / server environment:

```cron
17 3 * * * cd /path/to/streaming-tracker/backend && python manage.py refresh_availability --budget 100 >> ../logs/refresh.log 2>&1
```

## Options and Configuration

- **Schedule (`17 3 * * *`)**: Daily at 03:17 UTC to avoid peak API traffic.
- **Budget Cap (`--budget 100`)**: Caps the maximum number of stale availability requests per run to 100 items (the free tier daily budget cap).
- **Log Path (`./logs/refresh.log`)**: Output is appended to `./logs/refresh.log` within the project tree (matching `.gitignore` log rules).
- **Audit Logging**: Each invocation automatically creates a `RefreshRun` audit record in PostgreSQL, accessible via the Django Admin interface under `RefreshRun`.
