# RaiPlay HTTP Budget Design

**Status:** design only — implementation requires separate approval.

**Recommendation:** GO for a small, RaiPlay-only implementation after the acceptance criteria below are agreed. Do not run the current 400-entry default as a validation of a request limit: it has no enforceable HTTP ceiling.

## 1. Existing architecture and root cause

```text
refresh_provider_catalogue command
  -> tracker.services.bulk_refresh.process_catalogue_refresh()
  -> _refresh_one_source() [per-source PostgreSQL advisory lock]
  -> _default_catalogue_runner()
  -> AvailabilityRefreshService.refresh_catalogue()
  -> RaiPlaySource.fetch(cursor=stored SyncCursor.cursor)
  -> RaiPlaySource._entries()
       -> RaiPlayClient.sitemap_index()
       -> RaiPlayClient.sitemap_entries() for every programme sitemap
  -> RaiPlayClient.programme_page() for each selected programme
  -> RaiPlayClient.video_page() once or twice per programme
  -> AvailabilityRefreshService._persist()
       -> ProviderContent / mapping / Offer / SyncRun transactions
       -> CursorRepository.set() only after successful persistence
  -> RefreshRun result recorded by _refresh_one_source()
```

Concrete references:

- `backend/availability/sources/rai_play.py:239-304` is the client. Its live path calls `requests.get()` without an explicit `allow_redirects`; its test path calls an injected `requester`.
- `rai_play.py:339-453` decodes the v1 checkpoint, reads all sitemaps, slices by `max_contents`, and returns one in-memory `SourceFetchResult`.
- `rai_play.py:457-467` rereads the index and every matching programme sitemap on every window. `rai_play.py:517-574` makes one video request, plus a player request when the video URL returns a bare JSON record.
- `backend/availability/services/refresh.py:232-439` reads the durable cursor, invokes the source, persists the returned observations, then writes or clears the cursor. A fetch exception at `:323-330` occurs before persistence.
- `refresh.py:710-923` is the persistence path. It commits discovered content/mappings, then offers/SyncRuns under the writer barrier. RaiPlay cannot deactivate because its result and capability are non-authoritative.
- `backend/availability/persistence/repositories.py:1120-1175` advances a cursor only when `set()` is called. `set()` is deliberately post-persistence; `delete()` is reached only after a returned result has no cursor.
- `backend/tracker/services/bulk_refresh.py:991-1098` owns the source advisory lock and `RefreshRun`; any exception currently becomes `status="failed"` and `RefreshRun.error`. `:1100-1178` aggregates source outcomes.
- `process_pending_catalogue_refresh()` (`bulk_refresh.py:1181-1246`) invokes the same path from the worker marker. `refresh_worker` calls it after the title lane (`backend/tracker/management/commands/refresh_worker.py:197-208`).
- `refresh_provider_catalogue` currently exposes only `--source` (`backend/tracker/management/commands/refresh_provider_catalogue.py:41-49`).

The source's only runtime guard is `max_contents` (`rai_play.py:374`). It caps programme entries, not network attempts. The observed 25-entry run was 1 index + 37 programme sitemaps + 25 programme pages + 25 JSON videos + 25 player pages = **113 HTTP requests**. `requests.get()` follows redirects implicitly, so this is not an enforced ceiling.

The existing title-source quota machinery is not a fit. Catalogue execution explicitly bypasses title API budgets (`bulk_refresh.py:1105-1112`), while `SourceQuotaExceeded` represents provider-plan/local title quotas, not a per-invocation crawl allowance. Reusing it would conflate an operator stop with an upstream quota and would not meter this client's sitemap/detail traffic.

## 2. Alternatives and trade-offs

| Option | Assessment |
| --- | --- |
| A. Counter directly in `RaiPlayClient` | Close to every live request and easily unit tested. Alone it needs a configuration object and explicit redirect handling; otherwise constructor state is awkward to report/share. |
| B. Shared catalogue HTTP transport budget | Accurate only if every source is migrated. No common transport exists; this is a general framework with broad bypass/compatibility risk. Reject as oversized. |
| C. Per-invocation budget object passed to the client | **Recommended.** Client owns RaiPlay's only live transport path; source/coordinator own operation scope/status. No cross-source changes or duplicate accounting. |
| D. Existing quota/circuit infrastructure | Reject. It is persistent title API quota machinery, not an exact per-run attempt guard, and does not wrap this transport. |

Create a small, non-Django `RaiPlayHttpBudget(limit)` object in `rai_play.py` (or a nearby module only if necessary). Create it once per `RaiPlaySource.fetch()` and pass it to the existing client. It owns `attempted`, `limit`, and a check-and-reserve operation that raises `RaiPlayHttpBudgetExceeded(SourceFetchError)` before dispatch.

Only the client reserves attempts. The source/coordinator only create and report the budget. The existing per-source PostgreSQL advisory lock serializes production RaiPlay traversal; the object is local. Give it a `threading.Lock` now so a future parallelized traversal still has atomic check-and-increment. It is intentionally neither persisted nor cross-process shared.

## 3. Exact request semantics

The budget is a strict count of **client dispatch attempts**, not successful responses and not a claim about whether a locally failed socket reached the network. Reserve immediately before each HTTP-library call. It counts:

- sitemap index and every programme sitemap;
- programme pages, JSON video URLs, fallback player pages;
- each redirect hop, including a final hop that does not lead to 200;
- each retry attempt if retries are added later (there is no current RaiPlay retry loop);
- attempts that time out, fail DNS/TLS/connect, return 4xx/5xx, or return 404/410.

A 404/410 remains a valid stale-entry observation but is not free. Parsing, database work, sleeps, and fixture requester calls do not count. The requester seam stays fixture-only and receives the same logical URL sequence without live-network accounting.

Replace implicit redirects with a one-hop `GET(..., allow_redirects=False)` loop:

1. Reserve a token.
2. Perform exactly one request.
3. For a permitted 3xx with valid `Location`, resolve with `urljoin`, enforce an explicit new `https`/allowed-RaiPlay-host policy, then loop.
4. Fail loudly on missing/invalid/disallowed location, redirect loop, or a small explicit cap (recommend five).

The cap is not a budget escape: the response which crosses the cap has already consumed its token. No request begins once `attempted == limit`.

## 4. Exhaustion, status, and checkpoints

### Recommendation: fail the bounded window and retain the old cursor

Do **not** persist a partial prefix in this change. `RaiPlaySource.fetch()` accumulates the whole batch in memory; persistence, including its post-commit checkpoint advance, happens only after `fetch()` returns. Prefix persistence requires a new transaction/checkpoint protocol and expands the failure surface.

On exhaustion, `RaiPlayHttpBudgetExceeded` escapes `fetch()`. `AvailabilityRefreshService.refresh_catalogue()` re-raises at `refresh.py:323-330`, so it never calls `_persist`, `CursorRepository.set`, or the cursor-clear branch. The runner must catch this exception ahead of generic `Exception`, record `status="budget_exhausted"`, and write `RefreshRun.error` with source, limit, attempted count, blocked URL, and stage. It must not record success. The command returns non-zero, with the explicit JSON source outcome.

Consequences to test:

- no additional upstream attempt starts after exhaustion;
- the old `SyncCursor` remains byte-for-byte unchanged;
- no in-memory partial observations persist in that invocation;
- prior committed observations remain intact;
- later authorized replay of the prior window is duplicate-safe through existing upserts;
- RaiPlay remains non-authoritative, so absence cannot deactivate offers; legacy-source rows are outside RaiPlay upserts;
- exhaustion never returns `cursor=None`, so it cannot clear a checkpoint or masquerade as traversal completion.

This can replay useful in-memory work, deliberately. It is safer than splitting the atomicity boundary. A later prefix-commit phase is acceptable only if the observation transaction and its matching anchor advance commit atomically.

## 5. Operator controls

Proposed manual interface:

```text
python manage.py refresh_provider_catalogue --source rai_play \
  --max-contents 25 --max-http-requests 120 [--wall-clock-seconds 120]
```

- `--max-contents`: selected programme entries; reject values below 1. Keep the source constructor parameter for direct callers/tests.
- `--max-http-requests`: required for an explicit RaiPlay live-validation run; reject values below 1. Reject it for non-RaiPlay sources rather than silently ignore it.
- `--wall-clock-seconds`: optional, useful operationally but not required for HTTP safety. If added, check immediately before dispatch, raise a distinct timeout exception, retain cursor, and report `timed_out`. It is not a substitute for an HTTP ceiling.

Compatibility: retain the constructor's `max_http_requests=None` solely for existing direct callers and fixture tests; it must not be an operational bypass. Add explicit worker configuration such as `RAIPLAY_MAX_CONTENTS` and `RAIPLAY_MAX_HTTP_REQUESTS`, with no production default. The registry/worker path must refuse RaiPlay catalogue work with a visible configuration error until both are finite; the command similarly requires its two flags. This is an intentional deployment migration gate, not a silent change to generic collector defaults, and prevents accidental unattended unbounded execution.

Pass the override narrowly through the RaiPlay runner/injected service. Do not add generic new `fetch(**kwargs)` plumbing to every collector.

## 6. Exact proposed file changes

1. `backend/availability/sources/rai_play.py`
   - Add `RaiPlayHttpBudget`, `RaiPlayHttpBudgetExceeded`, and optional wall-clock exception.
   - Add operation-scoped budget input to the live client path, preserving fixture requester behavior.
   - Replace implicit redirects with explicit one-hop handling.
   - Add optional `max_http_requests`/deadline to `RaiPlaySource`; construct one budget per `fetch`, not per request.
2. `backend/availability/sources/__init__.py` only if coordinator imports the exception; otherwise keep it RaiPlay-private.
3. `backend/tracker/services/bulk_refresh.py`
   - Add `budget_exhausted` (and optional `timed_out`) to `CatalogueSourceSummary` and attempted/error aggregation.
   - Catch the budget exception ahead of generic failure; persist a precise `RefreshRun.error`; expose `requests_attempted`, `requests_limit`, and `blocked_url` in the result.
   - Add only RaiPlay-specific runner override plumbing.
4. `backend/tracker/management/commands/refresh_provider_catalogue.py`
   - Add/validate flags; print explicit JSON outcome and return non-zero on exhaustion.
5. `backend/availability/tests/test_rai_play.py`, `test_catalogue_checkpoints.py`, `test_rai_play_refresh.py`, and `backend/tracker/tests/test_refresh_provider_catalogue.py`
   - Add all fixture-only cases below.
6. `docs/RAIPLAY_BOUNDED_REFRESH_PLAN.md` and operational documentation
   - Replace RaiPlay “no API budget” wording with this local HTTP ceiling. Other sources remain unchanged.

No migration, persistent request ledger, `ApiCallCounter` modification, or cross-source transport refactor is needed.

## 7. Test matrix

All tests must use fake requesters/responses or a mocked one-hop session method; none may call RaiPlay.

| Scenario | Assertions |
| --- | --- |
| Exactly at limit | Last permitted request is made; successful result is allowed only if traversal completes; `attempted == limit`. |
| Next attempt exceeds | Next URL is not sent; exception has limit/attempted/URL/stage; attempted never exceeds limit. |
| Redirect chain | Every 3xx consumes one token; location handling is deterministic; cap/loop/missing location fails without a free follow-up. |
| Sitemap-stage exhaustion | Later sitemap not requested; no ProviderContent/Offer/SyncRun/cursor mutation; runner reports `budget_exhausted`. |
| Programme-stage exhaustion | No next programme request; old cursor exactly unchanged; no batch rows persist. |
| Video-stage exhaustion | Prefix remains unpersisted; player URL not requested after budget stop. |
| Persistence failure after fetch | Existing rollback/cursor tests remain distinct from budget exhaustion. |
| Successful retry | Larger authorized budget replays/resumes safely and advances checkpoint only after commit. |
| Duplicate-safe replay | No duplicate rows; IDs/first_seen stable; last_seen may advance. |
| Existing resume | v1 anchor resumes after its URL; sitemap rereads occur, completed programme pages do not. |
| Legacy/no-deactivation | Seed legacy + RaiPlay offers, exhaust at each stage, assert legacy unchanged, `deactivated == 0`, availability true. |
| Cross-source regression | CBC/PBS/Pluto/etc. retain outcomes/signatures when RaiPlay flags are absent. |
| Command/worker status | Command and marker-worker expose `budget_exhausted`, non-zero/incomplete, never succeeded. |
| Invalid controls | Bad limits or RaiPlay options on a different source fail before network or `RefreshRun` creation. |

## 8. Request envelope and first validation

At the observed current shape there are 38 discovery requests and three per selected programme: **expected = `38 + 3N`**.

The pinned dependency is `requests==2.32.*`. Current implicit redirect behavior must not be used as a safety bound. Conservatively, assuming its 30-redirect guard, a logical GET can cause up to 31 dispatches before error: current redirect-path envelope = `31 * (38 + 3N)`. This describes risk, not a recommended setting; explicit implementation uses a five-hop failure cap.

| Entries | Expected | Current implicit-redirect envelope | Recommended strict ceiling after implementation |
| ---: | ---: | ---: | ---: |
| 25 | 113 | 3,503 | 120 |
| 100 | 338 | 10,478 | 350 |
| 200 | 638 | 19,778 | 660 |
| 400 | 1,238 | 38,378 | 1,280 |

The rightmost number is a configured ceiling, not a prediction: at most that many client dispatch attempts begin, including redirects/future retries. If sitemap count grows, the job stops rather than exceeding the ceiling.

**First live validation:** one supervised RaiPlay-only command with 25 entries, 120 HTTP attempts, and optionally 120 seconds wall clock. The seven-attempt margin over measured 113 is enough to verify the guard while keeping a catalogue-shape surprise small. Review attempt count and cursor outcome before considering 100/350. Do not jump to 400/1,280 merely because 400 is today's source default.

## 9. Rollback, acceptance criteria, GO/NO-GO

Rollback is code/configuration-only: remove the override and deploy the prior image. There is no schema or durable budget state to unwind. Do not edit/delete `SyncCursor` rows; retained v1 anchors remain valid. Reverting an interrupted budgeted run causes safe duplicate replay.

Acceptance criteria:

1. A hard local limit blocks every dispatch after the configured count, including redirects and future retries.
2. Exhaustion is named/machine-readable and non-zero, never successful completion.
3. Old cursor is unchanged at every exhaustion stage.
4. No partial-batch persistence, legacy mutation, or offer deactivation occurs.
5. A later larger-budget retry is idempotent and checkpoint-safe.
6. RaiPlay resume and all non-RaiPlay catalogue tests pass with mocks/fixtures only.
7. Direct test/backward-compatible constructors remain unchanged, while command/worker RaiPlay execution is gated until an approved finite configuration is deployed.

**GO:** local client-enforced, coordinator-reported budget with fail-closed replay. **NO-GO:** implicit redirects, successful treatment of a budget stop, or any cursor advance/clear without atomically persisted matching observations.

