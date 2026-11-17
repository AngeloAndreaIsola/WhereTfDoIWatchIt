# Unofficial catalogue discovery operations

The command `discover_unofficial_catalogue` now persists raw listing observations
in `DiscoveredCatalogueRecord`. These records remain pending classification and
do not create canonical mappings, TMDb associations, or playback offers. Migration
`0053` adds conservative URL identity, original URL history, provenance state,
and access-policy state. The Compose database remains at migration `0049`; all
counts below are from the disposable `docker-compose.test.yml` PostgreSQL
instance migrated through `0053`.

Requests are sequential, bounded, robots-checked, and checkpointed. The budget
counts each HTTP dispatch, including `robots.txt`. Hydra runs used one-second
delays and zero retries. AnimeWorld uses only its first-party RSS snapshot.
The A–Z archive is not requested because AnimeWorld's published terms prohibit
scraping. See the official [terms](https://www.animeworld.ac/termini) and
[RSS changelog entry](https://www.animeworld.ac/changelogs).

## Current results (2026-11-17)

| Provider | Pages scanned | Unique URLs/IDs in staging | Rows persisted in durable staging | Total pages | Coverage | Status |
|---|---:|---:|---:|---|---|---|
| AnimeWorld | 1 RSS snapshot per run; 3 prior runs | 79 URLs; 15 RSS GUIDs | 79 (64 legacy A–Z rows marked restricted) | Not exposed by RSS | Recent updates only; archive coverage unknown | `CATALOGUE_DISCOVERY_PARTIAL` |
| HydraHD | Movies 1–89; TV 1–34 (123 partition pages represented) | 2,952 unique URLs; no native IDs | 2,952 | No stable total; navigation windows ended at 92 (movie page 89) and 37 (TV page 34) | Sequential through checkpoints; no terminal page or completeness proof | `CATALOGUE_DISCOVERY_PARTIAL` |
| CineBlog01 | 0 | 0 | 0 | Unknown | No listing request authorized by provenance | `CATALOGUE_DISCOVERY_BLOCKED` |
| AltaDefinizione | 0 | 0 | 0 | Unknown | No listing request authorized by provenance | `CATALOGUE_DISCOVERY_BLOCKED` |
| StreamingCommunity | 0 | 0 | 0 | Unknown | No listing request authorized by provenance | `CATALOGUE_DISCOVERY_BLOCKED` |
| KissKH | 0 in this task | 0 | 0 | Unknown | Preserved prior state; HTTP 451 remains a hard stop | `CATALOGUE_DISCOVERY_PARTIAL` |

**Overall verdict: `CATALOGUE_STAGING_PARTIAL`.** The inventory is durably
exported and import/replay verified. Hydra traversal remains open because its
page links do not expose stable totals or a terminal boundary. The available
backup cannot validate integration against the current Compose database.

The original disposable PostgreSQL container stopped after the first export;
its `tmpfs` database no longer survives container restart. The durable staging
export preserves all 2,047 records and both checkpoints before that happened.
After importing the export into a fresh database migrated through `0053`,
bounded Hydra continuation added 984 records. The final staging dataset has
3,031 rows: 79 AnimeWorld and 2,952 HydraHD. All remain pending classification,
with zero canonical mappings and zero offers. The Compose database was not
queried, migrated, or modified.

The checksummed export is in the ignored local path
`.catalogue-staging/catalogue-inventory-v1.jsonl.gz`, with its manifest beside
it. The gzip SHA-256 is
`0c38ce93f7257c513a4d6760d4cc0d7a55718ca1ded9397db2a9b34ba17dfe02`; the file
contains 3,031 rows (2,952 HydraHD and 79 AnimeWorld). The manifest includes
per-provider counts and the latest resumable checkpoints. Files are mode `0600`
and are excluded from Git.

### AnimeWorld

The public A–Z route returned listing HTML in earlier work, but its terms say
scraping is prohibited due to server load. No further A–Z request was made and
those 64 prior observations are marked `RESTRICTED`; they are not counted as
current permitted coverage. The site changelog describes `/rss/animes` as an
official RSS feed. A bounded GET returned HTTP 200 XML with a `lastBuildDate`
and 15 current items. There is no total or continuation, so each run reads one
snapshot and remains partial. Three command invocations each dispatched two
requests (one robots check and one RSS GET); the first successful run inserted
15 records, the next run updated metadata during initial normalization, and the
third replay reported 15 unchanged. The feed GUIDs are stored as provider-native
IDs. The feed's anime/movie hint remains raw metadata and classification stays
pending. RSS is the only AnimeWorld source used in this follow-up.
A focused snapshot test simulates an updated existing GUID plus a new GUID,
then replays both: one update, one insert, and two unchanged observations. No
new live RSS request was made during this follow-up.

### HydraHD

The working listing host is `hydrahd.gd`, with `/movies/date/N` and
`/tv-shows/date/N` routes. The source status page at [hydrahd.info](https://hydrahd.info/)
self-identifies `.gd` as the primary domain; this is self-asserted provenance,
not independent operator proof. The observed robots metadata permitted ordinary
reference use. The sitemap returned HTTP 403 in the earlier investigation and
was not retried. No fallback host was used during this traversal.

The initial staged inventory contained 1,968 HydraHD records: 48 earlier
observations from movie pages 1–2, 1,440 rows from the prior sequential movie
pages 3–62, and 480 from TV pages 1–20. The 48 earlier rows do not retain a
per-row page number, so their page attribution comes from the recorded prior
two-page run. The current continuation persisted movies 63–89 and TV 21–34,
adding 984 records. Every fetched page yielded 24 rows. Captured page metadata
has no gaps across 3–89 and 1–34, and the prior checkpoint covers movie pages
1–2. No provider-native IDs were present; URL identity is used without
manufacturing IDs.

The page links expose a rolling navigation window rather than a stable total:
the largest linked page was 65 at movie page 62, 76 at page 73, 83 at page 80,
88 at page 85, and 92 at page 89. TV navigation similarly showed page 30 at TV
page 27 and page 37 at page 34. These values are not catalogue totals. Pages
were followed sequentially from each checkpoint. The 47 continuation dispatches
included 6 robots checks and 41 listing requests; all returned usable pages.
No empty or HTTP 404 terminal page was observed. The prior 92 dispatches,
including robots checks and two page-35 checkpoint replays, are recorded in the
first-phase report. No same-page URL overlap was found in the continuation
(`items_updated=0`, `duplicates_skipped=0`); all 984 fetched rows were new URLs.
The deduplicated staging data contains 2,952 HydraHD URLs. No total number of
pages or complete-catalogue claim is justified; the latest checkpoints are
movie page 89 and TV page 34.

Release-year labels trend older on later movie pages, but the pages expose no
reliable per-item listing timestamps for strict date-order validation. URL
deduplication and sequential page labels show no observed overlap; date-ordered
pagination can still shift when new entries arrive, so a long traversal is not
a frozen snapshot and may omit records that move across page boundaries.

### Provenance blockers

The candidate home pages for CineBlog01 `.run`, AltaDefinizione `.ink`, and
StreamingCommunity `.army` had previously returned HTTP 200, but branding alone
does not establish that these domains belong to the providers. The command now
returns `BLOCKED` with zero requests for these providers until provenance is
verified. No candidate catalogue records were ingested.

- **CineBlog01:** AGCOM order [47/26/DDA](https://www.agcom.it/sites/default/files/provvedimenti/determina/2026/DDA_14849_determina_47_26_dda.pdf)
  references `cineblog001.cc` and `cb01new.ceo`; it does not link the current
  `.run` candidate. Status: `PROVENANCE_UNVERIFIED`.
- **AltaDefinizione:** AGCOM [145/18/DCA](https://www.agcom.it/provvedimenti/determina-145-18-dca)
  concerns the distinct `altadefinizione.ink` host; it does not establish the
  candidate `.ink` domain's provider relationship. Status: `PROVENANCE_UNVERIFIED`.
- **StreamingCommunity:** `.army` has no independently corroborated current
  domain linkage. Existing `.nexus` access restrictions remain relevant; no
  alternative domain was used to get around them. Status: `PROVENANCE_UNVERIFIED`.
- **KissKH:** HTTP 451 remains an access restriction. No request was made in
  this follow-up and no attempt to circumvent the restriction was made.

### Identity and classification

`identity_url` removes only common tracking keys (`utm_*`, `fbclid`, `gclid`,
`dclid`, `mc_cid`, and `mc_eid`), drops fragments and default ports, and retains
scheme, path, trailing slash, and every other query parameter. Host aliases are
provider-scoped and opt-in; the verified alias map is currently empty. Original
`source_url` is retained and variants are recorded in `source_urls_seen`. Names
are never an identity key. Existing observations were backfilled in migration
`0053`; URLs that collided after safe normalization were merged by URL identity
while preserving their original URL variants.

All discovered rows remain pending classification. Listing hints are preserved
as metadata, but no canonical movie/series mappings, TMDb matches, or offers
were created. Language extraction and playback verification are out of scope.

### Validation

The disposable database was migrated explicitly through `0053` using the
`backend-test` service with `manage.py migrate`; the normal backend entrypoint
and Compose database were not used for migration. The RSS and date-listing
commands used explicit request budgets. Persisted rows were queried after
ingestion. Focused tests passed (28 tests) across staging, checkpoint, URL
identity, RSS snapshot, overlap and rolling-navigation behavior. The complete
isolated backend suite passed (3,264 tests in 136.8 seconds).
`makemigrations --check --dry-run` reported no drift.

### Durable staging import and database rollout gate

The original `db-test` service uses `tmpfs`; it stopped after export and its
database no longer survives restart. The export remained available in the
ignored workspace staging directory. A separate empty database was migrated
through `0053`, then imported the final 3,031-row staging file. Import result:
3,031 inserted, two checkpoints restored. A second import produced 3,031
unchanged, zero inserted, and zero updated. The target retained 3,031 pending
records, zero `ScrapedContentMapping` rows, and zero `Offer` rows. Staging tests
also verify discovery timestamps, original URLs and metadata, checksum failure,
and checkpoint replay.

The available SQL backup
[`tracker_backup_20260926_140128.sql`](../../backups/tracker_backup_20260926_140128.sql)
has SHA-256 `693f7b40976b71cf7e19a4d01d69f9e5b8209f94083db5efac2b9cfa33a6d2a0`.
It restored successfully into an isolated database. It is not a valid substitute
for the stated post-incident live backup: its migration history ends at the
different `0033_fix_preference_json_fields` lineage, while the current branch
has a different `0033_merge_duplicate_providers`, and applied migration
`0013_merge_20260830_1219` is missing its `0011_availability_price_info_and_more`
dependency. A direct migration attempt failed with
`InconsistentMigrationHistory` before applying any migrations. The restored
clone contains zero `ScrapedContentMapping` rows and 4 `Movie` rows, not the
3,300-row baseline described for Compose. I did not fake or edit the migration
history. Mapping IDs and field hashes therefore cannot be compared against the
live baseline from this file.

**Persistent rollout readiness: blocked on a current, verified migration-0049
backup.** The explicit rollout plan is:

1. Produce a fresh restricted backup from the Compose database at `0049`; verify
   its checksum and record the mapping count and ordered ID/field hashes.
2. Restore that backup to an isolated PostgreSQL clone. Confirm the migration
   ledger is consistent with this branch before running any migration.
3. Apply `0050`–`0053` explicitly in the clone. Compare mapping IDs, row count,
   and hashes of all pre-existing mapping fields; investigate any difference.
4. Import the staged inventory twice. Require first-pass counts to match the
   manifest, second-pass `unchanged` count to equal 3,031, and zero mapping,
   TMDb association, or offer changes.
5. Restore the backup into a second clone to verify rollback. Only after these
   checks should a separate rollout be proposed and authorized. No live
   migration or import was run here.

### Why earlier probes returned no listings

The earlier probe reports did not retain exact HTTP statuses for several
raised fetch errors, so those historical failures cannot be assigned to DNS,
TLS, HTTP blocking, or parser errors after the fact. Current bounded probes
clarify the source-specific causes: AnimeWorld's old category URLs had drifted
to `/az-list`; HydraHD's old `.ru` host is marked disabled, while the current
`.gd` date pages parse 24 records per page; CineBlog01's old `.tires` target
differs from the current configured `.run` host, which responds but is not
verified as a permitted alias; AltaDefinizione's current `.ink` home responds
but its relationship to the provider is not established; StreamingCommunity's
previous `.nexus` request received HTTP 403, while the configured `.army` host
responds but its ownership is unverified. In the same live checks, parser
errors were not observed on the two ingested sources. No anti-bot, robots, or
access restriction was bypassed.

The older KissKH measurements below are historical and remain separate from
this follow-up. KissKH still has its own pending-classification path and the
homepage/API restriction evidence recorded below.

## Historical measured state (2026-11-17)

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
provider's `Anime` detail type includes both a series and a film. Partition
membership and episode count therefore do not establish a title's movie/series
type. Discovery now persists rows without explicit `Movie` or `TVSeries`
evidence in `KissKHPendingClassification`; they do not create canonical
movie/series records. A reusable detail fetcher and resolver promote the
observation only after explicit detail type metadata arrives. Episode count and
`Anime` alone remain ambiguous.

The detail responses contained release dates and episode lists, but no audio
or subtitle language fields. `country`, `sub` and the website's English-facing
interface are not language evidence. Current status:
`LANGUAGE_METADATA_UNAVAILABLE`. Audio and subtitle values stay unknown; the
API's title/episode metadata does not confirm playable availability.

### KissKH bounded partition and boundary probes (2026-11-17)

After the earlier three-page type 2 ingestion, a separate bounded probe made
12 listing API requests: page 1, a middle page, the reported last page and one
page beyond the reported end for each partition, all at `pageSize=20`. It also
requested `/robots.txt` and `/` once each. The listing endpoint answered HTTP
200 JSON for all 12 requests. `/robots.txt` again returned HTML; `/` returned
HTTP 451 HTML. The API was still directly reachable, but the homepage response
is an access restriction signal, so no discovery command, ingestion or further
live request was run after these probes. There were 0 failed listing requests
and 1 HTTP 451 access response.

| Partition | API total | Pages sampled (first / middle / last) | Rows / sampled IDs | Beyond-last behavior | Catalogue traversal |
|---|---:|---|---:|---|---|
| Type 1 | 6,899 | 1 / 172 / 345 | 59 / 59 | Request 346 returned page 345 again (19 rows) | Not traversed sequentially; PARTIAL |
| Type 2 | 1,595 | 1 / 40 / 80 | 55 / 55 | Request 81 returned page 80 again (15 rows) | Not traversed sequentially; PARTIAL |
| Type 3 | 2,552 | 1 / 64 / 128 | 52 / 52 | Request 129 returned page 128 again (12 rows) | Not traversed sequentially; PARTIAL |

Across the nine distinct sampled pages, 166 listing rows and 166 distinct
native IDs were observed after deduplicating the captured page-ID sets across
all partitions. No sampled ID repeated within or between those pages. This is a
boundary sample, not a sequential page traversal or a count of the complete
catalogue. The first and middle pages returned 20 rows each. The final short pages (19, 15 and 12 rows)
fit the reported totals and expected page counts (345, 80 and 128). Beyond-last
requests were clamped to the final page rather than returning an empty page.
The reported totals stayed constant across the sampled pages.

The endpoint is absent from the public JavaScript bundle inspected in the
previous probe and is therefore an undocumented, potentially legacy API. Its
continued HTTP 200 responses demonstrate current accessibility, not permission
for an unrestricted crawl. Stability risk is high. The root 451 response and
unreadable robots body leave the site's applicable access policy unclear, so
only this small probe was completed. No direct detail API requests were made in
this follow-up. Existing detail samples remain the four records above.

| Metric | Type 1 | Type 2 | Type 3 |
|---|---:|---:|---:|
| Reported records | 6,899 | 1,595 | 2,552 |
| Distinct pages probed / pages traversed | 3 / 0 sequential | 3 / 0 sequential | 3 / 0 sequential |
| Sampled unique native IDs | 59 | 55 | 52 |
| Movie / series classifications in this probe | 0 / 0 | 0 / 0 | 0 / 0 |
| Movie / series / ambiguous sampled rows | 0 / 0 / 59 | 0 / 0 / 55 | 0 / 0 / 52 |
| Duplicate IDs measured | 0 within/between sampled pages | 0 within/between sampled pages | 0 within/between sampled pages |
| Failed listing pages / HTTP requests | 0 / 4 | 0 / 4 | 0 / 4 |
| Last reported page / terminal evidence | 345 / short page and clamp confirmed | 80 / short page and clamp confirmed | 128 / short page and clamp confirmed |
| Sequential coverage | Unknown | Unknown | Unknown |

Total probe requests were 14 (12 catalogue, 1 robots, 1 homepage); the
discovery command dispatched 0 requests in this follow-up. The 166 sampled
observations were not persisted because of the 451 access signal. No
classification detail requests were made. The previous
60-row scratch ingestion is historical evidence from the preceding task, not
the current database state. Backend changes add pending-classification storage
in migration `0051`; isolated tests are the only database use for that schema.

An isolated `catalogue_discovery_scratch` database was migrated from an empty
PostgreSQL database through all migrations. Its command status was empty before
provider rows were seeded for bounded dry runs. Migration `0050` adds native
provider identity support and is exercised by the focused backend tests.
All 3,215 backend tests passed in a test container using that fixture plus a
read-only mount of the repository `scripts/` directory; all 274 frontend tests
passed. `makemigrations --check --dry-run` reported no model/migration drift.
For the classification follow-up, migration `0051` applied in the isolated
test database, 100 focused backend tests passed, and the complete isolated
backend suite passed 3,247 tests. `makemigrations --check --dry-run` reported
no drift. No frontend files changed, so frontend tests were not rerun.

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
| AnimeWorld | `/az-list?page=N`, card-local parser | Page 1 and page 2 were traversed sequentially; page-link maximum was 3 on page 2, leaving 1 reported page. Only 64 distinct source URLs were persisted after deduplication; the third page and terminal behavior are not verified. |
| KissKH | Undocumented API `type` partitions 1/2/3 and `pageSize=20` | Bounded probes verified first/middle/last page numbering, short final pages and beyond-last clamping for all partitions; no intermediate pages were traversed. Title-level explicit detail type is required because partition and episode count are not reliable classification evidence. The public homepage returned HTTP 451, so ingestion stopped. The provider remains PARTIAL. |
| HydraHD | `movies/date/N` and `tv-shows/date/N` | Pages 1–2 of the movie partition were traversed sequentially; 48 unique source URLs were persisted. No total, terminal page, or series partition completion is verified. |
| CineBlog01 | `/film/` and `/serie-tv/`, `/page/N/` | Current configured listing returned HTTP 200, but `.run`'s permitted provider affiliation is unverified. AGCOM named `.cc` and `cb01new.ceo`, not `.run`; no relationship was established and no listing parser or ingestion run was made. |
| StreamingCommunity | Existing root, film, series, archive listing paths | Current configured root returned HTTP 200 branded HTML, but alias ownership is unverified. The fallback domain was not contacted and no records were ingested. |
| AltaDefinizione | Existing root, film, series, archive listing paths | Current configured root returned HTTP 200 branded HTML, but the domain's provider affiliation and relation to an AGCOM-blocked domain family are unverified. No records were ingested. |

`coverage_percent` stays `UNKNOWN` unless the fetched population and a reliable
provider total are known to be comparable. `items_unique` describes observed
catalogue identities, not TMDb links or confirmed playback offers. Legacy rows
continue to use provider/title/year/type. New observations with an explicit
provider ID use provider/provider-ID/content-type identity; movie and series
remain separate, and an existing legacy row is promoted in place when a native
ID later becomes available. The new partial unique constraints preserve both
identity forms without rewriting existing scraped records.

No full-traversal request estimate, run time, or peak-memory figure is
available: only small sequential batches were run, with no complete traversal.
One command invocation dispatches at most its configured budget (20 by default), plus no
hidden retries beyond `--retry-limit`; repeated invocations resume full mode.

The management page calls these records **discovered titles**. Its catalogue
sync badge is independent of subscription active/inactive state. Per-provider
status is stored in `CatalogueDiscoveryState`; last committed page progress is
saved after each parsed page. Failed pages do not delete existing records.
