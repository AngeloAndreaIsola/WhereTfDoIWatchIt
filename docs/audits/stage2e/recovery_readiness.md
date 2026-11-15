# Stage 2E — Recovery readiness

## Status: `BLOCKED_BY_SCHEDULER_OR_LOCKING`

The data is recoverable in principle, but a safe controlled execution is not
ready on the current master deployment path.

## Evidence

- The canonical corpus is intact: 10,990 Offers and 4,742 ContentMappings;
  all recorded SyncRuns succeeded and source rows are enabled.
- Every Offer is stale because its last observation is between Sep 30 and Nov
  1 while declared windows are 12–24 hours. This is consistent with the
  stopped main application stack and no recent source execution; no
  persistence failure was observed.
- Compose has continuous title and catalogue workers, while the scheduled
  stale-watchlist refresh is an external cron command. The main stack is
  stopped. No available host cron history or per-source queue/RefreshRun
  snapshot proves that scheduled jobs still execute.
- A7 remains: queue workers use a PostgreSQL advisory drain lock, but
  `process_watchlist_refresh` invokes the same drain without it. Concurrent
  execution could duplicate requests and persistence work.
- A6 remains: scheduler freshness checks omit `Source.configuration`, even
  though the read path honors configured overrides.
- Live queue length, source configuration overrides (other than the seeded
  aggregator defaults), quota counters, and per-source last-attempt timestamps
  are not present in the preserved aggregate evidence. The real database was
  not opened or modified in this task.

## Conditions before execution

1. Engineering change or operational serialization that closes the A7 lock
   race; a one-off run must not race an active worker or watchlist refresh.
2. Read-only preflight of the selected source rows/configuration, pending
   requests, latest run timestamps, and quota counters against the actual
   application database.
3. An explicit source allowlist for the five planned source HTTP calls and
   strict request counter; the existing title worker does not expose a
   per-source command selector.
4. Explicit authorization for the bounded external source/provider requests
   described in `refresh_experiment_plan.md`.

No database writes, worker startup, activation, queue submission, provider
calls, API calls, migrations, or Docker volume operations occurred during this
task. The Stage 2C audit remains offline/read-only; its rerun command is
prepared in `refresh_experiment_plan.md` workflow notes and should only run
after the controlled evidence has been captured.
