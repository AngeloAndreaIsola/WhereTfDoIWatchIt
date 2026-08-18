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

As of this delegation plan:

- Phase 0 and Phase 1 are complete.
- Phase 2 is complete and committed.
- Phase 3 cron/audit work has been merged via delegated Task 1.
- Frontend watchlist UI has been merged via delegated Task 2.
- Frontend preferences UI has been merged via delegated Task 3.
- Shared loading/error/not-found states have been merged via delegated Task 5.
- Watchlist ambiguous resolve endpoint has been merged via delegated Task 6.
- Aggregated watchlist report endpoint is running separately as Task 4.

Before assigning a task, rebase it on latest `master` and remove/adjust any file restrictions that are no longer relevant.

## Files to avoid while Task 4 is open

Until Task 4 is merged, avoid assigning tasks that touch:

```text
backend/tracker/views.py
backend/tracker/urls.py
backend/tracker/tests/test_views.py
```

Task 4 owns the `/api/watchlist/report/` backend endpoint.

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

# Suggested 3-parallel-task batches

## Immediate batch while Task 4 is still running

These avoid Task 4 backend files:

1. `Task 7a — App navigation shell`
2. `Task 8 — Letterboxd user guide bundle`
3. `Task 9 — Letterboxd service hardening bundle`

If Task 7a feels too small, replace it with Task 8 or Task 9 first and save 7a as a follow-up after Task 4 merges.

## Next batch after Task 4 merges

1. `Task 7b — Watchlist report frontend page`
2. `Task 10 — Subscription management feature`
3. `Task 15 — Backend API documentation and contract examples`

## Later batch after report/subscription work stabilizes

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
