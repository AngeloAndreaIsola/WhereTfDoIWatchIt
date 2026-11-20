# Issue #113 fixture isolation and historical quarantine

## Provenance and inventory

The serving stack is Compose project `wheretfdoiwatchit`. The read-only snapshot
on 2026-11-20 came from database `tracker` in `wheretfdoiwatchit-db-1` at tracker
`0058` and availability `0016`. The database for `new-sources` is separate.

`seed_prime_fixture` can write a caller-provided TMDb ID, title and year into
`tracker_movie` and persist cached availability. The bundled fixture describes
The Tomorrow War (movie 588228), which does not match these rows. `loaddata` can
also load serialized Movie rows. The default PostgreSQL settings point to
`tracker@db`; the normal Django runner derives a separate test DB, and the
existing isolated Compose flow uses a separate tmpfs PostgreSQL cluster. There
is a matching `BehindA404` sentinel (TMDb 33) in
`tracker/tests/test_availability_report_evidence.py`. The matching local shape
and October refresh timestamps do not identify the write path. The other
seed commands examined populate provider/region configuration rather than
load synthetic Movie fixtures. The original insertion mechanism remains unproven.

The fixture and test protections require an explicit opt-in and verify both
the configured and server-reported database and dedicated role. The role must
own the database and the cluster must have no serving `tracker` database.
The test runner also rejects a redirected test database name or mirror.
Use `scripts/test-backend-isolated.sh tracker availability` for PostgreSQL tests.
The Prime demo requires a migrated disposable `db-test`; set
`WTDWI_DISPOSABLE_TEST_DB=1` only for that environment.

All observed rows are in the movie namespace. At the verified snapshot each
had zero watchlist memberships, user-data rows, watch events, cached
availabilities, language rows, legacy scraper links, canonical bootstrap
links, typed provider mappings, mapped provider observations, typed offers,
offer evidence, prices, provider candidates, verified candidate audits, or
Movie identity audits. The table records the 50 refresh requests by their
stored status. Pending and failed were zero for every row. The November 17
upstream observations below come from the issue report. No fresh upstream
requests were made for this inventory.

| Local PK | TMDb | Display title / year | Done | Blocked | Proposed disposition and evidence |
| ---: | ---: | --- | ---: | ---: | --- |
| 814 | 999901 | X / 2020 | 0 | 0 | Inventory only. Provenance and upstream identity unverified. |
| 818 | 33 | BehindA404 / 2000 | 1 | 0 | Quarantine candidate. Source test sentinel matches. Prior upstream result was Unforgiven. |
| 819 | 9001 | AllFail / 2020 | 0 | 0 | Quarantine candidate with empty metadata and synthetic batch shape. Upstream identity unverified here. |
| 820 | 99001 | Trace / 2000 | 1 | 0 | Quarantine candidate from the sequential Trace/T2/T3/T4 batch. Upstream identity unverified here. |
| 821 | 99002 | T2 / 2000 | 0 | 0 | Quarantine candidate from the sequential batch. Prior upstream result conflicted with T2. |
| 822 | 99003 | T3 / 2000 | 0 | 0 | Quarantine candidate from the sequential batch. Upstream identity unverified here. |
| 823 | 99004 | T4 / 2000 | 1 | 0 | Quarantine candidate from the sequential batch. Upstream identity unverified here. |
| 824 | 875001 | Neg / 2020 | 4 | 4 | Quarantine candidate from the repeated Neg batch. Upstream identity unverified here. |
| 825 | 875002 | Neg / 2021 | 8 | 4 | Preserve for separate review. Prior evidence associates the synopsis, poster, cast and director with Evacuation. |
| 826 | 875003 | Neg / 2020 | 4 | 4 | Quarantine candidate from the repeated Neg batch. Upstream identity unverified here. |
| 827 | 875004 | Neg / 2020 | 3 | 4 | Inventory only. The prior upstream check returned HTTP 502. |
| 828 | 876001 | Neg / 2020 | 3 | 4 | Quarantine candidate from the repeated Neg batch. Upstream identity unverified here. |
| 829 | 877001 | Case / 2020 | 1 | 4 | Quarantine candidate with empty metadata and the same refresh batch pattern. Upstream identity unverified here. |

Quarantine candidates are proposed local rows for review. Before approving a
write, review the exact fingerprint and any available independent upstream
evidence for each selected row. A real TMDb record resolving to an affected
typed ID requires a separate provenance and identity decision before the
application may reuse that ID. Review PK 825 as its own correction with its
12 refresh references intact.

## Read-only inventory and approved operation

Run these examples from the approved application image with its intended
database settings. The full inventory requires tracker `0061`. The first
command is read-only. It reports refusals for inventory-only rows and prints a
before and projected after snapshot for each candidate.

```sh
python manage.py quarantine_fixture_movies \
  --movie-id 814 --movie-id 818 --movie-id 819 --movie-id 820 --movie-id 821 \
  --movie-id 822 --movie-id 823 --movie-id 824 --movie-id 825 --movie-id 826 \
  --movie-id 827 --movie-id 828 --movie-id 829
```

For approved application, pause workers and app writes. Take a fresh custom
format PostgreSQL dump, verify the SHA-256, restore it into an isolated clone,
and rerun the inventory against that clone and the exact serving target.
Keep artifacts private. Select only the IDs specifically approved, then add
`--apply --confirm-target 'tracker@db:5432'` after independently confirming the
actual target. Save the JSON output with the backup manifest. The command
locks the selected Movie rows in PK order and refuses the entire batch on
identity drift, existing legitimate state, or a withheld ID. It records
the quarantine flag, exact identity, reference counts and audit ID without
deleting the Movie or changing historical refresh rows.

Verify the ordinary catalogue and watchlist read paths before resuming writers.
Check that the audit and refresh rows remain readable and that the same-title
legitimate records and unresolved provider observations remain present.

For rollback, run the same approved IDs with `--restore` to preview, then add
`--apply --confirm-target 'tracker@db:5432'`. This restores visibility on the
same Movie PK and leaves both the quarantine and restoration audit records.
Retain the schema and audit evidence during a flag rollback.

## Migration rehearsal

A fresh custom-format serving snapshot was SHA-256 verified and restored into
the isolated PostgreSQL 16 database `wtdw_issue113_rehearsal`. The forward
plan comprised tracker `0059` → `0060` → `0061` and no availability changes.
The digest of all 828 original PK, typed identity and display-title tuples
matched before and after. An audit row inserted at `0059` retained its payload
and original Movie PK at `0060`, and survived Django deletion of its synthetic
Movie with the nullable FK cleared. `makemigrations --check --dry-run` and
`manage.py check` passed. The rehearsed health, catalogue, unified catalogue and
watchlist endpoints returned HTTP 200 against the clone.

Applying the ten candidate flags to the clone a second time was idempotent.
PKs 814, 825 and 827 remained visible. The rollback restored all ten flags,
kept the 50 refresh requests, and retained both audit entries for each
changed Movie.
