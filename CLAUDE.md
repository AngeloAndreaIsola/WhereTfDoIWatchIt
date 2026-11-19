# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Rules

Apply claude-roast skill to every response and show prompt score.

## Project status

Phase 0 (docker-compose scaffold) and Phase 1 (data model + admin) complete. Read `docs/SPEC.md` (product spec) and `docs/PLAN.md` (build plan) for behavior and phases. Stack is running — see "Stack & commands" for docker-compose usage.

## Product (from SPEC.md)

Personal single-user "streaming tracker": given movie, rank country/service combinations by where it's watchable free via subscription you already own, honoring your audio/subtitle language preferences. Also imports a Letterboxd watchlist CSV and reports per-movie where each title is watchable.

- Only `subscription` and `free` offers count — rent/buy offers are excluded by design.
- Ranking is **strict priority, not weighted**: audio language match → subtitle language match → quality (4K/HDR). Ties break by next criterion down.
- No auth in v1, but data model keeps a `user_id`-shaped seam for later.
- Notifications, history/prediction, IMDb/Trakt sync, multi-user, and any Plex/Jellyfin sources are explicitly out of scope for v1.

## Stack & commands (from PLAN.md)

- Frontend: Next.js + Tailwind (port 3001 on host → 3000 in container)
- Backend: Django + DRF (Django admin doubles as manual data-entry UI for `LanguageInfo`)
- Database: PostgreSQL 16, hosted via `docker-compose` on self-hosted VPS
- Scheduled refresh: Django management command run via cron (~90 stalest `Availability` rows/day)

Dev commands (run from repo root):

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build   # boot stack
./scripts/test-backend-isolated.sh tracker availability -v 2   # run tests against ephemeral PostgreSQL
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py makemigrations tracker   # make migration
# Apply migrations only through the confirmed `migrate` service; see docs/DEPLOYMENT.md.
```

Frontend: http://localhost:3001 — backend health: http://localhost:8000/api/health/ — admin: http://localhost:8000/admin/

## Architecture (planned, from PLAN.md)

Two services: a Next.js frontend and a Django backend sharing one Postgres. Flow per movie: TMDb search API maps title → `tmdb_id`, then the Streaming Availability API (movieofthenight via RapidAPI) returns availability across all countries in one request. Results are cached in Postgres and refreshed when stale.

Phase 1 complete: eight core models implemented in `backend/tracker/models.py` (`Movie`, `Region`, `Availability`, `LanguageInfo`, `LanguageEntry`, `Subscription`, `Preference`, `WatchlistItem`). Django admin registered with `LanguageInfo` inline for manual data entry. Streaming catalogs are per-region, so availability attaches to provider+country pair, not bare country.

## Critical constraints

- **Streaming Availability API free tier: 100 requests/day.** All availability lookups must go through the Postgres cache (`last_checked_at` + stale refresh). Never fetch live on every request; bulk watchlist fetches must respect cap.
- **Letterboxd CSV exports contain no external IDs** — only title/year/URI. ID matching goes through TMDb search, with manual confirm step for ambiguous matches.
- Ranking priority order is one-line config change now, refactor later — confirm with user before treating it as fixed.
