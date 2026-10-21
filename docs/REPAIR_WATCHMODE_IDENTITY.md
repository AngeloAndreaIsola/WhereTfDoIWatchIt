# Repairing collapsed Watchmode provider-content identities (F4)

## The defect

`WatchmodeSource` keyed `ProviderContent` on `wm:{source_id}`, where
`source_id` is Watchmode's **provider** id:

| `source_id` | provider |
|---|---|
| 203 | Netflix |
| 296 | Tubi |
| 157 | Hulu |
| 372 | Disney+ |
| 444 | Paramount+ |
| 387 | Max |
| 391 | Pluto TV |

A Watchmode `/title/{id}/sources/` row therefore produced the same
`provider_content_id` for *every* title that provider carried. The
consequences, all observed in the local database:

* one `ProviderContent` row per provider, titled after whichever title was
  refreshed first (e.g. `tubi|wm:296|movie||`, title `Hannibal`);
* one `ContentMapping` for that first title (`wm:296 -> tmdb 391`);
* every other title's offer kept its correct `tmdb_id` but shared the
  collapsed identity, so reconciliation reported `mapping points to tmdb 391;
  also active for tmdb …` and marked **24 of Tubi's 25** offers unavailable;
* the availability report still counted the raw rows, so
  `/api/prime/movie/5/` said `available_on: ["prime","tubi"]` while the
  reconciled Tubi offer was hidden — the two read paths disagreed.

The collision is both **between titles at one provider** and **across
providers**: seven collapsed rows, one per streaming provider.

## The fix

`availability.services.watchmode_identity.build_watchmode_content_id`
defines the identity as:

```
wm:{provider_source_id}:{content_type}:tmdb-{tmdb_id}
```

with `imdb-{id}` and `title-{slug}` as documented fallbacks for a name-only
search, and no region in the key (one provider content entry has offers in
several regions). `WatchmodeSource._content_id` uses it; `SOURCE_VERSION` is
now `watchmode-v2`.

No schema migration is required: `ProviderContent.identity` is already a
unique string and `provider_content_id` is a `CharField(100)`.

Historical rows are repaired by an idempotent, reviewed command:

```
python manage.py repair_watchmode_identity            # dry run (default)
python manage.py repair_watchmode_identity --json     # machine-readable plan
```

## Gate 1 — the new code must be deployed first

The repair is only stable while the `watchmode-v2` writer is live. Old code
running after the repair would re-create `wm:{provider}` rows (it would not
corrupt the split rows, but it would re-introduce the defect). Do not run the
repair on a host still serving the pre-fix image.

## Gate 2 — writers paused, backup verified

1. Stop the scheduler and the refresh workers so no Watchmode refresh can start:

   ```bash
   crontab -l > /tmp/crontab.before            # keep for restoration
   crontab -r                                  # or comment the availability lines
   docker compose stop worker catalogue-worker
   ```

2. Back up and **prove the backup restores** before touching data:

   ```bash
   mkdir -p ./backups && chmod 700 ./backups
   docker compose exec -T db pg_dump -U tracker -d tracker \
       --no-owner --no-privileges > ./backups/pre-f4-$(date +%Y%m%dT%H%M%S).sql

   # restore drill into a scratch database, then drop it
   docker compose exec -T db psql -U tracker -d tracker -c \
       "CREATE DATABASE f4_restore_check;"
   docker compose exec -T db psql -U tracker -d f4_restore_check -q \
       < ./backups/pre-f4-<stamp>.sql
   docker compose exec -T db psql -U tracker -d f4_restore_check -tAc \
       "select count(*) from availability_offer;"
   docker compose exec -T db psql -U tracker -d tracker -c \
       "DROP DATABASE f4_restore_check;"
   ```

   The scratch count must equal the source-database count printed by
   `select count(*) from availability_offer;`.

3. Record the pre-state (used again in post-verification):

   ```sql
   select count(*) from availability_providercontent
    where provider_content_id like 'wm:%' and provider_content_id not like 'wm:%:%';
   select count(*) from availability_offer where provider_content_id like 'wm:%';
   select count(*) from availability_contentmapping where provider_content_id like 'wm:%';
   ```

## Gate 3 — review the dry run

```bash
docker compose exec -T backend python manage.py repair_watchmode_identity
```

The command prints, per collapsed row: the row being retired, every per-title
row it will create, how many offers move, and whether a mapping is moved or
created. It also prints a SHA-256 digest over everything apply will write: the
derived content rows (including title, release date, provider URL and the
observation window), every offer identity move, evidence/price rekeys and their
planned merge targets, rows to retire, and blocked rows. Generation time is not
part of the digest, so an unchanged plan produces the same digest on every dry
run; any changed row or merge decision changes it. Example from a full local
copy of the catalogue:

```
collapsed_rows                     7
content_rows_to_create_or_update   141
offers_to_move                     183
mappings_to_move                   7
mappings_to_create                 134
evidence_rows_to_move              0
price_rows_to_move                 0
blocked_rows                       0
Digest: 16cff63984bf9c48fd577c59ee9e3857db3d7a79ae06ea7f0bb116d15b2c487d
```

**A non-zero `blocked_rows` is a stop condition.** The command refuses to
apply while any row is blocked; each block names the row and the reason
(no offers and no mapping, multiple mappings, a mapping linked to another
content row, a target identity that already exists). Resolve those rows — they
are genuinely ambiguous — and run a new dry run. Derived identities are capped
at the 100-character column limit by a deterministic hash, so an over-long title
cannot block the repair.

## Scratch rehearsal (before Gate 4)

Rehearse the exact apply against an isolated copy restored from the Gate 2
backup. None of these commands may name the active database. PostgreSQL folds
unquoted names, so keep the scratch name lowercase.

```bash
SCRATCH=wtf_f4_scratch_$(date -u +%Y%m%dt%H%M%Sz)
docker compose exec -T db sh -c \
    'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
     -c "CREATE DATABASE \"$1\""' sh "$SCRATCH"
docker compose exec -T db sh -c \
    'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1' sh "$SCRATCH" \
    < ./backups/pre-f4-<stamp>.sql

# Run the Gate 2 pre-state queries and the Gate 3 dry run against the copy.
docker compose run --rm --no-deps --entrypoint python \
    -e POSTGRES_DB="$SCRATCH" backend manage.py repair_watchmode_identity

# Apply only the reviewed scratch digest, then run the Gate 5 checks with
# -d "$SCRATCH" instead of the active database. A second dry run must print
# "No collapsed Watchmode identities found."
docker compose run --rm --no-deps --entrypoint python \
    -e POSTGRES_DB="$SCRATCH" backend manage.py repair_watchmode_identity \
    --apply --expect-digest <scratch-digest> --assume-workers-paused
docker compose run --rm --no-deps --entrypoint python \
    -e POSTGRES_DB="$SCRATCH" backend manage.py repair_watchmode_identity

# Drop only the explicitly named scratch database after verification.
docker compose exec -T db sh -c \
    'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
     -c "DROP DATABASE \"$1\""' sh "$SCRATCH"
```

The `POSTGRES_DB` override is the only connection change from Gate 4. If the
restored copy has drifted, its digest changes and the apply refuses; that is the
intended fail-closed behavior. Verify `current_database()` before applying and
keep the scratch name in the audit record.

## Gate 4 — apply

```bash
docker compose exec -T backend python manage.py repair_watchmode_identity \
    --apply \
    --expect-digest 16cff63984bf9c48fd577c59ee9e3857db3d7a79ae06ea7f0bb116d15b2c487d \
    --assume-workers-paused
```

`--expect-digest` is mandatory; the plan is rebuilt inside the transaction and
the command aborts if the database changed since review. `--assume-workers-paused`
is an explicit operator acknowledgement; the command separately refuses if any
`watchmode` `SyncRun` is still `running`. All writes happen in one
`transaction.atomic()` after `SELECT … FOR UPDATE` on every affected row.

What it writes, in order: per-title `ProviderContent` rows, the moved/created
`ContentMapping` rows, the re-keyed `Offer` rows (identity recomputed), then
`OfferEvidence` / `OfferPrice` rows keyed by the new offer identity, and
finally deletes the collapsed rows after asserting nothing still points at
them.

## Gate 5 — post-verification (target environment, read-only)

```sql
-- 1. no collapsed identity remains
select count(*) from availability_providercontent
 where provider_content_id like 'wm:%' and provider_content_id not like 'wm:%:%';   -- 0

-- 2. counts moved, nothing else did
select count(*) from availability_offer where provider_content_id like 'wm:%:%';             -- 183
select count(*) from availability_providercontent where provider_content_id like 'wm:%:%';  -- 141
select count(*) from availability_contentmapping where provider_content_id like 'wm:%:%';   -- 141

-- 3. the previously hidden offer is now the right identity
select provider_content_id, country_id, identity
  from availability_offer
 where tmdb_id = 5 and provider_id = (select id from tracker_provider where slug='tubi');
```

```bash
# 4. reconciliation no longer reports mapping doubt for a split title
docker compose exec -T backend python manage.py shell -c "
from availability.services.query import AvailabilityQueryService
for c in ('CA','NL','JP'):
    r = AvailabilityQueryService().get_reconciled_for_content(5,'movie',c)
    print(c, [(o.provider, o.currently_available, [x.kind for x in o.conflicts]) for o in r.offers])
"
```

Expected: Tubi/CA available with no `mapping` conflict (a `quality` conflict is
a legitimate source disagreement and is not a regression).

```bash
# 5. idempotent: a second dry run finds nothing
docker compose exec -T backend python manage.py repair_watchmode_identity
```

Then restore the scheduler and workers:

```bash
docker compose start worker catalogue-worker
crontab /tmp/crontab.before
docker compose exec -T backend python manage.py refresh_provider_catalogue --source watchmode
```

## Rollback and forward recovery

The repair **deletes** the seven collapsed `ProviderContent` rows after their
offers, mappings and evidence have been moved. That deletion is the only
irreversible step, and it is deliberate: a provider id is not a content id and
the row cannot be made truthful.

* **Before apply** (any failed gate): nothing was written; restore the
  scheduler and stop.
* **After a failed apply**: the transaction rolled back; the dry run will
  produce the same digest (or a new one if the underlying data moved — review
  it again).
* **After a successful apply, if a problem is found**: restore the pre-apply
  dump into the existing database:

  ```bash
  docker compose stop backend worker catalogue-worker frontend
  docker compose exec -T db psql -U tracker -d tracker -c "
    DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
  docker compose exec -T db psql -U tracker -d tracker < ./backups/pre-f4-<stamp>.sql
  docker compose start db backend frontend worker catalogue-worker
  ```

  This also reverts anything else written after the dump, so take it
  immediately after the repair if you intend to use it as the rollback point.
* **Forward recovery** is preferred when the app code is correct: re-run the
  dry run. The repair is idempotent, so a partially observed state converges
  from either side.
* **Do not roll the image back after applying.** The old `watchmode-v2`
  predecessor would re-create `wm:{provider}` rows on the next refresh; the
  split rows would survive, but the defect would return. Roll back the image
  only together with a database restore.

## Evidence recorded before applying (2026-10-21)

Verified against the local Docker stack and a scratch copy restored from a
`pg_dump` of the development database — no other database was touched.

| Check | Result |
|---|---|
| Collapsed rows | 7 (`wm:203/296/372/387/391/444/157`) |
| Offers on those rows | 183 |
| Mappings | 7 moved, 134 derived (same TMDb id the offer already carried) |
| Evidence / price rows | 0 / 0 in this database |
| Blocked rows | 0 |
| Non-Watchmode offers/contents/mappings | byte-identical hash before and after apply |
| Reconciliation after apply | `tmdb 5` Tubi/CA available, no mapping conflict |
| Idempotency | second dry run: “No collapsed Watchmode identities found.” |
| Backup/restore drill | 29 MB dump restored into `wtf_f4_scratch_20261021t130604z`; full apply reached 0 collapsed rows, 141 contents, 141 mappings, 183 offers, 0 dangling references; non-Watchmode hashes unchanged; second dry run empty; scratch dropped |
| Cross-source corroboration | 141 / 183 offers corroborated by another source with an active offer; 0 contradicted; 42 uncorroborated (absence is not evidence). Deferred recheck list: `docs/WATCHMODE_UNCORROBORATED_OFFERS.md` |

**Remaining gap:** upstream re-verification through the Watchmode API was
attempted and **blocked by the account quota** (`HTTP 429`). The 183 offers are
therefore attributable to the TMDb id each refresh requested (by construction),
corroborated as above, but not re-checked against Watchmode in this session.
This is why the F4 claim is “code and repair verified”, not “upstream-verified”.
