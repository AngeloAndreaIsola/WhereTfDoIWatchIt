# Unofficial catalogue discovery operations

The command `discover_unofficial_catalogue` now persists raw listing observations
in `DiscoveredCatalogueRecord`. These records remain pending classification and
do not create canonical mappings, TMDb associations, or playback offers. Migration
`0053` adds conservative URL identity, original URL history, provenance state,
and access-policy state. Counts in the provider traversal sections below describe
the isolated staged inventory. A fresh live database audit and restore-first
validation are recorded in “Current database rollout validation” below.

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

**Catalogue coverage verdict: `CATALOGUE_STAGING_PARTIAL`.** The inventory is
durably exported and import/replay verified. Hydra traversal remains open because
its page links do not expose stable totals or a terminal boundary. The current
database rollout validation is reported separately below.

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

The tracked SQL backup
[`tracker_backup_20260926_140128.sql`](../../backups/tracker_backup_20260926_140128.sql)
has SHA-256 `693f7b40976b71cf7e19a4d01d69f9e5b8209f94083db5efac2b9cfa33a6d2a0`.
It is stale and incompatible with this branch: its tracker ledger ends at the
different `0033_fix_preference_json_fields` lineage, and its applied
`0013_merge_20260830_1219` lacks the `0011_availability_price_info_and_more`
dependency. Its clone has zero `ScrapedContentMapping` rows and four `Movie`
rows. It is not the post-incident dump identified by SHA
`9417b89cc9c47a4022bcbab9de0d24a3d761cacc599b7149e5ba0f982ffd96cf`.

### Current database rollout validation (2026-11-17)

The independent staging copy is outside every Git worktree and Docker volume:
`/Users/angeloandreaisola/Documents/catalogue-staging/WhereTfDoIWatchIt/`.
The compressed inventory SHA-256 is
`0c38ce93f7257c513a4d6760d4cc0d7a55718ca1ded9397db2a9b34ba17dfe02`; the
manifest SHA-256 is
`3fca6f67663b3ebc16d557c43192c20084aa0fcfceb495cae4e1165006670a47`. Source
and independent copies match both checksums; `cp -p` preserved their timestamps
and file modes. The original ignored worktree copy was retained.

The inspected Compose target is project `new-sources`, service/container
`db`/`new-sources-db-1`, database `tracker`, PostgreSQL 16.15, volume
`new-sources_pgdata`. It currently has 50 providers, 3,300
`ScrapedContentMapping` rows, zero `Movie` rows, zero discovery records, zero
`ProviderContent`, zero `ContentMapping`, and zero offers/evidence/prices. The
tracker migration ledger has 55 rows through
`0053_catalogue_url_identity_provenance`. Thus the earlier statement that the
live database remained at `0049` is superseded by this direct observation.
Read-only container logs show that the running backend applied migrations
`0050`–`0053` at 2026-11-17 13:41:19–13:41:20 UTC; a later startup reported no
migrations pending. This audit performed no live migration or write.

The backup discrepancy was file selection. The expected older checksum
`9417b89...ffd96cf` identifies
`/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-DBBackups/new-sources-tracker-postmigration-7617f73.dump`.
That file's checksum was reverified, its archive list was readable, and a fresh
restore confirmed 3,300 mappings, zero movies, and tracker migration `0049`.
That verified `0049` clone then applied migrations `0050`–`0053` successfully.
The failed zero-mapping restore instead used the tracked September SQL file
above (SHA `693f7b...d2a0`), which has a divergent migration history. No
evidence indicates a stale Compose volume was selected: the live container
labels, Compose project files, database identity, and mounted volume all match
the documented `new-sources` target. This is a wrong-file restore, not a wrong
Compose target.

A fresh direct `pg_dump --format=custom --no-owner --no-acl` from that
identified live container is stored outside Git and database volumes at
`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_20261117T143100Z.dump`.
SHA-256:
`fd04b86731fdc1419f5cc78ea1cab7186d494750137e8f4b67f9e038d557703e`.
The dump restored successfully with `pg_restore --exit-on-error` into
disposable PostgreSQL database `wtdwi_live_restore2`. Before and after staging
import, live and clone matched on all 3,300 mapping IDs, all mapping field
values (`a4dc1f57b43fa66cbeb48107af072154f2694637d23e4b6c0a1a34d67ec88079`),
provider rows (`cf6b4d7e137f9e6ea0dedc9880c490be337c22278c53de784190e9cc2d0d609c`),
and the complete migration history (`08833c313f96f379389c2071b5bd0a13049aa6b7f04b6b5793ac37e6689acd68`). Mapping ID hash:
`6307580580df5823adc49e2b91631216e90268b7d22c2cdad9982a4e7548a09a`.
Both schemas were dumped from the same PostgreSQL 16.15 version; the only
textual schema diff was pg_dump's random `\restrict` token and equivalent
formatting of one check constraint. Clone `migrate --check` found no pending
migrations through `0053`.

Import from the independent copy into the fresh live backup clone inserted
3,031 records (HydraHD 2,952, AnimeWorld 79) and restored two checkpoints.
Replay inserted/updated zero and reported 3,031 unchanged. The expected
`9417...` backup clone was separately migrated from `0049` through `0053` and
passed the same import/replay: 3,031 inserted on the first pass, then 3,031
unchanged and zero inserts/updates on replay. Both clones retain all 3,300
legacy mappings with IDs and field hashes matching live. The imported records
are all `PENDING`; source URL, identity URL, timestamps and original metadata
are carried in the inventory. Both clones still have zero movies, zero
`ProviderContent`, zero `ContentMapping`, and zero offers. No automatic
canonical mapping or playback offer was made.

**Database rollout verdict: `CATALOGUE_ROLLOUT_READY` for a separately
authorized rollout.** The following commands are the proposed rollout sequence;
they are documentation only and were not run against the live database.

1. Stop writes and confirm project/service/database/volume and migration head:

   ```sh
   docker compose ps db backend
   docker inspect new-sources-db-1 --format '{{ index .Config.Labels "com.docker.compose.project" }} {{ index .Config.Labels "com.docker.compose.service" }} {{ range .Mounts }}{{ .Name }} {{ end }}'
   docker exec new-sources-db-1 psql -X -U tracker -d tracker -tAc "SELECT current_database(), current_user; SELECT name FROM django_migrations WHERE app='tracker' ORDER BY name DESC LIMIT 1;"
   ```

2. Create and verify a fresh direct backup outside Git and database volumes;
   record mapping count and ID/field hashes and restore-test the exact file
   before proceeding:

   ```sh
   docker exec new-sources-db-1 sh -c 'pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$BACKUP"
   shasum -a 256 "$BACKUP"
   docker exec -i new-sources-db-test-1 pg_restore --list < "$BACKUP"
   ```

   Restore into a newly created disposable database and require counts,
   migration history, schema, IDs and mapping field hashes to match source.
   The audited baseline was 3,300 mappings, 50 providers, zero movies,
   zero discovery rows, and zero provider content/content mappings/offers.
   For the audit backup, the mapping ID SHA-256 was
   `6307580580df5823adc49e2b91631216e90268b7d22c2cdad9982a4e7548a09a` and the
   all-field mapping SHA-256 was
   `a4dc1f57b43fa66cbeb48107af072154f2694637d23e4b6c0a1a34d67ec88079`.
3. Check the staged gzip and manifest against the recorded SHA-256 values above.
   Apply migrations only after an explicit deployment authorization, with the
   guarded service and exact-target confirmation:

   ```sh
   docker compose --profile ops run --rm \
     -e EXPECTED_DATABASE_NAME=tracker -e EXPECTED_DATABASE_HOST=db \
     -e CONFIRM_MIGRATION_TARGET=tracker@db migrate
   ```

4. Import the inventory with the backend entrypoint explicitly replaced (so
   this one-shot operation cannot trigger application startup work):

   ```sh
   docker compose run --rm --entrypoint python \
     -v "$STAGING_DIR:/app/stage:ro" backend manage.py import_catalogue_inventory \
     --data /app/stage/catalogue-inventory-v1.jsonl.gz \
     --manifest /app/stage/catalogue-inventory-v1.manifest.json
   ```

5. Require tracker migration head `0053`, 3,031 discovered records (2,952
   HydraHD and 79 AnimeWorld), zero pending staged rows omitted, unchanged
   legacy mapping count/hash, and zero newly created mappings, canonical
   movies, `ProviderContent`, `ContentMapping`, or offers. Replay once and
   require zero inserts, updates, or deletes and 3,031 unchanged.
6. If any preflight, backup restore, migration, import, count, or hash check
   fails, stop and do not retry against the partially changed database. For an
   approved rollback, stop application writers and restore the verified
   pre-rollout backup into the documented Compose database/volume; verify its
   checksum and baseline counts/hashes before reopening writes. Do not reverse
   migrations or restore a different file by basename alone. Rollback commands
   for the audited custom-format backup are:

   ```sh
   docker compose stop backend
   shasum -a 256 "$BACKUP"
   docker exec -i new-sources-db-1 pg_restore --clean --if-exists \
     --exit-on-error --no-owner --no-privileges --dbname=tracker < "$BACKUP"
   docker exec new-sources-db-1 psql -X -U tracker -d tracker -c \
     "SELECT count(*) AS mappings FROM tracker_scrapedcontentmapping;"
   docker compose start backend
   ```

   Reopen writes only after checking the restored migration head and recorded
   mapping ID/field hashes against the backup manifest. The example rollback
   uses the fresh **pre-import** backup above; a backup made after import cannot
   roll back the imported inventory.

No live migration or import was performed in this task. The existing Compose
database was only read and backed up; its migration state was already `0053`.
Focused isolated tests passed (36 tests across staging, identity, discovery,
and guarded migration modules). The complete isolated backend suite passed
(3,264 tests in 125.0 seconds). `makemigrations --check --dry-run` reported no
drift, both restored clones passed `migrate --check` at `0053`, and
`git diff --check` passed.

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
