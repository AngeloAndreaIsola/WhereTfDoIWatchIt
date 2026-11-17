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

5. At the original 3,031-record snapshot, require tracker migration head
   `0053`, 3,031 discovered records (2,952 HydraHD and 79 AnimeWorld), zero
   pending staged rows omitted, unchanged
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

### Continued discovery and import preflight (`55571ca` follow-up)

Before collection, both expected artifacts matched their checksums and the
live Compose counts matched the verified backup: 3,300 legacy mappings, 50
providers, 0 discoveries, and tracker migration `0053`. The mapping field hash
remained `a4dc1f57b43fa66cbeb48107af072154f2694637d23e4b6c0a1a34d67ec88079`,
the ordered ID hash remained
`6307580580df5823adc49e2b91631216e90268b7d22c2cdad9982a4e7548a09a`, and
provider and migration hashes still match the backup restore. No new backup
was needed.

Four bounded Hydra invocations used a request budget of 8 each, zero retries,
and one-second delay. Each used 1 robots check plus 7 listing fetches. Pages
were resumed from the imported cursors, not sampled out of order:

| Provider/partition | Sequential pages in this follow-up | Listing records observed | New identities persisted | Duplicates | Requests | Latest checkpoint / navigation window |
|---|---:|---:|---:|---:|---:|---|
| HydraHD movies | 90–103 (two batches) | 672 | 336 | 0 | 16 | page 103; navigation max 106 |
| HydraHD TV | 35–48 (two batches) | 672 | 336 | 0 | 16 | page 48; navigation max 51 |
| AnimeWorld RSS | 1 snapshot | 15 | 0 new GUIDs; 15 metadata updates | 0 | 2 | RSS build `Tue, 17 Nov 2026 14:53:12 GMT` |

Hydra totals are not exposed. Its navigation maxima are rolling links, not
catalogue totals: movie navigation moved from 99 at page 96 to 106 at page
103; TV navigation moved from 44 at page 41 to 51 at page 48. Pages were
traversed sequentially with no observed repeated pages or duplicate URL
identities, but no terminal page was reached. Coverage remains partial and
pagination can shift while the site changes. The four Hydra batches dispatched
32 HTTP requests (28 listing pages and 4 robots checks); RSS dispatched 2 (1
feed and 1 robots check). No retries occurred.

The RSS snapshot retained the same 15 nonempty GUIDs and no new GUIDs. The
baseline had one additional URL-identified AnimeWorld record without a GUID;
that record was retained. All 15 observed GUID records changed only
`original_metadata.feed_title`. The other 64 AnimeWorld records remain earlier
A–Z observations marked restricted; no A–Z request was made. The RSS is a
recent-updates snapshot, not historical archive coverage.

The three other provider discovery commands returned `BLOCKED` before HTTP
dispatch, each with `requests_made=0`. Current candidate domains remain
unverified: CineBlog01 `.run` is not linked to the historical AGCOM domains;
AltaDefinizione `.ink` is not linked to the provider and its cited historical
AGCOM host is distinct; StreamingCommunity `.army` has no corroborated link.
Recent official AGCOM material names other StreamingCommunity domains
(`.kitchen`, `.marketing`, `.land`) and does not establish `.army` provenance
([AGCOM order](https://www.agcom.it/sites/default/files/provvedimenti/determina/2026/DDA_14822%20e%20DDA_14827_determina_24_26_dda.pdf)).
No candidate catalogue page was fetched or ingested. KissKH was not requested;
its HTTP 451 remains a hard stop and was not bypassed.

The durable export at this snapshot was **3,703 records**: 3,624 HydraHD and 79 AnimeWorld.
The 3,031-record source artifact was retained as
`catalogue-inventory-v1-baseline-3031.jsonl.gz` with its original manifest.
The current gzip SHA-256 is
`c2a38c523fb2884b2a3b5e261d261a32b3ef47143ab5e5505d9286b6e9e3cd28`; the
manifest SHA-256 is
`9316864a87ff2265ff82fc4e0f9b78d84ee6d2f623ac8e70f83a866c3b08516f`. A
record-by-record identity comparison found zero missing baseline identities,
672 new HydraHD URL identities, and 15 changed AnimeWorld metadata records.
There are 3,703 unique provider-scoped identity URLs and no duplicate identity
groups. All 3,703 discoveries remain pending classification. The directory is
outside Git worktrees and database volumes, mode `0700`; artifacts are mode
`0600`.

The current backup was restored a second time into isolated database
`wtdwi_final_import_verify`. Importing the new export inserted 3,703 records
and restored five provider states (the two discovery checkpoints and three
provenance-blocked states); replay inserted/updated zero and reported 3,703
unchanged. The clone retained 3,300 legacy mappings with matching ID and field
hashes, zero movies, zero `ProviderContent`, zero `ContentMapping`, and zero
offers. The importer's single transaction makes interruption safe: a failed
transaction rolls back and the complete command can be replayed idempotently.

The exact live-import command is prepared but **not run**. It now imports all
3,703 current staged records, including subsequent discoveries:

```sh
STAGING_DIR=/Users/angeloandreaisola/Documents/catalogue-staging/WhereTfDoIWatchIt
docker compose run --rm --no-deps --entrypoint python \
  -v "$STAGING_DIR:/app/stage:ro" backend \
  manage.py import_catalogue_inventory \
  --data /app/stage/catalogue-inventory-v1.jsonl.gz \
  --manifest /app/stage/catalogue-inventory-v1.manifest.json
```

Preconditions are: separate explicit authorization for the live import; live
database still identified as project `new-sources`, DB `tracker`, migration
`0053`; fresh backup checksum and restore test verified; live legacy mapping
count/hash equal the backup; staging checksums and provider counts equal this
manifest; and application writes paused during the operation. At the time of
this note the command pointed to a 4,759-row inventory; the current 4,903-row
inventory is documented in the final pagination follow-up below. For the
current file, verify after import: 4,903 discoveries (HydraHD 4,824;
AnimeWorld 79), 4,903 pending,
unchanged legacy mapping IDs/field hash, zero new canonical mappings or movies,
zero offers, and five restored provider states. Replay and require 4,903
unchanged with zero inserts/updates. Live import remains pending authorization.

### Migration-startup incident follow-up

The backend container that applied `0050`–`0053` was created from image
`sha256:80079c2d…` on 2026-11-16 07:03 UTC. Its baked `/entrypoint.sh` contained
`python manage.py migrate`; Compose selected that image entrypoint with command
`python manage.py runserver`. The source safeguard was committed later at
`7d283d2` (2026-11-17 11:19 UTC), but the running container was not rebuilt.
Its logs show the migrations applied at 13:41:19–13:41:20 UTC. No
`--profile ops` guarded migration invocation or explicit target confirmation
appears in the available shell history. The migration service was not used.
The Docker event buffer did not retain the initiating start command; the
available history shows `docker builder prune -af` at 13:41 UTC but does not
establish that as the restart cause. Intentional authorization cannot be
verified from the available evidence.

To fail closed on stale images, `docker-compose.yml` now invokes
`/bin/sh /runtime-guard-entrypoint.sh` and bind-mounts the checked-out
`backend/entrypoint.sh` read-only at that path. A stale image lacking the
mounted runtime path cannot start; when mounted, the host script runs
`migrate --check`, never `migrate`. A regression test verifies the Compose
override and mount. The backend was recreated with the updated Compose
configuration, using `BACKEND_PORT=8000` because the root Compose project owns
port 8002. Initial attempts failed closed on the occupied port and then the
source script's missing executable bit; Compose now invokes it through `/bin/sh`.
The current container is running with the read-only host guard mounted. Its
startup completed without applying migrations; the live DB remains at `0053`
with 3,300 mappings and zero discovery/offer rows. No live migration or import
was performed.
After this repair, focused tests passed (37/37) and the full isolated backend
suite passed (3,265 tests in 128.4 seconds). The restored clone passed
`migrate --check`; `makemigrations --check --dry-run` and `git diff --check`
also passed.

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

## Final import preflight and continued discovery (2026-11-17)

The live import remains **pending explicit authorization**. No migration or
catalogue import was run against the live database. Read-only inspection
confirmed Compose project `new-sources`, database `tracker`, tracker head
`0053_catalogue_url_identity_provenance`, 3,300 scraped mappings, 50 providers,
and zero discovery records, movies, provider content, content mappings, offers,
offer evidence, or offer prices. The running backend uses the mounted runtime
guard, which runs `migrate --check` before startup and never applies migrations.

The expected backup
`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_20261117T143100Z.dump`
still matches SHA-256
`fd04b86731fdc1419f5cc78ea1cab7186d494750137e8f4b67f9e038d557703e` and was
restored with `pg_restore --exit-on-error` into a fresh disposable PostgreSQL
database. The clone retained the audited baseline (3,300 mappings; 50
providers; tracker `0053`; zero discoveries and offers). Since the live counts
still match that restore, no new backup was necessary.

### Reconciliation of the 3,703-record snapshot

The earlier follow-up table reported 672 new identities in each HydraHD
partition, which conflicts with its 3,703-row export. A durable identity
comparison between the preserved 3,031-row snapshot and the 3,703-row snapshot
shows **672 new HydraHD identities total: 336 movies and 336 TV**. The prior
672-per-partition values are listing observations, not new unique identities.
All 3,031 prior identities were retained, with no duplicate identity groups;
15 AnimeWorld RSS records had metadata changes and no new GUIDs. The earlier
table now reflects the corrected per-partition identity counts.

### Isolated HydraHD continuation

The verified pre-import backup was restored into disposable database
`wtdwi_discovery_final_20261117` on the `db-test` service. The 3,703-row
inventory imported there (3,703 inserted, five provider states restored), and
the live database was not used for discovery. Eight sequential HydraHD runs
then advanced each partition from the imported page 103 / page 48 checkpoints:

| Partition | Pages scanned | Dispatches | New URLs | Repeated URLs | Updated | Latest page | Navigation max |
|---|---:|---:|---:|---:|---:|---:|---:|
| Movies | 104–125 | 26 | 528 | 0 | 0 | 125 | 128 |
| TV shows | 49–70 | 26 | 528 | 0 | 0 | 70 | 73 |

Six runs used budget 8 (one robots request and seven listings each); two
next-page probes used budget 2 (one robots request and one listing each). All
52 dispatched requests succeeded with zero retries. The 44 listing pages were
sequential with no gaps; they added 1,056 unique URLs. Every run reported zero
duplicates and zero updates. Navigation maxima moved from 106 / 51 at the
imported checkpoints to 128 / 73, including after the one-page probes. The
listing route therefore exposes rolling navigation links, not a stable
catalogue total. No terminal page or fixed total was observed; coverage stays
partial.

AnimeWorld's permitted RSS workflow ran once in the same clone: 2 dispatches,
15 records seen and unchanged, zero new GUIDs, and the checkpoint recorded the
feed's `Tue, 17 Nov 2026 15:20:06 GMT` build time. Its report remains partial
because RSS is a recent-updates snapshot rather than a full archive.

The latest durable inventory contains **4,759 records** (4,680 HydraHD, 79
AnimeWorld), all pending classification. It retains all 3,703 previous
identities and adds 1,056 new HydraHD identities (528 per partition). No
canonical mappings or offers were created. The compressed inventory SHA-256 is
`ce97c8a486f96b17ac2463a0ff0abb0b31be831ee5856ab46f013e6c4b910cc4`; the
manifest SHA-256 is
`57d2d0b23e6c552a876fe0522ea2cecd08ed0c9e40264dfd5062837eb953d56e`. Manifest
checkpoints are movies page 125 / navigation max 128 and TV page 70 / max 73.
An independently checksummed 3,703-record recovery snapshot is retained under
`snapshots/20261117T1521Z-pre-hydra-continuation-3703/` beside the 3,031-row
baseline snapshot. The current staging directory remains outside the worktree,
mode `0700`, with data and manifest mode `0600`.

The 4,759-row export at the time of this note replayed in the isolated clone
with 4,759 unchanged, zero inserted, and zero updated; the clone still had
3,300 scraped mappings and zero movies, provider content, content mappings,
or offers. The later 4,903-row export and its checksums are in the final
pagination follow-up below. The live target still reports its original
zero-discovery baseline. Live and clone mapping IDs
and fields match under the same deterministic row serialization: ID SHA-256
`1d6225cafcab013c77e85ecdfb6a14e530368600592353e8da75aec7ae4021a` and
all-field SHA-256
`1d52c51d7d0e2abf001347c4e852b19ead83329e27bba5a0099bba58a87cf163`. The
live import command continues to require separate explicit authorization.

For provider provenance, official AGCOM records identify CineBlog01-related
`.cc` / `.ceo` domains and StreamingCommunity `streamingcommunityz` `.kitchen`,
`.marketing`, and `.land` domains; they do not establish the configured `.run`
or `.army` candidates. Search results for AltaDefinizione `.al` self-assert
official status but provide no independent linkage to the configured `.ink`
candidate or the historically cited host. These providers remain blocked with
zero dispatches; no unverified catalogue page was fetched. KissKH remains
untouched after HTTP 451 and no restriction was bypassed.

Focused tests passed (37 tests across staging, identity, discovery, and guarded
migration). The complete isolated backend suite passed (3,265 tests in 127.3
seconds). No UI files changed, so frontend tests were not relevant. The staged
import is ready for its existing guarded process; UI visibility checks are
pending the separately authorized live import.

## HydraHD pagination and quality investigation (2026-11-17)

### Pagination evidence

HydraHD's `robots.txt` allows `/` for the generic user agent and declares
`Content-Signal: search=yes, ai-train=no, use=reference`. Inspection stayed on
the existing `hydrahd.gd` listing and its linked title pages. Fetching listing
routes returns HTML with a self-canonical URL, 24 `a.hthis` title links, and
literal pagination anchors in the response body. This is a real HTML listing
route with a sequential `Next` link; the observed anchors are not an
interpretation of a numeric total. The source is a sliding window: page 125
links to 122–128 and its `Next` target is 126; page 126 links to 123–129 and
`Next` is 127. TV page 70 links to 67–73 and `Next` is 71; page 71 links to
68–74 and `Next` is 72. The active page is rendered without a link.

No sampled HTML exposed a last-page link, fixed page total, or catalogue
total. The former `navigation_max` value is the largest visible window link.
On later pages it was current page +3 (for example, 125→128 and 126→129);
the first two pages showed the initial range through page 7. It is not a total
and must not drive completion. No finite archive bound was observed. The route
is also not a parser hallucination: the target links,
canonical URLs, and content were present in the returned HTML. Client-side
code may still update the page, but the sampled pagination itself was already
present in the response body.

No terminal condition was observed. The discovery service now keeps HydraHD
`PARTIAL` at the previous committed page when a later page returns HTTP 404,
and reports a successful empty listing as an unverified terminal candidate
without advancing the checkpoint. Neither a fetch failure nor a parser-empty
response can mark HydraHD complete.

The response fingerprint below is SHA-256 of the fetched HTML body. All
responses were HTTP 200 with the matching canonical URL and 24 distinct title
URLs. First/last rows preserve returned listing order and show only source
release years; no exact movie/show publication date was exposed on these
listing cards.

| Partition / page | First → last listing title (year) | `Next` target | HTML body SHA-256 |
|---|---|---:|---|
| Movies 1 | Kalocsa (2026) → National Theatre at Home: The Little Big Things (2024) | 2 | `47473f6c974273868fca4372c18b6472a92befea73cab694c6e56b506d3a11fc` |
| Movies 2 | Janelle James: How Romantic (2026) → Heartland (2026) | 3 | `3bf44feac2f944688d2169ca200b57d67356e6327016814bc12f09c0b2c37cfc` |
| Movies 124 | Cursed Waters (2026) → Supertramp: In Performance (2006) | 125 | `32ad8c7c15e799c74497275c5b76626ce284187fe4f30951f9dde9148e47da8a` |
| Movies 125 | Legends of Flight (2010) → A Doctor to Die For: The Sarah Harris Story (2026) | 126 | `0ba47a2fbddcf3e326bf1ba68bcb9ea28e3a480137bdba7c1aba62cceaa69dd1` |
| Movies 126 | Bad Kitties (2013) → The Devil’s Trap (2024) | 127 | `1b789df051968b691019f4a542b6fd2e3f9bf5afb5ba08f919b7ba1b3283185e` |
| TV 1 | Home and Away (1988) → WWE Raw (1993) | 2 | `c7fced7d5d89bb98dbc9b656f59b2866157f9572e364ba4c06c093ec8ed624c9` |
| TV 2 | Harlan Coben’s Final Twist (2026) → 60 Minutes (1968) | 3 | `a2a0591504af1bf81c8b3130a9208a135378a45c09b86d6f4722d7799aa2d458` |
| TV 70 | Carrie (2026) → A Tale of Two Cities (2026) | 71 | `8cfabd711c762f7acf94bff65b374d0cb54a6bfbeb4d0742abe101559052f469` |
| TV 71 | Outside (2026) → GeGeGe no Kitaro (2018) | 72 | `6261ac10493fe81d5ba7fcc2d8902d04d1e7316b116988d19275430d6e62730a` |

Adjacent sampled pages had no URL overlap. Movie source IDs decreased in
listing order across pages 1–2 and 124–126; that ordering follows source ID,
not the displayed release year. TV links also differed between the adjacent
samples, but TV URLs expose no comparable numeric ID order. This is evidence
of sequential page content, not proof that the site cannot reorder or insert
records while a long traversal is in progress.

### Stratified detail-page sample

The audit sampled 24 staged records, four at spread positions from each of
movies pages 3, 62, and 125 and TV pages 1, 35, and 70. Results:

| Check | Passed | Sample |
|---|---:|---:|
| Detail URL returned HTTP 200 without redirect | 24/24 (100%) | 12 movies, 12 TV |
| Canonical detail URL equals staged source URL | 24/24 (100%) | 12 movies, 12 TV |
| Detail heading matches staged title | 24/24 (100%) | 12 movies, 12 TV |
| URL path matches expected partition (`/movie/` or `/watchseries/`) | 24/24 (100%) | 12 movies, 12 TV |
| Distinct canonical detail URLs | 24/24 (100%) | 12 movies, 12 TV |
| Exact release date stored for the sampled record | 0/24 (0%) | Inventory carries a year only |

The detail pages labelled movie dates as `Year` and show dates as `Year
Started`; episode air dates on a series page are not the series release date.
The full staged HydraHD inventory has 4,824 unique provider-scoped URLs,
4,824 nonblank titles, 4,824 years, and no wrong-partition path. The sample
does not establish title accuracy or URL validity for every record.

There are three normalized title/year collision groups in each partition
(six records per partition). Examples include `The Nest` (2025), `Junk`
(2024), and `Bad Apples` (2026) among movies; and `Drag Race Spain` (2021),
`Who Killed Our Daughter?` (2025), and `Temptation Island` (2019) among TV.
These have distinct source URLs and are retained as separate identities; the
available metadata cannot determine whether each pair is a duplicate release,
regional edition, or distinct catalogue entry. Zero staged URLs were recorded
on multiple page numbers.

### Bounded continuation and current staging

Following the actual `Next` links from movie 125 and TV 70 reached pages 126
and 71, each with 24 distinct rows and a further sequential `Next` link. The
existing checkpointed discovery command then ran one bounded batch per
partition, budget 4, zero retries, one-second delay: three listing pages plus
one robots request each. It scanned movies 126–128 and TV 71–73, inserting 72
new URLs per partition, with zero repeated URLs, updates, or errors. The latest
checkpoints are movies page 128 / navigation maximum 131 and TV page 73 / max
76. Both remain `PARTIAL`; neither has terminal-page evidence.

The stored movie page metadata is contiguous for pages 3–128, 24 records each;
its 48 legacy observations without a page number match all 48 URLs currently
shown on pages 1–2. TV page metadata is contiguous for pages 1–73, 24 each.
Thus the staged records cover the observed pages 1–128 and 1–73 without a
recorded gap or same-URL page overlap. This does not rule out records being
skipped if the provider changes ordering between page requests, and no claim
of complete catalogue coverage is made.

The durable export now contains **4,903 pending records**: 4,824 HydraHD
(3,072 movies, 1,752 TV) and 79 AnimeWorld. It retains all 4,759 previous
identities with no changes to their saved URLs or metadata, and adds 144 new
HydraHD identities. The compressed file SHA-256 is
`17d0cdc6dfb62eebfcdf2b57a16c702ac8eb2cc81fbfdaf41f5472361f683764`; manifest
SHA-256 is
`ab35047139e9874f06bb2b178703a5ce02ae7b883f9416d39a0af1063b088c8e`. A
checksummed 4,759-record recovery snapshot is retained in
`snapshots/20261117T1541Z-pre-discovery-4759/`.

Replay of this 4,903-record export in the disposable restore clone returned
4,903 unchanged, zero inserted, zero updated, and restored all five provider
states. The live database still has migration `0053`, 3,300 scraped mappings,
and zero discovery rows or offers. The backup SHA-256 remains
`fd04b86731fdc1419f5cc78ea1cab7186d494750137e8f4b67f9e038d557703e`; it has
already been restore-tested. The current staged inventory is ready for the
existing guarded import, but the live import remains pending explicit
authorization. No application UI check is possible until that import is
authorized and completed.

Focused discovery tests passed (18 tests), including HTTP 404 and empty-page
checkpoint preservation. The complete isolated backend suite passed (3,267
tests). No frontend files changed. The live database was only read. AnimeWorld
RSS state and the prior KissKH and provider-provenance restrictions remain
unchanged.

## Provider inventory visibility and provenance follow-up (2026-11-17)

### Read-only rollout preflight

The external durable inventory remains at 4,903 pending records: 4,824 HydraHD
and 79 AnimeWorld. Its gzip SHA-256 remains
`17d0cdc6dfb62eebfcdf2b57a16c702ac8eb2cc81fbfdaf41f5472361f683764`; the
manifest SHA-256 remains
`ab35047139e9874f06bb2b178703a5ce02ae7b883f9416d39a0af1063b088c8e`. The
pre-import backup is still present at
`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_20261117T143100Z.dump`
with SHA-256
`fd04b86731fdc1419f5cc78ea1cab7186d494750137e8f4b67f9e038d557703e`; its
custom archive header and table of contents were readable in the isolated
`db-test` container. This exact backup had already passed a full disposable
restore test in the previous rollout audit.

Read-only checks again identified Compose project `new-sources`, database
`tracker`, tracker migration `0053_catalogue_url_identity_provenance`, and
3,300 existing `ScrapedContentMapping` rows. Discovery records, canonical
movies, `ProviderContent`, `ContentMapping`, and offers all remain at zero.
No import, migration, or other live write was performed. The live import still
requires separate explicit authorization.

### Provider-management cards

The existing `/api/providers/` response and subscriptions provider cards now
include batched discovery state separately from canonical availability:
inventory total, pending-classification count, last committed checkpoint
timestamp, scanned page by partition, coverage status, and the current
discovery error/restriction. Pending discoveries do not increase the
available-title count and do not create canonical content or offers. The
pending-count card treatment is labeled separately from availability.

This is a presentation and API check against isolated fixtures. The live
inventory remains empty pending authorization, so live card counts cannot yet
be verified. Focused backend provider-card tests passed (17); the ProviderGrid
component tests passed (11). The complete isolated backend suite passed
(3,267 tests in 129.2 seconds); the frontend suite passed (275 tests across 33
files).

### Other-provider source provenance

No suitable current source was established for ingestion:

- **CineBlog01:** AGCOM's 2026 record describes a redirect from `.ceo` to
  `.cc`; it does not validate the configured `.run` candidate or establish a
  permitted metadata interface. [AGCOM 47/26/DDA](https://www.agcom.it/sites/default/files/provvedimenti/determina/2026/DDA_14849_determina_47_26_dda.pdf).
- **AltaDefinizione:** AGCOM's 2018 decision orders blocking access to the
  historical `.ink` host. It is not evidence that a present-day alternate
  domain is the same operator or offers a permitted feed. [AGCOM 145/18/DCA](https://www.agcom.it/provvedimenti/determina-145-18-dca).
- **StreamingCommunity:** AGCOM records describe a domain-change portal used
  to reach a site subject to blocking orders. They do not verify the `.army`
  candidate; no alternate domain was used to work around access restrictions.
  [AGCOM proceeding 1513/DDA/GDS](https://www.agcom.it/sites/default/files/migration/comunicazione/Comunicazione%2030-09-2021%201633447127913.pdf).

No candidate catalogue page was fetched and no records were added for these
providers. Their discovery states remain `BLOCKED` with zero dispatches. No
new AnimeWorld RSS item was fetched in this follow-up; its permitted RSS
checkpoint remains resumable. KissKH remains paused at HTTP 451.

## Authorized live import (2026-11-17)

The user explicitly authorized the one-time import to Compose project
`new-sources`, database `tracker`. Before import, the exact target was
confirmed as `tracker@db`, the tracker migration head was
`0053_catalogue_url_identity_provenance`, and the database had 3,300 scraped
mappings and zero discovery records, movies, provider content, content
mappings, or offers. The running backend entrypoint was
`/runtime-guard-entrypoint.sh`; it runs `migrate --check` and never applies
migrations. That check passed before and after import. No migration was run.

The staging gzip and manifest matched their expected hashes. A fresh direct
pre-import backup was saved outside Git and database volumes at
`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_preimport_20261117T162513Z.dump`;
SHA-256 `e41b17529d7bfad63233d459da5ee5b0dab60dad3d00f5f0ebaff5cff3d03a6a`.
It was restored with `pg_restore --exit-on-error` into a disposable PostgreSQL
database. The restored clone matched migration `0053`, all baseline counts,
and the live mapping export byte-for-byte (SHA-256
`47eea20212bf43be6e60acff43ea48bd1d7c58bc048e4718b448886d00985845`).
The backend writer was paused while the fresh backup and import ran, then
restarted through the migration-check guard.

The existing `import_catalogue_inventory` management command inserted 4,903
records: 4,824 HydraHD and 79 AnimeWorld, and restored five provider discovery
states. It reported zero updates. The live database now contains 4,903
discoveries, all 4,903 pending classification; no pending row was omitted.
The staged and live identity/content tuples match exactly: 4,903 unique rows,
zero missing, zero unexpected. Provider and partition counts are:

| Provider | Partition | Pending records |
|---|---|---:|
| HydraHD | Movies | 3,072 |
| HydraHD | TV shows | 1,752 |
| AnimeWorld | RSS snapshot | 79 |

Import replay reported 4,903 unchanged, zero inserted, zero updated, and five
checkpoints restored. The original 3,300 scraped mappings remain byte-for-byte
unchanged from the pre-import export (SHA-256 above). The provider count
remains 50. Canonical movies, `ProviderContent`, `ContentMapping`, and offers
remain zero. No TMDb associations or playback offers were created.

Restored checkpoints retain HydraHD `PARTIAL` at movies page 128 and TV page
73 (rolling navigation values 131 and 76), and AnimeWorld `PARTIAL` at RSS
snapshot page 1. CineBlog01, AltaDefinizione, and StreamingCommunity remain
`BLOCKED` on provenance with no requests dispatched. KissKH remains paused.

The running `/api/providers/` response now reports pending counts independently
of existing active scraped mappings: HydraHD 4,824 pending and 480 active
mappings; AnimeWorld 79 pending and 174 active mappings. The provider cards
label these separately as “Pending classification” and “active mappings”; the
20 focused ProviderGrid and subscriptions tests passed after that label change.
No availability offers were created. The inventory is live and still pending
classification; catalogue coverage remains partial.

## Resumable discovery operations (2026-11-17)

Discovery now has independent `historical` and `incremental` cursors. The
legacy CLI mode `full` remains an alias for historical backfill. Historical
runs resume after their last committed page and keep a per-page checkpoint;
incremental runs rescan a configurable overlap window (default three pages)
and store page fingerprints and identities separately. Completing the latest
window does not change historical progress or claim catalogue completeness.
AnimeWorld remains an incremental RSS snapshot only and reports
`official_rss_snapshot_not_full_archive`; it does not claim historical RSS
coverage.

The existing `refresh_worker` catalogue lane can invoke the lightweight
scheduler using `UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES`. The variable is
empty by default, so recurring discovery is disabled. When explicitly
configured, each provider and mode can be enabled independently with separate
intervals, request budgets, delays, retry limits and overlap sizes. The
scheduler honors stored cooldowns, uses the existing provider-scoped run lock,
and isolates a provider/mode failure from other scheduled runs. Long
`Retry-After` values persist a cooldown instead of keeping a worker sleeping.
No worker schedule was enabled as part of this change.

### Bounded live runs

The live baseline before these runs was 4,903 pending discoveries, 3,300
legacy scraped mappings, zero canonical movies, zero `ProviderContent`, zero
`ContentMapping`, and zero offers. No migration was run. Using the existing
guarded discovery command against the running backend, the following
sequential requests were made:

| Provider/mode | Pages visited | Requests dispatched | Inserted | Updated | Existing identities (unchanged) | Result |
|---|---|---:|---:|---:|---:|---|
| HydraHD historical, movies | 129–132 | 5 | 96 | 0 | 0 | PARTIAL; no gaps or terminal evidence |
| HydraHD historical, TV | 74–77 | 5 | 96 | 0 | 0 | PARTIAL; no gaps or terminal evidence |
| HydraHD incremental, movies | 1–2 | 3 | 0 | 48 | 48 (0) | Two-page overlap complete |
| HydraHD incremental, TV | 1–2 | 3 | 0 | 48 | 48 (0) | Two-page overlap complete |
| AnimeWorld RSS incremental | snapshot 1 | 2 | 0 | 0 | 15 (15) | PARTIAL RSS snapshot |

Request counts include the one `robots.txt` check for each run. The 96
incremental updates reflect differences between currently listed records and
their stored discovery metadata; no new identities resulted from refresh.
The feed replay was idempotent. The live inventory is now **5,095 pending
records**: 5,016 HydraHD and 79 AnimeWorld. The historical HydraHD cursor is
Movies 132 and TV 77; the separate incremental cursor has a completed
two-page window for both partitions. AnimeWorld's incremental RSS checkpoint
is snapshot page 1. All provider states remain `PARTIAL`; HydraHD navigation
maxima moved to 135 (movies) and 80 (TV), which is not terminal evidence.

Read-only post-run checks found the original **3,300 scraped mappings** still
present, zero canonical movies, zero `ProviderContent`, zero
`ContentMapping`, and zero offers. All 5,095 discovery rows are still pending
classification. `/api/providers/` exposes the separate discovery count,
pending count, mode checkpoints, last sync, and coverage/error state; the
provider cards keep pending records distinct from active mappings and
availability. The public API view showed HydraHD at 5,016 pending and
AnimeWorld at 79 pending. No TMDb association or availability offer was
created.

The scheduler and independent cursor behavior are covered by focused tests for
resumption, interruption, overlap, changing page contents, replay, lock
contention, cooldown, and provider failure isolation. The full isolated
backend suite passed (3,279 tests); the frontend suite passed (305 tests in 37
files). `makemigrations --check --dry-run` reported no model changes. The
catalogue remains partial because HydraHD has no verified fixed total or
terminal-page signal. No provider crawl is scheduled automatically.

## Reconciliation readiness follow-up (2026-11-17)

### Reconciliation implementation and isolated dry run

Added `reconcile_unofficial_catalogue`, a read-only-by-default proposal
command. Classification requires source-specific evidence: HydraHD's
`movies`/`tv-shows` partition, or an explicit `anime_type` in the first-party
AnimeWorld RSS snapshot. The 64 legacy AnimeWorld A–Z rows remain ambiguous;
their parser's `listing_content_type_hint` is not treated as proof. Titles are
retained as observed, years are accepted only from an explicit metadata field,
and contradictory type hints block classification. The command reports the
evidence and confidence per record. It proposes canonical matches only for a
type-compatible exact title+year match or an explicit matching TMDb identifier;
missing-year title matches and type/year conflicts require review. No TMDb
search was made. Provider-native IDs, URL-derived identities, and original
URLs are retained. Same-title collisions are reported without merging.

`--apply` is an explicit second step: it can persist confident classifications
as `ProviderContent` and create only high-confidence canonical mappings when a
matching `Source` row exists. It does not create `Offer` rows. The command was
not run with `--apply` against either the live database or the isolated copy.
Migration `0054` adds `last_seen_at`, separate from `updated_at`; it was
applied only to the disposable reconciliation copy during validation.
For pre-migration rows, `last_seen_at` is initialized from `updated_at`, the
closest persisted observation timestamp available; it is not a reconstructed
per-request history.

The representative run used a full read-only dry run of the actual 5,095-row
live inventory after a read-only dump was restored into the disposable
`reconciliation_sample` database. No live write or public detail-page request
was made. Results:

| Metric | Result |
|---|---:|
| Records evaluated | 5,095 |
| Titles extracted | 5,095 |
| Explicit years extracted | 5,016; 79 missing (AnimeWorld RSS records) |
| Confident movies | 3,169 (HydraHD 3,168; AnimeWorld RSS 1) |
| Confident series | 1,862 (HydraHD 1,848; AnimeWorld RSS 14) |
| Ambiguous classification | 64 (all restricted legacy AnimeWorld A–Z rows) |
| High-confidence canonical matches | 0 |
| Possible canonical matches for review | 0 |
| Classified records unmatched to canonical titles | 5,031 |
| Duplicate provider identity collisions | 0 (also database-constrained) |
| Same-title candidate groups | 6; retained separately for review |
| Detected evidence conflicts / false-positive findings | 0 |
| Offers created | 0 |

The dry run also compared discoveries to the 3,300 legacy
`ScrapedContentMapping` rows as source evidence, without using them as canonical
matches. All 3,300 legacy rows have `movie_id=NULL`, `is_confirmed=false`, and
no provider-native ID. The report found 456 discovered records with an exact
legacy source-URL match, 481 with exact title/year/type evidence, and 25
title-only or conflicting candidates requiring review. These cross-table
overlaps remain distinct source observations; no rows were merged. HydraHD's
480 legacy mappings substantially overlap the current catalogue; AnimeWorld's
174 legacy mappings produce 16 discovered-record title-only/review candidates
and no exact source-URL matches. The review candidates split 9 HydraHD and 16
AnimeWorld records. Legacy overlap counts do not increase canonical-match
confidence.

There are no canonical `Movie` rows in the current database, so the run cannot
measure false-positive rates for canonical matches. The six same-title groups
are not asserted to be duplicate content; none were merged. Matching will
become measurable after canonical titles or trusted external identifiers are
present. No detail-page enrichment was needed to classify the permitted
HydraHD partitions or the 15 RSS records. The 64 ambiguous rows are restricted
observations and were not fetched for enrichment.

Example command (defaults to dry run):

```sh
python manage.py reconcile_unofficial_catalogue --provider hydrahd --provider animeworld --summary-only
```

### Analysis of the 96 incremental HydraHD updates

Compared the pre-refresh 4,903-row durable inventory with the isolated copy of
the current 5,095-row database, keyed by provider identity. Of 4,903 shared
records, exactly 96 had different stored observations; all 96 were HydraHD.
For those records, title, year, source URL, identity URL, media-type hint, and
canonical classification were unchanged. The differences were source-page
metadata only:

- 96 acquired `listing_observations` and `source_urls_seen`.
- 48 acquired `listing_page` and `observed_source_url`.
- 48 acquired explicit HydraHD provenance/access-policy metadata.
- 0 title/year/media-type changes, parser serialization-only changes, or
  changes to the original source URL were found.

The separate 96 new historical rows from that run were not counted as updates.
Discovery persistence now reports semantic changes as `items_updated`, exact
semantic replays as `items_unchanged`, and source-page-only metadata changes in
`items_source_metadata_updated`. Source-page-only changes also count as
semantically unchanged. Exact replays advance `last_seen_at` without changing
`updated_at`; metadata updates still change `updated_at` as a row-mutation
timestamp. Focused tests cover page movement, URL tracking variants, native-ID
promotion, and exact replay.

### Scheduler validation and conservative recommendation

The scheduler remains disabled unless
`UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES` is explicitly configured. Existing
code and isolated tests verify independent historical/incremental cursors and
due times, per-mode request budgets, provider-scoped PostgreSQL advisory locks,
checkpoint resume, bounded per-dispatch requests, worker failure isolation,
and persisted cooldowns for long `Retry-After` values. Lock release is in a
`finally` block and PostgreSQL releases a session lock if its connection dies.
Each successful page is committed before the next request; interruption keeps
the last committed checkpoint. Migration writes remain separate behind the
guarded migration entrypoint. Scheduler reports and errors are logged by the
catalogue worker. No schedule was enabled.

The worker has no custom SIGTERM handler. A graceful interrupt during a
collector marks the run partial and retains its committed checkpoint. A hard
process stop releases the database lock when its connection closes; the last
committed checkpoint remains resumable, although state can remain marked
`RUNNING` until the next scheduled attempt. Keep initial schedules sparse so
operators can inspect diagnostics before increasing cadence.

Recommended starting point after separate authorization (budgets include
robots and other HTTP dispatches):

```json
{
  "enabled": true,
  "providers": {
    "hydrahd": {
      "enabled": true,
      "historical": {"enabled": true, "interval_seconds": 86400, "request_budget": 5, "delay_seconds": 2, "retry_limit": 0},
      "incremental": {"enabled": true, "interval_seconds": 43200, "request_budget": 8, "delay_seconds": 2, "retry_limit": 0, "overlap_pages": 3}
    },
    "animeworld": {
      "enabled": true,
      "incremental": {"enabled": true, "interval_seconds": 86400, "request_budget": 2, "delay_seconds": 2, "retry_limit": 0}
    }
  }
}
```

Hydra historical uses a small daily budget while it remains open-ended;
incremental refresh revisits a three-page overlap twice daily. AnimeWorld reads
only its first-party RSS snapshot once daily. These settings are a proposal,
not active configuration. Provider runs share a lock across modes, so a
historical run cannot overlap an incremental run for the same provider.

### Remaining provider feasibility

| Provider | Status | Blocker | Evidence that would permit resuming |
|---|---|---|---|
| CineBlog01 | `PROVENANCE_UNVERIFIED` | Candidate `.run` is not linked to historical domains in the cited AGCOM records; search results surfaced changing third-party “new address” claims, which do not establish attribution. | A first-party, independently corroborated redirect/domain statement plus a permitted catalogue feed/API or explicit collection permission. |
| AltaDefinizione | `PROVENANCE_UNVERIFIED` | Existing AGCOM evidence concerns other hostnames, not the candidate `.ink`; no attributable permitted feed/API found. | Independent provider-domain linkage and a permitted catalogue metadata source or explicit permission. |
| StreamingCommunity | `PROVENANCE_UNVERIFIED` | Candidate `.army` lacks independent linkage; public third-party code/wiki references do not establish operator ownership or permission. | An independently verified provider domain and a documented permitted metadata endpoint/feed. |
| KissKH | Access restricted (`451` remains a hard stop) | Search indexing exposes title metadata on `kisskh.do`, but no first-party feed/API permission or reliable independent domain attribution was found; changing community links are insufficient. No request was retried. | A provider-published public catalogue API/feed with use terms, or written permission identifying the exact hostname and allowed request pattern. |

Web searches did not identify a reliably attributable, permitted catalogue
metadata source for these four providers. Search results for KissKH included
a publicly indexed detail page ([example](https://kisskh.do/Drama/Revamp--The-Undead-Story?id=11278)),
but did not surface collection terms or establish that another host is an
authorized alias. AGCOM's [2023 decision on CineBlog01](https://www.agcom.it/sites/default/files/migration/determina/Determina%20435-DDA-23.pdf)
records historical domain changes; it does not link the current `.run`
candidate. These search results do not justify trying alternate hosts. No
HTTP 403/451 was retried and no unverified host was fetched.

### Integration and validation

The management UI already separates discovery counts, pending
classification, canonical mappings, and verified offers; no UI change was
needed. No language extraction, playback offer, or automated availability
association was added. Focused backend tests and migration drift checks passed
before the complete suite. The complete isolated backend suite passed after
the final implementation and test updates: 3,290 tests. Frontend tests were not run
because no frontend files changed. The 5,095-row dry run and migration were
performed only in the disposable database; no unrestricted crawl or live
write permission was used.

### Canonical bootstrap and reconciliation validation (2026-11-17)

The application already has one canonical title identity model: `tracker.Movie`,
uniquely keyed by TMDb ID. Watchlist, watched-title and official availability
rows reference it; `availability.ContentMapping` associates provider-native
catalogue entries with its TMDb ID. The empty catalogue was not a missing
model: this database contained 0 `Movie` rows, 0 watchlist/watched rows, 0
official `Availability`/`Offer` rows, 0 provider-content/mapping rows, and 0
TMDb search/details cache rows. The 3,300 legacy scraped mappings all had a
null `movie_id`, `is_confirmed=false`, and no provider-native ID, so they were
not canonical identity evidence. The configured TMDb credential was present;
its value was not printed. No watchlist titles existed to use as a seed or
validation sample.

I restored a read-only dump of the branch-local 5,095-record database into the
isolated PostgreSQL `catalogue_validation_20261117` database on the disposable
test container. All bootstrap, classification audit, reconciliation, and
apply writes below were confined to that clone. The database began with the
same 5,095 discoveries and 3,300 legacy mappings and no canonical records.

The new `bootstrap_canonical_catalogue` command is dry-run by default and
requires an explicit bounded `--allow-network` for cache misses and `--apply`
for writes. It searches by classified type and explicit source title/year,
then verifies the returned TMDb ID, title, year, and type with the details
endpoint. It only creates missing `Movie` rows, records one-to-one
`CanonicalBootstrapEvidence`, and does not update existing canonical metadata.
TMDb search and detail responses use the existing positive/negative caches;
request budgets include retries and a minimum request interval. Replays do
not repeat cached lookups or duplicate canonical identities. Bootstrap
evidence is reviewable in Django admin. The TMDb IDs in this validation came
from official TMDb search and details responses, not title-derived ID guesses
or legacy mappings.

The stratified read-only classification audit sampled 87 distinct records:

| Stratum | Sampled | Correct | Incorrect | Unresolved / pending |
|---|---:|---:|---:|---:|
| HydraHD movies | 15 | 15 | 0 | 0 |
| HydraHD series | 15 | 15 | 0 | 0 |
| AnimeWorld classified RSS | 15 | 1 | 0 | 14 |
| Missing year | 15 | 0 | 0 | 11, plus 4 title-only type candidates held for review |
| Same-title candidate records | 12 | 6 | 0 | 6 |
| Ambiguous AnimeWorld records | 15 | not applicable | not applicable | 10, plus 5 title-only type candidates held for review |
| Conflicting metadata | 0 | — | — | Population count: 0 |

Observed accuracy was **37/37 (100%) among records with a unique exact TMDb
title/type result and, when present, matching year, followed by a details
check**. This is a selective evidence denominator, not evidence that all 5,031
classifications are correct: 41 sampled rows lacked unique title-level
evidence, and nine yearless ambiguous entries had a unique TMDb type candidate
but remain pending review. All 64 legacy AnimeWorld A–Z records remain
`PENDING`; title-only evidence never becomes a canonical association. The six
same-title groups include cases where TMDb has multiple identities; those
remain unmatched/reviewable. No false-positive classification was observed in
the verified sample.

The audit used a fixed random seed and TMDb's cached, budgeted gateway. Across
three bounded audit passes, 205 HTTP attempts succeeded, with no failures or
retries; the largest pass was limited to 180 attempts and used a 0.25-second
minimum interval. The final audit was served entirely from cache (220 cache
hits). TMDb documents its current upper limit as roughly 40 requests/second,
subject to change, and asks clients to respect HTTP 429; these runs stayed
below that rate and did not receive a 429 ([TMDb rate limiting](https://developer.themoviedb.org/docs/rate-limiting),
[TMDb errors](https://developer.themoviedb.org/docs/errors)).

The clone had no watchlist sample, so I bootstrapped a representative
discovery sample instead. TMDb API search/details evidence validated 20
source records and identified 18 unique canonical IDs. The bootstrap replay
made zero network requests (40 cache hits), created 18 `Movie` rows and 20
provenance rows, and did not overwrite any existing canonical metadata.
Reconciliation then classified 5,031 discoveries and kept 64 pending. It
created 20 `ContentMapping` rows, each backed by record-specific TMDb evidence;
the 5,011 remaining classified records were unmatched. A second `--apply`
preserved all 20 existing mappings. The six duplicate-title groups remained
separate source identities. `ProviderContent` rows are marked
`currently_available=false`; no `Offer` row was created. The identity-only
HydraHD `Source` row is disabled. Discovery observations do not imply current
streaming availability.

`ContentMapping.match_evidence` stores confidence evidence. Trusted explicit
IDs and record-specific bootstrap evidence are `MATCH_VERIFIED`; exact title
and year alone are reported as `MATCH_HIGH` proposals and are not auto-applied.
Untrusted source-provided IDs are routed to review. Verified TMDb alternative
titles require exact year and compatible type. Ambiguous same-title candidate
sets remain review-only. Existing mappings are preserved so an incorrect
association can be corrected in admin without reconciliation silently
replacing it. Fuzzy matches are proposal-only and require compatible type,
exact year, at least 0.92 title similarity, and at least 0.60 synopsis-token
overlap against cached TMDb details; they are never auto-applied.

Discovery recovery now records a provider-scoped owner, heartbeat, and
configurable lease (`UNOFFICIAL_CATALOGUE_LEASE_SECONDS`, default 3,600). The
PostgreSQL advisory lock still prevents concurrent jobs for one provider.
Fresh heartbeats block reclamation; an expired lease is marked `PARTIAL` with
recovery diagnostics and resumes from the last committed checkpoint. Tests
cover a hard-crash-shaped stale `RUNNING` state, cursor resumption, and refusal
to reclaim a fresh lease. Scheduler configuration remains disabled.

Final validation: the isolated backend suite passed (3,300 tests),
`makemigrations --check --dry-run` found no drift, and the reconciliation
apply/replay validation left 18 canonical `Movie` rows, 20 evidence rows, 20
canonical mappings, 5,031 classified provider-content rows, 64 pending
classifications, and 0 offers in the isolated clone. Frontend tests were not
needed because no UI behavior changed.

### Canonical scale validation (2026-11-17, branch `new-sources`)

Verdict: **`CANONICAL_RECONCILIATION_PARTIAL`**. Two explicitly selected batches
of 250 evaluated **500 distinct records** and established **388 verified
associations / 388 unique canonical identities** on disposable PostgreSQL.
These are fresh-clone totals, not an addition to the earlier 20-record pilot.
The application database received **no migrations, cache, bootstrap or
reconciliation writes** during this work.

The [machine-readable audit](../audits/canonical_scale_20261117.json) includes
both manifests, per-batch metrics, all 388 record-specific evidence snapshots,
112 pending outcomes, sampled checks, negative candidates and preservation
hashes. Dumps, cache fixtures and resumable checkpoints are retained locally
under the git-ignored `.catalogue-staging/canonical-scale/`; they are not
published with the report. Credentials are absent from the report.

#### Integrity audit and changes

`Movie` previously enforced global uniqueness of `tmdb_id`, although TMDb
movie and TV IDs occupy separate namespaces. Migration `0056` replaces this
with uniqueness on **`(tmdb_id, content_type)`**. All canonical creation paths
now select both fields. Typed detail/catalogue reads select the matching
namespace; legacy numeric-only reads and mutations fail closed if both exist.
Watchlist selection deduplicates the full pair. Tests demonstrate that movie
and series ID 42 coexist, neither overwrites the other, and same-type duplicate
inserts fail at the database constraint. The existing foreign keys retain
canonical row IDs. TMDb search and details caches already had media type in
their database keys; reconciliation's in-memory indexes now do too.

Provider-native IDs retain their original values and provider scope. Discovery
uniqueness, URL identity and `ProviderContent`'s slot-aware identity remain
intact. `ContentMapping` deliberately reserves `(provider, provider_content_id)`;
if an existing mapping disagrees on media type or TMDb ID, reconciliation
reports `existing_canonical_mapping_conflict` and preserves it. Separate
same-title releases are never merged merely because their titles agree.

The audit also closed these unsafe paths:

- Cached original-title/year matches and trusted-ID-only proposals could be
  promoted without record-specific details verification. Both are now
  proposal-only until bootstrap proof exists.
- Bootstrap proof was insufficiently tied to the current observation. Apply
  and replay now check source title/year/type, provider/native identity and
  original URLs, and reject conflicting trusted IDs. Apply locks and rechecks
  the source row; changed observations require another review.
- ASCII-only normalization could erase non-Latin titles. Normalization now
  preserves Unicode letters and digits, and empty normalized titles cannot
  verify a match.
- Resume previously advanced a moving query window. Checkpoints now freeze at
  most 250 selected IDs and source fingerprints, bind to the database and
  selection/apply scope, and atomically journal completed outcomes. A changed
  source or scope fails closed. New observations enter a new batch.
- API failure counters omitted failed retry attempts or double-counted final
  failures. Each dispatched failure is now counted once; exception logs omit
  request URLs and credentials.

Exact title/year search results remain proposals until a separate TMDb details
response verifies identifier, title (display or original), year and endpoint
media type. Multiple exact IDs, competing source releases, and truncated
search pages stay pending. Existing canonical metadata and operator mappings
are preserved. No new canonical model, playback probe, language enrichment or
offer creation was introduced.

#### Bounded results and accuracy checks

The first batch contains 100 HydraHD movies, 71 HydraHD series, all 15
AnimeWorld RSS records without years and all 64 previously ambiguous AnimeWorld
records. All six same-title/year groups are included. The second contains 150
additional HydraHD movies and 100 series, using a separate fixed seed and no
records from the first batch. Every record initially lacked an association in
the fresh live restore.

| Metric | Batch 1 | Batch 2 | Total |
|---|---:|---:|---:|
| Records evaluated | 250 | 250 | 500 |
| Single exact candidates proposed, including held releases | 158 | 237 | 395 |
| Verified TMDb identifiers | 152 | 236 | 388 |
| Canonical records created | 152 | 236 | 388 |
| Canonical associations applied | 152 | 236 | 388 |
| Ambiguous, including unresolved source types | 82 | 10 | 92 |
| Unmatched, including missing years | 16 | 4 | 20 |
| Rejected candidate observations | 154 | 135 | 289 |
| Conflicting details | 0 | 0 | 0 |
| Bootstrap HTTP attempts | 317 | 486 | 803 |
| Bootstrap cache hits on initial proposal passes | 6 | 0 | 6 |

Rejected candidates count per-record candidate observations, not unique TMDb
IDs. Pending outcomes and title-only proposals are not successes. Within the
500-record selection, 112 remain unassociated. Across the complete inventory,
**4,707 / 5,095 remain without verified associations**: 4,595 were outside the
batch selections. Coverage is **9.81% evaluated and 7.62% associated**. A full
**read-only** inventory pass finds 388 verified, 7 review candidates, 4,636
unmatched and 64 unresolved classifications; it can classify 5,031 records.
Only the 500 selected records were eligible for isolated apply, producing 436
classified provider-content identities. No unrestricted reconciliation apply
was performed.

A fixed-seed sample assessed 50 proposed associations: 25 movies and 25 series
across the two batches. A separately implemented, budgeted query omitted the
year filter and checked the returned ID/year/type: **49/50 confirmed**. `Blue`
(1968), movie 169869, was absent from the capped broad-title results. Manual
inspection of its original search/details, source URL containing 1968, director
Silvio Narizzano and cast found no contradiction. It is reported as one
inconclusive secondary search, not a successful independent search check.
All sampled source URLs/title/year and cached overview/cast metadata were also
inspected. No classification error or automatic-association contradiction was
observed. These checks share the TMDb authority and do not establish independent
provider-detail ground truth or a catalogue-wide false-positive rate.

Concrete negatives held or excluded:

- `The Nest` (2025): TMDb movies 1446729 and 1547367, plus separate provider
  releases; both discoveries remain ambiguous.
- `Bad Apples` (2026): movies 1198654 and 1642114 remain ambiguous.
- `Perfect Strangers` (2003), movie 41258: other same-title releases from 1945,
  2015, 2016, 2017 and 2025 cannot replace the verified 2003 identity.
- `Sacrifice` (2021): series 137948 remains separate from movie 631583 even
  though title and year overlap.
- `Are We There Yet?` (2010): series 36473 is distinct from movie 434212 with
  the same title/year and movie 11637 from 2005.
- `Drag Race Spain` / `Drag Race España` remains a held alternative-title
  proposal; title translation alone does not establish record-specific proof.

AnimeWorld matching stops at this boundary: 64 records lack verified types and
15 RSS records lack explicit years. No source detail fetch or title-derived
year inference was used. The second bounded batch was justified by 152 verified
first-batch outcomes, no observed contradictions, and about two requests per
verified association. No third batch or unrestricted loop was started.

#### Resource usage, caching and replay

Budgets were 350 requests for batch 1 and 490 for batch 2, including retries,
with 0.3 seconds minimum spacing and at most two transient retries. Bootstrap
stops immediately on 401/403/429 and leaves the interrupted record resumable;
operators must wait for service recovery before resuming. No failure, retry or
restriction was observed. The two sample checks used a further **54 requests**
under separate 45-request ceilings, for **857 total HTTP attempts**.

Bootstrap cost was **1.606 requests per evaluated record**, or **2.070 per
verified association**. Including sample validation, cost was **1.714 per
evaluated record**, or **2.209 per verified association**. No monetary API cost
was measured. Local read-only reconciliation of the two 250-record selections
took 0.386 seconds, 12 SQL queries and zero API requests on the warmed clone:
0.773 ms and 0.024 queries per record. This excludes bootstrap network latency
and writes and is not a production latency guarantee.

Both bootstrap applies were cache-only: **809 cache hits, zero HTTP requests**.
Fresh no-checkpoint bootstrap replays reused all 388 record-specific proofs,
created no canonical rows and made zero HTTP requests; the remaining unresolved
searches used 33 cache hits. Including the original proposal and validation
passes, the main experiment recorded **849 cache hits** (49.77% of cache hits
plus HTTP dispatches). This aggregate includes replay savings; the cold proposal
passes themselves had only six cache hits. Fresh-restore rehearsal hits are
excluded from these totals.

The existing reusable cache holds 469 searches (two negative) and 388 details.
Positive searches, ambiguous candidate sets and details expire after 30 days;
negative lookups expire after seven days. Reconciliation itself never searches
TMDb. Bootstrap uses trusted source IDs directly for details when present,
then reuses record-specific verified evidence or the existing search/details
caches before bounded network access. The restored source inventory contains
no usable trusted external IDs, so none were guessed from titles. Expired
negative/ambiguous evidence may be refreshed in another explicitly bounded
batch. A complete checkpoint does not reselect missing-year records forever;
changed/new discoveries need a new reviewed manifest.

Apply and replay preserved hashes of all discovery identities/original URLs,
all 3,300 legacy rows and all discovery checkpoint state. Reconciliation replay
also preserved complete row hashes for `Movie`, bootstrap evidence,
`ProviderContent`, `ContentMapping` and discoveries, including timestamps. The
final primary clone contains 388 canonical rows, 388 evidence rows, 388 mappings,
436 provider-content rows and **zero offers**.

#### Fresh restore and limited live procedure

A second fresh read-only dump of the application database again contained
5,095 discoveries, 3,300 legacy mappings, zero canonical records, zero canonical
mappings and zero offers. Its identity/legacy/checkpoint hashes matched the
initial restore. Applying pending migrations and reviewed caches on a second
disposable database reproduced the first 250-record manifest and its 152
associations without HTTP requests. Reconciliation replay preserved all audited
row hashes. The live database remained unchanged.

The reproducible clone-only driver is
[`scripts/canonical_scale_validation.py`](../../scripts/canonical_scale_validation.py).
It checks the actual PostgreSQL database and expected host and refuses database
names outside `canonical_*`. `plan` freezes deterministic manifests;
`bootstrap` is cache-only/dry-run unless flags explicitly allow network/writes;
`sample`, `reconcile` and `audit` save reviewable JSON. For example, with clone
credentials supplied through the existing environment:

```sh
python scripts/canonical_scale_validation.py --database canonical_scale --host canonical-scale-db --output /evidence --batch 1 plan
python scripts/canonical_scale_validation.py --database canonical_scale --host canonical-scale-db --output /evidence --batch 1 --allow-network --request-budget 350 bootstrap
# Inspect proposals and sample checks before clone apply.
python scripts/canonical_scale_validation.py --database canonical_scale --host canonical-scale-db --output /evidence --batch 1 --apply bootstrap
python scripts/canonical_scale_validation.py --database canonical_scale --host canonical-scale-db --output /evidence --batch 1 --apply reconcile
```

**The following live steps require separate explicit authorization; none were
executed.** The initial live allowance should cover at most 25 specifically
reviewed record IDs, a finite API budget and the exact target host/database.

1. Take a fresh read-only backup and audit actual counts, source snapshots,
   native IDs, URLs, existing canonical/mapping rows, offers and discovery
   checkpoints. Compare every approved record with the committed evidence.
   Rehearse on a new restore if source identity or baseline counts changed.
2. Apply required migrations through the existing guarded migration service,
   confirming the actual host/database. Do not enable worker schedules.
3. Use a reviewed JSON list of at most 25 IDs. Cache transfer, if authorized,
   must use the composite search/details keys and original expiry times,
   without cloning cache primary keys or overwriting newer live metadata.
   Otherwise permit a separate proposal pass with at most 50 HTTP attempts.
4. Review each proposal against the approved source snapshot and type/year/ID
   evidence. The apply pass must omit `--allow-network`; a cache miss stops
   with `CACHE_MISS` and remains resumable. Use a distinct apply checkpoint.
5. Reconcile only those same IDs. Recheck identity/legacy/checkpoint hashes,
   exact mapping targets, pending records and zero offers. Repeat that same
   bounded selection to prove no-op replay before authorizing another batch.
   Stop on any unexpected mapping, source change, restriction or count change.

The management-command invocation can be constructed without shell expansion
of credentials or IDs, after loading and validating the approved JSON list:

```python
flags = [flag for pk in approved_ids for flag in ("--record-id", str(pk))]
assert 0 < len(set(approved_ids)) == len(approved_ids) <= 25
# Authorized proposal only; review its JSON before the next calls.
call_command("bootstrap_canonical_catalogue", *flags, "--limit", "25",
             "--allow-network", "--request-budget", "50", "--interval", "0.3")
# After review; fresh cache only, no network. Use a checkpoint unique to this manifest.
call_command("bootstrap_canonical_catalogue", *flags, "--limit", "25",
             "--apply", "--checkpoint-file", "/approved/batch-001-apply.json")
call_command("reconcile_unofficial_catalogue", *flags, "--apply")
```

Both bootstrap and reconciliation CLI applies are capped at 250 records;
reconciliation apply now requires explicit IDs or a bounded limit. Preserve
checkpoints and cache fixtures when pausing. A complete bootstrap checkpoint
correctly evaluates zero further records; use the same IDs without that
checkpoint for an independent no-op evidence replay. Never delete mappings
or replace operator decisions as an automatic rollback.

HydraHD historical checkpoints remain **movies page 132 / TV page 77**.
AnimeWorld RSS and historical/incremental discovery remain independent of
reconciliation. No restricted/unverified provider was retried. Schedules remain
disabled; newly collected records can enter the next explicit manifest without
schema changes.

Validation: **3,319 full isolated backend tests**, focused identity/bootstrap/
reconciliation checks, budget/cache/retry and restriction tests, source-mutation
checks, interruption between search/details, crash after commit before
checkpoint, fixed-selection resume, ambiguous/remake/media-collision negatives,
no-op replay, two fresh restores, and migration drift checks passed. Migration
`0056` remains unapplied on live. The limited rollout is prepared, but live
canonical writes and further catalogue expansion still require separate scopes.

### First canonical rollout readiness and continued reconciliation (2026-11-17)

Verdict: **`CANONICAL_ROLLOUT_READY`** for a separately authorized, narrow
**387-association** first rollout. **No live canonical application or migration
was performed.** The actual `new-sources/tracker` database still has 5,095
discoveries, 3,300 legacy mappings, zero canonical titles, zero canonical
associations and zero offers. All 57 live table counts and complete row hashes
match the fresh pre-rehearsal backup after this work.

The [current canonical audit](../audits/canonical_rollout_20261117.json)
supersedes the earlier scale report for rollout decisions. The historical
[scale audit](../audits/canonical_scale_20261117.json) remains unchanged.
Two self-contained machine-readable manifests contain each source record ID,
complete source observation and hash, provider-content identity, typed TMDb
canonical ID, original search results, details evidence and expiry, and
expected database changes:

- [First live proposal: 387 associations](../audits/canonical_first_live_20261117.manifest.json).
  SHA-256: `eec5c6717a0e7bf185cf29c7c3f213eeff5b4382ff6d0d1277f140322518df8a`.
- [Next isolated batch: 475 associations](../audits/canonical_next_batch_20261117.manifest.json).
  SHA-256: `2496712fc69725d6b814c4c4ee516718341920485ffb8ef6846c150064de316f`.
  These are **not part of the first live proposal**.

#### Complete first-set audit and the held association

All **388/388 retained record-specific proofs** were rechecked against the
freshly restored source observations and retained TMDb search/details evidence.
Checks cover identifier and namespace, explicit release year, compatible display
or original title, a unique exact candidate, complete search results, competing
same-title source releases, trusted-ID contradictions, existing canonical years,
provider-content collisions and existing operator mappings. Thresholds were
preserved. Typed identity remains `(tmdb_id, content_type)`; a clone's `Movie`
primary key is not a portable canonical identifier.

`Blue` (1968), source record **1771**, TMDb **movie 169869**, is excluded. Its
cached year-filtered search/details, source URL year, director Silvio Narizzano
and cast are internally consistent. The retained independent broad-title
search did not return this ID within its capped results, and there is no
additional source-specific corroboration. This remains inconclusive, so the
proposed first rollout contains **387**, rather than 388, associations. It
requires stronger evidence before inclusion; it was not automatically retried.
The other 49 historical independent sample checks remain confirmed. Such
queries share the TMDb authority and do not establish independent provider-detail
ground truth or a catalogue-wide false-positive rate.

#### Fresh guarded backup, restore, import and replay

The exact source was checked as project `new-sources`, service `db`, container
`new-sources-db-1`, database `tracker`, volume `new-sources_pgdata`, tracker
migration head `0053`. The backend writer was stopped for the backup and then
returned to its started state. A pre-existing restart/endpoint failure reports
missing `tracker_cataloguediscoverystate.heartbeat_at`; the live schema was left
untouched. Authorized rollout must first apply availability `0016` and tracker `0054`–`0056` using the guarded
migration workflow. Clone checks validate that schema prerequisite; they do not
constitute a live application health check.

Fresh custom-format backup, outside Git and database volumes:

`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_canonical_20261117T193249Z.dump`

SHA-256: `b1f01749b4137b82f46b57cc1f0d58ca81e47ed644c1289a988ec305c7379d24`.
The dump passed TOC inspection and three disposable PostgreSQL restores. The
initial and final restores matched every source table's count and complete row
hash before migrations. Migrations used `guarded_migrate` with exact database,
host and target-token confirmation, only on `canonical_*` clones.

The final first-rollout rehearsal used `canonical_rollout_final_20261117` and
**did not import any TMDb cache fixtures**. Its embedded, reviewed manifest
alone inserted 387 typed canonical titles, 387 record-specific evidence rows,
387 unavailable provider-content identities and 387 canonical mappings, plus
one disabled HydraHD identity-only `Source`. It preserved existing Source
settings. All 4,708 unselected discoveries were checked in full, including
pending outcomes. All 3,300 legacy rows, discovery identities, checkpoints and
provider settings were unchanged; there were zero offers and zero HTTP calls.
Replay changed no table row or timestamp. All 14 preservation/relationship
checks passed.

The next 475 associations were imported and replayed separately on
`canonical_rollout_20261117`. The same 14 checks passed. Its final counts are
862 canonical titles/evidence/mappings/provider-content rows, 5,095 discoveries,
3,300 legacy rows and zero offers. Every prior association and unselected
source row was preserved. Replay again preserved complete database row hashes.

#### Consecutive batch coverage

The next 500 previously unevaluated IDs were frozen into two 250-record
selections (3 and 4). They exclude **every ID from both earlier batches**,
including ambiguous and unmatched outcomes. Discovery records with changed or
new observations can enter future reviewed selections; completed pending
outcomes are not silently reselected. The continuation driver now accepts
`--previous-audit` and requires it for batches after 2. Use the current audit,
which retains all four ID selections, when continuing.

The earlier two sub-batches below reflect the completed audit: `Blue` now
appears as a hold in sub-batch 1, rather than a successful rollout association.
HTTP/cache figures in this table cover initial proposals and independent
validation; historical apply/replay cache savings are accounted separately.

| Metric | Sub-batch 1 | Sub-batch 2 | Sub-batch 3 | Sub-batch 4 |
|---|---:|---:|---:|---:|
| Previously evaluated | 0 | 250 | 500 | 750 |
| Newly evaluated | 250 | 250 | 250 | 250 |
| Exact candidates | 158 | 237 | 238 | 239 |
| Verified associations | 151 | 236 | 237 | 238 |
| Rejected candidate observations | 154 | 135 | 86 | 107 |
| Ambiguous candidates | 82 | 10 | 8 | 9 |
| Unmatched records | 16 | 4 | 5 | 3 |
| Explicit rollout holds | 1 | 0 | 0 | 0 |
| Unique canonical titles | 151 | 236 | 237 | 238 |
| TMDb requests, including validation | 351 | 506 | 511 | 512 |
| Cache hits, proposal/validation scope | 7 | 0 | 0 | 0 |
| Independent validation confirmed | 29 | 20 | 24 | 24 |
| Independent validation inconclusive | 1, excluded | 0 | 0 | 0 |
| Remaining unevaluated inventory | 4,845 | 4,595 | 4,345 | 4,095 |
| Remaining unassociated inventory | 4,944 | 4,708 | 4,471 | 4,233 |
| Verified association yield | 60.4% | 94.4% | 94.8% | 95.2% |

The new 500 yield **475 verified, 17 ambiguous and 8 unmatched**, with 477 exact
candidate proposals and 193 rejected candidate observations. All 48 newly
sampled year-free validations confirmed the selected ID/year/type. Full
record-specific evidence audit passed for all 475 before isolated application.

| Provider / content type, new selection | Evaluated | Verified | Ambiguous | Unmatched | Yield | Bootstrap HTTP |
|---|---:|---:|---:|---:|---:|---:|
| HydraHD movies | 300 | 282 | 14 | 4 | 94.0% | 582 |
| HydraHD series | 200 | 193 | 3 | 4 | 96.5% | 393 |

Cumulatively, HydraHD movies have 507/550 verified and series 355/371 verified.
The earlier AnimeWorld inventory retains 64 unresolved types and 15 missing-year
outcomes (one movie, 14 series), with zero verified associations. The audit
includes provider/content-type breakdowns for every sub-batch. Older per-record
HTTP allocation was not recorded and is reported as unavailable; sub-batch 3's
allocation is reconstructed exactly from its dispatch shape and zero
failures/retries/cache hits. Sub-batch 4 and subsequent bootstrap runs record
per-record HTTP/cache and candidate accounting directly in their journals.

Overall, **1,000/5,095 evaluated (19.63%)** and **862/5,095 verified proposals
(16.92%)** leave **4,095 unevaluated** and **4,233 unassociated**. Among evaluated
records, 109 are ambiguous, 28 unmatched and one explicitly held. The initial
500's audited yield is 77.4%, the next 500's yield is 95.0%, and the combined
yield is 86.2%. Selections differ, particularly the AnimeWorld blockers in the
first selection; these measurements do not project future catalogue yield.
All these association figures describe isolated databases/reviewed proposals;
live still has zero canonical associations.

#### Resource bounds, tooling and authorized rollout boundary

New bootstrap ceilings were 520 HTTP attempts per 250 records (actual 487 and
488), plus 50 attempts per independent validation pass (actual 24 each).
Minimum dispatch spacing remains 0.3 seconds, transient retries at most two,
and service restrictions stop the batch. Existing Retry-After handling, positive
and negative TTLs, and checkpoint resumption remain intact. No restriction,
failure, retry or restricted-provider request occurred. There is no unattended
loop. Current work consumed **1,023 new requests and zero cache hits**;
cumulative accounting is **1,880 HTTP requests and 849 cache hits**. All
manifest imports and replays used zero HTTP requests and zero cache mutations;
embedded proof reuse is not counted as a cache hit.

`export_canonical_manifest` audits and exports exact evidence. The new
`apply_canonical_manifest` is offline and dry-run by default. Apply requires
an actual PostgreSQL target, exact expected database/host, matching confirmation
token, and the reviewed file's SHA-256. It locks identity tables, preflights the
whole set and applies atomically through the existing bootstrap/classification
requirements. Expired evidence, source changes, incomplete/multiple candidates,
new competing releases, provider identity collisions or mapping conflicts fail
closed. The first embedded proof expires **2026-12-17 19:03:32 UTC**; a later
rollout requires fresh reviewed evidence. Reapplication preserves existing
metadata and row timestamps. No live apply command was executed.

After **separate explicit live authorization**, take another fresh guarded
backup and restore-test it, compare source/settings/checkpoint hashes, then
apply required migrations through the guarded service. A reviewable example of
the subsequent offline first-manifest operation is:

```sh
# ONLY after separate live authorization and guarded migration/backup checks.
docker compose run --rm --entrypoint python \
  -v "$PWD/docs/audits:/app/rollout:ro" backend \
  manage.py apply_canonical_manifest \
  --manifest /app/rollout/canonical_first_live_20261117.manifest.json \
  --apply --expected-database tracker --expected-host db \
  --confirm-target tracker@db \
  --confirm-manifest-sha256 eec5c6717a0e7bf185cf29c7c3f213eeff5b4382ff6d0d1277f140322518df8a
```

Use the new pre-live backup as the rollback artifact, rather than the older
pre-rehearsal dump if live has since changed. Preserve the current 387-only
scope, compare all relationships/counts/hashes, then replay and require no row
changes before considering another batch. The clone-only
[`verify_canonical_rollout.py`](../../scripts/verify_canonical_rollout.py)
provides `snapshot` and `verify` phases, checks the actual expected
`canonical_*` PostgreSQL database/host, and verifies full preservation and
replay hashes plus every manifest relationship. Its verify phase takes
`--before`, `--imported` and `--manifest` snapshots and writes a machine-readable
report. Local receipts/checkpoints remain under the ignored
`.catalogue-staging/canonical-scale/rollout/` and
`.catalogue-staging/canonical-rollout/` directories.

HydraHD historical checkpoints remain movies **132**, series **77**; the
incremental window and AnimeWorld RSS state are unchanged. Provider identities,
scheduling controls and restricted-provider safeguards are preserved. No
unattended discovery/reconciliation was enabled. CineBlog01, AltaDefinizione,
StreamingCommunity and KissKH retain their documented provenance/access
blockers. There is no audio/subtitle enrichment or playback availability work.

Validation: **67 focused tests passed**, including canonical identity,
movie/TV collisions, offline manifest rejection/atomic rollback/replay,
bootstrap checkpoint/cache/budget and gateway retry/restriction checks.
The complete isolated PostgreSQL backend suite passed **3,334 tests**. Both
actual backup-clone integration imports passed all 14 checks. Migration
consistency (`makemigrations --check --dry-run`, `migrate --check`) and Django
system checks passed. No live migration was applied.

### P0 live schema diagnosis and final rollout procedure (2026-11-17)

Verdict: **`CANONICAL_LIVE_ROLLOUT_SAFE`** for the documented, separately
authorized procedure. This supersedes the earlier rollout-readiness summary.
**Live remains unmigrated and has received no canonical writes.** The success
criterion was verified on faithful restores, rather than bypassing the live
migration guard. The actual live backend remains blocked until authorization.

The [exact eight-stage rollout/rollback commands](CANONICAL_LIVE_ROLLOUT.md)
cover preflight, a fresh verified backup, clone rehearsal, guarded migrations,
post-migration integrity, exactly 387 imports, replay, post-import checks and
verified database restoration. Every live-write operation has an authorization
ID. **Do not execute the procedure as part of this preparation task.** The
[current machine-readable audit](../audits/canonical_rollout_20261117.json)
contains source history, physical schema/constraints/indexes, reproduced errors,
clone receipts, constraint and endpoint checks, rollback results and coverage.

#### Precise root cause and correction

Read-only checks confirmed project `new-sources`, database container
`new-sources-db-1`, database/user `tracker`, host `db` and volume
`new-sources_pgdata`. Live history ends at **tracker `0053` and availability
`0015`**. Physical objects agree with that history; no false migration record or
failed migration implementation was found. The checked-out models require four
pending migrations:

| Migration | Required schema/data change |
|---|---|
| `availability.0016_contentmapping_match_evidence` | Add non-null JSON `ContentMapping.match_evidence` |
| `tracker.0054_discovered_catalogue_last_seen` | Add/index/backfill non-null `last_seen_at` from original `updated_at`/`discovered_at` |
| `tracker.0055_cataloguediscoverystate_heartbeat_at_and_more` | Add `heartbeat_at`, `lease_expires_at`, `lease_owner`; create `CanonicalBootstrapEvidence` and its constraints/index |
| `tracker.0056_movie_tmdb_content_type_identity` | Replace global `Movie.tmdb_id` uniqueness with `(tmdb_id, content_type)` uniqueness |

Tracker dependencies form `0053 → 0054 → 0055 → 0056`. Availability `0016`
depends on its `0015` and is an additional prerequisite omitted from the earlier
summary. **Targeting tracker `0056` alone does not apply availability `0016`.**
The reviewed all-app guarded plan contains exactly the four migrations above.

The retained provider endpoint error is
`column tracker_cataloguediscoverystate.heartbeat_at does not exist`
(**SQLSTATE 42703**). Read-only SQL reproduced the missing column and also
confirmed missing `last_seen_at`, missing `match_evidence` (both 42703), and the
missing bootstrap-evidence relation (**42P01**). The other new lease fields are
absent. `tracker_movie_tmdb_id_key` still enforces the old global numeric-ID
uniqueness; `uniq_movie_tmdb_content_type` is absent.

The current host-mounted runtime entrypoint correctly runs `migrate --check`
before starting the server. It exits **1** for the four pending migrations,
causing the current restart loop. It must not be changed to run migrations
automatically. A stale baked entrypoint is not in use: Compose selects the
host-mounted guard, and the development backend mounts current source. The
migration service is separately exposed through the `ops` profile and requires
exact database, host and confirmation-token checks. No guard was bypassed on
live; no history was faked or constraints manually changed there.

#### Fresh backup and faithful migration/import/rollback rehearsals

A new read-only custom-format dump was saved outside Git/database volumes:

`/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_schema_20261117T200359Z.dump`

SHA-256: `c296db28fbbebdb9e5aaffc73ea5c4f36df8e9416816e0f4783d11ba87900979`.
The backend was already blocked by its schema check; no other application
sessions were present. Source row hashes stayed stable before/after `pg_dump`.
No live service stop/start, migration, reconciliation or rollback was executed.
The original dump was restored into fresh disposable PostgreSQL databases;
all **57 source table counts and full row hashes** matched, as did the inspected
physical columns, constraints, indexes and original migration history.

The final `canonical_schema_verified_20261117` restore applied the four guarded
migrations without implementation changes. **15 migration integrity checks
passed**: all 5,095 discoveries and 3,300 legacy mapping IDs/fields were retained;
old observation fields, provider settings and checkpoint values were unchanged;
every `last_seen_at` matched its original timestamp; no deletion or offer
occurred. Django's expected metadata changes are explicit: four migration
history rows, one content type and four permissions for the new evidence model.
Existing content types and permissions remain unchanged.

The committed first manifest is byte-for-byte unchanged, SHA-256
`eec5c6717a0e7bf185cf29c7c3f213eeff5b4382ff6d0d1277f140322518df8a`.
All 387 proofs passed fresh offline validation. Its exact import inserted 387
typed canonical titles, evidence rows, unavailable provider-content identities
and mappings, plus one disabled identity-only Source. All **15 import/replay
checks passed**, including every relationship, all 4,708 unselected discoveries,
unrelated row hashes, settings/checkpoints and identical replay timestamps.
Zero HTTP requests, cache writes or offers resulted. The 475-association second
batch and `Blue` (1968) were not included.

On the migrated clone, explicit negative-PK, rollback-only probes demonstrated
that movie and series rows can share a numeric TMDb ID and same-namespace
duplicates fail against `uniq_movie_tmdb_content_type`. Probes left no rows or
sequence changes. The read-only `/api/providers/` smoke check returned **200
with 50 providers**, with HTTP upstream dispatch forbidden. Thus the exact
missing-column reader works after the correction. Live health was not changed.

A separate isolated PostgreSQL instance then rehearsed all migrations plus the
387 import followed by **database replacement from the verified backup**.
Complete rows, original history and inspected schema returned exactly to the
pre-migration state. The runbook uses `pg_restore --clean --if-exists --create`
with `postgres` as the maintenance connection and checks the archive database
name `tracker`: a plain object-level `--clean` restore cannot reliably remove
the new evidence table/foreign keys absent from the old archive. This destructive
rollback requires its own explicit authorization. Restoring the baseline also
restores the original code/schema mismatch; current application code must stay
stopped until an operator selects compatible code or another authorized attempt.

#### Safer verification and continued isolated coverage

`audit_canonical_rollout` now provides exact-target **read-only** `snapshot`,
`migrations`, `import` and `unchanged` checks for live or clone verification.
The process enforces read-only transactions and preserves audit output files.
Migration validation distinguishes allowed new columns and Django metadata
from existing data and checks original-column hashes. The existing clone-only
verification script shares the same integrity implementation. Manifest
validation now explicitly rejects missing migration history or physical schema
requirements before attempting association writes, including the overlooked
availability migration and an absent typed constraint despite recorded history.

After completing schema investigation and first-manifest validation, one new
bounded sub-batch evaluated **250 previously unevaluated records** on the
separate reconciliation clone. All 1,000 prior evaluated IDs, including pending
outcomes, were excluded. No old ambiguity was automatically retried. The full
239-record evidence audit, isolated import and idempotent replay passed; these
additional associations are **not part of the first live rollout**.

| Metric | Sub-batch 5 |
|---|---:|
| Previously evaluated | 1,000 |
| Newly evaluated | 250 |
| Exact candidates | 240 |
| Verified associations / unique canonical titles | 239 |
| Rejected candidate observations | 104 |
| Ambiguous candidates | 9 |
| Unmatched records | 2 |
| TMDb requests, including independent validation | 513 (489 bootstrap + 24 validation) |
| Cache hits | 0 |
| Independent validation | 24/24 confirmed |
| Remaining unevaluated inventory | 3,845 |
| Remaining unassociated inventory | 3,994 |
| Matching yield | 95.6% |

HydraHD movies: **145/150 verified**, five ambiguous, zero unmatched, 295
bootstrap requests. Series: **94/100 verified**, four ambiguous, two unmatched,
194 bootstrap requests. Ceilings remained 520 bootstrap and 50 validation
attempts, 0.3-second minimum dispatch spacing, at most two transient retries,
restriction-stop behavior and existing Retry-After/cache/checkpoint rules.
No failures, retries or rate limits occurred. The
[239-association isolated manifest](../audits/canonical_batch5_20261117.manifest.json)
retains complete evidence and expected changes. The rolling audit retains all
five ID selections and all pending outcomes for later continuation.

Cumulative results are **1,250/5,095 evaluated (24.53%)**, **1,101 verified
associations (21.61% inventory coverage)**, **118 ambiguous**, **30 unmatched**
and **one held** (`Blue`). There are **3,845 unevaluated** and **3,994
unassociated** records. Consecutive audited yields are 60.4%, 94.4%, 94.8%, 95.2%
and 95.6%; the changing provider/type composition prevents a catalogue-wide
yield projection. Cumulative HydraHD verified counts are 652 movies and 449
series; AnimeWorld blockers are unchanged.

Current work used **513 new TMDb requests, zero cache hits**. Totals are
**2,393 requests and 849 cache hits** (26.19% including historical replay hits).
Independent checks total 121 confirmations and one historical inconclusive hold;
they still share the TMDb authority and do not establish provider-detail ground
truth or a catalogue-wide false-positive rate. Prior cache/evidence results are
preserved. All discovery cursors, RSS state, provider settings, disabled schedules
and restricted-provider blockers remain intact. No language extraction, playback
offers or automatic availability inference was introduced.

Validation passed: **75 focused tests**, **3,342 isolated PostgreSQL backend
tests**, migration consistency and system checks, actual faithful-clone migration
and 387 import/replay checks, physical namespace probes, provider endpoint smoke
check and exact rollback restoration. The runbook's Bash blocks pass syntax
validation without executing the live procedure. A final read-only live check
confirmed every source row, history entry and inspected schema object unchanged.

## Incremental reconciliation operation

The completed inventory now continues through a bounded incremental worker.
See [Incremental unofficial canonical reconciliation](INCREMENTAL_RECONCILIATION.md)
for eligibility, deferred retries, leases, atomic checkpoints, disabled schedule
configuration, management commands and disposable-clone validation. Existing
discoveries are acknowledged as a baseline; this does not rerun bulk matching.
