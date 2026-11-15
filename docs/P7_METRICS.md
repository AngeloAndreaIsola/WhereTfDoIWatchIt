# P7 — Operational Metrics: definitions and authoritative fields

Observational only. P7 reads persisted state and adds no new writes to the
refresh pipeline. **No external availability API call is made by anything in
this phase**, including metric collection.

## Window

`now − 7×24h` through `now`, computed with timezone-aware UTC datetimes
(`django.utils.timezone.now()`, which is UTC). All filtering happens in the
database on `created_at`/`requested_at` columns; no Python-side date bucketing
over an unbounded result set. Presentation converts to local only in the
frontend.

Two different timestamp columns are used deliberately, and mixing them would
produce wrong numbers:

| Question | Column | Why |
|---|---|---|
| Which refresh runs fall in the window | `AvailabilityRefresh.created_at` | Insertion time. `started_at` is nullable and a queued run has none, so a run queued inside the window but started later would vanish from a `started_at` filter. |
| Which requests fall in the window | `RefreshRequest.requested_at` | When work was enqueued. |
| Which source rows fall in the window | `AvailabilityRefreshSource` → parent refresh `created_at` | The row has no creation column of its own; it inherits its run's window. |

`AvailabilityRefreshSource.updated_at` is deliberately *not* used as a window
filter: it moves on every status write, so a long-running run would be counted
in a window it started before.

## Refresh metrics

`AvailabilityRefresh.status`, using the P6b reducer's own vocabulary —
`completed`, `partial`, `failed`, plus `running`/`queued` for active work.

Refresh failure is **never** inferred from unknown-title counts. It comes only
from `status='failed'`, which P6b writes solely through the genuine lifecycle
failure path.

| Metric | Source |
|---|---|
| `total` | `COUNT(*)` in window |
| `completed` / `partial` / `failed` | grouped `COUNT(*)` on `status` |
| `active` | `status IN ('queued','running')` |
| `completion_rate` etc. | `completed / terminal`, `None` when no terminal run exists |
| `average`/`median`/`max` duration | `finished_at − started_at` over **terminal** runs only |

Duration deliberately excludes non-terminal runs: an unfinished run has no
duration, and averaging in `NULL` as zero would understate every figure.

Rates are `None`, not `0.0`, when there is no denominator. "No runs finished"
must not render as "0% completion".

## Title metrics

Two independent sources, deliberately not conflated:

- **Per-refresh counts** (`AvailabilityRefresh.total_titles`,
  `available_titles`, `unavailable_titles`, `unknown_titles`) summed over the
  window — what the runs themselves recorded.
- **Current resolution state** of the watchlist, from
  `build_run_summary()` — the live `available`/`unavailable`/`unknown` split.

Both are exposed and labelled. The second is a point-in-time reading; the first
is a historical total. Presenting one as the other would be a lie.

`unknown_reasons` (JSONField) is passed through and broken down using the
existing `AvailabilityResolutionReason` vocabulary. No new enum is introduced.

## Request metrics

`RefreshRequest.status` (`pending`/`done`/`failed`/`blocked`) and
`blocked_reason`, grouped in a single query.

Quota-blocked requests are counted from `blocked_reason='quota_exhausted'`,
which is distinct from `policy_not_needed`, `source_disabled`, `cancelled` and
`superseded`. These are reported separately, never merged into one "blocked".

`resolved` is *not* stored per request — a request row records that a source
answered, not what it found. Offer counts come from `Offer` instead.

## Source metrics

One grouped query over `AvailabilityRefreshSource` joined to its window's
refreshes, aggregating the per-source counters that already exist on the row:

```text
total, completed, successful, failed, unknown, queued
```

These are maintained by `sync_run_progress`, so P7 does not recompute them.

The row status vocabulary is kept fully distinct — collapsing these into a
single number would destroy the information:

| `status` | Meaning | Terminal for the run? |
|---|---|---|
| `completed` | Ran and answered | yes |
| `failed` | Ran and could not answer | yes |
| `quota_exhausted` | Out of budget — never asked | yes |
| `skipped` | Policy — deliberately not needed | yes |
| `not_applicable` | Source cannot serve this title | yes |
| `not_checked` | No evidence either way | yes |
| `not_run` | Collector with no run history | yes |
| `queued` / `running` | Still in progress | **no** |

A source that never participated in the window is reported as `null` for its
counters, never as `0`. "Never ran" and "ran and found nothing" are different
facts, and a zero would assert the second.

Positive offers and no-offer results come from `Offer.last_seen` within the
window, grouped by `Offer.source_id` — grouped aggregation, not per-source
queries.

### Source duration

`finished_at − started_at` on the source row, over terminal rows only, same
reason as refresh duration.

## Timestamp semantics (preserved, explicitly tested)

`last_attempted_at` and `checked_at` are **not** interchangeable and P7 does not
conflate them:

- `checked_at` requires an actual answer (`Offer.last_seen`) — a completed
  no-offer check has the attempted stamp and not the checked one.
- A quota-blocked source has neither.
- Attempted-but-unresolved work is therefore represented as *attempted* only.

## Quota metrics

`ApiCallCounter` is a **single row per service per day** holding a count. It is
current-state only and carries **no exhaustion event history**, so:

- current quota state: derivable (`count` vs `daily_limit`).
- requests blocked by quota in window: derivable (`RefreshRequest.blocked_reason`).
- **exhaustion events with timestamps: NOT derivable.** `count` reaching the
  limit leaves no row recording when or whether it happened. Reported as
  `null` with an explicit reason rather than invented.

This is called out in the API response itself (`quota.events_available: false`)
so no consumer mistakes an absence of data for an absence of exhaustion.

## Not measurable from the current schema

Reported as `null`, with a reason string, never fabricated:

| Metric | Why |
|---|---|
| Quota exhaustion event timestamps | `ApiCallCounter` keeps one daily count; no event log |
| Quota remaining at an arbitrary past moment | Same — only today's count is stored |
| Per-request outcome (`resolved` / `no_offer`) | Request rows record that a source answered, not what it found |
| Historical unknown-*reason* snapshots | `unknown_reasons` is overwritten per run, not append-only |
| Collector "positive offers" attributed to a run | `Offer.sync_run_id` links to `SyncRun`, while source rows link to `AvailabilityRefresh`; no shared key |

## Performance

A fixed, small number of queries regardless of source count or run count:

1. refresh status histogram (grouped) + duration aggregate (one query)
2. per-refresh title counters (aggregate sum)
3. request status + blocked_reason histogram (grouped, one query)
4. source-row histogram + counters (grouped, one query)
5. offer counts per source (grouped, one query)
6. registry/limiter read (no query beyond the counter rows)

No query is issued per source and none per refresh.