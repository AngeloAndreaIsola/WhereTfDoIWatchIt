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

The initial probes against all six providers found no parsed listing pages.
This follow-up validated KissKH only. Its ingestion and checkpoint evidence
below came from an isolated PostgreSQL database migrated through `0050`; the
existing Compose database remained unchanged. Counts in the table are legacy
mapping counts in the existing Compose database, not the isolated KissKH
sample. No provider is verified complete.

| Provider | Before | After | Tested domain | Listing result | Pages | Site total / coverage | HTTP dispatch evidence | Errors | Checkpoint / status |
|---|---:|---:|---|---|---:|---|---|---:|---|
| KissKH | 1,800 | 1,800 | `kisskh.do` (`kisskh.co` fallback) | HTTP 200 JSON listing parsed and persisted in scratch | 3 pages (type 2 of 80) | API `totalCount`: type 2=1,595, type 1=6,899, type 3=2,552; coverage unknown | 16 command dispatches plus 11 bounded metadata/policy GETs; 0 HTTP failures | 0 | Scratch checkpoint at type 2 page 3; PARTIAL |
| HydraHD | 480 | 480 | `hydrahd.ru` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Budget 2 exhausted before a page; second probe budget 4, exact dispatch count not returned on error | 1 fetch error | None; BLOCKED pending accessible listing |
| AnimeWorld | 174 | 174 | `animeworld.ac` | Listing unavailable or blocked | 0 | 6,879 is a stale 2026-11-17 report, not revalidated; coverage unknown | Probe budgets 2 and 4; exact dispatch counts not returned on raised errors | 1 fetch error | None; BLOCKED pending accessible listing |
| StreamingCommunity | 143 | 143 | `streamingcommunityz.nexus` | HTTP 403; stopped without challenge workarounds | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 HTTP 403 | None; BLOCKED by access response |
| CineBlog01 | 182 | 182 | `cineblog001.tires` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 fetch error | None; BLOCKED pending accessible listing |
| AltaDefinizione | 110 | 110 | `altadefinizionex.fit` | Domain fallback did not produce a listing | 0 | Unknown / unknown | Probe budget 4; exact dispatch count not returned on raised error | 1 fetch error | None; BLOCKED pending accessible listing |

For the other five providers, earlier raised errors did not return exact
dispatch counts; those remain unknown rather than inferred from budgets. Their
status remains as reported above. The KissKH count includes a bounded targeted
search probe that returned 104 results; its 103 newly inserted rows were
removed from the disposable database because that query was broader than the
desired sample. The final isolated database contains only records from three
bounded full-list pages.

### KissKH live validation

`GET https://kisskh.do/api/DramaList/List?page=1&type=2&...&pageSize=10`
returned HTTP 200, `application/json; charset=utf-8`, and a 2,054-byte JSON
body with `page`, `pageSize`, `totalCount` and `data`. The page held 10 rows;
each observed row had a stable integer `id`, `title`, `episodesCount`, `label`,
`thumbnail` and `favoriteID`. List rows did not contain year, a per-row content
type, audio-language or subtitle-language fields. The previous “unexpected
shape” result was caused by discovery's response reader: its JSON decode and
return statements were unreachable after an unconditional HTTP-error raise.
Successful HTTP responses now return decoded JSON, while a JSON-shaped body
served as HTML is rejected as an access/interstitial response.

The same endpoint returned these provider-reported first-page totals with
`pageSize=10`: type 2=1,595, type 1=6,899, and type 3=2,552. The current public
JavaScript bundle refers to `DramaList/Show`, `DramaList/LastUpdate`,
`DramaList/Search` and `DramaList/Drama`; it does not refer to `/List`. `/List`
nevertheless returned valid JSON in bounded probes. Totals are therefore
reported as API partition totals, not independently verified population totals.

The site returned HTTP 200 HTML for both `/robots.txt` requests instead of a
robots document, so no explicit crawl directives or crawl delay could be read.
No access challenge or rate limit appeared during these probes. Discovery used
the normal robots check, sequential requests, one-second configured delay,
zero retries and an explicit request budget for every invocation.

The command run record (each dispatch count includes the robots request):

| Run | Budget / requests | Pages | Parsed / unique | Inserted / updated / unchanged | Result |
|---|---:|---:|---:|---:|---|
| Dry-run, type 2 page 1 | 2 / 2 | 1 | 20 / 20 | 0 / 0 / 0 | PARTIAL; no writes |
| Persist type 2 page 1 | 2 / 2 | 1 | 20 / 20 | 20 / 0 / 0 | PARTIAL; checkpoint page 1 |
| Replay type 2 page 1 | 2 / 2 | 1 | 20 / 20 | 0 / 0 / 20 | PARTIAL; same 20 native IDs |
| Resume type 2 page 2 | 2 / 2 | 1 | 20 / 20 | 20 / 0 / 0 | PARTIAL; checkpoint page 2 |
| Resume type 2 page 3 | 2 / 2 | 1 | 20 / 20 | 20 / 0 / 0 | PARTIAL; checkpoint page 3 |
| Targeted query `One Piece` | 4 / 4 | Search | 104 / 104 | 103 / 0 / 1 | PARTIAL; 103 temporary scratch rows then removed |

The first page-1 replay (two additional dispatches) ran before the report
comparison fix and was incorrectly counted as 20 updates because ingestion
normalizes `content_type` into the stored payload. The corrected replay shown
above was then repeated and correctly counted 20 unchanged records. Thus the
16-command-dispatch total includes both replay attempts.

The full-list checkpoint resumed from page 1 to 2 and then 3 across separate
command invocations. Its final scratch counts are 60 `ScrapedContentMapping`
rows with 60 distinct `(provider ID, content type)` identities and 60 canonical
`ProviderContent` rows. It has zero `ContentMapping`, `Offer` and legacy movie
links. Every row is marked `unverified_discovery`; no automatic TMDb association
or playback offer was created. The endpoint reports 80 pages for type 2 at the
discovery page size of 20; only pages 1–3 were scanned. No terminal page was
observed, so coverage remains `UNKNOWN` and state remains `PARTIAL`.

The public detail API route was found in the current site JavaScript as
`/api/DramaList/Drama/{id}?isq=...`. Bounded metadata probes returned:

| ID / title | List partition | Detail type | Release year | Episode evidence |
|---|---|---|---:|---|
| 10973, “High (School) On Sex - Season 2” | type 2 | `Movie` | 2025 | 6 episodes |
| 9987, “Ang Mutya ng Section E - The Muse of Section E” | type 1 | `TVSeries` | 2025 | 19 episodes |
| 1266, “One Piece” | type 3 | `Anime` | 2021 | 1,180 episodes |
| 5972, “One Piece Film: Red” | type 3 | `Anime` | 2022 | 1 episode |

KissKH's repository adapter previously had no detail-fetch method. Listing
fields remain the persisted observations; detail probes were used to validate
the partition signal and metadata availability, not to claim playback. The
provider's `Anime` detail type includes both a series and a film, so type 3 is
no longer treated wholesale as series: multi-episode anime is classified as a
series, while a one-episode anime row without explicit `Movie` or `TVSeries`
evidence blocks that page instead of being guessed. The type 2 and type 1
partition signals were corroborated by the detail samples above.

The detail responses contained release dates and episode lists, but no audio
or subtitle language fields. `country`, `sub` and the website's English-facing
interface are not language evidence. Audio and subtitle values stay unknown;
the API's title/episode metadata does not confirm playable availability.

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
| KissKH | API `type` partitions 2/1/3 and `pageSize=20` | Types 2 and 1 were corroborated as Movie and TVSeries by detail samples. Type 3 is the mixed Anime category; a one-episode Anime detail is ambiguous and blocks that page. Type 2 has 80 reported pages; only pages 1–3 were traversed, so the provider remains PARTIAL. |
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
