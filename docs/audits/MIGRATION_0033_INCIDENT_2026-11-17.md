# Live database migration incident audit

Audit date: 2026-11-17
Repository reference: `7617f73` (`new-sources`)
Verdict for historical integrity: **unverified**

## Re-audit and backup reconciliation (`4bda936`, 2026-11-17)

The database state described below was a historical observation and is no
longer current. A read-only inspection of `new-sources-db-1` identified Compose
project `new-sources`, service `db`, database `tracker`, PostgreSQL 16.15, and
volume `new-sources_pgdata`. The live database has 3,300
`ScrapedContentMapping` rows, 50 providers, zero `Movie` rows, zero
`DiscoveredCatalogueRecord` rows, zero `ProviderContent`, zero
`ContentMapping`, and zero offers, evidence, or offer prices. Its tracker
migration history is now through
`0053_catalogue_url_identity_provenance` (55 tracker migration rows).

Container logs establish the intervening migration event: the running backend
applied `0050`–`0053` at 2026-11-17 13:41:19–13:41:20 UTC. Its later startup
reported “No migrations to apply.” This audit did not start or restart that
application and made no live database writes.

The reported SHA-256
`9417b89cc9c47a4022bcbab9de0d24a3d761cacc599b7149e5ba0f982ffd96cf` belongs to
the custom-format dump
`/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-DBBackups/new-sources-tracker-postmigration-7617f73.dump`.
The failed restore that returned zero scraper mappings used a different file:
the tracked
`backups/tracker_backup_20260926_140128.sql`, SHA-256
`693f7b40976b71cf7e19a4d01d69f9e5b8209f94083db5efac2b9cfa33a6d2a0`. Both the
repository-root and worktree copies have that same stale digest. That SQL file
contains the divergent `0033_fix_preference_json_fields` lineage and its
`0013_merge_20260830_1219` lacks the declared `0011_availability_price_info_and_more`
dependency; it restores with zero mappings and four Movie rows. Evidence
therefore points to selecting a different backup file, not selecting the
wrong Compose project or volume. Do not identify backups by filename alone.

A fresh custom-format backup was made directly from the identified PostgreSQL
container with `pg_dump`, without invoking an application entrypoint. It is
outside Git and database volumes:

```text
/Users/angeloandreaisola/Documents/database-backups/WhereTfDoIWatchIt/new-sources_tracker_20261117T143100Z.dump
SHA-256 fd04b86731fdc1419f5cc78ea1cab7186d494750137e8f4b67f9e038d557703e
```

It restored successfully with `pg_restore --exit-on-error` into disposable
database `wtdwi_live_restore2`. Live and restored clone have identical counts,
mapping IDs and field values, provider rows, tracker migration history, and
schema. Hashes: mapping IDs
`6307580580df5823adc49e2b91631216e90268b7d22c2cdad9982a4e7548a09a`, mapping
fields `a4dc1f57b43fa66cbeb48107af072154f2694637d23e4b6c0a1a34d67ec88079`,
provider rows `cf6b4d7e137f9e6ea0dedc9880c490be337c22278c53de784190e9cc2d0d609c`,
and all migrations `08833c313f96f379389c2071b5bd0a13049aa6b7f04b6b5793ac37e6689acd68`.
`migrate --check` on the clone found no migration pending through `0053`.

The independently copied catalogue artifact also passed checksum verification
and import/replay in that restored clone: first pass inserted 3,031 pending
records (2,952 HydraHD and 79 AnimeWorld) and restored two checkpoints; replay
inserted/updated zero and reported 3,031 unchanged. The clone retained all
3,300 original mapping rows and still had zero canonical movies, provider
content, content mappings, and offers. No migration or import was run against
the live Compose database. The direct backup and current clone validation
establish a restore point for the database's current state; they do not
reconstruct the pre-`0033` 3,711-row state discussed below.

The separately reported `9417...ffd96cf` dump was also reverified at its
documented path and restored to a second isolated database. That restore had
3,300 mappings, zero movies, and migration `0049`; migrations `0050`–`0053`
then applied successfully. Its mapping ID and all-field hashes match both the
live database and the migrated clone. Importing the independent staging copy
into this migrated clone inserted 3,031 records, then replay inserted/updated
zero and reported 3,031 unchanged. Its post-import counts also remained at
3,300 mappings, zero movies/provider content/content mappings/offers, and
3,031 pending discoveries. This exercises the original backup through the
actual outstanding migration sequence as well as validating the fresh current
backup restore.

**Current rollout assessment:** a checksum-verified backup of the actual
database restored and passed through the `0053` migration check; staging import
and replay passed without altering original rows. A separate authorized live
rollout is therefore technically ready, subject to the documented preflight,
maintenance window, and exact-target guard. This is not authorization to run it.
Focused isolated tests passed (36/36), the complete isolated backend suite
passed (3,264 tests in 125.0 seconds), and `makemigrations --check --dry-run`
reported no model drift. Both the fresh-backup clone and the `0049` backup clone
passed migration checks through `0053`.

## Startup migration root cause and fail-closed repair (`55571ca` follow-up)

The migration process was the backend container's baked entrypoint, not the
guarded `migrate` service. The live container was created on 2026-11-16 07:04
UTC from image `sha256:80079c2d16711916099e35d4fdc51bf135fe0f64122d1a603a406cf6aa2ba7a0`
(image created 07:03 UTC). Its inspected `/entrypoint.sh` was 95 bytes, SHA-256
`30d969eb485f437055b6db372336523ac4167399575cdf04d74ba5f8b08ee2ee`, and ran
`python manage.py migrate` before `collectstatic` and the service command.
Compose configured the backend command as `python manage.py runserver`, with
the image entrypoint left in place. The dev Compose overlay also bind-mounted
the current `backend/` tree at `/app`, so the stale image's old startup script
ran the current checked-out Django migration code. At 13:41:19–13:41:20 UTC
that path applied `0050`–`0053`.

The read-only startup guard had been committed in `7d283d2` at 2026-11-17
12:19 local time (11:19 UTC), after the running image was built. The source
entrypoint in that commit ran `migrate --check`, but a later start of the
existing container continued to use its stale image and bypassed that source
change. No `--profile ops` migration-service invocation or exact target
confirmation was found in the available shell history. The Docker event buffer
no longer retained the start event's initiating command. Local history records
`docker builder prune -af` at 13:41 UTC but no `docker compose ... migrate`
operation in that window. Therefore the startup mechanism and stale image are
confirmed; the exact command that started the container and whether a human
intended the schema update cannot be established from available records.

The Compose backend definition now invokes
`/bin/sh /runtime-guard-entrypoint.sh` and bind-mounts the checked-out
`backend/entrypoint.sh` there read-only. That host script only runs
`migrate --check`, `collectstatic`, and the configured service command. A
stale image cannot silently fall back to its old baked migration entrypoint:
the Compose override selects the host-mounted script, and a missing
mount/path fails startup. Regression coverage verifies the Compose entrypoint
and mount. The test Compose service mounts the configuration read-only for
this check.

The live backend was recreated with the updated Compose files and current
read-only entrypoint guard. The first recreate attempt failed closed because
port 8002 is owned by the root Compose project; the next attempt also failed
closed because the checked-out script did not have its executable bit set.
Invoking the mounted script through `/bin/sh` and setting the `new-sources`
host port to 8000 resolved both issues without stopping the other project.
Inspection confirms `entrypoint=["/bin/sh", "/runtime-guard-entrypoint.sh"]`
and a read-only mount from this worktree's `backend/entrypoint.sh`. The backend
is running. Startup passed its read-only migration check; migration ledger
timestamps and database counts remain unchanged. No live migration or
inventory import was performed. The service was briefly unavailable during
recreation.

The live database still matches the verified backup: 3,300 mappings; mapping
ID and field hashes, provider-row hash, and migration-history hash remain
unchanged. The current inventory has since grown to 3,703 records (3,624
HydraHD and 79 AnimeWorld); its current gzip SHA-256 is
`c2a38c523fb2884b2a3b5e261d261a32b3ef47143ab5e5505d9286b6e9e3cd28`. It was
imported and replayed only in disposable restored clones. Focused guard and
catalogue tests passed after the Compose fail-closed change (37/37), and the
complete isolated suite passed (3,265 tests in 128.4 seconds).
`migrate --check`, `makemigrations --check --dry-run`, and `git diff --check`
passed.

## Current Compose database and backup

The inspected Compose target is project `new-sources`, service `db`, image
`postgres:16-alpine`, database `tracker`, user `tracker`, host `db`, volume
`new-sources_pgdata` mounted at `/var/lib/postgresql/data`. The backup command
ran directly inside the PostgreSQL container with `pg_dump`; it did not start
the backend or its entrypoint.

The current post-migration custom-format backup is stored outside the
repository at:

```text
/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-DBBackups/new-sources-tracker-postmigration-7617f73.dump
```

It is mode `0600` and approximately 1.2 MB. `pg_restore --list` succeeded. It
was restored with `pg_restore --exit-on-error` into isolated database
`wtdwi_audit_restore_7617f73`; a second restoration into
`wtdwi_audit_rollback_7617` was used to verify rollback by restoration. Both
restores had 3,300 `ScrapedContentMapping` rows with IDs 1–6282 and matching
hashes of their existing data fields.

SHA-256: `9417b89cc9c47a4022bcbab9de0d24a3d761cacc599b7149e5ba0f982ffd96cf`.

The live database currently records 84 migrations across installed apps, with
51 `tracker` and 15 `availability` migrations. The latest tracker migration is
`0049_scraped_content_type_identity`; `0050` is not applied live. Current
measured rows: 3,300 `ScrapedContentMapping`, 0
`CatalogueDiscoveryState`, 0 `ProviderContent`, and 0 `ContentMapping`. All
3,300 scraper rows currently have a URL; none has a `movie_id`, is confirmed,
or carries one of the inspected audio/subtitle language payload keys. The
provider table contains 50 rows and the mapping table has its two provider and
movie foreign keys.

The recorded migration history matches the schema observed on the live
database, so this audit found no migration-history repair to perform. Keep
`0050` pending until a fresh backup and restore-first rollout are approved.
The reversible plan is to retain the verified `0049` dump, restore it to a new
disposable database, run the guarded migration command against the exact
database/host, compare row IDs, mapping counts and constraints, then discard
the clone. If a later production rollout is explicitly approved and fails its
post-checks, restore the preserved `0049` dump rather than reverse-applying
`0050`. No production migration was run in this audit.

The dump contains application data and must remain local. It was not added to
Git. Its verified restore is a rollback point for the **current** post-migration
state, not a recovery of the former 3,711-row state.

## Search for pre-migration evidence

The only repository SQL dump found is
`backups/tracker_backup_20260926_140128.sql`. It contains zero
`ScrapedContentMapping` rows and 50 migration-history rows, with tracker history
ending at `0033_fix_preference_json_fields`; it is not a snapshot of the
reported 3,711 rows immediately before this incident. No other relevant
repository SQL or custom-format dump was found. It cannot support a
row-by-row comparison or recover the missing pre-migration state.

The incident notes record 3,711 rows before and 3,300 afterward, a difference
of 411. That count delta is consistent with the deletion paths in migration
`0033`, but there is no pre-run record of the affected mapping IDs or a
migration-side deletion count. It is therefore **not verified that all 411
rows were semantic duplicates**, nor can the historical state of each field be
reconstructed. No claim of zero data loss is made.

## What migration `0033_merge_duplicate_providers` does

The migration groups providers by the lower-cased domain root (the domain up
to its first `/`), keeps the lowest-ID provider in each group, and folds the
other provider rows into it. The lowest ID is deterministic; this audit cannot
prove it was the semantically preferred provider for every historical group.

For a duplicate scraper mapping, it searches for a canonical-provider twin
with the same `scraped_title` and `scraped_year`. The migration predates
`scraped_content_type`, so that key is not part of the twin test. When a twin
exists, the canonical row survives and the alias row is deleted. The merge
function applies these rules:

| Field | Result on canonical row |
|---|---|
| `movie_id` | Alias link fills a null canonical link. If both links are present and differ, canonical wins. |
| `is_confirmed` | Logical OR; a confirmed alias makes the canonical row confirmed. |
| `confidence_score` | Maximum of the two values. |
| `scraped_url` | Alias URL fills a blank canonical URL. A different nonblank alias URL is discarded. |
| `country_code` | Alias value fills a blank canonical value. A conflicting nonblank value is discarded. |
| `raw_payload` | Alias payload fills a falsey canonical payload. If canonical payload is already nonempty, alias payload—including any audio/subtitle metadata in it—is discarded. |
| `is_available` | Logical OR. |
| `last_scraped_at` | Later non-null timestamp wins. |
| Other fields | Canonical row remains authoritative; the merge helper does not combine them. |

A duplicate-provider scraper row without a title/year twin keeps its mapping
row and is reassigned to the canonical provider, then ingested again.

The migration also redirects or deduplicates related records:

- `Subscription` rows are redirected; a second subscription for the same user
  and canonical provider is deleted. `ExternalProviderMapping` rows are
  redirected; duplicate `(provider, integration_name)` rows are deleted.
- `ProviderContent`, `ContentMapping`, and `Offer` rows are moved to the
  canonical provider with their provider-dependent identity rebuilt. If the
  save raises **any** `IntegrityError`, `_repoint` deletes the row, assuming a
  uniqueness collision. This can discard a row's conflicting metadata such as
  provider URL or audio/subtitle fields; the migration does not field-merge
  these canonical records before deletion.
- `SyncRun` and `SyncCursor` are repointed; a uniqueness collision also causes
  the alias row to be deleted. Duplicate `Region` rows are removed by provider
  slug and country. Finally, the duplicate `Provider` is deleted.

Foreign keys are explicitly repointed for the related models present at that
migration point; duplicate rows are deleted where unique keys collide. The
post-migration schema enforces foreign keys, but the current database cannot
prove which historical relation or metadata value won each collision. The
legacy scraper bridge stores languages in `raw_payload`; migration `0033`
does not merge two nonempty payloads. Canonical `Offer` rows store languages
separately, but a colliding alias offer is deleted rather than language-merged.

## Migration `0050` clone validation

Migration `0050_scraped_provider_native_identity` was applied only to the
restored disposable clone. Before and after it:

- Mapping count remained 3,300; IDs remained distinct from 1 through 6,282.
- A hash over all existing mapping fields (including title, year, content
  type, URL, country, raw payload/language metadata, movie link, confirmation,
  confidence, availability and timestamps) was identical:
  `eeef994830d4c75efdb0d7ad3b0e94fc`.
- All legacy `provider_content_id` values remained empty.
- The `uniq_scraped_content_mapping_legacy` and
  `uniq_scraped_content_mapping_native` partial unique indexes were present.
- A transactional ORM probe promoted a legacy row in place, retained its
  confirmed movie association, updated the same native ID idempotently,
  represented a second same-title/year movie ID separately, represented the
  same native ID separately for series, and rejected duplicate native identity
  within the same content type. The probe transaction rolled back; mapping
  count and IDs returned unchanged.
- Restoring the backup into a fresh disposable database returned the original
  0049 schema, 3,300 mappings and the identical all-fields hash. This verifies
  restore-based rollback for the clone.

`0050` is validated for the clone's current post-merge data. It does not repair
or reconstruct the 411-row pre-migration difference and was not applied live.

## Recurrence prevention

`docker compose run` replaces the service command but retains its image
entrypoint. Docker's documented `--entrypoint` option is required to override
that behavior; the previous validation omitted it, so the app startup script
ran before the test command. [Docker Compose `run` reference](https://docs.docker.com/reference/cli/docker/compose/run/).

The backend entrypoint now performs only `migrate --check` and static-file
collection. The worker performs only the read-only migration check. Actual
schema writes are available through the `migrate` service in the `ops` profile;
its wrapper requires a matching database name, host and explicit confirmation
token. Backend tests run through `scripts/test-backend-isolated.sh`, which
creates a unique Compose project with a separate PostgreSQL service on tmpfs
and removes that test environment on exit.

The isolated backend suite first exposed a mismatch in its own test settings:
the existing Compose proxy topology test sends `HTTP_HOST=backend:8000`, while
the new test service initially allowed only `localhost`. The test service now
allows `backend`; the focused case and the complete isolated backend suite
passed afterward (3,223 tests). The temporary restore and rollback databases
were dropped after validation. Final live checks remained at 3,300 mappings,
zero discovery-state rows, tracker migration `0049`, and no `0050` history row.

## Remaining limitation

The post-migration database is backed up and restorable, and `0050` is
validated on a clone. The pre-migration 3,711-row state was not preserved in
the artifacts found. Recovery of any individual record or metadata value
consolidated by `0033` remains impossible unless another external pre-migration
backup is located.
