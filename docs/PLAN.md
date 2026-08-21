# Streaming Tracker — Build Plan

## Stack

- **Frontend**: Next.js + Tailwind CSS
- **Backend**: Django + Django REST Framework (admin panel doubles as the manual data-entry UI for `LanguageInfo`)
- **Database**: PostgreSQL 16
- **Hosting**: small self-hosted VPS via `docker-compose`
- **Auth**: none in v1; single-user, but data model leaves room to add it later

## Current status as of 2026-08-18

Phase 0 through Phase 4 are implemented and merged. The original checklist is retained below as the build history, with current status reflected in the checkboxes.

- Docker Compose, Django, Next.js, and PostgreSQL scaffolding are in place.
- Core data model and Django admin support are in place.
- Single-movie search, TMDb matching, cache-first availability lookup, subscription/free filtering, and strict-priority ranking are implemented.
- Cron-driven background refresh and audit logging are implemented with a default budget of 90 requests/day.
- Letterboxd CSV upload, ambiguous TMDb match resolution, quota-aware bulk refresh, watchlist reporting, and report analytics are implemented.
- Frontend smoke tests now cover the search, preferences, watchlist report, and ambiguous-match resolver flows.

## Phase 0 — Project setup

- [x] Provision `docker-compose` with Postgres + Django + Next.js services
- [x] Django project + app scaffolding, Postgres connection
- [x] Configure API-key seams for TMDb and Streaming Availability API (RapidAPI)
- [x] Confirm and document cache-first Streaming Availability API usage constraints

## Phase 1 — Data model & admin

- [x] Django models: `Movie`, `Region`, `Availability`, `LanguageInfo`, `LanguageEntry`, `Subscription`, `Preference`, `WatchlistItem`
- [x] Django admin config for manual `LanguageInfo` entry
- [x] Seed/admin path for single-user `Subscription` and `Preference` data

## Phase 2 — Single-movie search

- [x] TMDb search integration (title → `tmdb_id`)
- [x] Streaming Availability API client with Postgres caching (`last_checked_at`) and refresh-if-stale behavior
- [x] Filter offers to `subscription`/`free` type only, matched against configured subscriptions; rent/buy offers stay excluded
- [x] Ranking logic: strict priority (audio → subtitles → quality)
- [x] API endpoints for search and per-movie ranked availability results
- [x] Next.js search page with ranked results, best match highlighting, and missing-language-info entry path

## Phase 3 — Background refresh job

- [x] Django management command run by cron to refresh the stalest `Availability` rows
- [x] Default scheduled budget is 90 requests/day, leaving reserve under the 100 requests/day free-tier cap
- [x] Basic logging and refresh audit records for observability

## Phase 4 — Letterboxd watchlist free-to-watch check

- [x] CSV upload endpoint for Letterboxd watchlist export
- [x] Title+year → TMDb ID matching, with review/confirm screen for ambiguous matches
- [x] Bulk availability fetch path that respects the daily rate cap and cache-first architecture
- [x] Report endpoint: per movie, which region(s) have it free via subscriptions; unavailable movies flagged
- [x] Next.js upload/report flow, including summary analytics by country and provider

## Release-hardening plan Claude Code can execute next

Work through this in order. Do not make live Streaming Availability API calls during verification; tests should use mocks or cached/local test data.

1. **Verify documentation alignment**
   - Confirm `docs/API.md` and `docs/examples/api/watchlist_report_response.json` include the `summary` object returned by `GET /api/watchlist/report/`.
   - Confirm `docs/CRON.md` and `docs/DEPLOYMENT.md` use `--budget 90`, not `--budget 100`.
   - Confirm `docs/DELEGATION.md` clearly states Tasks 1–15 are historical and no longer active assignments.

2. **Run frontend verification**
   - `npm --prefix frontend test`
   - `npm --prefix frontend run lint`
   - `npm --prefix frontend run build`
   - If lint/build fails because the local dependency install or Next.js config is stale, fix the source/dependency mismatch and rerun.

3. **Refresh backend container dependencies**
   - Rebuild the dev stack so source dependencies such as WhiteNoise are installed in the backend image:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
     ```
   - Then run backend tests:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py test tracker -v 2
     ```

4. **Inspect final diff before commit**
   - `git diff --stat`
   - `git diff -- docs frontend backend`
   - Check that no generated build artifacts, logs, secrets, or local environment files were added.

5. **Commit when verification is clean**
   - Suggested commit title: `Add frontend smoke tests and align release docs`
   - If backend tests still fail because a local container is stale or unavailable, report the exact command/output instead of marking the work fully verified.

## Prime Video MVP hardening (active — Claude Code, as of 2026-08-21)

The Prime Video MVP (`/api/prime/popular/`, `/api/prime/movie/<id>/`, homepage grid, movie detail, greyed-out
preferences) is merged, but two issues block it from rendering real data. This is the core integration path and
stays with Claude Code (not delegated). See `docs/PRIME_MVP_HARDENING.md` for the full plan.

- [ ] **CC-1** Add `image.tmdb.org` to `frontend/next.config.mjs` `images.remotePatterns` — homepage posters
  currently crash at runtime (`next/image` with an unconfigured host); the build passes so it was missed.
- [ ] **CC-2** Reconcile `services/streaming_api.py` parsing + the Prime provider id against one real
  movieofthenight response. `DEFAULT_PREFERENCE_PROVIDER` is the literal `"amazon"`, but the API's Amazon
  service is id `"prime"` / name `"Prime Video"`, so real lookups currently match nothing. Standardize on the
  service **id**; keep tests mocked (no live calls in tests).
- [ ] **CC-3** Verify the click → auto-create Movie → live fetch (`PRIME_LIVE_FETCH`) → cache → ranked-offers
  path end-to-end; document `TMDB_API_KEY`/`RAPIDAPI_KEY` in `.env.example`; optional `seed_prime_fixture`
  command so the UI renders from cached sample data without spending quota.

## Phase 5 — Polish (active queue as of 2026-08-21)

Current post-Prime-MVP polish items. Delegatable ones are briefed in `docs/DELEGATION.md` (Task 16–21); the
Prime MVP hardening above and editable preferences stay with Claude Code.

- [ ] Add CI to run backend tests and frontend test/lint/build on every pull request — **delegated: Task 16**
- [ ] Homepage search box (find a specific movie, not just browse trending) — **delegated: Task 17**
- [ ] Enhance ambiguous TMDb match review with posters and richer metadata — **delegated: Task 18**
- [ ] Add broader frontend coverage for unhappy paths and API error states — **delegated: Task 19**
- [ ] Review npm audit output and update vulnerable frontend dependencies deliberately — **delegated: Task 20**
- [ ] Improve manual language-entry UX beyond the current functional form/admin paths — **delegated: Task 21 (backlog)**
- [ ] Editable preferences end-to-end (un-grey UI, `PATCH /api/preferences/`, ranking reads the DB `Preference` row instead of `DEFAULT_PREFERENCE`) — **Claude Code (not delegated)**

## Explicitly deferred (not v1)

- Notifications
- Availability history / leaving-soon predictions
- IMDb / Trakt sync
- Browser extension
- Real user accounts / multi-tenancy
- Monetization
- Personal library sources (Plex/Jellyfin/custom archives)
- "Watch Together" synchronized playback + public movie rooms/timeline chat — a large, separate future direction (see SPEC.md "Future Directions"); would need its own phased plan (sync infra, provider adapters, browser extension, moderation) if ever pursued

## Open question for future ranking changes

The current ranking priority is audio language match → subtitle language match → quality. Confirm with the user before changing that priority order or treating a different order as fixed.
