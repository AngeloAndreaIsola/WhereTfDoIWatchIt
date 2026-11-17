# Unofficial catalogue discovery operations

The six unofficial provider implementations now have an explicit, bounded
management command. It writes observations through `persist_scraped_item()`
and the existing legacy ingestion bridge. Every catalogue observation carries
`unverified_discovery`; discovery creates no `Offer` and does not produce an
automatic TMDb association.

```sh
# Inspect durable state and counts
python manage.py discover_unofficial_catalogue --status

# Inspect requests and parsed items without database writes
python manage.py discover_unofficial_catalogue --provider animeworld \
  --mode full --request-budget 3 --retry-limit 1 --delay 1 --dry-run

# Start a bounded full traversal; repeat the command to resume a PARTIAL run
python manage.py discover_unofficial_catalogue --provider kisskh \
  --mode full --request-budget 25 --retry-limit 1 --delay 1

# Check recent pages for additions/updates; no historical pages are revisited
python manage.py discover_unofficial_catalogue --provider hydrahd \
  --mode incremental --request-budget 8 --delay 1

# Provider search is reported PARTIAL because it is not a catalogue walk
python manage.py discover_unofficial_catalogue --provider animeworld \
  --mode targeted --query "Frieren" --request-budget 3
```

There is no scheduled job. Full discovery runs only when explicitly invoked.
`Ctrl-C` stops after the current page batch and leaves the last committed
cursor available for resume. The provider scoped lock uses the existing
PostgreSQL advisory-lock convention. The request budget counts HTTP dispatches
including robots checks and domain fallback. Requests run sequentially
(concurrency 1) so page order and checkpoints are deterministic. Bounded
retries honor `Retry-After`; robots directives and crawl delay are checked for
each origin before catalogue requests.

## Current measured state

The database still contains the Phase 1 snapshot counts below. No catalogue
backfill was executed in this change, so “new count” is the unchanged count,
not a crawl result. The running backend database does not record its migration
history and lacks the new discovery table; its two-request dry-run stopped
before any network request. Migration and smoke evidence must be collected in
an environment with the tracker migrations applied.

| Provider | Previous count | New count | Site total | Pages scanned | Coverage | Status |
|---|---:|---:|---:|---:|---:|---|
| KissKH | 1,800 | 1,800 | UNKNOWN | 0 | UNKNOWN | IDLE / unverified |
| HydraHD | 480 | 480 | UNKNOWN | 0 | UNKNOWN | IDLE / unverified |
| AnimeWorld | 174 | 174 | 6,879 reported on 2026-11-17; not rechecked | 0 | UNKNOWN | IDLE / unverified |
| StreamingCommunity | 143 | 143 | UNKNOWN | 0 | UNKNOWN | IDLE / PARTIAL traversal |
| CineBlog01 | 182 | 182 | UNKNOWN | 0 | UNKNOWN | IDLE / unverified |
| AltaDefinizione | 110 | 110 | UNKNOWN | 0 | UNKNOWN | IDLE / PARTIAL traversal |

## Traversal status and current limits

| Provider | Strategy | Completion evidence / current limit |
|---|---|---|
| AnimeWorld | `/az-list?page=N`, card-local parser | Follows explicit page links and probes until an empty page when no total is shown. Page count is not itself a title total. No live snapshot was fetched in this implementation, so the published 6,879 / 276 observation is not treated as verification. |
| KissKH | API `type` partitions 2/1/3 and `pageSize=200` | Uses API page/total metadata when present; otherwise requires an empty page. Provider totals and page semantics still need live fixture evidence. |
| HydraHD | `movies/date/N` and `tv-shows/date/N` | Separate cursors; follows pagination metadata or probes until an empty listing. Layout/domain failure is BLOCKED, never terminal success. |
| CineBlog01 | `/film/` and `/serie-tv/`, `/page/N/` | Page count comes from page links; without it, an empty page is required. Redirects use verified fallback hosts. |
| StreamingCommunity | Existing root, film, series, archive listing paths | Partial. No verified public paginator was available in the repository snapshot; the command records each current listing but does not invent `/page/N/` endpoints. |
| AltaDefinizione | Existing root, film, series, archive listing paths | Partial for the same evidence gap. The code does not invent pagination endpoints. |

`coverage_percent` stays `UNKNOWN` unless the fetched population and a reliable
provider total are known to be comparable. `items_unique` describes observed
catalogue identities, not TMDb links or confirmed playback offers. The legacy
mapping identity still uses provider/title/year/type; provider-native variants
that share all four values need a future explicit native-ID identity migration
before they can be represented separately.

No full-traversal request estimate, run time, or peak-memory figure is
available: the live traversal was deliberately not started. One command
invocation dispatches at most its configured budget (20 by default), plus no
hidden retries beyond `--retry-limit`; repeated invocations resume full mode.

The management page calls these records **discovered titles**. Its catalogue
sync badge is independent of subscription active/inactive state. Per-provider
status is stored in `CatalogueDiscoveryState`; last committed page progress is
saved after each parsed page. Failed pages do not delete existing records.
