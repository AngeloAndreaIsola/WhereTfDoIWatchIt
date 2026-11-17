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

## Measured state (2026-11-17)

The live discovery probes used the existing command in dry-run mode and a
separate migrated scratch database. No titles were persisted and no checkpoint
was created. The prior counts are the pre-probe provider counts; after counts
are unchanged. The live backend's current post-migration counts match these
values. These probes did not establish catalogue completeness.

| Provider | Before | After | Tested domain | Listing result | Pages | Site total / coverage | HTTP dispatch evidence | Errors | Checkpoint / status |
|---|---:|---:|---|---|---:|---|---|---:|---|
| KissKH | 1,800 | 1,800 | `kisskh.co` | Unexpected API response shape; no parsed listing | 0 | Unknown / unknown | Budget 2 was exhausted before a page; second probe budget 4, exact dispatch count not returned on error | 1 API response error | None; BLOCKED pending valid API response |
| HydraHD | 480 | 480 | `hydrahd.ru` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Budget 2 exhausted before a page; second probe budget 4, exact dispatch count not returned on error | 1 fetch error | None; BLOCKED pending accessible listing |
| AnimeWorld | 174 | 174 | `animeworld.ac` | Listing unavailable or blocked | 0 | 6,879 is a stale 2026-11-17 report, not revalidated; coverage unknown | Probe budgets 2 and 4; exact dispatch counts not returned on raised errors | 1 fetch error | None; BLOCKED pending accessible listing |
| StreamingCommunity | 143 | 143 | `streamingcommunityz.nexus` | HTTP 403; stopped without challenge workarounds | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 HTTP 403 | None; BLOCKED by access response |
| CineBlog01 | 182 | 182 | `cineblog001.tires` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 fetch error | None; BLOCKED pending accessible listing |
| AltaDefinizione | 110 | 110 | `altadefinizionex.fit` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 fetch error | None; BLOCKED pending accessible listing |

The first two probes reported `requests_made=2`; at the time of later probes,
the command raised on page errors before returning its counter. Those historical
dispatch counts remain unknown rather than inferred from their budgets. Failed
runs now include the exact dispatched count and checkpoint in their structured
error report. No provider had a parsed page, duplicate sample, classification
sample, terminal-page signal, or resumable live checkpoint. Fixture tests
exercise persistence, duplicate handling, movie/series identity,
interruption/resumption and checkpoint writes, but do not replace live evidence.

An isolated `catalogue_discovery_scratch` database was migrated from an empty
PostgreSQL database through all migrations. Its command status was empty before
provider rows were seeded for bounded dry runs. Migration `0050` adds native
provider identity support and is exercised by the focused backend tests.
All 3,215 backend tests passed in a test container using that fixture plus a
read-only mount of the repository `scripts/` directory; all 274 frontend tests
passed. `makemigrations --check --dry-run` reported no model/migration drift.

### Runtime database migration state

The live Compose database originally had 46 migration-history rows and no
discovery-state table. During validation, the backend image entrypoint ran the
normal migration command before the test command; it applied pending migrations
through `0049`. The six unofficial-provider mapping counts above were unchanged.
The reported `ScrapedContentMapping` count moved from 3,711 to 3,300 during
the migration run that included `0033_merge_duplicate_providers`. This is
consistent with that migration's mapping deletion paths, but no pre-run row
IDs or deletion count were retained. It is not verified that all 411 removed
rows were semantic duplicates, and historical field conflicts cannot be
reconstructed from the current database. See the [migration incident audit](../audits/MIGRATION_0033_INCIDENT_2026-11-17.md).
`0050_scraped_provider_native_identity` remains unapplied to that database.

Do not reverse or fake this history. Before any further runtime migration,
create a restorable PostgreSQL backup, restore it into a disposable database,
apply the exact pending migrations there, and compare provider counts, mapping
IDs and fields, canonical legacy projections, and uniqueness constraints. For
the current schema, plan the `0050` rollout against that clone; keep the
pre-rollout backup as the rollback mechanism because restoring it is safer than
reverse-applying a migration after ingestion has written native IDs. No further
runtime migrations were run during validation.

## Traversal status and current limits

| Provider | Strategy | Completion evidence / current limit |
|---|---|---|
| AnimeWorld | `/az-list?page=N`, card-local parser | Traversal logic is fixture-tested. The live domain probe did not return a listing, so page links, classification and terminal behavior remain unverified. |
| KissKH | API `type` partitions 2/1/3 and `pageSize=200` | Traversal logic is fixture-tested. The live API response shape did not match the expected `data` array; totals, empty-page behavior and partition termination remain unverified. |
| HydraHD | `movies/date/N` and `tv-shows/date/N` | Separate cursor logic is fixture-tested. Live fallback did not produce listing HTML; pagination and terminal behavior remain unverified. |
| CineBlog01 | `/film/` and `/serie-tv/`, `/page/N/` | Archive traversal logic is fixture-tested. Live fallback did not produce a listing; pagination links and terminal behavior remain unverified. |
| StreamingCommunity | Existing root, film, series, archive listing paths | Live response was HTTP 403. No workaround or additional path guessing was attempted; public pagination is unverified. |
| AltaDefinizione | Existing root, film, series, archive listing paths | Live fallback did not produce a listing. Public pagination is unverified; the command does not invent `/page/N/` endpoints. |

`coverage_percent` stays `UNKNOWN` unless the fetched population and a reliable
provider total are known to be comparable. `items_unique` describes observed
catalogue identities, not TMDb links or confirmed playback offers. Legacy rows
continue to use provider/title/year/type. New observations with an explicit
provider ID use provider/provider-ID/content-type identity; movie and series
remain separate, and an existing legacy row is promoted in place when a native
ID later becomes available. The new partial unique constraints preserve both
identity forms without rewriting existing scraped records.

No full-traversal request estimate, run time, or peak-memory figure is
available: the live traversal was deliberately not started. One command
invocation dispatches at most its configured budget (20 by default), plus no
hidden retries beyond `--retry-limit`; repeated invocations resume full mode.

The management page calls these records **discovered titles**. Its catalogue
sync badge is independent of subscription active/inactive state. Per-provider
status is stored in `CatalogueDiscoveryState`; last committed page progress is
saved after each parsed page. Failed pages do not delete existing records.
