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
- **Event-driven request drain**: before the background stale sweep, each run settles pending `RefreshRequest` rows first (manual actions, never-fetched titles, refresh-on-view, watchlist adds/imports, mark-watched), in user-intent priority order, through this same budget, the same rate limiter and the same `RefreshRun`. With a budget of 2 or more, one held-back unit is spent last on the longest-waiting request (bounded fairness), so an endless stream of detail views or watchlist adds cannot starve the low-priority backlog; the daily quota stays authoritative either way. A request left pending because the budget or quota ran out simply waits for the next drain — primarily the dedicated `worker` service (`docker compose` → `python manage.py refresh_worker`, a queue-only loop that drains events within seconds and never runs the sweep), with this scheduled run as its fallback. The JSON summary in the log carries `requests_drained` (requests settled this run) and `requests_pending` (backlog remaining).

## Title availability cache

JustWatch and Watchmode reuse a successful title check for seven days, keyed
by typed title, country and source. Configure a shorter operational window
per aggregator with `Source.configuration["title_refresh_interval_seconds"]`.
Offer-evidence freshness and catalogue health still use
`expected_refresh_interval_seconds`. A known catalogue traversal does not fill
the title cache.

When fresh first-party evidence confirms a departure, the eligible title
aggregator checks become due every 24 hours within seven days. In the final
24 hours, automation can perform one check per source. A check that already
started in that window counts even if it failed. Disputed, withdrawn,
expired and aggregator-only dates do not shorten the cache.

Title opens enqueue local work. The manual detail action can recheck the
selected title and market before the cache expires. A check attempted in the
last ten minutes serves repeated clicks for the same scope. The worker
continues to apply source-specific limits and its run budget. Empty or failed
checks never remove last-known offers or make them current.

### Source-reported title offers

Only offers tied to the exact successful routine check's targeted sync runs
can use the seven-day display window. Purpose-built audit or pilot runs and
older rows without routine provenance stay in last-known history. The title response places these offers in
`source_reported_offers` with source, original observation and last-seen
timestamps, and the successful check time. They do not enter independently
qualified availability or watchability counts. A newer failed, interrupted,
empty or inconclusive check leaves older positives in last-known history.
Disputed dates and conflicting reports do not gain current status.

Detail GETs read local titles and metadata. A tracked title can enqueue due
work for its country without waiting for a provider. The manual action
bypasses the ordinary lifetime for the selected typed title and market.
Quota, locks, the ten-minute cooldown and pending-request dedup still apply.

### Reviewed upstream permissions

An enabled Source row supplies no permission by itself. Set
`Source.configuration["outbound_availability_permission"]` only after reviewing
the current authorization. The review record holds:

- `status` equal to `authorized`
- Exact `endpoint`, approved ISO `countries` and explicit `operations`
- Sanitized `evidence_reference`, `reviewed_by`, and timezone-aware
  `valid_from` and `valid_until`
- JustWatch only, a positive `maximum_requests` for the receipt and a positive
  `minimum_interval_seconds` from the reviewed rate policy

For JustWatch use `https://apis.justwatch.com/graphql`, with `search` for movies
and `search` plus `seasons` for series. The default transport also tests the
exact document and country for separately authorized `catalogue`, `providers`,
`details`, `episodes` and `title_offers` operations. The Watchmode receipt uses
`https://api.watchmode.com/v1`, `search` and `title_sources`. An explicit
Streaming Availability check requires
`https://streaming-availability.p.rapidapi.com` and `show_availability`.
Existing credentials and quotas still apply. The receipt must cover the
application's title workload for those markets. A narrow diagnostic approval
does not authorize this workload. No receipt is seeded.

The queue rechecks permission before execution. The ordinary JustWatch
transport commits an attempt against the receipt before each HTTP POST. It
does not follow redirects. Denials stop the reviewed receipt, rate limits
honor the upstream retry window, and interrupted sends retain their
reservation until separately reconciled. The bounded #97 experiment has
its own authorization, transport and ledger.

## Provider Catalogue Refresh

Provider catalogues (CBC Gem, BBC iPlayer, Tubi, Prime Video, RaiPlay, Pluto TV, PBS, and ABC iview) are refreshed by a separate command: a catalogue source reports its whole configured scope through public catalogue endpoints, so **no API budget applies** and there is no `--budget` flag.

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
The Phase E sources land at both ends of that range: PBS is cheap (one index
page plus ~20 batch calls), Pluto TV is a handful of grid pages per catalogue,
and ABC iview is the heaviest walker yet — one detail request per series,
~1613 requests paced 0.18 s apart (≈5 minutes per run).
Give each heavy source its own, slower slot with `--source`:

```cron
17 */6 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source cbc_gem >> ./logs/catalogue.log 2>&1
17 4 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source bbc_iplayer >> ./logs/catalogue.log 2>&1
17 1 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source tubi >> ./logs/catalogue.log 2>&1
17 7 * * *  cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source prime_video >> ./logs/catalogue.log 2>&1
17 10 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source rai_play >> ./logs/catalogue.log 2>&1
17 13 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source pluto_tv >> ./logs/catalogue.log 2>&1
17 16 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source pbs >> ./logs/catalogue.log 2>&1
17 19 * * * cd /path/to/streaming-tracker && docker compose exec -T backend python manage.py refresh_provider_catalogue --source abc_iview >> ./logs/catalogue.log 2>&1
```

`--source <slug>` targets one source and bypasses the `enabled` filter (manual
override, logged as such); everything else — locking, country initialization,
`RefreshRun` audit rows, and the exit codes above — behaves exactly as in a
multi-source run. Omit `--source` if you would rather refresh everything
together at one frequency.

#### Tubi scheduling notes

- **Bounded walk**: a run requests at most `DEFAULT_MAX_CONTENTS` (500) **title
  pages**. Sitemap discovery first keeps only the direct `<loc>` of each
  `<url>` entry and drops image/video extension assets, so a 500-page bound now
  buys 500 titles instead of 250 titles plus 250 posters. Hitting the bound
  logs `tubi_catalogue_truncated title_urls=<n> batch=<n>
  remaining_in_cycle=<n>`.
- **Durable checkpoint**: progress lives in `availability.SyncCursor`
  (`provider=tubi`, `source=tubi`, `country=US`) as a small JSON document with
  one last-seen URL per title sitemap. A batch resumes immediately after those
  URLs, so repeated runs no longer re-read the newest 500 entries forever. The
  checkpoint advances after every successful batch — including a batch that
  maps zero offers — and is left untouched when the fetch or persistence step
  fails, so the same window is retried rather than skipped.
- **Drift behaviour**: sitemap head insertions do not shift the checkpoint
  (new entries are picked up at the next cycle). If a checkpointed URL
  disappears, only that sitemap restarts from its head; the other queues keep
  their progress. A cursor from an older `CURSOR_VERSION` restarts the cycle.
  At 500 pages/run one full 82,386-title cycle takes ~165 runs.
- **Tracked titles first**: the generic catalogue runner passes the local
  tracker catalogue to sources declaring `supports_priority_hints`. Tubi slug-
  matches those titles against the sitemap and fetches the matching pages in
  addition to the traversal batch (bounded by `DEFAULT_MAX_PRIORITY`, 300;
  currently ~173 matches). This is only candidate selection — identity is
  still decided by the existing title/year mapping after parsing, never by the
  slug. A typical run is therefore ~500 traversal pages + ~173 priority pages,
  roughly 14 minutes at the measured 1.27 s/detail page.
- **Sitemap retries**: a sitemap document is retried twice with linear backoff
  on transport or parse failure. After the bounded retries the run still fails
  loudly; a malformed or consent page is never converted into an empty
  "successful" catalogue. Diagnostics include status/content type/size and a
  short, sanitized body prefix (never cookies or auth headers).
- **US egress required**: Tubi geo-blocks non-US callers (a GDPR redirect
  answers instead of the catalogue). From an EU egress the run fails cleanly
  with `SourceFetchError` → exit `1`/`2` and no writes, never an empty
  "successful" catalogue.
- **Absence is logged, not fatal**: a stale title that answers with the SPA
  shell (`tubi_content_missing`), an offer window that is closed or has not
  opened (`tubi_offer_expired`, `tubi_offer_not_yet_available`), and a series
  with no episode on its page (`tubi_content_only`) skip that row only. A walk
  that observes nothing at all logs `tubi_catalogue_nothing_observed` as a
  warning; only transport/parse failures (missing `window.__data`, malformed
  sitemap, undecodable state) fail the source.
- **Never authoritative**: every Tubi run reports
  `authoritative=False`/`complete_snapshot=False`, so a truncated or partially
  observed run deactivates no offer and removes no catalogue row. The
  checkpoint is traversal progress, never evidence that an unvisited title is
  unavailable.

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

#### Phase E scheduling notes (Pluto TV, PBS, ABC iview)

- **Measured / estimated cost**: PBS is one `allShows` page-walk (~10 pages)
  plus ~20 `showsBatch` calls — tens of requests per run, safe on the bare
  `17 */6 * * *` entry. Pluto TV reads one `collection` plus its grid pages
  (24 rows each) per catalogue. ABC iview reads one index plus one detail
  per series: ~1613 detail requests at the built-in `DEFAULT_DELAY`
  (0.18 s) ≈ **5 minutes per run**; the walk stops at `MAX_SERIES` (3000,
  code-level default) and logs `abc_iview_series_cap` if the index ever
  runs away.
- **Cadence**: daily is plenty for all three — catalogues churn slowly and
  offer windows change rarely. Keep `abc_iview` on its own slot (it is the
  longest run); the cheap `pbs` may stay on the bare command.
- **Egress**: none of the three needs a special egress. The PBS content API
  serves the same US catalogue from any IP (verified), the iview catalogue
  endpoints answer from any IP (verified from a non-AU egress; only playback
  is geo-blocked, and playback is never fetched), and Mediaset's gateway
  exposes no egress probe. Pluto TV is the exception: `collection.region` is
  derived server-side from the egress IP, so an IP Pluto serves only its
  international landing to is a successful observation of nothing — logged
  `pluto_tv_unsupported_region`, exit `0`, no offers invented. A browser
  `Mozilla/5.0*` UA also trips Pluto's bot gate from datacenter IPs; the
  client already pins the verified `python-requests` UA, so no config is
  needed.
- **Never authoritative**: all three report `authoritative=False`
  (`complete_snapshot=False`, `sync_mode=FULL`), so a truncated walk, a
  dropped batch or a vanished series deactivates no offer and removes no
  catalogue row.
- **Targeted siblings**: `youtube` and `mediaset_infinity` are *not*
  catalogue sources — they run title-targeted inside the budgeted daily
  `refresh_availability` sweep (`docs/CRON.md` budget/quota section), not
  under `refresh_provider_catalogue`.

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

- **Freshness deadline (per source, from `availability.freshness`)**: every
  observation is classified `fresh`/`stale` against the read time using that
  source's own window, and a group that mixes states reports a `freshness`
  conflict. The window resolves in order: `Source.configuration =
  {"expected_refresh_interval_seconds": <seconds>}` (admin → Availability →
  Sources) overrides the built-in `SOURCE_FRESHNESS` policy — `21600` (6h)
  for `cbc_gem`, `43200` (12h) for `legacy_scraper`, `86400` (24h) for
  `streaming_availability`, `bbc_iplayer`, `tubi`, `prime_video`, `rai_play`,
  `youtube`, `pluto_tv`, `mediaset_infinity`, `pbs` and `abc_iview`. Keep an override **at or above that source's crontab cadence**
  or every observation will read stale between runs. Slugs in neither place
  (undeclared sources) report `unknown` — the read side has deliberately no
  global default timeout; only the refresh side falls back to 24h.
- **Failed runs stay visible, offers stay readable**: the latest
  `availability.SyncRun` per (provider, source, country) becomes an
  observation's `sync_status`, and a failed run is counted in the
  reconciliation diagnostics (`failed_syncs`). It never hides an offer — the
  scheduled jobs above are what create those runs, so a source that is down
  for a cycle degrades to a diagnostic, not to missing results.

## Abandoned refresh-run reconciliation

Preview with `docker compose exec -T backend python manage.py
cleanup_availability_history --dry-run`. The default 120-minute threshold is
well above the previously measured 31-minute collector run. Recheck the
threshold against the current slowest expected job before enabling a schedule.
The command defers if a worker holds a PostgreSQL advisory lock, including a
worker that acquires the lock while the candidate preview is being written.

After separate approval, add one daily entry away from the title and catalogue
refresh windows. Run it on a host using the reviewed Compose project and
environment for the intended database.

```cron
43 5 * * * cd /path/to/WhereTfDoIWatchIt && docker compose -p wheretfdoiwatchit exec -T backend python manage.py cleanup_availability_history --apply --min-age-minutes 120 >> ./logs/refresh-history-cleanup.log 2>&1
```

Keep the dry-run report before enabling the entry. Review the reconciled
count, no-op message, lock deferral, or nonzero exit in the log. Duplicate
invocations serialize through a transaction-scoped PostgreSQL advisory lock.
The command records abandoned runs as errors and preserves completed runs,
offers and checkpoints. It uses no external requests. Disable future runs by
removing the single cron entry. Keep command logs and existing audit rows
available for separately reviewed corrections if a row was misclassified.
