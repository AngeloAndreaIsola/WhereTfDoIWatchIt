# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Rules

Apply the claude-roast skill to every response and show the prompt score.

## Project status

Greenfield — no code committed yet. This repo currently contains only planning docs: `docs/SPEC.md` (product spec) and `docs/PLAN.md` (build plan). Read both before starting any feature; the SPEC is the source of truth for behavior, the PLAN defines the stack and phases. Nothing is scaffolded — don't assume any existing structure beyond what's in the docs.

## Product (from SPEC.md)

A personal single-user "streaming tracker": given a movie, rank country/service combinations by where it's watchable free via a subscription you already own, honoring your audio/subtitle language preferences. Also imports a Letterboxd watchlist CSV and reports per-movie where each title is watchable.

- Only `subscription` and `free` offers count — rent/buy offers are excluded by design.
- Ranking is **strict priority, not weighted**: audio language match → subtitle language match → quality (4K/HDR). Ties break by the next criterion down.
- No auth in v1, but the data model keeps a `user_id`-shaped seam for later.
- Notifications, history/prediction, IMDb/Trakt sync, multi-user, and any Plex/Jellyfin sources are explicitly out of scope for v1.

## Planned stack & commands (from PLAN.md — not yet built)

- Frontend: Next.js + Tailwind
- Backend: Django (Django admin doubles as the manual data-entry UI for `LanguageInfo`)
- Database: PostgreSQL, hosted via `docker-compose` on a self-hosted VPS
- Scheduled refresh: Django management command run via cron (~90 stalest `Availability` rows/day)

Commands will be run through docker-compose once scaffolded; none exist yet.

## Architecture (planned, from PLAN.md)

Two services: a Next.js frontend and a Django backend sharing one Postgres. The flow per movie: TMDb search API maps title → `tmdb_id`, then the Streaming Availability API (movieofthenight via RapidAPI) returns availability across all countries in one request. Results are cached in Postgres and refreshed when stale.

Core entities (`Movie`, `Region` = provider+country pair, `Availability`, `LanguageInfo`, `Subscription`, `Preference`, `WatchlistItem`) are modeled in Django. Streaming catalogs are per-region, so availability attaches to a provider+country pair, not a bare country.

## Critical constraints

- **Streaming Availability API free tier: 100 requests/day.** All availability lookups must go through the Postgres cache (`last_checked_at` + stale refresh). Never fetch live on every request; bulk watchlist fetches must respect the cap.
- **Letterboxd CSV exports contain no external IDs** — only title/year/URI. ID matching goes through TMDb search, with a manual confirm step for ambiguous matches.
- Ranking priority order is a one-line config change now, a refactor later — confirm with the user before treating it as fixed.
