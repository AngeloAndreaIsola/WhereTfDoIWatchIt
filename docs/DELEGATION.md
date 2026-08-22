# Delegation Plan

This document tracks side-feature work that can be delegated to Jules/Codex Cloud/Devin while main development continues in Claude Code connected to Omniroute.

## Working model

- **Main development owner:** Claude Code + Omniroute.
- **Delegated worker:** Jules, used for whole side features rather than tiny chores.
- **Daily limit:** 15 tasks/day.
- **Parallel limit:** 3 tasks at a time.
- **Delegation target size:** each delegated task should be substantial enough to justify a task slot, ideally a self-contained feature bundle likely to produce hundreds to ~1000+ lines across implementation, tests, and docs.
- **Avoid wasting tasks:** prefer bundles with follow-up parts, e.g. `Task 7a`, `Task 7b`, `Task 7c`, where each part is useful on its own and can be merged independently.
- **Conflict rule:** no two parallel Jules tasks should touch the same files unless one is explicitly sequenced after the other.
- **API quota rule:** no delegated task should add live Streaming Availability API calls on page load or in tests. All availability lookups must respect the Postgres cache and the 100 requests/day free-tier constraint.

## Current known state

> Status update (2026-08-18): delegated Tasks 1–15 have been completed and merged. The detailed task briefs below are retained as historical implementation context, not as active parallel work assignments.

As of 2026-08-18:

- Phase 0 and Phase 1 are complete.
- Phase 2 single-movie search is implemented.
- Phase 3 cron/audit work has been merged.
- Phase 4 watchlist upload, ambiguous-match resolution, bulk refresh, report endpoint, report frontend, and analytics summary work have been merged.
- Tasks 1–15 in this file are historical records of completed delegated work.

Before assigning new work, rebase it on latest `master`, inspect the current code, and create fresh task briefs rather than reusing the stale sequencing below.

---

# Active delegation queue (as of 2026-08-21)

> This is the current set of delegatable work. Everything below the **"Historical file restrictions"** heading (Tasks 7–15) is retained as history only.
> The core integration path — **Prime MVP hardening (CC-1..CC-3 in `docs/PRIME_MVP_HARDENING.md`)** and **editable preferences end-to-end** — stays with Claude Code + Omniroute and is **not** delegated.

**Context:** Phases 0–4 plus a Prime Video MVP are merged. Product direction is **Prime-first**: extend the Prime path; keep watchlist/subscriptions/multi-provider working but secondary; delete nothing.

**Rules (unchanged):** one branch per task; no two parallel tasks touch the same files; **no delegated task may add a live Streaming Availability API call on page load or in tests.** TMDb calls are fine — TMDb is uncapped; the 100 req/day cap applies to the Streaming Availability API only.

**Suggested first parallel batch (zero file overlap): Task 16 + Task 17 + Task 18.**

> **⚠ Known issue (Claude-owned, blocks green CI): `frontend/__tests__/pages.test.ts` is stale.** Its first test (`"searches for a movie and shows the no-offers availability state"`) imports `@/app/page` and expects a `"Search for a movie…"` input + `Search` button + `/api/search/` → `/api/movie/<id>/availability/`. But the homepage is now the **Prime trending grid** (`fetchPopularMovies` → `/api/prime/popular/`, no search input), so that test fails today — it has gone unnoticed because there is no CI yet. **Claude Code will update `pages.test.ts` to match the current homepage** before/with CI. Implications: **Task 16** should expect that fix to land so CI is green; **Task 19** must NOT edit `pages.test.ts` (add a new file instead); **Task 17** re-adds a homepage search box but with different behavior (dropdown → navigate to `/movie/<id>`), so it should not try to satisfy the old test.

| Task | Summary | Exclusive files | Depends on |
|------|---------|-----------------|------------|
| 16 | CI pipeline (GitHub Actions) | `.github/workflows/**`, new `docs/CI.md` | — |
| 17 | Homepage search box | new `components/SearchBar.tsx`, `app/page.tsx`, `lib/api.ts` | CC-1 (image config) — effectively parallel |
| 18 | Ambiguous-match review posters | `services/letterboxd.py` (1-line candidate passthrough) + `test_letterboxd.py` + `app/watchlist/ambiguous/page.tsx` + optional new component | — |
| 19 | Frontend unhappy-path tests | new `frontend/__tests__/error-states.test.tsx` only (Claude fixes stale `pages.test.ts`) | after 17/18 |
| 20 | npm audit + dependency hardening | `frontend/package.json`, lockfile, new `docs/DEPENDENCY_AUDIT.md` | after 16 (ideally after 17/18/19) |
| 21 | Manual language-entry UX | `components/OfferList.tsx` + optional `components/LanguagePicker.tsx` + co-located test + `backend/tracker/admin.py` | after 19 |

## Task 16 — CI pipeline (backend tests + frontend lint/build/test)

**Branch:**

```text
ci/github-actions
```

**Goal:**

Add a GitHub Actions workflow that runs backend tests and the frontend lint/build/test on every pull request and on pushes to `master`, so regressions are caught automatically (Phase 5 item).

**Allowed files:**

```text
.github/workflows/ci.yml
docs/CI.md
```

**Do not touch:**

```text
backend/**
frontend/**
docs/DELEGATION.md
docs/PLAN.md
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
ci/github-actions

Goal:
Add a GitHub Actions CI workflow. Do NOT change any application code.

Allowed files:
- .github/workflows/ci.yml
- docs/CI.md

Do not touch:
- backend/**, frontend/** (application code), docs/DELEGATION.md, docs/PLAN.md

Requirements:
1. Trigger on pull_request (all branches) and on push to master.
2. Job "backend" (working-directory ./backend): actions/setup-python with python-version "3.12" (matches backend/Dockerfile python:3.12-slim) and cache: "pip". Add a postgres:16 service container with a health check (matches docker-compose). Job env: POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD (any test values), POSTGRES_HOST=localhost, POSTGRES_PORT=5432, and TMDB_API_KEY="" + RAPIDAPI_KEY="" (tests are already mocked — the workflow MUST NOT make live external API calls). Steps: install requirements, `python manage.py migrate`, then `python manage.py test tracker -v 2`.
3. Job "frontend" (working-directory ./frontend): actions/setup-node with node-version "20" (matches frontend/Dockerfile node:20-alpine) and cache: "npm" (cache-dependency-path: frontend/package-lock.json). Steps: `npm ci`, `npm run lint`, `npm run build` (env NEXT_TELEMETRY_DISABLED=1), then `npm test` (already `vitest run` — non-interactive).
4. Both jobs run on ubuntu-latest and must pass independently.
5. Add docs/CI.md briefly describing the workflow and how to read a failing run.

Verification:
- Open a draft PR; both jobs run and pass.

Acceptance criteria:
- CI runs on PRs and master pushes.
- Backend + frontend jobs are green.
- No live Streaming Availability or TMDb calls occur in CI.
- No application code changed.
```

## Task 17 — Homepage search box

**Branch:**

```text
feat/homepage-search
```

**Goal:**

Add a search box to the homepage so a user can find a specific movie (not just browse trending) and jump straight to its Prime Video detail page, using the existing `GET /api/search/` endpoint.

**Allowed files:**

```text
frontend/components/SearchBar.tsx
frontend/app/page.tsx
frontend/lib/api.ts
```

**Do not touch:**

```text
backend/**
frontend/next.config.mjs
frontend/components/MovieCard.tsx
frontend/app/movie/**
frontend/app/watchlist/**
frontend/app/preferences/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/homepage-search

Goal:
Add a search box on the homepage that calls the EXISTING backend search endpoint and routes to a movie's detail page. Do not add any new backend endpoints or any Streaming Availability API calls.

The search endpoint's shape is already confirmed, build to it exactly: GET /api/search/?q=<title> returns JSON {"results": [{"tmdb_id": number, "title": string, "year": number|null}, ...]} (max 10; built inline in the view — there is no serializer, and there is NO poster_path/overview field). It hits TMDb (free/uncapped), never the Streaming Availability API. Note: app/page.tsx currently has NO search input — it only renders the trending grid — so SearchBar is a brand-new element added ABOVE the grid, not a replacement.

Allowed files:
- frontend/components/SearchBar.tsx  (new)
- frontend/app/page.tsx
- frontend/lib/api.ts

Do not touch:
- backend/**, frontend/next.config.mjs, frontend/components/MovieCard.tsx, other app routes

Requirements:
1. Add to frontend/lib/api.ts: an exported type `SearchResult = { tmdb_id: number; title: string; year: number | null }` and an async `search(query: string): Promise<SearchResult[]>` that calls `GET /api/search/?q=${encodeURIComponent(query)}` and returns the response's `results` array. Guard empty/whitespace queries client-side (return [] without calling — the backend returns 400 on an empty q).
2. Create frontend/components/SearchBar.tsx (Client Component): a text input with ~300ms debounced querying, a floating results dropdown, loading/empty/error states, click-outside-to-close, and keyboard navigation (ArrowUp/ArrowDown to move highlight, Enter to select, Escape to close). Each row shows title + year only (search returns no poster).
3. Selecting a result navigates to /movie/<tmdb_id> using next/navigation useRouter.
4. Render SearchBar near the top of app/page.tsx, above the trending grid. Keep the existing grid working.
5. Use Tailwind consistent with the app (light/dark mode, max-w-6xl container).
6. Do NOT use next/image (avoid a dependency on the image config); if you show any thumbnail use a plain <img>.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- Typing a title shows matching results from /api/search/.
- Selecting a result navigates to that movie's detail page.
- Loading/empty/error states are handled.
- No backend changes; no new Streaming Availability calls.
```

## Task 18 — Ambiguous-match review posters + richer metadata

**Branch:**

```text
feat/ambiguous-match-posters
```

**Goal:**

Improve the Letterboxd ambiguous-match resolver so each candidate shows a poster, year, and short overview — making it far easier to pick the right TMDb match (Phase 5 item).

**Allowed files:**

```text
backend/tracker/services/letterboxd.py
backend/tracker/tests/test_letterboxd.py
frontend/app/watchlist/ambiguous/page.tsx
frontend/components/AmbiguousMatchCard.tsx
```

**Do not touch:**

```text
backend/tracker/views.py
backend/tracker/serializers.py
backend/tracker/models.py
frontend/next.config.mjs
frontend/components/MovieCard.tsx
frontend/app/page.tsx
frontend/lib/api.ts
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/ambiguous-match-posters

Goal:
Enrich the ambiguous TMDb match review UI with posters and metadata. This needs ONE tiny backend passthrough plus the frontend work — no new endpoints, no new API calls.

Confirmed state (do not re-litigate): the ambiguous payload's candidates are built by _candidate_dict() in backend/tracker/services/letterboxd.py, which currently emits only {tmdb_id, title, year}. The TMDbMovie objects it receives ALREADY carry poster_path and overview (populated by the same TMDb search that produced the matches), so exposing them is a pure passthrough — NO extra API call, no new request.

Allowed files:
- backend/tracker/services/letterboxd.py      (extend _candidate_dict only)
- backend/tracker/tests/test_letterboxd.py     (update candidate-shape assertions)
- frontend/app/watchlist/ambiguous/page.tsx
- frontend/components/AmbiguousMatchCard.tsx    (new, optional but recommended)

Do not touch:
- backend/tracker/views.py, backend/tracker/serializers.py, backend/tracker/models.py
- frontend/next.config.mjs, frontend/components/MovieCard.tsx, frontend/app/page.tsx, frontend/lib/api.ts

Requirements:
1. Backend: extend _candidate_dict() in services/letterboxd.py to also return poster_path and overview from the TMDbMovie (both already exist on the dataclass). Do NOT add any request/API call. Update any candidate-shape assertion in tests/test_letterboxd.py to include the two new keys.
2. Frontend: extend the Candidate interface with poster_path?: string | null and overview?: string | null. For each candidate render the poster via a plain <img src="https://image.tmdb.org/t/p/w185${poster_path}"> (NOT next/image, to stay independent of the image config), plus title, year, and a line-clamped overview ("No overview available." fallback when empty).
3. Handle a null/missing poster_path gracefully with a placeholder box.
4. Keep the existing selection/confirm behavior and endpoint calls exactly as they are; clicking anywhere on a candidate card selects its radio.
5. Tailwind consistent with the app; light/dark mode.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py test tracker.tests.test_letterboxd -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- _candidate_dict returns poster_path + overview; test_letterboxd passes with updated assertions.
- Each candidate shows poster (or placeholder) + year + overview.
- Confirm/selection flow still works and hits the same endpoint.
- No new API calls; no next/image dependency.
```

## Task 19 — Frontend unhappy-path test coverage

**Branch:**

```text
test/frontend-error-states
```

**Goal:**

Expand frontend test coverage for error, empty, and loading states (Phase 5 item), using the existing test setup.

**Allowed files:**

```text
frontend/__tests__/error-states.test.tsx   (new — all new tests go here)
```

**Do not touch:**

```text
frontend/__tests__/pages.test.ts   (STALE — Claude Code is fixing it; see the Known issue note above)
any non-test frontend source; backend/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
test/frontend-error-states

Goal:
Add frontend tests for unhappy-path states (error / empty / loading). Add ONLY a new test file — modify no source.

IMPORTANT — do NOT touch frontend/__tests__/pages.test.ts. Its first test targets an old search homepage that no longer exists (the homepage is now a Prime grid); Claude Code is fixing that separately. Put ALL your new tests in a NEW file: frontend/__tests__/error-states.test.tsx.

Test setup (copy the existing conventions from frontend/__tests__/pages.test.ts): Vitest + @testing-library/react + @testing-library/user-event. Mock next/link and next/navigation. Stub the network with vi.stubGlobal("fetch", fetchMock) returning sequential mockResolvedValueOnce({ ok, status, json: async () => body }). Every response is mocked — NO real network, NO Streaming Availability calls.

Cover these exact states (routes, seams, and asserted strings are confirmed):

1. Homepage "/" (import @/app/page) — fetches GET /api/prime/popular/?limit=24
   - Error: mock { ok:false, status:500, body:{} } -> renders banner "Failed to load popular movies (500)".
   - Empty: mock { ok:true, body:{ results:[], count:0, cached:true, source:"tmdb" } } -> renders "No movies found."

2. Movie detail "/movie/[tmdb_id]" (import @/app/movie/[tmdb_id]/page; render with params={ tmdb_id: "603" }) — fetches GET /api/prime/movie/603/
   - Error: { ok:false, status:500, body:{} } -> banner "Failed to load offers (500)".
   - Empty: { ok:true, body:{ movie:{ tmdb_id:603, title:"The Matrix", year:1999 }, offers:[] } } -> "Not available on Prime Video in any country."
   - (Optional) Loading: assert "Loading Prime Video offers…" shows before the promise resolves.

3. Preferences "/preferences" (import @/app/preferences/page) — fetches GET /api/preferences/
   - Error: { ok:false, status:500, body:{} } -> banner "Failed to load preferences (500)" AND the form is hidden (queryByLabelText("Audio Languages") is null).

4. Watchlist report "/watchlist/report" (import @/app/watchlist/report/page) — fetches GET /api/watchlist/report/
   - Error: { ok:false, status:429, body:{ error:"Rate limited — please try again later." } } -> banner shows that message. (500 with empty body -> "Failed to load report (500)".)
   - Empty: { ok:true, body:{ report:[], summary:{ total_movies:0, available_movies:0, unavailable_movies:0, countries:[], services:[] } } } -> "No availability data recorded for any country yet." and "No matching movies found." are present.

5. (Stretch) Watchlist "/watchlist" (import @/app/watchlist/page) — fetches GET /api/watchlist/ then per-movie GET /api/movie/<id>/availability/
   - Watchlist load 429 -> "Rate limited — try again later." ; availability empty offers -> "No streaming offers found for your preferences." ; availability 429 -> "Daily API limit reached; try again later." (This page needs a click to load availability — include only if straightforward.)

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm test

Acceptance criteria:
- frontend/__tests__/error-states.test.tsx covers items 1–4 (5 optional) and passes.
- No source files changed; pages.test.ts untouched; no live network calls.
```

## Task 20 — npm audit review + dependency hardening

**Branch:**

```text
chore/npm-audit-hardening
```

**Goal:**

Triage `npm audit`, apply safe upgrades, and document deferred/breaking ones with rationale (Phase 5 item). Run after Task 16 (CI) merges so CI validates the result.

**Allowed files:**

```text
frontend/package.json
frontend/package-lock.json
docs/DEPENDENCY_AUDIT.md
```

**Do not touch:**

```text
application source; backend/**; .github/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
chore/npm-audit-hardening

Goal:
Review frontend dependency vulnerabilities and apply safe fixes without breaking the build.

Allowed files:
- frontend/package.json, frontend/package-lock.json, docs/DEPENDENCY_AUDIT.md (new)

Do not touch:
- application source, backend/**, .github/**

Requirements:
1. Run `npm audit`; categorize findings by severity and prod-vs-dev scope.
2. Apply only non-breaking fixes: `npm audit fix` WITHOUT --force, plus deliberate minor/patch bumps that stay within the current major ranges. If a fix would require changing any application source file, do NOT apply it — defer and document it (source changes are out of scope for this task and would conflict with other tasks).
3. Do NOT apply breaking major upgrades. Expect these to require deferral: Next.js 14 -> 15/16 (pulls React 18 -> 19, an architectural jump for this App Router app), and eslint 8 -> 9 / eslint-config-next major. Document each with reason, risk, and a recommended future upgrade path.
4. docs/DEPENDENCY_AUDIT.md must contain: (a) audit summary — counts by severity, split prod vs dev, with the audit date; (b) fixes applied — each safe minor/patch bump and why it's safe; (c) deferred items — each breaking/major vuln with rationale, risk assessment, and upgrade path; (d) a short "re-audit cadence" note.
5. Keep `npm run build`, `npm run lint`, and `npm test` green after changes.

Sequencing: run after Task 16 (so CI validates the lockfile change) and ideally after Tasks 17/18/19 land (those change frontend source/tests; sequencing avoids a dep bump breaking their build). This task's file set (package.json / lockfile / doc) does not overlap any other task — the risk is semantic, not file-level.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm test

Acceptance criteria:
- Safe fixes applied; audit results and deferrals documented.
- Build, lint, and tests all pass.
```

## Task 21 — Manual language-entry UX

**Status:** backlog; lower priority than the first wave. **File set is now confirmed (below).** Sequence AFTER Task 19 — both concern the `/watchlist` page and `OfferList.tsx`, so running them together would race (Task 19 asserts current states; Task 21 changes the language-entry modal).

**Confirmed entry paths (verified in code):**
- **Frontend:** the language-info edit modal lives inside `frontend/components/OfferList.tsx` (opened from the `/watchlist` page). It has two comma-separated text inputs (audio, subtitle), validates ISO 639-1 codes via `parseAndValidateCodes`, and PUTs to `/api/movie/<tmdb_id>/language-info/`. This is the thing to improve — enhance it in place or extract a reusable `frontend/components/LanguagePicker.tsx`.
- **Django admin:** `backend/tracker/admin.py` — `LanguageInfoAdmin` with a `LanguageEntryInline` (TabularInline). `PreferenceAdmin` already adds ArrayField help text ("Comma-separated ISO 639-1 codes, first = highest priority.") — mirror that pattern if you touch admin.

**Allowed files:**

```text
frontend/components/OfferList.tsx
frontend/components/LanguagePicker.tsx            (new, optional)
frontend/components/__tests__/OfferList.test.tsx  (new — co-located, NOT in frontend/__tests__)
backend/tracker/admin.py
```

**Do not touch:**

```text
frontend/__tests__/**            (Task 19 owns these)
frontend/app/watchlist/page.tsx  (leave the page; change the component it renders)
frontend/app/page.tsx, frontend/lib/api.ts   (Task 17)
backend/tracker/views.py, backend/tracker/models.py, backend/tracker/services/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/manual-language-entry-ux

Goal:
Make manual audio/subtitle language entry faster and clearer. The current UI (a modal in frontend/components/OfferList.tsx) uses two raw comma-separated text inputs. Replace them with a better picker while keeping the SAME save contract.

Confirmed scope (do not re-map): the frontend entry point is the language-info modal inside frontend/components/OfferList.tsx, which PUTs comma-separated ISO 639-1 codes to /api/movie/<tmdb_id>/language-info/. Do not change that endpoint, the request body shape, or the ranking/Streaming Availability logic. No live Streaming Availability API calls anywhere.

Allowed files:
- frontend/components/OfferList.tsx
- frontend/components/LanguagePicker.tsx (new, optional but recommended)
- frontend/components/__tests__/OfferList.test.tsx (new — co-located; do NOT add files under frontend/__tests__)
- backend/tracker/admin.py (optional, light polish only)

Requirements:
1. Frontend: a searchable/filterable multi-select for ISO 639-1 languages showing English names + code (e.g. "English (en)", "Japanese (ja)"), selected items as removable tag badges, and quick-add buttons for common languages (en, ja, es, fr, de, it). It must still produce the same list of 2-letter codes and PUT them to the existing endpoint unchanged; keep the existing 2-letter validation behavior (reject non-ISO-639-1 input the way parseAndValidateCodes does).
2. Show what is already entered when the modal opens (prefill from current values).
3. Keep light/dark Tailwind styling consistent with the app.
4. Add co-located component tests (Vitest + testing-library) for select/remove/quick-add and that the correct code list is submitted. Mock fetch — no live calls.
5. (Optional) Admin polish in backend/tracker/admin.py only: mirror PreferenceAdmin's ArrayField help text / minor display refinements on LanguageInfoAdmin/LanguageEntryInline. The frontend modal is the primary focus; heavy admin autocomplete is not required.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm test
(if admin touched) docker compose ... exec -T backend python manage.py test tracker -v 2

Acceptance criteria:
- Entering languages is measurably faster/clearer (search, tags, quick-add) while submitting the identical code list to the unchanged endpoint.
- New co-located tests pass; existing tests still pass; no live calls; no changes to endpoints/models/ranking.
```

## Historical file restrictions

Task 4 previously owned the `/api/watchlist/report/` backend endpoint and temporarily restricted parallel edits to:

```text
backend/tracker/views.py
backend/tracker/urls.py
backend/tracker/tests/test_views.py
```

Those restrictions are no longer active because Task 4 has merged.

---

# Delegation queue

## Task 7 — Frontend app shell, report page integration, and smoke tests

Use Task 7 as a three-part sequence. Do not run 7a, 7b, and 7c in parallel unless file ownership is changed first.

### Task 7a — App navigation shell

**Branch:**

```text
feat/frontend-navigation-shell
```

**Goal:**

Add a shared app navigation shell now that the app has Home, Watchlist, and Preferences pages.

**Allowed files:**

```text
frontend/app/layout.tsx
frontend/components/AppNav.tsx
frontend/components/AppShell.tsx
```

**Do not touch:**

```text
frontend/app/page.tsx
frontend/app/watchlist/**
frontend/app/preferences/**
backend/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/frontend-navigation-shell

Goal:
Add a simple shared frontend navigation shell for the Next.js app.

Allowed files:
- frontend/app/layout.tsx
- frontend/components/AppNav.tsx
- frontend/components/AppShell.tsx

Do not touch:
- frontend/app/page.tsx
- frontend/app/watchlist/**
- frontend/app/preferences/**
- backend/**

Requirements:
1. Create frontend/components/AppNav.tsx.
2. AppNav must use Next.js Link components from next/link.
3. AppNav should be a Client Component using usePathname() from next/navigation for simple active-link styling.
4. Include links to:
   - Home: /
   - Watchlist: /watchlist
   - Preferences: /preferences
5. Create frontend/components/AppShell.tsx.
6. AppShell should render a top header with:
   - brand link: Where TF Do I Watch It
   - AppNav
   - children below the header
7. Avoid nested <main> tags. Existing pages may already render their own <main>. Use a header plus a plain div wrapper around children.
8. Use Tailwind styling consistent with the existing app:
   - max-w-4xl, not max-w-7xl
   - px-4 sm:px-6
   - border-b border-gray-200 dark:border-gray-800
   - light/dark mode support
   - simple hover and active states
9. Update frontend/app/layout.tsx to wrap children with AppShell.
10. Do not add dropdowns, icons, mobile menus, external UI libraries, or backend changes.

Verification:
Run:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- App has visible nav on all pages.
- Home, Watchlist, and Preferences links work.
- Active link is visually distinct.
- Existing pages still build.
- No backend files changed.
```

### Task 7b — Watchlist report frontend page

**Dependency:** wait until Task 4 is merged.

**Branch:**

```text
feat/watchlist-report-page
```

**Goal:**

Add a frontend report page that consumes `GET /api/watchlist/report/` and presents per-movie availability in a useful way.

**Allowed files:**

```text
frontend/app/watchlist/report/page.tsx
frontend/components/ReportSummary.tsx
frontend/components/ReportTable.tsx
frontend/components/ReportMovieCard.tsx
```

If the existing watchlist page needs one link to the report page, also allow:

```text
frontend/app/watchlist/page.tsx
```

**Do not touch:**

```text
backend/**
frontend/app/preferences/**
frontend/app/page.tsx
```

**Expected backend response from Task 4:**

```json
{
  "report": [
    {
      "movie": { "id": 1, "tmdb_id": 123, "title": "Movie", "year": 2004 },
      "available_countries": ["GB", "US"],
      "best_country": "GB",
      "best_service": "Netflix"
    }
  ]
}
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/watchlist-report-page

Dependency:
Start only after the backend Task 4 report endpoint is merged.

Goal:
Create a frontend report page for /watchlist/report that consumes GET /api/watchlist/report/ and displays per-movie availability.

Allowed files:
- frontend/app/watchlist/report/page.tsx
- frontend/components/ReportSummary.tsx
- frontend/components/ReportTable.tsx
- frontend/components/ReportMovieCard.tsx
- frontend/app/watchlist/page.tsx only if adding a small link/button to /watchlist/report

Do not touch:
- backend/**
- frontend/app/preferences/**
- frontend/app/page.tsx

Requirements:
1. Create a client page at frontend/app/watchlist/report/page.tsx.
2. Fetch /api/watchlist/report/ on page load.
3. Show loading, error, empty, and success states.
4. Display summary metrics:
   - total movies
   - available movies
   - unavailable movies
   - countries represented
   - top services by count if easy to compute client-side
5. Display each movie with:
   - title and year
   - best service and best country if available
   - all available countries
   - unavailable state if no best_country/best_service
6. Add basic client-side filters:
   - all
   - available
   - unavailable
7. Add a small search/filter input for movie title.
8. Use Tailwind styling consistent with the app and dark mode.
9. Do not fetch per-movie availability from /api/movie/<tmdb_id>/availability/ here. This page must use only /api/watchlist/report/.
10. If modifying frontend/app/watchlist/page.tsx, only add a small link/button to /watchlist/report.

Verification:
Run:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- /watchlist/report renders data from /api/watchlist/report/.
- Loading/error/empty states are user-friendly.
- Available and unavailable movies are easy to distinguish.
- Client-side filtering works.
- No backend files changed.
```

### Task 7c — Frontend smoke tests and API contract mocks

**Dependency:** run after 7a and 7b are merged.

**Branch:**

```text
test/frontend-smoke-contracts
```

**Goal:**

Add frontend test infrastructure or a lightweight smoke-test script so future UI changes are less risky.

**Allowed files:**

```text
frontend/package.json
frontend/package-lock.json
frontend/tests/**
frontend/playwright.config.ts
frontend/app/**/__tests__/**
frontend/components/**/__tests__/**
docs/FRONTEND_TESTING.md
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
test/frontend-smoke-contracts

Dependency:
Start after frontend navigation shell and watchlist report page are merged.

Goal:
Add practical frontend smoke testing for the main user-facing routes.

Allowed files:
- frontend/package.json
- frontend/package-lock.json
- frontend/tests/**
- frontend/playwright.config.ts
- frontend/app/**/__tests__/**
- frontend/components/**/__tests__/**
- docs/FRONTEND_TESTING.md

Requirements:
1. Prefer Playwright if it is a clean fit for the existing Next.js setup.
2. Add smoke tests for:
   - home page loads and has search UI
   - navigation links are visible
   - preferences page loads
   - watchlist page loads
   - watchlist report page handles a mocked successful report response
   - watchlist report page handles a mocked empty report response
3. Mock network calls where needed. Do not require live backend or live external APIs for frontend tests.
4. Add npm scripts for running the tests.
5. Document how to run the tests in docs/FRONTEND_TESTING.md.
6. Keep setup minimal. Do not introduce a large test framework pile if Playwright alone is sufficient.

Verification:
Run:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
Run the new frontend smoke test command you add.

Acceptance criteria:
- Frontend tests run without live external APIs.
- Main routes are smoke-tested.
- Test command is documented.
- Existing lint/build still pass.
```

---

## Task 8 — Letterboxd import documentation and user guide bundle

Use this as one larger docs task, not three tiny tasks.

**Branch:**

```text
docs/letterboxd-user-guide
```

**Goal:**

Create full user/operator documentation for the Letterboxd import workflow, including examples and troubleshooting.

**Allowed files:**

```text
docs/LETTERBOXD_IMPORT.md
docs/examples/letterboxd-watchlist-sample.csv
docs/examples/letterboxd-watchlist-ambiguous-sample.csv
docs/TROUBLESHOOTING.md
docs/OPERATIONS.md
```

Do not create a root `README.md` unless it already exists and has a suitable docs section.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
docs/letterboxd-user-guide

Goal:
Create a complete documentation bundle for Letterboxd import and day-to-day operation.

Allowed files:
- docs/LETTERBOXD_IMPORT.md
- docs/examples/letterboxd-watchlist-sample.csv
- docs/examples/letterboxd-watchlist-ambiguous-sample.csv
- docs/TROUBLESHOOTING.md
- docs/OPERATIONS.md

Do not touch:
- backend/**
- frontend/**

Requirements:
1. Create docs/LETTERBOXD_IMPORT.md covering:
   - how to export a watchlist from Letterboxd
   - expected CSV columns: Title, Year, Letterboxd URI
   - Letterboxd exports do not contain TMDb IDs
   - TMDb title/year matching
   - ambiguous match review/confirmation
   - skipped rows and no-match behavior
   - idempotent imports and duplicate handling
   - manually confirmed rows may use source letterboxd_resolved
   - CSV import does not consume Streaming Availability API quota
   - availability/ranking is fetched separately after movies are in the watchlist
   - TMDB_API_KEY may be required for matching
2. Add docs/examples/letterboxd-watchlist-sample.csv with about 5 normal rows:
   - Inception, 2010
   - Parasite, 2019
   - Spirited Away, 2001
   - The Dark Knight, 2008
   - Interstellar, 2014
3. Add docs/examples/letterboxd-watchlist-ambiguous-sample.csv with about 5 plausibly ambiguous rows:
   - Dune
   - Pinocchio
   - The Thing
   - Batman
   - Avatar
4. Create docs/TROUBLESHOOTING.md covering:
   - TMDb key missing
   - RapidAPI key missing
   - Streaming Availability API rate limit
   - no results for a movie
   - ambiguous Letterboxd matches
   - Docker compose stack not running
   - migrations not applied
5. Create docs/OPERATIONS.md covering:
   - booting the dev stack
   - running backend tests
   - running frontend lint/build
   - applying migrations
   - running the refresh availability command
   - what the 100 requests/day limit means operationally
6. Keep docs accurate to current PLAN/SPEC constraints.
7. Do not invent unsupported features.

Acceptance criteria:
- A new user can understand how to import Letterboxd CSVs.
- A developer/operator can run the stack and troubleshoot common failures.
- Sample CSVs have exactly the expected headers.
- No code files changed.
```

---

## Task 9 — Letterboxd service hardening bundle

This is a larger backend-only testing and edge-case hardening task.

**Branch:**

```text
test/letterboxd-service-hardening
```

**Goal:**

Add comprehensive service-level tests for Letterboxd parsing/importing and minimally fix service bugs discovered by those tests.

**Allowed files:**

```text
backend/tracker/tests/test_letterboxd.py
backend/tracker/services/letterboxd.py
```

Optional only if inline strings become too large:

```text
backend/tracker/tests/fixtures/**
```

**Do not touch:**

```text
backend/tracker/views.py
backend/tracker/urls.py
backend/tracker/tests/test_views.py
frontend/**
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
test/letterboxd-service-hardening

Goal:
Add comprehensive backend service-level tests for Letterboxd CSV edge cases and minimally fix backend/tracker/services/letterboxd.py if tests reveal real bugs.

Allowed files:
- backend/tracker/tests/test_letterboxd.py
- backend/tracker/services/letterboxd.py
- backend/tracker/tests/fixtures/** only if inline CSV strings become unwieldy

Do not touch:
- backend/tracker/views.py
- backend/tracker/urls.py
- backend/tracker/tests/test_views.py
- frontend/**

Requirements:
1. Use inline CSV strings and mocked/fake TMDb search responses where practical.
2. Do not call live TMDb, RapidAPI, or Streaming Availability APIs.
3. Add tests for:
   - missing year + one TMDb match resolves successfully
   - missing year + multiple TMDb matches becomes ambiguous with reason multiple_matches
   - parse_csv skips empty/whitespace-only titles
   - duplicate CSV rows create only one WatchlistItem
   - importing the same rows twice remains idempotent
   - ambiguous result includes title, year, uri, reason, candidates
   - no-match result is skipped with reason no_match and includes uri
   - LetterboxdRow preserves uri from CSV
   - Movie and WatchlistItem do not store Letterboxd URI
   - candidate dictionaries do not include URI because they represent TMDb candidates
4. If ambiguous/skipped output currently omits uri, minimally fix backend/tracker/services/letterboxd.py to include it.
5. Do not redesign the importer.
6. Do not modify views, URLs, serializers, frontend, or Task 4 files.

Verification:
Run:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker.tests.test_letterboxd -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2

Acceptance criteria:
- New edge-case tests pass.
- Full tracker test suite passes.
- No live API calls are made.
- Only allowed backend service/test files changed.
```

---

## Task 10 — Subscription management feature

This should be delegated as a whole feature because it spans model/API/frontend/tests. Do not run in parallel with other tasks touching preferences or shared settings UI.

**Branch:**

```text
feat/subscription-management
```

**Goal:**

Add a user-facing way to manage streaming subscriptions instead of relying only on Django admin.

**Likely files:**

```text
backend/tracker/serializers.py
backend/tracker/views.py
backend/tracker/urls.py
backend/tracker/tests/test_views.py
frontend/app/subscriptions/page.tsx
frontend/components/SubscriptionForm.tsx
frontend/components/SubscriptionList.tsx
frontend/components/AppNav.tsx
```

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/subscription-management

Goal:
Add a frontend-safe subscription management feature for the single-user v1 app.

Requirements:
1. Backend:
   - Add DRF endpoint /api/subscriptions/.
   - GET returns subscriptions for user_id=1.
   - POST creates a subscription for user_id=1.
   - DELETE removes a subscription by id or name; choose the simplest RESTful shape and document it in tests.
   - Prevent duplicate subscription names for the same user-facing flow.
   - Keep v1 single-user behavior. Do not add auth.
2. Serializers:
   - Add a SubscriptionSerializer exposing id and name.
   - Do not expose unnecessary internal fields unless existing style requires it.
3. Frontend:
   - Add /subscriptions page.
   - Show current subscriptions.
   - Add form to add a service name.
   - Add remove buttons.
   - Show loading/error/empty states.
   - Add navigation link if AppNav exists.
4. Behavior:
   - Keep service names simple strings matching provider names from availability data.
   - Do not change ranking logic in this task.
   - Do not change PreferenceSerializer to include subscriptions.
5. Tests:
   - GET empty list.
   - POST creates subscription.
   - Duplicate POST does not create duplicate rows and returns a clear response.
   - DELETE removes subscription.
   - Frontend lint/build pass.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- User can manage subscriptions without Django admin.
- Existing ranked availability behavior still works.
- Full backend tests and frontend lint/build pass.
```

---

## Task 11 — Manual language info management feature

This is Phase 5 polish but useful because language info is central to ranking.

**Branch:**

```text
feat/language-info-management
```

**Goal:**

Add frontend and API support for entering/editing audio/subtitle language info for a movie-region availability row.

**Warning:** this is a broad feature. Assign only after subscription/report work has stabilized.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/language-info-management

Goal:
Add user-facing manual language-entry flow so the app does not rely only on Django admin for LanguageInfo.

Requirements:
1. Backend:
   - Add endpoint(s) for reading and updating LanguageInfo for a movie/region pair.
   - Keep v1 single-user/no-auth assumptions.
   - Reuse existing LanguageInfo and LanguageEntry models; do not redesign the data model unless absolutely required.
   - Use ISO 639-1 language codes as existing preferences do.
2. Frontend:
   - On movie availability results, show when audio/subtitle info is missing.
   - Provide a link/button to add or edit language info.
   - Add a small form that accepts comma-separated audio/subtitle language codes.
   - Validate/dedupe/lowercase codes client-side.
   - Save to backend and refresh the relevant UI.
3. UX:
   - Make it clear that language info is manually entered and may be incomplete.
   - Show success/error states.
4. Tests:
   - Backend endpoint tests for create/update/read.
   - Frontend lint/build.

Constraints:
- Do not add external scraping.
- Do not fetch live availability just to edit language info.
- Keep the feature focused on manual entry.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- User can add/edit language info outside Django admin.
- Ranking can use the saved language info on subsequent availability views.
- Existing tests still pass.
```

---

## Task 12 — Bulk refresh queue and quota-aware watchlist processing

This is a serious backend feature and a good Jules-sized task. Assign after Task 4 report endpoint is merged and verified.

**Branch:**

```text
feat/quota-aware-watchlist-refresh
```

**Goal:**

Make watchlist availability processing explicit and quota-aware instead of relying on ad hoc refreshes.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/quota-aware-watchlist-refresh

Goal:
Add quota-aware backend support for refreshing watchlist movie availability in batches while respecting the Streaming Availability API free-tier limit.

Requirements:
1. Backend service:
   - Add a service that selects watchlist movies needing availability refresh.
   - Respect the existing ApiCallCounter/rate limit behavior.
   - Refresh only up to a caller-provided budget.
   - Prefer stale/missing availability first.
   - Return a structured summary: attempted, refreshed, skipped_cached, skipped_rate_limited, errors.
2. Management command:
   - Add or extend a command for refreshing watchlist availability with --budget.
   - Do not exceed budget.
   - Log useful per-run summary.
3. API endpoint, if appropriate:
   - Add a small POST endpoint to trigger a budgeted watchlist refresh manually.
   - If adding an endpoint, keep it single-user and no-auth for v1.
4. Tests:
   - budget is respected
   - cached fresh rows are skipped
   - stale rows are refreshed
   - rate limit stops further refreshes cleanly
   - errors for one movie do not crash the whole batch
5. Documentation:
   - Add operational note explaining how to run the batch refresh and why not to refresh huge watchlists all at once.

Constraints:
- No live external API calls in tests.
- Do not increase daily quota above configured limit.
- Do not add Celery/Redis unless explicitly approved; keep cron/management-command architecture.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2

Acceptance criteria:
- Watchlist refreshes can be processed in controlled batches.
- API quota is respected.
- Failures are observable and non-fatal.
```

---

## Task 13 — Provider/country analytics for watchlist report

This is a side feature that builds on Task 4.

**Branch:**

```text
feat/watchlist-report-analytics
```

**Goal:**

Add analytics on top of the watchlist report: best countries, best services, unavailable count, and coverage breakdowns.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
feat/watchlist-report-analytics

Dependency:
Start after Task 4 and the watchlist report frontend page are merged.

Goal:
Add useful analytics to the watchlist report without changing the core ranking algorithm.

Requirements:
1. Backend:
   - Extend /api/watchlist/report/ response with a summary object if appropriate:
     - total_movies
     - available_movies
     - unavailable_movies
     - countries ranked by number of available movies
     - services ranked by number of available movies
   - Keep existing report rows backward-compatible.
2. Frontend:
   - Display summary cards.
   - Display top countries and services.
   - Make unavailable movies easy to identify.
3. Tests:
   - Backend tests for summary counts.
   - Frontend lint/build.

Constraints:
- Do not change ranking priority.
- Do not fetch live availability beyond existing cache behavior.
- Do not remove existing report fields.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build

Acceptance criteria:
- Report includes useful aggregate coverage data.
- Existing report consumers remain compatible.
- Full verification passes.
```

---

## Task 14 — Production deployment hardening

Good delegated infrastructure/docs/backend-config task after core v1 stabilizes.

**Branch:**

```text
chore/deployment-hardening
```

**Goal:**

Make the Docker/self-hosted deployment safer and better documented.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
chore/deployment-hardening

Goal:
Harden and document the docker-compose deployment path for a small self-hosted VPS.

Requirements:
1. Review existing Dockerfiles and docker-compose files.
2. Add or improve production environment documentation:
   - required env vars
   - ports
   - database persistence
   - migrations
   - static files if relevant
   - cron/refresh command setup
   - backup notes for Postgres
3. Add .env.example or improve existing env example if present.
4. Ensure dev compose commands in docs still work.
5. Do not introduce Kubernetes, Terraform, or heavyweight deployment tooling.
6. If making config changes, keep them minimal and backward-compatible.

Verification:
docker compose -f docker-compose.yml -f docker-compose.dev.yml config
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2

Acceptance criteria:
- Deployment steps are clear enough to follow on a VPS.
- Required secrets/config are documented.
- Existing dev workflow remains intact.
```

---

## Task 15 — Backend API documentation and contract examples

Docs-heavy but large enough if done properly with examples for every endpoint.

**Branch:**

```text
docs/api-contracts
```

**Goal:**

Document backend API contracts so future frontend/delegated tasks stop asking the same shape questions.

**Prompt:**

```text
You are working in WhereTfDoIWatchIt.

Create branch:
docs/api-contracts

Goal:
Create API contract documentation for the Django/DRF backend.

Allowed files:
- docs/API.md
- docs/examples/api/**

Requirements:
1. Document all current API endpoints:
   - GET /api/health/
   - GET /api/search/?q=
   - GET /api/movie/<tmdb_id>/availability/
   - GET/PUT /api/preferences/
   - GET/POST /api/watchlist/
   - POST /api/watchlist/resolve/
   - GET /api/watchlist/report/ if Task 4 has merged
   - any subscription/language endpoints if they exist by then
2. For each endpoint include:
   - purpose
   - method/path
   - query parameters
   - request body
   - success response example
   - error response example
   - notes about caching/rate limits
3. Add JSON example files under docs/examples/api/ for important responses.
4. Keep docs accurate to code. Do not invent endpoints.

Acceptance criteria:
- Frontend agents can implement against docs/API.md without reading backend code.
- Examples are valid JSON.
- Rate-limit/cache behavior is clearly described.
```

---

# Historical 3-parallel-task batches

These batches were used while Tasks 1–15 were in flight. They are no longer an active assignment queue.

## Immediate batch while Task 4 was still running

These avoided Task 4 backend files:

1. `Task 7a — App navigation shell`
2. `Task 8 — Letterboxd user guide bundle`
3. `Task 9 — Letterboxd service hardening bundle`

## Next batch after Task 4 merged

1. `Task 7b — Watchlist report frontend page`
2. `Task 10 — Subscription management feature`
3. `Task 15 — Backend API documentation and contract examples`

## Later batch after report/subscription work stabilized

1. `Task 11 — Manual language info management feature`
2. `Task 12 — Bulk refresh queue and quota-aware watchlist processing`
3. `Task 14 — Production deployment hardening`

## Optional analytics batch

1. `Task 13 — Provider/country analytics for watchlist report`
2. Follow-up UI polish task for report filters/sorting/export
3. Follow-up docs task updating API and user docs

---

# Delegation quality checklist

Before sending a Jules task, confirm:

- The task has a branch name.
- The task has allowed files and forbidden files.
- The task has explicit tests/verification commands.
- The task does not require live external APIs in tests.
- The task can be reviewed independently.
- The task is not blocked by an unmerged task unless marked as a dependency.
- The task is big enough to justify one of the daily task slots.
- The task does not overlap another parallel task's files.

# Main-development rule

Use Claude Code + Omniroute for the core integration path:

1. keep `master` green;
2. review/merge delegated side features;
3. resolve cross-feature integration;
4. make final architectural calls;
5. protect API quota and data-model consistency.

Use Jules for isolated side features with clear ownership, especially:

- frontend routes/components;
- docs bundles;
- backend service hardening;
- endpoint + frontend feature slices when no other task touches those files;
- test coverage expansion;
- deployment/documentation improvements.
