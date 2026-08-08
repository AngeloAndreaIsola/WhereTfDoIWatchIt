# Streaming Tracker — Build Plan

## Stack

- **Frontend**: Next.js + Tailwind CSS
- **Backend**: Django (admin panel doubles as the manual data-entry UI for `LanguageInfo`)
- **Database**: PostgreSQL
- **Hosting**: small self-hosted VPS (e.g. Hetzner) via `docker-compose`
- **Auth**: none in v1; single-user, but data model leaves room to add it later

## Phase 0 — Project setup
- [ ] Provision VPS, set up `docker-compose` with Postgres + Django + Next.js services
- [ ] Django project + app scaffolding, Postgres connection
- [ ] Get API keys: TMDb, Streaming Availability API (RapidAPI)
- [ ] Confirm Streaming Availability API coverage for your target countries (NL, IT, US, JP) and response shape for global (no-country-param) queries

## Phase 1 — Data model & admin
- [ ] Django models: `Movie`, `Availability`, `LanguageInfo`, `Subscription`, `Preference`, `WatchlistItem`
- [ ] Django admin config for `LanguageInfo` (this is your manual data-entry screen)
- [ ] Seed `Subscription` and `Preference` for yourself (no UI needed yet — admin or a fixture is fine)

## Phase 2 — Single-movie search
- [ ] TMDb search integration (title → tmdb_id)
- [ ] Streaming Availability API client, with Postgres caching (`last_checked_at`) and a "refresh if stale" check on lookup
- [ ] Filter offers to `subscription`/`free` type only, matched against your configured `Subscription` list (exclude rent/buy)
- [ ] Ranking logic: strict priority (audio → subtitles → quality)
- [ ] API endpoint: given a title, return ranked, subscription-filtered region results
- [ ] Next.js page: search box → ranked results, best match highlighted; flag rows missing `LanguageInfo` with a prompt to fill it in (links to Django admin, or a small custom form)

## Phase 3 — Background refresh job
- [ ] Scheduled job (cron, or Django management command run via cron) that refreshes ~90 stalest `Availability` entries/day
- [ ] Basic logging so you can see what's been refreshed and catch API errors early

## Phase 4 — Letterboxd watchlist free-to-watch check
- [ ] CSV upload endpoint for Letterboxd watchlist export
- [ ] Title+year → TMDb ID matching, with a review/confirm screen for ambiguous matches
- [ ] Bulk availability fetch (respecting the daily rate cap — queue if the watchlist is large) with caching, filtered to subscription/free offers only
- [ ] Report view: per movie, which region(s) (if any) have it free via your subscriptions; movies with no free option flagged as unavailable
- [ ] Next.js page: upload CSV → see per-movie report

## Phase 5 — Polish (optional, only if v1 feels worth continuing)
- [ ] Manual language-entry form in the Next.js app itself (instead of only Django admin)
- [ ] Better handling of ambiguous TMDb matches (posters/year shown side by side)
- [ ] Basic error/empty states throughout

## Explicitly deferred (not v1)
- Notifications
- Availability history / leaving-soon predictions
- IMDb / Trakt sync
- Browser extension
- Real user accounts / multi-tenancy
- Monetization
- Personal library sources (Plex/Jellyfin/custom archives)
- "Watch Together" synchronized playback + public movie rooms/timeline chat — a large, separate future direction (see SPEC.md "Future Directions"); would need its own phased plan (sync infra, provider adapters, browser extension, moderation) if ever pursued

## Open question before Phase 2
Confirm your actual priority order for ranking if it's not audio > subtitles > quality — it's a one-line config change now, a refactor later.
