# Stage 2H — Read-only preflight results

Observation time: `2026-11-14 08:19 UTC`.

## Scope and result

No provider endpoint was contacted. No migration, authorization, queue write,
source activation, worker startup, or database write was performed.

The required merge condition was not met. At inspection time PRs #78, #79,
#80, and #81 were all open. Fetched `origin/master` was
`00d9dc843c558dc2000f0bb4fba055671c5d012f`; it contains neither the Stage 2H
runner nor migration `availability.0015_availability_experiment_ledger`.
The A7/A6 branch and the Stage 2G implementation were inspected as proposed
code only; they are not deployed master contracts.

## Database identity and migration state

Compose configuration maps `POSTGRES_DB` into Django and mounts named volume
`pgdata` at `/var/lib/postgresql/data`. The local `.env` has `POSTGRES_DB=tracker`
and `POSTGRES_HOST=db`; this verifies the configured database name, not that a
running container is using the named volume. The prior Stage 2D environment
inventory identifies that deployment volume as `wheretfdoiwatchit_pgdata`.

The Docker CLI could not connect to its daemon socket, so the target container,
volume mount, and target PostgreSQL instance could not be inspected. I did not
attempt to start Docker. A separate host PostgreSQL 18 instance was reachable
at `127.0.0.1:5432`, with data directory `/opt/homebrew/var/postgresql@18`.
That is not the Compose volume and its contents do not match the preserved
Stage 2D snapshot; its results below are explicitly non-target diagnostics.

Read-only migration query against that separate host database `tracker`:

| App | Latest applied migration | Stage 2G ledger table |
|---|---|---|
| availability | `0014_wave2a_verified_mapping_acceptance` | absent |
| tracker | `0046_availabilityrefresh_country` | n/a |

The Stage 2G migration is absent from current master and from this non-target
database. Applying migration `0015_availability_experiment_ledger` to the
actual application database would require separate authorization. No migration
was applied. The actual Docker-volume migration state remains unknown.

The Stage 2H command preflight is **blocked**: its runner and ledger schema are
not present on current master, and the target Docker database was unreachable.
Equivalent read-only SQL diagnostics were run only against the separate host
database; they do not substitute for target-database preflight.

## Findings by required category

### Passed with evidence

- Compose and local `.env` configuration name the application database
  `tracker`; Compose declares persistent volume `pgdata`.
- Current source code registers `justwatch` and `mediaset_infinity` in the
  proposed Stage 2G branch. The historical Stage 2D snapshot reports both
  persisted and enabled in the then-inspected Docker database.
- Stage 2G runner code bypasses ordinary queue selection and uses an explicit
  fixed manifest. It checks exact identity/country, expected offer type, and
  JustWatch title/year resolution; it disables authoritative snapshots and
  drops source withdrawals and unrelated provider-content writes.
- Historical Stage 2D/2E evidence records stale canonical observations and
  does not turn missing rows or aggregator omissions into confirmed
  unavailability. Current host-database query results are not used to upgrade
  that evidence.
- No host `refresh_worker`, cron, or Django application process was observed
  in the process list at inspection time.

### Failed

- PRs #78–#81 are not merged, so current master does not contain the requested
  reports, A7 shared drain lock, A6 configuration-aware freshness contract, or
  Stage 2G ledger/runner.
- The non-target host `tracker` database has no experiment ledger tables and
  has only availability migration 0014. This does not establish the target
  volume's migration state.
- The non-target host database has no `Movie`, canonical `Offer`, modern
  `ContentMapping`, candidate, or legacy mapping rows for TMDb IDs 438631 or
  27205. It therefore cannot establish the target titles' current evidence.
- The preserved Stage 2D/2E snapshot of the Docker database is more informative
  historically: it lists Inception and Dune among the materialized titles and
  Stage 2E records selected-scope canonical offers. Those offers were stale at
  Stage 2D observation time; the aggregate reports do not prove current
  freshness or preserve the GUID/provider URL details needed here.
- No current quota attestation was present in the shell or `.env`; no source
  configuration contained quota evidence. Absence of recent local call-counter
  rows is not evidence that an upstream quota is available.

### Unknown

- Whether the live application still uses database `tracker` on Docker volume
  `wheretfdoiwatchit_pgdata`; Docker daemon access was unavailable.
- Target-database migrations, source rows/configuration, target Movie/Country
  rows, canonical offers/freshness, exact pending queue, refresh runs, and
  experiment reservations.
- Whether Docker worker/catalogue-worker containers or host cron jobs are
  running. No host processes were observed, but Docker was unavailable.
- Target-database advisory locks. The separate host database had zero granted
  advisory locks at query time; current master lacks A7, and this is not a
  target observation.
- Current JustWatch or Mediaset rate/quota status. Historical “unmetered
  public” labels are not a live quota check.

### Requires authorization

- Apply the Stage 2G migration to the actual database after PR #81 and its
  prerequisites merge; this audit did not apply it.
- Obtain a read-only connection to the confirmed Docker-volume database and
  rerun the command preflight there.
- Retrieve new primary-provider evidence pages. Existing records do not
  provide exact primary-provider page URLs for these three cases.
- Any live source execution, quota attestation, or authorization-token
  creation.

## Non-target host database snapshot

The separate Homebrew PostgreSQL `tracker` database was inspected only in
`BEGIN READ ONLY` transactions. It contained JustWatch and Watchmode source rows
(both enabled, with only `expected_refresh_interval_seconds` configuration
keys); no Mediaset row. IT and NL Country rows were enabled. No Movie or Offer
rows existed for the selected IDs. Existing refresh requests were all `done`,
with no pending rows. There were zero unfinished `RefreshRun` rows and zero
granted advisory locks at the observation instant. These facts are useful only
to explain the mismatch with the target; they do not certify the Docker
database.

The host database had no selected-title ContentMapping, provider-content
candidate, external-provider mapping, or legacy scraped mapping. API call
counter tables had no rows in the prior seven days. These counters would not
prove provider quota availability even if they belonged to the target.
