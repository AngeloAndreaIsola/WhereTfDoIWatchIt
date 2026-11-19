# Stage 2R — Availability experiment readiness reassessment

Assessment date: 2026-11-14  
Code baseline: fetched `origin/master`, `e1480035d2237c79951f64d1f6e89a4f21dfa61d`  
Database: Docker PostgreSQL `tracker`, volume `wheretfdoiwatchit_pgdata`  
Outcome: **EXPERIMENT_READINESS_ASSESSED**

This is a planning and read-only verification record. No provider request, experiment, worker, collector, migration, authorization token, source activation, or application database write was performed. Historical mappings were not modified.

## 1. Integration and environment

Fetched remote `master`; it resolves to the stated baseline. The checked-out workspace is on `codex/stage2e-freshness-recovery` at a different commit and has pre-existing untracked worktree/audit directories. Assessment of application code below uses an isolated archive of fetched `master`; the existing workspace and its untracked data were left intact.

The Docker container `wheretfdoiwatchit-db-1` is attached to the requested volume. PostgreSQL reports database and role `tracker`, PostgreSQL 16.15, system identifier `7689819453405220898`. The Stage 2G preflight reported application database identity digest `c9139f575fcaf416780341d45d7083971b297970f50f2d2f90f4b26b004afbbd`.

Applied migrations include `availability.0015_availability_experiment_ledger` and `tracker.0047_preference_ranking_preferences`. Each experiment ledger table is empty: experiment, attempt, outcome, and scope counts are all zero.

The current Stage 2G read-only preflight was run against this database using the fetched-master application snapshot. It returned `readiness: blocked`, with these blockers:

- No current quota evidence for JustWatch or Mediaset.
- Mediaset Dune has no independently verified provider GUID.

Other observations from the preflight and read-only database checks:

- JustWatch and Mediaset source rows exist, are enabled, and are registered. JustWatch configuration contains a 43,200-second expected refresh interval; Mediaset configuration is empty. Streaming Availability and Watchmode are also enabled in the database but are out of the proposed scope.
- The two NL movie rows match their requested title and year. Their countries are enabled. The preflight found five Inception NL offers and eight Dune NL offers in total; existing selected-source observations are stale.
- No `RefreshRequest` is pending. Existing rows are terminal: 10,524 blocked, 6,682 done, and 177 failed. The catalogue queue has six done and 25 failed rows, with no pending rows. No `RefreshRun` is unfinished; no active availability refresh was found.
- No granted advisory lock was held at inspection time, including the title-drain and source locks checked by the preflight.
- The project Docker listing contained the database container but no backend or worker container for this Compose project. PostgreSQL cannot attest to processes outside its own view; this assessment did not start one to check deployment.

The integrated `availability.services.identity_policy` is present in fetched `master`. It quarantines Mediaset and YouTube title-search mappings unless they are manual or their external-ID assertion comes from an independent source. Stage 2Q's recommendation to retain the seven historical mappings without writes is consistent with this policy. Code presence is verified; runtime deployment of this policy to any backend/worker is not.

## 2. Scope comparison

### Original manifest

The current fixed manifest has a global ceiling of 11, with two JustWatch scopes at three attempts each and Dune IT/Mediaset at two. It leaves room for three provider-page requests in the ceiling, but no provider-page request implementation or provider-evidence scope exists in the runner. Mediaset remains blocked by the missing verified GUID and reviewed manual mapping. The current read-only preflight also requires quota evidence for Mediaset.

Keeping this manifest preserves the old scope but cannot produce a useful complete run while Mediaset is blocked; execution will stop on that scope. Independent provider evidence is not actually executable through this runner.

### Reduced JustWatch-only manifest

| Scope | Source | Maximum low-level HTTP attempts |
|---|---|---:|
| Inception (2010), NL | JustWatch | 3 |
| Dune (2021), NL | JustWatch | 3 |
| Mediaset / Watchmode / Streaming Availability / provider-page requests | None | 0 |
| **Global maximum** | | **6** |

This is the smaller useful diagnostic for confirming the JustWatch refresh and canonical offer-persistence path. No Dune IT/Mediaset identity is involved.

The runner rejects any manifest other than the exact compiled-in `APPROVED_MANIFEST`. Therefore the minimal implementation is a reviewed fixed-scope revision, not merely a JSON file edit:

1. Replace the compiled-in scope list with exactly the two NL scopes and set the manifest maximum to six.
2. Set the global ceiling to six; set JustWatch's source ceiling to six; set Mediaset, provider evidence, Watchmode, and Streaming Availability ceilings to zero. Keep their operations absent/denied, so a manifest or caller cannot route requests to them.
3. Update operation ceilings to permit only `GetSearchTitles` for the two JustWatch scopes, with three attempts per scope. Update validation, authorization payload checks, reservation checks, and preflight reporting to use the six-attempt ceiling consistently.
4. Update the example manifest to those two scopes only. Generate no token as part of this assessment; any later authorization must be digest- and database-bound to this exact revision.
5. Add focused tests proving the old Mediaset scope, provider-evidence scope, all other sources, extra scopes, changed attempt counts, and attempts above six are rejected; verify two exact NL scopes are accepted and per-scope/source/global accounting cannot exceed 3/6/6. Retain the reservation-before-request and incomplete-reservation reconciliation behavior.

Do not lower the budget by mutating `GLOBAL_LIMIT` alone: the manifest equality check, signed authorization payload, per-source and per-operation ceilings, reservation enforcement, preflight, and tests must agree.

## 3. Freshness versus accuracy

These are separate goals:

1. **Refresh/persistence diagnostic:** a bounded JustWatch response is parsed, normalized, and persisted through the canonical offer path. A six-attempt JustWatch-only run can provide evidence about this path and freshness timestamps.
2. **Agreement with actual provider availability:** JustWatch is an aggregator. Its result cannot independently prove that a provider currently offers the title, in the claimed country and offer class. This requires a separate observation of the exact official provider page(s), recorded independently from the JustWatch result.

Saved evidence contains no exact official-provider title URL for either NL case. Inception has a saved JustWatch NL search URL (`https://www.justwatch.com/nl/search?q=inception`), which is secondary evidence; its provider names were not recoverable from the saved record. Dune NL has no saved NL URL. No official-provider page was contacted in this assessment.

For independent comparison, first identify the specific provider and class in the fresh JustWatch result, then verify the matching official NL title page and class:

- **Inception NL:** historical records mention Netflix, Max, and Prime subscription offers, plus subscription and rent/buy categories in an aggregator search. No provider-to-offer pairing or exact title URL is saved. The primary checks would need the exact Inception page on each provider actually returned by the run, and would separately distinguish subscription from rent and buy where those classes are being evaluated.
- **Dune NL:** historical records mention Max and Prime Video, including Prime base/add-on subscription and rent/buy rows. No exact official title URL is saved. The primary checks would need the exact Dune (2021) page on each provider actually returned, with Prime base subscription, Prime channel/add-on subscription, Max subscription, rental, and purchase treated as distinct classes when present.

Those are candidate provider/class pairs, not confirmed current offers. The matching official URLs and exact current classes must be established in a separately authorized evidence task. A missing, ambiguous, login-walled, or redirected page must remain unknown, not become a negative claim. Dune IT's saved JustWatch search is also not an official page; `/it/film/dune` was previously identified as the 1984 film and must not be used to identify the 2021 title.

## 4. JustWatch request behavior and operational budget

The experiment client calls the fixed HTTPS GraphQL endpoint `https://apis.justwatch.com/graphql`, using POST, the exact search operation and title/country variables, a 30-second timeout, JSON content type/accept headers, and a browser-style Chrome 122 User-Agent. It sends no API key or account credential. The guarded transport disables environment proxy inheritance, mounts an HTTP adapter with zero implicit retries, and calls `requests` with redirects disabled. The durable attempt reservation is committed before each low-level HTTP request. A timeout/reset leaves a spent ambiguous reservation and aborts the source for reconciliation.

The existing JustWatch client allows three tries by default; the experiment sets retries to the scope ceiling (three). It retries timeouts, connection errors, and HTTP 408, 425, 429, 500, 502, 503, and 504. Backoff is exponential from 0.8 seconds plus up to 0.8 seconds of jitter. Other 4xx responses and GraphQL errors are not retried. The six-attempt source and global reservations cap the two title calls at six low-level requests total.

The client does **not** inspect or honor `Retry-After`; it retries 429 using the same generic backoff. There is no proactive JustWatch rate limiter or published quota value in this code. The endpoint is unofficial, and “no billed API quota” is not evidence of unlimited service capacity or a permitted request rate. Current service-side limits, acceptable cadence, and the current operator's rate-limit state remain unknown. Three attempts per title are bounded but may repeat a throttled request.

Before execution, the reduced-scope implementation should fail fast on HTTP 429 (one spent reservation, then stop that title) or implement and test bounded `Retry-After` handling. It must not reinterpret throttling as an empty result. Keep exact destinations, redirects disabled, global/per-source/per-operation accounting, reservation-before-network, and crash-safe ambiguous-attempt handling intact. Do not add provider requests to the JustWatch runner.

Required current quota evidence is absent. The existing preflight accepts only operator-supplied evidence checked within one hour and marked `unmetered_public` or `available`; an `available` report must show at least the configured source ceiling remaining. For the reduced manifest that means genuine JustWatch-only evidence adequate for six attempts. Obtain it from an actual current operator/service-policy check; do not infer it from the absence of a billed key and do not fabricate an attestation. The present preflight reports an empty quota-evidence object.

## 5. Separate Stage 2Q provenance follow-ups

These are independent follow-up issues and do not affect a JustWatch-only run. Do not broaden the Stage 2R manifest task into adapter refactoring.

### Watchmode conflicting TMDb identity

`WatchmodeSource._select_title` accepts a result when either its TMDb ID matches or its IMDb ID matches. If the result reports a conflicting non-null TMDb ID but the IMDb ID matches the request, the IMDb branch can still accept it. Required follow-up tests:

- Requested TMDb and IMDb IDs both supplied; one result reports the matching IMDb ID and a different TMDb ID. It must be rejected, and the detail/source endpoint must not be fetched.
- Both IDs match: accept the unique result.
- TMDb ID absent and IMDb ID matches: accept only if the defined policy explicitly allows IMDb-only corroboration; test that policy separately.
- Multiple matching or contradictory candidates: reject as ambiguous.

Acceptance: a conflicting explicit TMDb assertion cannot be overridden by an IMDb match; no offers or canonical identity are emitted for a rejected result; existing exact-match and duplicate-result behavior remains covered.

### Streaming Availability request identity copied as external evidence

`StreamingAvailabilitySource` calls a TMDb-keyed endpoint, then copies the request TMDb ID into `DiscoveredContent.external_ids` and labels the record with `streaming_availability`. That value is request context, not an independent title-ID assertion. Required follow-up tests:

- The API response omits a TMDb ID; adapter output must not promote the request ID into independent external-ID metadata.
- The API reports a different explicit TMDb ID; it must be rejected or represented as a conflict and never silently replaced by the request ID.
- Persistence/reconciliation of request-context identity must not satisfy `EXTERNAL_ID_ASSERTION_SOURCES` or make a Mediaset/YouTube mapping trusted merely because the request used Streaming Availability.

Acceptance: request identity remains distinguishable from independently reported identity end to end; contradictory IDs cannot create or validate a canonical mapping; existing legitimate independent assertions continue to work.

## 6. Recommendation and gates

**Recommend A: prepare the reduced six-attempt JustWatch diagnostic.** Do not retain the original manifest as the next experiment: its Mediaset case is unresolved and the runner's reserved provider-page capacity is not implemented. The reduced diagnostic has a bounded, useful objective and removes the unrelated identity blocker. It does not establish agreement with official provider availability.

The reduced experiment is **not ready to execute**. The smallest next implementation task is the reviewed fixed-manifest revision described above, including fail-fast 429 behavior and focused scope/budget/retry tests. After that revision, all of these non-authorization gates must pass:

- Run the Stage 2G read-only preflight against the same named Docker volume and exact revised manifest. Confirm the database identity, applied migration 0015/0047, empty ledger (or explicitly reconcile any existing entries), correct title/year/country/source configuration, no pending scope work, no unfinished runs, no relevant advisory locks, and no ambiguous reservations.
- Obtain genuine, current JustWatch quota/rate-policy evidence covering the six-request ceiling. Existing preflight evidence is missing; no attestation is supplied by this report.
- Confirm operational ownership/maintenance windows for ordinary workers and re-check queue/lock state immediately before execution. PostgreSQL alone cannot prove that no external process is running.
- If the intended claim is accuracy against real providers, complete a separate primary-evidence task first. It must establish exact official URLs, provider identity, country, offer class, retrieval policy, and separately stored observations. Without it, report only refresh/persistence behavior.

Only after those gates should a separate explicit authorization authorize the exact reduced manifest, database identity, six-request maximum, expiry, and allowed purpose. Authorization must not be inferred from this readiness report. Do not mint a token until that later approval step.

### Request and persistence acceptance criteria for a future diagnostic

- No more than three reserved low-level requests for either title, six for JustWatch total, and six globally; no request to Mediaset, Watchmode, Streaming Availability, or any provider page.
- Every reservation precedes its network request and counts even if the process crashes or receives no response. A 429 spends one attempt and stops that title under the proposed fail-fast guard.
- Only exact Inception (2010)/NL and Dune (2021)/NL JustWatch search responses are accepted. Ambiguous or conflicting title identity is an error, not an empty availability result.
- Successful returned offers are persisted through the canonical offer path with source, provider, country, offer type, URL, and freshness timestamps scoped correctly. Omitted offers are not treated as provider-confirmed unavailability; no unrelated offer is deactivated by this targeted observation.
- The final record reports attempts/responses, identity result, offer inserts/updates, freshness changes, and any blocked/ambiguous outcome without converting aggregator observations into primary-provider truth.

**EXPERIMENT_READINESS_ASSESSED** — integration and database preflight have been assessed; required quota evidence and revised fixed-manifest support are not yet satisfied. This is not `EXPERIMENT_READY` and does not authorize execution.
