# Incremental unofficial canonical reconciliation

This continues the completed 5,095-discovery inventory. It uses the existing
collectors, canonical bootstrap verifier, TMDb candidate gateway, classification
writer, provider advisory locks and `refresh_worker --lane catalogue` scheduler.
It creates catalogue identities and verified canonical associations only. It
never creates offers or changes streaming availability. Language enrichment is
outside this workflow.

## Eligibility and the completed baseline

Migration `0057` adds durable per-discovery outcomes and provider run leases.
Migration `0058` acknowledges existing discoveries without evaluating matches,
requesting metadata, or changing discoveries, canonical identities, mappings,
provider settings or discovery checkpoints. Existing associated records are
marked `ASSOCIATED`; unresolved records are `BASELINED` with no timed retry.
It can be replayed without replacing an existing reconciliation outcome.
The acknowledgement is bounded to the captured inventory (record IDs through
5,095, updated through `2026-11-17T23:09:14.771940Z`). Records added or edited
after that boundary remain eligible on deployment. Its processed timestamp is
the captured boundary, so relevant cache evidence arriving later can also
unlock an unresolved record.

The incremental selector reads matching-input fingerprints and existing
association/cache identities. It scans the stored inventory for eligibility;
it does **not** run matching on the inventory. Only a bounded selection enters
the shared verifier. Eligible observations are:

- Discoveries without a reconciliation outcome, including imports and RSS GUIDs.
- Records whose meaningful matching inputs changed, regardless of their ID.
- Previously associated records whose canonical mapping is now missing.
- Unresolved records with new, fresh, relevant positive TMDb cache evidence or
  new record-specific verification evidence.
- Records whose persisted retry time is due.
- Explicitly requested review IDs, capped by the run's record limit.

Matching inputs include title, provider/native identity, identity/source URLs,
access/provenance policy, partition and RSS type evidence, years, trusted
external IDs, original title and matching summaries. Pagination, feed build
times, observation timestamps and URL history do not trigger matching.
The versioned input-fingerprint helper is kept stable for migration replay.
Existing bootstrap file checkpoints retain their original fingerprint format.

The outcome stores `BASELINED`, `ASSOCIATED`, `AMBIGUOUS`, `UNMATCHED`,
`CONFLICTING`, `ERROR`, `CACHE_MISS`, `BUDGET_EXHAUSTED`, or `BLOCKED`, with an
attempt count, input fingerprint, evidence/reason and next retry time.
Missing type/year, ambiguity, conflicts and offline cache misses have no timed
retry; new inputs/evidence or bounded review must unlock them. A fully evaluated
unmatched title with a known type/year retries after 30 days. Transport errors
back off from five minutes to one day. Budget exhaustion defers the unfinished
record for one minute; an explicit review can resume it immediately. Service
restrictions honor a persisted cooldown of at least five minutes and any longer
`Retry-After`. A cooldown also blocks explicit review until it expires.

## Verification and association safety

The shared `CanonicalVerifier` implements the original bootstrap's exact
title/year/type and search-completeness checks. It retains the competing-source
release guard and trusted-ID details verification. Movie and series identifiers
remain separate `(tmdb_id, content_type)` identities. The existing conservative
fuzzy matcher remains available for proposals; fuzzy proposals do not become
incremental associations without record-specific verification.

Existing canonical identities can be reused with fresh TMDb details, including
an agreeing original title. Cached identity reuse cannot bypass a fresh search
response with ambiguity or a negative result. Search/details caches remain
scoped to media type, locale and endpoint version. Expired or negative details
cannot verify an association. Record-specific proof expires after 30 days for
new association writes; an existing association is preserved.

Meaningful edits to an associated observation cause reevaluation. Refreshed
proof may update the same canonical identity, but cannot change its target.
The classification writer also preserves existing provider/operator mappings;
a differing target becomes `CONFLICTING`. Canonical metadata is never replaced
by this worker. Source identity evidence is rechecked under a row lock before
commit. Restricted records and unresolved AnimeWorld archive classifications
are not used to manufacture associations or retry archives/domains.

## Commands and recovery

Commands target the database selected by Django's environment. Run these against
a disposable database first. Production migration/application requires separate
authorization; this implementation does not enable deployment or schedules.

```sh
# Read persisted status and bounded eligible IDs; no verification or writes.
python manage.py reconcile_incremental_catalogue --provider hydrahd --status

# Evaluate eligible observations using fresh caches; no writes by default.
python manage.py reconcile_incremental_catalogue --provider hydrahd --limit 25

# Explicit bounded apply; network must be separately authorized with this flag.
python manage.py reconcile_incremental_catalogue --provider hydrahd \
  --limit 25 --request-budget 40 --allow-network --apply

# Reconsider one deferred observation. Ordinary runs never force all unresolved IDs.
python manage.py reconcile_incremental_catalogue --provider animeworld \
  --record-id 123 --review --limit 1 --request-budget 2 --apply
```

Runs allow 1–250 observations, 1–500 HTTP attempts, 0–2 retries and 0–60 seconds
between requests. Every HTTP attempt, including retries and failed requests,
reserves budget before dispatch. Fresh cache reads consume no HTTP budget.
Offline mode is the default; network mode requires a TMDb key. Dry runs neither
write cache entries nor create progress/identity/mapping rows.

The existing PostgreSQL advisory-lock mechanism excludes competing jobs for the
same provider. A separate provider reconciliation lease expires after 15 minutes.
A live database lease rejects reentry even on a reused connection; an expired
lease can be recovered once the advisory lock is available. Ownership and expiry
are rechecked on each commit, so a replaced worker cannot write progress.

HTTP verification happens before the atomic commit. Evidence, safe association,
record outcome and successful run checkpoint then commit together. A crash
inside that transaction rolls all of them back. A crash after commit leaves an
ineligible completed record. Cached searches from a budget interruption can be
reused on resume. The last-completed ID is observational only; it is never used
as a high-water filter that would lose later edits to older discoveries.
Discovery and reconciliation checkpoints/locks are independent. A `KeyboardInterrupt`
retains committed progress and marks the run interrupted; an ungraceful process
death retains progress for lease recovery. Reports expose per-record reasons,
outcomes, request/cache counters, restrictions, errors and lease recovery.

## Scheduler configuration

`UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES` is empty by default in Compose and
`.env.example`. The existing catalogue worker receives this variable. All
provider/mode switches below are deliberately disabled; this is a template,
not an enabled deployment configuration.

The [conservative canary template](INCREMENTAL_RECONCILIATION_CANARY.json) also
keeps every switch and network access disabled. It proposes at most five
reconciliation records / ten HTTP attempts, two-second spacing, no retries,
and daily intervals. Discovery budgets are one historical page or two
incremental requests for HydraHD, and one permitted RSS request for AnimeWorld.
AnimeWorld historical archive discovery is not configured. A future authorized
canary should run one provider once with explicit record IDs, inspect its
associations, request counters and zero-offer result, then obtain authorization
before enabling any recurring mode. This template is not loaded automatically.
Schedule enable flags require JSON `true`; quoted boolean strings never enable
a mode, and `allow_network` must be a JSON boolean.

```json
{
  "enabled": false,
  "providers": {
    "hydrahd": {
      "enabled": false,
      "historical": {
        "enabled": false, "interval_seconds": 86400,
        "request_budget": 8, "delay_seconds": 1, "retry_limit": 1
      },
      "incremental": {
        "enabled": false, "interval_seconds": 21600,
        "request_budget": 8, "overlap_pages": 3, "delay_seconds": 1
      },
      "reconciliation": {
        "enabled": false, "interval_seconds": 3600,
        "record_limit": 25, "request_budget": 8,
        "delay_seconds": 1, "retry_limit": 1, "allow_network": false
      }
    },
    "animeworld": {
      "enabled": false,
      "incremental": {"enabled": false, "request_budget": 2},
      "reconciliation": {"enabled": false, "record_limit": 10, "request_budget": 8}
    }
  }
}
```

Historical discovery, incremental refresh and reconciliation have separate
enable switches, due times, request budgets and checkpoints. Scheduled requests
are capped at 40 per provider/mode, record limits at 250, and intervals at 60
seconds–31 days. Cooldowns and existing provider locks are enforced inside the
run as well as during scheduling. Errors are reported without preventing other
provider/mode runs. AnimeWorld remains limited to its permitted recent RSS feed;
there is no historical archive collector or new provider-domain fallback.

## Disposable-clone rehearsal

Restore a current `pg_dump` into a separately named PostgreSQL container/database.
Before applying migrations to that restored clone, verify migration compatibility:

```sh
python scripts/validate_incremental_migrations.py \
  --database incremental_catalogue --host YOUR_CLONE_HOST \
  --output /evidence/clone-migrations.json
```

This guarded rehearsal applies, reverses and reapplies `0057`/`0058`, verifies
all original application-table hashes and the deferred baseline, and leaves the
clone migrated. It requires the restored `0056`/`0016` baseline and the audited
completed inventory. All commands here must use the clone's environment.

With the runner configured for the migrated clone, run:

```sh
python scripts/validate_incremental_catalogue.py \
  --database incremental_catalogue --host YOUR_CLONE_HOST \
  --output /evidence/clone-rehearsal.json
```

The script requires a PostgreSQL database whose name starts with `incremental_`,
checks the exact supplied host/database, and requires the completed inventory
counts. It intercepts all HTTP and rolls every simulated operation back. It
checks every application table before/after the rollback. The matching cache
responses exercise the real gateway budget/verification code; they do not
represent live TMDb verification of a newly released real-world title.

```sh
./scripts/test-backend-isolated.sh \
  tracker.tests.test_catalogue_discovery tracker.tests.test_catalogue_identity \
  tracker.tests.test_canonical_identity tracker.tests.test_canonical_bootstrap \
  tracker.tests.test_catalogue_reconciliation \
  tracker.tests.test_incremental_catalogue_reconciliation \
  tracker.tests.test_catalogue_discovery_scheduler
./scripts/test-backend-isolated.sh tracker availability -v 1
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
```
