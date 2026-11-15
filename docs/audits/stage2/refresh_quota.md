# Refresh lifecycle, evidence and quota audit

Baseline: `4332732977ac414b051c0e2308e142af9c5803af`. No scrapers or external APIs were activated. Offline actual-function reproductions are recorded in `discrepancies.md`.

## Lifecycle

1. User/system event calls `request_availability_refresh`: local idempotent enqueue, no HTTP (`tracker/services/refresh_requests.py:163-207`). Partial uniqueness deduplicates pending `(movie, source, country)` scopes.
2. Worker drains pending requests, resolves source and applies per-source freshness, policy and quota; `AvailabilityRefreshService` owns source fetch and canonical writes.
3. Client reserves quota immediately before each outbound request; retries must reserve each attempted HTTP call.
4. Adapter produces source facts; provider/title mapping rejects ambiguous/unknown identities; persistence records positive evidence and cursors.
5. Reconciliation evaluates active status, validity window, source confidence, mapping confidence and source authority. Stale and unverified rows are returned as last-known, not current.
6. A source can deactivate unseen offers only when it is explicitly authoritative and the source result is a complete snapshot. Registered aggregators and archive collector are non-authoritative; omission is not a withdrawal.

## Edge-case outcomes

| Case | Expected invariant | Baseline evidence / gap |
|---|---|---|
| Fresh cache | Source/country/media scope independently skips refresh inside its freshness window | Per-source freshness exists (`availability/freshness.py`); source config override is not passed in scheduler (A6). |
| Stale cache | Old observation leaves primary offers and appears as last-known until rechecked | Read path splits verified/current from stale or unverified (`availability_reads.py:90-103`). |
| Credible negative from authoritative complete snapshot | May deactivate only same-source/scope offer when source contract permits | Reconciliation implements authority/completeness check. |
| Aggregator omission | Must not deactivate another source's positive offer | JustWatch and Watchmode explicitly non-authoritative; tests guard omission semantics. |
| Timeout/schema/auth error | Failed outcome; no empty-success or negative evidence | Client-specific. Watchmode HTTP-200 error-shaped envelope is a potential schema/absence conflation, unverified. |
| Partial catalogue refresh | Persist bounded page and cursor; do not treat partial walk as complete snapshot | Catalogue checkpoint service; adapter capabilities differ. Public archive provider seed issue prevents checkpointing on fresh install (A5). |
| Interrupted resumable scan | Resume cursor for same provider/source/country partition | `SyncCursor`; JustWatch and catalogue adapters implement cursors. Missing identity rows can disable cursor persistence. |
| Multiple sources same offer | Keep independent evidence; reconcile/dedupe presentation without erasing source provenance | Canonical Offer key and source facts; aggregator silence is non-authoritative. |
| Duplicate provider identifiers | Market mapping takes precedence; unknown/colliding mapping should fail closed | `ExternalProviderMapping` unique market slots plus JustWatch alias mapping. Static/live accuracy still needs provider-page validation. |
| Quota exhausted | Stop before next HTTP; mark blocked/deferred, not unavailable | Watchmode local reservation double-charge fixed in this audit branch (A2). Streaming Availability local daily/monthly caps; source gate defaults off. |
| Invalid credentials | Error/blocked, never empty result | Watchmode explicitly raises auth error before reservation. Other clients require focused tests; no credentials read. |
| User refresh racing scheduled refresh | Idempotent enqueue + single drain lock, one actual execution | Queue worker uses advisory lock; watchlist command/sweep path bypasses it (A7). |
| Concurrent reservations | Atomic ledger lock prevents two workers crossing local cap | `RateLimiter.acquire` locks counter before monthly ledger. |
| Failure then recovery | Failed request remains diagnostic; later fresh event can enqueue and successful response replaces evidence | Retry max and recent-attempt cooldown; no live recovery run. |

## Quota contract

| Source | Local policy | Accounting point | Failure / retry semantics |
|---|---|---|---|
| Streaming Availability | 100/day, 1,000 per rolling 31 days; lower than/reflects documented API cap | `reserve_streaming_availability_call` at HTTP seam, coordinator reservation suppressed by context | Attempts including transport failures/retries should count. Gate `AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED` is read per call and defaults `0`; therefore caching does not prevent calls unless gate enabled or freshness says fresh. |
| Watchmode | Provider plan 2,500/month; local default 2,200, max configurable 2,500 | Coordinator `source_budget` plus `WatchmodeClient._reserve_call` per outbound attempt | 429 is classified quota; local plan exhaustion terminal, rate window retryable. Fixed in this audit: the first attempt consumes the coordinator's one-shot credit; each retry still reserves one unit. |
| Direct/catalogue collectors | No common invented quota; adapters may implement bounded request budget | Adapter-specific request counter / bounded pages | Budget exhaustion/partial walk is not complete negative evidence. RaiPlay and public-domain archive have bounded HTTP/request limits. |
| JustWatch | No declared local request quota | bounded pages/offset/cursor | Unofficial API can rate-limit; failure must not become absence. |

## Concurrency and negative evidence

- `RefreshRequest` uniqueness prevents duplicate pending rows but does not itself claim an executing row.
- Queue drain advisory lock serializes worker-to-worker drains. Watchlist refresh can invoke the common drain without that lock (A7).
- Cross-scope refresh history is collapsed by movie only. Any successful request makes `CHECKED`, but `_empty_result` now keeps empty offers unknown even if the success was for another source/country or older than a newer failure (A3). Negative verdict is owned by the canonical scoped absence classifier.
- Non-authoritative absence must not erase independent positive evidence. A failed run, unsupported country, quota refusal, incomplete scan, or missing provider mapping is not negative evidence.

## Validation limits

No provider catalogue network calls or quota use. PostgreSQL is listening locally and the repository venv contains Django. The complete backend suite passed: 3,011 tests against disposable `test_tracker`. Frontend Vitest passed 246 tests; lint, TypeScript, production build, migration check, and `git diff --check` passed. No local watchlist or offer rows were queried. These results establish offline behavior only; all 124 sample availability outcomes remain UNVERIFIED.
