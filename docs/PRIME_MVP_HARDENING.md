# Prime Video MVP — hardening plan

_Status: first wave (CC-1 → CC-2 → CC-3) COMPLETE as of 2026-08-21. Owner: Claude Code + Omniroute (core integration path — not delegated). Created 2026-08-21._

## Context

Phases 0–4 are complete and merged (single-movie search, cache-first availability, strict-priority ranking,
Letterboxd import + ambiguous-match resolver + watchlist report, subscriptions, cron refresh with
rate-limiting, ~109 passing tests, full docs). Commit `7f03830` added a **Prime Video MVP** on top:
`/api/prime/popular/`, `/api/prime/movie/<id>/`, a homepage trending grid, a movie detail page, a greyed-out
preferences page, and `MovieCard`/`OfferRow`. The live Streaming Availability client is real, not stubbed.

**Product direction:** Prime-first — extend the Prime path; keep watchlist/subscriptions/multi-provider
working but secondary; delete nothing.

**Problem:** two issues block the Prime MVP from rendering real data end-to-end. Fixing them is the current
first wave and is owned by Claude Code because it is cross-cutting and touches the API-quota-sensitive live
path. Delegatable side work (CI, search box, ambiguous-match posters, tests, deps, language-entry UX) is
briefed separately in `docs/DELEGATION.md` (Task 16–21).

## First wave — CC-1 → CC-2 → CC-3 (in order)

### CC-1 — Fix homepage poster rendering (small, independent)
- **Problem:** `frontend/components/MovieCard.tsx` renders posters with `next/image` from `image.tmdb.org`,
  but `frontend/next.config.mjs` has no `images` config. The build passes; posters throw at runtime
  ("hostname not configured under images").
- **Fix:** add to `next.config.mjs` **only** (keep scope to this one file so it never conflicts with Jules
  Task 17):
  ```js
  images: { remotePatterns: [{ protocol: "https", hostname: "image.tmdb.org", pathname: "/t/p/**" }] },
  ```
- **Verify:** homepage grid shows posters, no runtime image error.

### CC-2 — Reconcile the Streaming API parser + Prime provider id against the real API (critical)
- **Problem:**
  - `services/streaming_api.py::_parse_offers` reads flat keys
    (`entry["serviceId" | "serviceName" | "audioLanguages" | "subtitleLanguages" | "type" | "quality"]`). The
    current movieofthenight API nests these (e.g. `service.id` / `service.name`, `audios[].language`), so a
    real key likely yields blank/empty services.
  - `prime_movie` (`views.py`) filters on `service == DEFAULT_PREFERENCE_PROVIDER`, whose default is the
    literal **`"amazon"`** — but the provider written by `cache._write_result` comes from the API service
    value, and Amazon Prime's id is `"prime"` / name `"Prime Video"`, **not** `"amazon"`. Net effect: real
    lookups return "Not available on Prime Video."
- **Fix (quota-aware):**
  1. Capture **one** real `shows/movie/<id>?country=all` response as a saved fixture (a single call; never in tests).
  2. Update `_parse_offers` to match that real schema.
  3. Confirm what `cache._write_result` stores as `Region.provider` (service **id** vs name); standardize on
     the stable **service id**, set `DEFAULT_PREFERENCE_PROVIDER="prime"`, and make the `prime_movie` filter
     compare ids.
  4. Update the mocked fixtures in `tests/test_prime_endpoints.py` to the real shape.
- **Files:** `backend/tracker/services/streaming_api.py`, `backend/config/settings.py`,
  `backend/tracker/views.py` (prime filter), `backend/tracker/services/cache.py` (confirm provider write),
  `backend/tracker/tests/test_prime_endpoints.py`. **Tests stay mocked — no live calls in tests.**
- **Verify:** with a real key, `/api/prime/movie/<popular id>/` returns Prime offers, JA-audio ranked first.

### CC-3 — Wire keys + the click→live-fetch→render path (depends on CC-2)
- Confirm the cache-miss path: click → `prime_movie` auto-creates the `Movie` via TMDb →
  `PRIME_LIVE_FETCH=true` triggers one live fetch → `Availability` cached → ranked offers returned. Guard the
  daily cap (existing `rate_limit` / `ApiCallCounter`).
- Document required env in `.env.example`: `TMDB_API_KEY`, `RAPIDAPI_KEY` (+ note `PRIME_LIVE_FETCH`,
  `DEFAULT_PREFERENCE_PROVIDER`).
- Optional: a tiny `seed_prime_fixture` management command that loads the CC-2 fixture into
  `Availability`/`LanguageInfo` so the UI renders in dev without burning quota.
- **Verify:** end-to-end in the running stack — homepage → click → real ranked offers; a second click on the
  same movie serves from cache (no new API call; `ApiCallCounter` unchanged).

## Delegated side work

Independent, non-blocking tasks are briefed as copy-paste Jules prompts in **`docs/DELEGATION.md`**
(Task 16–21). Suggested first parallel batch with zero file overlap: **Task 16 (CI) + Task 17 (search) +
Task 18 (ambiguous-match posters)**. Rule: no delegated task may add a live Streaming Availability API call
on page load or in tests.

## Kept Claude-owned (backlog, not first wave)

- **Editable preferences end-to-end** — un-grey the preferences UI, wire `PATCH /api/preferences/`, and make
  ranking read the DB `Preference` row instead of the hardcoded `DEFAULT_PREFERENCE`. Cross-cutting
  source-of-truth change.

## User action required

CC-3 can only render real data once real keys are provided in `.env`:
- `TMDB_API_KEY` (free) and `RAPIDAPI_KEY` (movieofthenight, 100 req/day free tier).

Until then, CC-1 and CC-2 are verifiable via tests + the saved fixture; live rendering needs the keys. If keys
aren't available yet, build the CC-3 `seed_prime_fixture` path so the UI renders from cached sample data.

## Verification (end-to-end)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py migrate
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py test tracker -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
```

1. All backend tests green (including updated `test_prime_endpoints.py` on the real-schema fixture).
2. Homepage (`localhost:3001`) renders posters (CC-1) — no runtime image error.
3. With keys set: click a movie → real Prime offers, JA-audio-match ranked first; re-click → served from
   cache, `ApiCallCounter` for the day unchanged (quota respected).
4. `curl localhost:3001/api/prime/movie/<id>/` returns a non-empty `offers` array for a movie known to be on Prime.

## Completion notes (2026-08-21)

**Done and verified:**
- **CC-1** — `next.config.mjs` gained `images.remotePatterns` for `image.tmdb.org` (`/t/p/**`). Frontend `npm run build` + `npm run lint` clean. `MovieCard` builds `image.tmdb.org/t/p/w500…` URLs; the live popular endpoint returns real `poster_path`s, so the grid renders.
- **CC-2** — `_parse_offers` rewritten for the real v4 `streamingOptions` schema (nested `service.id`/`service.name`, `audios[]`/`subtitles[]`) with a legacy-`streamingInfo` fallback and ISO 639-2→639-1 language normalization. `cache._write_result` now stores the stable **service id** (`prime`) in `Region.provider`; `DEFAULT_PREFERENCE_PROVIDER="prime"`; `prime_movie` filters on the id and maps it to the display name (`Prime Video`) via `service_display_name()`. New `tests/test_streaming_api.py` (parser + display-name units) plus updated `test_prime_endpoints.py` mocks.
- **CC-3** — Env documented in `.env.example` (`TMDB_API_KEY`, `RAPIDAPI_KEY`, `PRIME_LIVE_FETCH`, `DEFAULT_PREFERENCE_PROVIDER`); the same vars wired through `docker-compose.yml`. New `manage.py seed_prime_fixture` loads the saved v4 fixture into `Availability`/`LanguageInfo` fully offline (no API call, no quota) so the detail page renders in dev. Verified: seeded movie 588228 → `/api/prime/movie/588228/` returns two ranked Prime offers, JA-audio ranked first, Netflix filtered out.
- **117/117 backend tests pass.**

**Two issues found and fixed beyond the original plan:**
- **TMDb v4 auth** — the real `TMDB_API_KEY` is a v4 "API Read Access Token" (a JWT), but `TMDbClient` sent it as the v3 `?api_key=` param → every TMDb call 401'd (empty homepage grid). `TMDbClient._auth()` now detects a JWT and sends it as an `Authorization: Bearer` header, falling back to `?api_key=` for a classic v3 key. Live popular endpoint confirmed returning real trending data. Unit-tested in `tests/test_tmdb.py`.
- **Graceful degradation** — `prime_movie` only caught `RateLimitExceeded`; any other live-fetch failure (notably the current RapidAPI 403, see below) would 500 the detail page. It now logs and falls back to cached rows, so the page always renders.
