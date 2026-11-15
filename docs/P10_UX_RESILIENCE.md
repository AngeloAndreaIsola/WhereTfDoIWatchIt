# P10 — UX Resilience: active refresh survives browser reload

The invariant this phase exists to prove:

```text
THE PAGE IS NOT THE REFRESH.
```

A browser may reload, navigate away, or mount a brand-new React tree. When the
page comes back it must inspect the backend and discover the truth.

## Audit finding: the persisted lifecycle was already correct

No production code was changed. Measured before deciding anything:

| Concern | Status |
|---|---|
| `AvailabilityRefresh` persists run identity + status | present, documented for exactly this purpose |
| `is_active` = `queued` \| `running` | present |
| `GET /api/availability/refreshes/` returns `active` | present |
| Detail pages derive `refresh_pending` / `refresh_status` from `RefreshRequest` rows | present |
| `WatchlistRefreshButton` resumes `active` on mount | present, and already tested |
| Browser storage used for lifecycle | none |
| Frontend-owned refresh state machine | none |

`AvailabilityRefresh`'s own docstring states the design intent: *"The refresh
button used to own the state of a long-running operation in component state, so
reloading the browser erased every trace of what was running… This model is that
reconstruction."*

P10 therefore verified an existing contract instead of building one.

## How active state is persisted

```text
POST /api/availability/refresh/        → AvailabilityRefresh(status=queued|running) + RefreshRequest rows
POST /api/movie/<id>/refresh/          → RefreshRequest(status=pending) for that title
worker drains                         → statuses settle; the worker owns the transition
```

Two different scopes, two different persisted shapes:

- **watchlist** → `AvailabilityRefresh` + `AvailabilityRefreshSource` per source,
  read back via `GET /api/availability/refreshes/`;
- **detail** → `RefreshRequest` rows for that title, read back via
  `_prime_detail_response`, which computes `refresh_pending` from a live
  `PENDING` row and `refresh_status` from the newest row.

Neither is derived from anything the browser sent.

## How the frontend reconstructs it

`WatchlistRefreshButton` on mount calls `getAvailabilityRefreshes()`, adopts
`active` if present, and resumes polling that run's id. The detail page derives
`waitingForRefresh` from the backend's own `refresh_pending` and polls while it
is true. Both stop only when the backend reports a terminal status.

## Live browser verification

Playwright against the running stack (`:3001`), real backend, real worker.

### Watchlist — start, reload, terminal

```text
button before      : "Refresh all availability"   (idle)
after click        : run #15 created
backend at that moment:
  status    = queued      is_active = True
  requests  = {blocked: 454, done: 10, pending: 137}
  sources   = mediaset_infinity running, youtube running,
              watchmode running, streaming_availability queued
full page reload (browser_navigate to the same URL)
after reload       : "✓ Refreshed — 27 still unknown"
backend terminal   : status=partial, unknown_titles=27, total_titles=191,
                    unknown_reasons={not_applicable: 23, title_not_found: 4}
```

The reloaded page showed `27 still unknown`, matching the persisted
`unknown_titles=27` exactly. The run was reconstructed from the database, not
from memory — the previous page instance was gone.

### Detail — active state across a reload

A live per-title refresh settles in well under a second, so racing one is not
reproducible. The persisted state a live in-flight refresh produces — a
`RefreshRequest(status=PENDING)` row — was therefore created directly through
the model, then the browser was reloaded:

```text
backend pending(backend) = True, endpoint refresh_pending = True
full page reload
rendered               : button "Refreshing…" [disabled]
settled to done/failed by the worker
reload again
rendered               : button "Refresh availability" (enabled), terminal
```

The fresh page instance rendered the active state with no click, no React state
and no storage.

## Honest limitations

- The **detail** mid-refresh observation required creating the `PENDING` row
  directly, because the real per-title refresh completes in under a second. The
  row is the same one the real queue writes; the worker's own request is not
  slowed or intercepted, and no production code path is changed. The
  **watchlist** observation used a genuinely live refresh, which stays active
  for tens of seconds because it covers 601 requests.
- Terminal *partial* is verified live; terminal *failed* for the detail page was
  observed (the worker settled the probe row as `failed` and the UI returned to
  idle). Both are covered deterministically by the existing suites.
- P10 added no new tests, because the behaviours it was asked to prove are
  already covered by `watchlist-refresh-button.test.tsx` ("resumes an active run
  on mount, with no click"), `availability-refresh.test.tsx` (reload
  reconstruction) and `test_availability_refresh_api.py` (14 backend contract
  tests). Adding parallel tests would have duplicated them.

## Streaming Availability

```text
SA before P10:        11
SA after P10:         15
P10-attributed calls: 0
```

The increase is the background worker draining SA work that P10's two POSTs
*queued* (they create `RefreshRequest` rows; the worker performs the fetches).
Attributed from persisted evidence: 58 SA `SyncRun` rows in the window, all with
`offers_created=0`, created in a single worker batch. P10's own browser actions
were GETs plus two POSTs that only enqueue.

## Verification

See the commit message. No migration, no new TypeScript errors, P9 behaviour
untouched.