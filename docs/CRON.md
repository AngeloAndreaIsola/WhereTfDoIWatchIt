# Scheduled Refresh (Cron Setup)

This document describes how to configure cron for automatic background refreshes: the daily title-targeted availability refresh and the provider catalogue refresh.
For full server setup and deployment instructions, see [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Recommended Crontab Entry

Run the availability refresh command daily at 03:17 UTC with a budget cap of 90 stalest items. The external free tier allows 100 requests/day; keeping the scheduled job at 90 preserves a small reserve for ad-hoc lookups.

```cron
17 3 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_availability --budget 90 >> ./logs/refresh.log 2>&1
```

Or if running directly on the host / server environment:

```cron
17 3 * * * cd /path/to/streaming-tracker/backend && python manage.py refresh_availability --budget 90 >> ../logs/refresh.log 2>&1
```

## Options and Configuration

- **Schedule (`17 3 * * *`)**: Daily at 03:17 UTC to avoid peak API traffic.
- **Budget Cap (`--budget 90`)**: Caps the scheduled refresh to 90 stale availability requests per run, leaving roughly 10 calls/day available for manual or on-demand lookups under the 100 request/day free-tier cap.
- **Log Path (`./logs/refresh.log`)**: Output is appended to `./logs/refresh.log` within the project tree (matching `.gitignore` log rules).
- **Audit Logging**: Each invocation automatically creates a `RefreshRun` audit record in PostgreSQL, accessible via the Django Admin interface under `RefreshRun`.
- **Event-driven request drain**: before the background stale sweep, each run settles pending `RefreshRequest` rows first (manual actions, never-fetched titles, refresh-on-view, watchlist adds/imports, mark-watched), in user-intent priority order, through this same budget, the same rate limiter and the same `RefreshRun`. With a budget of 2 or more, one held-back unit is spent last on the longest-waiting request (bounded fairness), so an endless stream of detail views or watchlist adds cannot starve the low-priority backlog; the daily quota stays authoritative either way. A request left pending because the budget or quota ran out simply waits for the next run — no separate queue process exists. The JSON summary in the log carries `requests_drained` (requests settled this run) and `requests_pending` (backlog remaining).

## Provider Catalogue Refresh

Provider catalogues (CBC Gem, BBC iPlayer, Tubi, Prime Video, and RaiPlay) are refreshed by a separate command: a catalogue source reports its whole configured scope through public catalogue endpoints, so **no API budget applies** and there is no `--budget` flag.

Recommended: every 6 hours at minute 17 (offset from the 03:17 availability job):

```cron
17 */6 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue >> ./logs/catalogue.log 2>&1
```

Or directly on the host / server environment:

```cron
17 */6 * * * cd /path/to/streaming-tracker/backend && python manage.py refresh_provider_catalogue >> ../logs/catalogue.log 2>&1
```

### Per-source cadence

The bare command refreshes *every* catalogue-capable source, which is cheap for
CBC Gem (a handful of index requests) but expensive for BBC iPlayer — a full run
walks all 27 A-Z pages and then one detail page per brand, thousands of
requests — for Tubi, which reads one detail page per title, for Prime Video,
which reads one 140 kB..1 MB SSR detail page per title after its index walk,
and for RaiPlay, which reads a programme page plus its video pages per title.
Give each heavy source its own, slower slot with `--source`:

```cron
17 */6 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source cbc_gem >> ./logs/catalogue.log 2>&1
17 4 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source bbc_iplayer >> ./logs/catalogue.log 2>&1
17 1 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source tubi >> ./logs/catalogue.log 2>&1
17 7 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source prime_video >> ./logs/catalogue.log 2>&1
17 10 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source rai_play >> ./logs/catalogue.log 2>&1
```

`--source <slug>` targets one source and bypasses the `enabled` filter (manual
override, logged as such); everything else — locking, country initialization,
`RefreshRun` audit rows, and the exit codes above — behaves exactly as in a
multi-source run. Omit `--source` if you would rather refresh everything
together at one frequency.

#### Tubi scheduling notes

- **Bounded walk**: a run requests at most `DEFAULT_MAX_CONTENTS` (500) title
  pages, selected round-robin across the movie and series sitemaps so even a
  capped run mixes both types. Hitting the bound logs
  `tubi_catalogue_truncated`; to walk the whole ~100k-URL catalogue (hours of
  requests at 0.1 s apart) raise that constant in
  `backend/availability/sources/tubi.py` — it is a code-level default, not a
  CLI flag.
- **US egress required**: Tubi geo-blocks non-US callers (a GDPR redirect
  answers instead of the catalogue). From an EU egress the run fails cleanly
  with `SourceFetchError` → exit `1`/`2` and no writes, never an empty
  "successful" catalogue.

#### Prime Video scheduling notes

- **Bounded walk**: a run seeds the index queue from `/categories` plus
  `/store`, `/movie`, `/tv` and the genre paths the category page publishes,
  round-robines index pages while it follows each page's `body.pagination.url`,
  and requests at most `DEFAULT_MAX_CONTENTS` (500) detail pages before
  stopping with `prime_video_catalogue_truncated discovered=<n>
  max_contents=<n> pending_index_pages=<n>`. The bound is a code-level default
  in `backend/availability/sources/prime_video.py`, not a CLI flag. Requests
  are spaced `DEFAULT_DELAY` (0.2 s) apart; at 500 pages that is minutes of
  transfer on top of the delay (each detail page ships 140 kB..1 MB), so a
  daily slot is plenty.
- **NL egress required**: the source declares the single territory it was
  researched against (`COUNTRY == "NL"`) and reads
  `RequestContext.currentTerritory` from *every* page it fetches (including the
  `/categories` seed). A non-NL egress makes Prime answer with another
  territory, and the source raises `SourceFetchError` rather than publishing
  another country's catalogue → exit `1`/`2`, nothing written. See the region
  requirement note in `docs/DEPLOYMENT.md`.
- **Never authoritative**: index pages are editorially ordered and Prime
  publishes no per-item removal signal, so the result is always
  `authoritative=False` / `complete_snapshot=False` (`sync_mode=FULL`,
  `cursor=None`) — a capped or failed run never deactivates existing content or
  offers.
- **Disabled seed pages are tolerated, a broken discovery is not**: if the
  category page cannot be parsed the run logs
  `prime_video_categories_unavailable` and continues with the remaining seeds;
  a missing hydration marker, an unreadable payload, or a transport failure
  raises `SourceFetchError` and fails the source (exit `1`/`2`).
- **Absence is logged, not fatal**: a stale title that answers with the SPA
  shell (`tubi_content_missing`), an offer window that is closed or has not
  opened (`tubi_offer_expired`, `tubi_offer_not_yet_available`), and a series
  with no episode on its page (`tubi_content_only`) skip that row only. A walk
  that observes nothing at all logs `tubi_catalogue_nothing_observed` as a
  warning; only transport/parse failures (missing `window.__data`, malformed
  sitemap, undecodable state) fail the source.
- **Never authoritative**: every Tubi run reports
  `authoritative=False`/`complete_snapshot=False`, so a truncated or partially
  observed run deactivates no offer and removes no catalogue row.

#### RaiPlay scheduling notes

- **Measured cost**: one bounded run reads one programme page and one `.json`
  video URL per title — plus the `.html` player page whenever the `.json`
  endpoint answers with the bare JSON record (the shape served since
  2026-09-30). Measured live on 2026-09-30: **1.21 s per content**
  (100 contents in 121 s, ≈300 requests including the 0.1 s spacing). With the
  default bound (`DEFAULT_MAX_CONTENTS` = 400 in
  `backend/availability/sources/rai_play.py`) a run is ≈ **8 minutes and ≈1.2k
  requests**; walking the whole catalogue (~2.2k `/programmi/` URLs, growing
  monthly) would be ≈45 minutes and ≈6.7k requests, which is why the bound
  exists. Hitting it logs `rai_play_catalogue_truncated discovered=<n>
  scanned=<n> max_contents=<n>`; the bound is a code-level default, not a CLI
  flag.
- **Cadence**: daily is plenty. RaiPlay republishes its sitemaps monthly and
  offer windows change rarely, and the walk is round-robined across months so
  each run sees a mixed slice of the catalogue. If you keep the bare
  `17 */6 * * *` entry above, RaiPlay also runs there — 4×/day, ≈4.8k
  requests/day, harmless on a CDN-backed public site but no fresher data; the
  daily `--source rai_play` slot is the recommendation when you are already
  splitting heavy sources onto their own slots.
- **No egress requirement**: unlike Tubi (US) and Prime Video (NL), RaiPlay
  serves the same Italian catalogue and access specs from any egress — the
  source declares `IT` and skips an offer whose `eligibleRegion` says
  otherwise instead of failing the run. See `docs/DEPLOYMENT.md`.
- **Never authoritative**: every run reports `authoritative=False`,
  `complete_snapshot=False` (`sync_mode=FULL`, `cursor=None`), so a truncated,
  partially observed or failed run never deactivates an offer or removes a
  catalogue row.
- **Failures are loud, absences are not**: a transport error, a non-200
  (other than 404/410), a payload without the `window.WashiContext` marker or
  a malformed hydration value raises `SourceFetchError` → exit `1`/`2` with
  nothing written; a retired sitemap entry, a dead video, a closed or
  not-yet-open offer window, an unsupported layout or a live channel skips
  only that row (`rai_play_content_missing`, `rai_play_offer_skipped`,
  `rai_play_unsupported_content`, `rai_play_live_content_skipped`). A run that
  observes nothing at all logs `rai_play_catalogue_nothing_observed`.
- **No API key, no quota**: RaiPlay is a public site, so the run never touches
  the Streaming Availability budget that `refresh_availability` guards.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | Success — every attempted source succeeded. Also returned when there was nothing to do, or when sources were skipped cleanly (disabled / already running). |
| `1` | Complete failure — every attempted source failed. |
| `2` | Partial failure — some sources succeeded, some failed. |

Usage errors (unknown `--source`, or a `--source` that is not catalogue-capable) exit non-zero with a traceback *before* anything is recorded.

### Scheduling semantics

- **Discovery** is capability-driven: every registered source declaring `supports_catalogue_sync`, minus database `Source` rows with `enabled=False`. A missing `Source` row counts as enabled (not yet configured). `--source <slug>` targets one source and bypasses the enabled filter (manual override, logged as such).
- **Locking**: each source refreshes under a per-source Postgres advisory lock, so two overlapping cron runs of the *same* source never refresh concurrently — the second skips cleanly (reported `locked`, still exit `0`) while other sources continue independently.
- **Country scope**: the source's capability-declared countries are initialized as `Country` rows before writing, so a scheduled run never silently skips offers because a country row is missing.
- **Provider-native content**: every catalogue entry the source publishes is upserted into `availability.ProviderContent` (keyed on the provider's own content id) before mapping runs, so an entry that never resolves to a TMDb id is still recorded — unresolved discovery is kept, not discarded. Discovery and mapping share one transaction, so a mapping failure rolls those rows back too. `ProviderContent` rows are never deactivated by absence; only offers follow the authoritative-snapshot rules.
- **Failure isolation**: one source failing never stops the remaining sources. A failed fetch writes nothing (no `SyncRun`, no `ProviderContent`), records the error on `RefreshRun.error`, and is never disguised as a successful empty run.
- **Audit & logs**: one `RefreshRun` row per attempted source, per-group `availability.SyncRun` rows from the refresh service, and lifecycle lines `catalogue_refresh_started` / `catalogue_refresh_completed` / `catalogue_refresh_failed` / `catalogue_refresh_summary` in `./logs/catalogue.log`.

The `17 */6 * * *` frequency is a recommendation, not enforced — the per-source lock makes overlapping runs safe regardless of the slot you choose.

### What the refresh feeds

`ProviderContent` rows written here are read back offline through `ProviderCatalogueQueryService` → `ProviderContentRepository` (`ProviderContentView` per entry: provider identity, metadata, observation lifecycle, optional `tmdb_id`, active-offer presence). Reads never trigger a fetch, never call an external API, and stay fully usable when a scheduled refresh has not run yet — a stale catalogue is a slower `last_seen`, not an error.

Legacy scraper rows (`tracker.ScrapedContentMapping`) reach the same canonical tables through the ingestion bridge (`availability.services.legacy_ingest`) — written on every scraper save/confirm, with a one-off backfill (`python manage.py ingest_legacy_scraper_catalogue`) for rows that predate it. The bridge needs no cron entry; only re-ingestion of pre-existing rows does, once.

### Read-side freshness (reconciliation)

Availability is served through the reconciliation layer
(`backend/availability/services/reconciliation.py`): raw observations from
several sources are grouped per (content, provider, country) and merged into
one effective offer, and disagreements are reported as conflicts rather than
resolved. It is read-only and needs no cron entry of its own, but two things
on this page feed it:

- **Freshness deadline (optional, per `Source`)**: set
  `Source.configuration = {"expected_refresh_interval_seconds": <seconds>}`
  (admin → Availability → Sources) and every observation from that source is
  classified `fresh`/`stale` against the read time; a group that mixes states
  reports a `freshness` conflict. Keep the value **at or above that source's
  crontab cadence** — `21600` for a `17 */6 * * *` slot, `86400` for a daily
  slot — or every observation will read stale between runs. Sources without
  the key report `unknown`; there is deliberately no global default timeout.
- **Failed runs stay visible, offers stay readable**: the latest
  `availability.SyncRun` per (provider, source, country) becomes an
  observation's `sync_status`, and a failed run is counted in the
  reconciliation diagnostics (`failed_syncs`). It never hides an offer — the
  scheduled jobs above are what create those runs, so a source that is down
  for a cycle degrades to a diagnostic, not to missing results.
