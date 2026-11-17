# Live database migration incident audit

Audit date: 2026-11-17
Repository reference: `7617f73` (`new-sources`)
Verdict for historical integrity: **unverified**

## Current Compose database and backup

The inspected Compose target is project `new-sources`, service `db`, image
`postgres:16-alpine`, database `tracker`, user `tracker`, host `db`, volume
`new-sources_pgdata` mounted at `/var/lib/postgresql/data`. The backup command
ran directly inside the PostgreSQL container with `pg_dump`; it did not start
the backend or its entrypoint.

The current post-migration custom-format backup is stored outside the
repository at:

```text
/tmp/codex-db-audit-7617f73/new-sources-tracker-postmigration.dump
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
