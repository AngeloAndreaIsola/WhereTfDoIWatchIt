# Stage 2E — Freshness root cause

Observation date: 2026-11-14. Code SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f`.

## Finding

The strongest supported explanation is an operations lifecycle gap: successful
source observations stopped by 2026-11-01, and no running main application
stack remained to consume queued work. The saved Stage 2D inventory records
that the application's Compose stack was stopped; its offer observations
span 2026-09-30 through 2026-11-01. By the audit date, even the longest
relevant declared windows (24 hours) had expired. All 10,990 canonical offers
were therefore stale and none was `VERIFIED`.

Confidence: **high** that offers became stale because observations aged past
their freshness policy; **medium** that the stopped application stack was the
operational cause of the final missed refreshes. The exact shutdown time and
host cron history were not captured in the preserved evidence.

## Lifecycle trace

1. **Source configuration.** Code registers sources in
   `availability.sources`; database `Source` rows own enabled state and
   per-source configuration. Stage 2D found 14 registered rows persisted and
   enabled, with `public_domain_archive` registered but lacking a row.
2. **Scheduling.** `docs/CRON.md` documents `refresh_availability` as a daily
   host/Compose cron task for stale title scopes. Catalogue refreshes also use
   separately scheduled commands. The Compose `worker` runs
   `refresh_worker --lane title`; `catalogue-worker` runs the catalogue lane.
   These workers loop continuously while their containers run, but the title
   worker drains queued requests only; it does not run the stale-watchlist
   sweep. Compose sets `restart: unless-stopped` for worker services, which
   does not restart a stack that is stopped or never deployed.
3. **Queue creation and execution.** API/detail/watchlist actions enqueue
   `RefreshRequest` rows. The worker consumes pending rows. Stage 2D reports
   did not retain a pending-queue count or a per-source last `RefreshRun`, so
   the backlog at shutdown cannot be established from preserved artifacts.
4. **Source observations and persistence.** Stage 2D measured 7,198 successful
   `SyncRun` rows and 10,990 canonical `Offer` rows, with no failed recorded
   syncs. The latest persisted offer observations were no later than Nov 1.
   Persisted successful history proves prior execution, not a continuing
   schedule. No evidence indicates failed canonical persistence.
5. **Freshness and verdict.** `availability/freshness.py` resolves freshness
   from `Source.configuration.expected_refresh_interval_seconds`, then the
   code default. The comparison audit classifies stale offers as stale and
   requires fresh verified app evidence before agreement. Stage 2D's
   `APP_DATA_STALE` count is zero because all offers were stale and the
   relevant cohort cases instead lacked a local Movie or external evidence.
   No stale offer was treated as currently verified.

## A5 / A6 / A7 status on verified master

- **A5 — Internet Archive provider/source provisioning: not implemented.**
  `public_domain_archive` is code-registered but the Stage 2D database has no
  `Source` row. The current code still depends on the `internet_archive`
  canonical provider being seeded for persistence/checkpoints; no migration
  seeds that provider. The adapter must remain inactive until provisioning is
  addressed. This task does not activate it.
- **A6 — configured freshness in scheduler: not implemented.** The read path
  passes `Source.configuration` to `declared_freshness` in
  `tracker/services/availability_report.py`; scheduler helpers in
  `availability/freshness.py` and `tracker/services/bulk_refresh.py` call
  `freshness_for(slug)` without a configuration snapshot. A short override
  can therefore make the read path stale while the scheduler still skips;
  a long override can cause avoidable requests. Persisted override values for
  non-aggregator sources were not retained in Stage 2D reports.
- **A7 — shared watchlist/queue drain lock: not implemented.**
  `process_refresh_queue` acquires `try_title_drain_lock()` around the drain.
  `process_watchlist_refresh` calls `drain_refresh_requests()` directly and
  does not acquire that lock. Concurrent queue-worker and watchlist-refresh
  execution can overlap. No race was executed in this read-only audit.

No evidence in the checked-out master code or local branch metadata indicated
that A5, A6, or A7 had been merged elsewhere into the verified master. This is
a source-code finding, not a GitHub-wide PR inventory.

## Alternative explanations checked

- **Workers intended to be continuous:** yes, `refresh_worker` is a loop and
  Compose configures restart-on-exit. Their intended operation depends on the
  stack being deployed. The stale sweep itself is scheduled separately by
  external cron.
- **Jobs scheduled but not executed:** no current cron logs or persisted
  scheduler state were available. Stage 2D has no post-Nov-1 offer
  observations; it does not prove whether cron was absent, skipped, or failed
  before creating a run.
- **Disabled or capped source:** all persisted source rows were enabled.
  Streaming Availability is gated by
  `AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED` (default off in Compose)
  and has a documented daily budget; this can limit that source but cannot
  explain why every unmetered source also aged stale. Watchmode has a monthly
  paid-plan quota, but it is only one source. No preserved quota-counter
  snapshot shows exhaustion.
- **Persistence defect:** not indicated. Canonical offers and mappings were
  present, and recorded SyncRuns succeeded. The dominant issue is that no
  sufficiently recent observation remained.

## Recovery gate

Do not run the refresh experiment until the queue/watchlist drain has one
shared lock contract (A7), the selected source configuration and per-source
budget state are read back, and the selected requests can be isolated from
metered sources. The current evidence is insufficient to certify the queue
state or live quota counters.
