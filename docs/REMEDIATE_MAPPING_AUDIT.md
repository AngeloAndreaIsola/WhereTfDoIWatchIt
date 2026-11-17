# Runbook: `remediate_mapping_audit`

Two-phase, audited removal of the six historical confidence-`0.7` mappings that
resolved provider-native content to the wrong TMDb title (plus their 20 offers
and one evidence row).

The command never decides *what* to change: the allow-list is fixed in
`availability/services/mapping_remediation.py` (`APPROVED_PLAN`). It only proves
the live database still matches that plan, then does exactly that. The default
mode is a read-only dry run.

> **Do not run `--apply` until a separate data-remediation task has been
> explicitly approved.** This document prepares that operation; it is not
> approval to execute it.

> There are no database backups in this workflow. Backups are a **separate,
> manual prerequisite** (see §2); the command neither creates nor restores one.

---

## 0. What the command will change (fixed allow-list)

| Item | Action |
| --- | --- |
| `ContentMapping` `399` | set `tmdb_id=2996`, `match_method=manual`, `confidence=1.0` |
| `ContentMapping` `400`, `386`, `388`, `1`, `4760` | delete |
| `Offer` `3504`–`3519`, `3455`, `3457`, `1`, `10820` | delete (20 rows) |
| `OfferEvidence` `1` | delete |
| `ContentMapping` `128`, `133`, `146`, `151`, `419`, `4369`, `4375`, `4376` | **unchanged**, but guarded |
| `OfferPrice` for the 20 target offers | none expected; any such row aborts |

Expected counts: **6** target mappings, **20** target offers, **1** target
evidence row, **0** target `OfferPrice` rows, **8** protected mappings.

The command refuses to run if any target row is missing or changed, if an extra
row appears inside the targeted provider-content scope, if a targeted
`OfferPrice` row exists, if **any** orphan already exists, if the eight protected
mappings are missing or changed, if a manifest was edited, if a manifest comes
from another database instance, if the process's matcher or write-barrier code
is stale, or if the exclusive write barrier cannot be acquired.

It does **not** run `OfferEvidenceRepository.purge_orphans()` or
`OfferPriceRepository.purge_orphans()`. Orphan cleanup is deliberately narrow:
only evidence id `1` is deleted, and a pre-existing unrelated orphan aborts the
run instead of being swept up. Unrelated evidence and price rows are not
touched, including when the transaction fails.

---

## 1. Generate and review the manifest (read-only)

```bash
# print the manifest to stdout; writes nothing anywhere
docker compose run --rm --no-deps -v "$PWD/backend:/app" \
  --entrypoint python backend manage.py remediate_mapping_audit

# or persist it for review (a manifest file, never a database backup)
docker compose run --rm --no-deps -v "$PWD/backend:/app" \
  --entrypoint python backend manage.py remediate_mapping_audit \
  --write-manifest /tmp/remediate-manifest.json
```

The output includes:

* the **manifest schema** (`…/manifest-v3`);
* the **database identity**: vendor, server-reported name, configured host/port,
  server address/port, cluster identifier when readable, and an identity
  strength;
* the row counts and protected-mapping count;
* `local matcher fix` and `writer barrier code` presence;
* the **manifest digest** (`sha256:…`) and its 12-character confirmation prefix.

Read the summary and confirm: 6 target mappings, 20 target offers, 1 target
evidence row, 0 target `OfferPrice` rows, 8 protected mappings. Review the
captured rows and the `protected_mappings` section, then record the full
**manifest digest** and its first 12 hex characters.

### Database identity (manifest v3)

Manifests v1 and v2 are rejected. A v3 manifest records:

* vendor and server-reported database name (`current_database()`);
* configured host and port (context only);
* PostgreSQL server address and port (`inet_server_addr()` /
  `inet_server_port()`), which are `(unix socket/unknown)` when the server
  reports none;
* the cluster's `system_identifier` when the role may read
  `pg_control_system()`.

A manifest from a server with the same database name but a different address,
port, configured host/port, or cluster identifier is rejected. If PostgreSQL
reports neither a network address nor a readable cluster identifier, the
identity strength is `weak` and the command **fails closed** rather than treating
a configured hostname as proof. Connect over TCP or grant the role permission to
read the cluster identifier, then regenerate the manifest.

The identity contains no user, password, connection string, or other secret.

Pre-flight an already-approved manifest without writing anything:

```bash
docker compose run --rm --no-deps -v "$PWD/backend:/app" \
  --entrypoint python backend manage.py remediate_mapping_audit \
  --manifest /tmp/remediate-manifest.json
# -> "Validation only (no --apply): nothing was written."
```

---

## 2. Prerequisite: backup **and** verify a restore (separate, manual)

Do this **before** the mutation window and keep it outside the command:

```bash
# 1. Dump. Keep it off the app volume.
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" \
  > /secure/backups/tracker-$(date +%Y%m%dT%H%M%S).dump

# 2. Prove the dump restores into a scratch database.
docker compose exec -T db createdb -U "$POSTGRES_USER" tracker_restore_check
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d tracker_restore_check \
  < /secure/backups/tracker-<timestamp>.dump
docker compose exec -T db psql -U "$POSTGRES_USER" -d tracker_restore_check \
  -c "SELECT count(*) FROM availability_offer;"
docker compose exec -T db dropdb -U "$POSTGRES_USER" tracker_restore_check
```

A dump that has never been restored is not a backup. Record the dump path and
the restore-check result. This is **disaster recovery**, not the transaction
rollback described in §6. The command itself never creates, overwrites, or
restores a backup.

---

## 3. Prerequisite: deploy the fixed matcher and writer barrier everywhere

Two pieces of code must be live in every process that can write availability
rows before the mutation:

1. the fixed movie matcher (`availability/services/matching.py`), so the next
   catalogue refresh cannot recreate the wrong mapping;
2. the shared refresh write barrier (`availability/services/write_barrier.py`),
   so a refresh that starts during the mutation is excluded instead of racing.

The command checks the code loaded in the process that applies the mutation; it
**cannot** inspect the worker containers. Verify all of them yourself:

```bash
for svc in backend worker catalogue-worker; do
  printf '%s matcher: ' "$svc"
  docker compose exec -T "$svc" \
    grep -c "A known, conflicting year is evidence against the" \
    /app/availability/services/matching.py
  printf '%s barrier: ' "$svc"
  docker compose exec -T "$svc" \
    grep -c "AVAILABILITY_MAINTENANCE_BARRIER_V1" \
    /app/availability/services/write_barrier.py
done
```

Each must print `1` (or more). If any prints `0`, redeploy that service and
re-run the check. The barrier only excludes writers that actually take it; an
old container with pre-barrier code does not participate.

---

## 4. Prerequisite: exclude every refresh writer for the window

The `pg_locks` count is only a diagnostic. The real protection is a
**shared/exclusive PostgreSQL advisory lock**:

* every participating canonical writer holds the shared lock
  (`pg_try_advisory_lock_shared`) for the duration of its write transaction:
  `AvailabilityRefreshService` (`_persist`, covering `ProviderContent`,
  `ContentMapping`, `Offer`, `OfferEvidence` and `OfferPrice`), the legacy
  scraper ingestion bridge (`ingest_scraped_mapping` /
  `delete_scraped_mapping`) and provider deletion
  (`DELETE /api/providers/<slug>/`);
* the remediation acquires the same key in exclusive mode
  (`pg_try_advisory_lock`). PostgreSQL grants it only when no shared writer
  exists; while it is held, a new writer's shared acquisition fails and that
  writer refuses to write instead of waiting.

The command also fails if **any other advisory lock** is held by another
session, which catches an old pre-barrier worker still running. That check is a
conservative backstop, not the barrier itself.

Stop both worker lanes and disable every scheduled entry for the window:

```bash
# 1. Stop the long-running worker processes.
docker compose stop worker catalogue-worker

# 2. Disable the cron entries that can write availability rows, including:
#      refresh_availability
#      refresh_provider_catalogue
#      refresh_justwatch_catalogue
#    (and any custom scheduler invoking the same management commands).
#    Comment them out or stop the scheduler; do not rely on the database lock
#    to make an unmanaged cron job safe.

# 3. Leave the backend web service up only if it cannot start a refresh.
#    The scraper write paths and provider deletion now take the shared barrier
#    and refuse while the exclusive lock is held, but keep them out of the
#    window operationally. Django-admin deletes, manage.py shell writes and the
#    orphan-purge commands do NOT take the barrier; never run them during the
#    window.
```

Before applying, run the dry run again and confirm it reports:

* `writer barrier code : present`;
* `other advisory locks : 0`;
* the same manifest digest you reviewed.

The exclusive barrier is acquired **before** any mutation and held through the
in-transaction checks and the post-commit checks. If the command cannot prove
exclusion, it aborts before mutation.

---

## 5. Apply (explicit, transactional) — only after separate approval

The confirmation is bound to the exact reviewed manifest. Its syntax is:

```text
TICKET@DIGEST_PREFIX
```

`DIGEST_PREFIX` is the first **12 hex characters** of the `sha256:…` manifest
digest. The command prints both the full digest and the prefix. A ticket alone
is rejected, and a generic word is rejected even with a prefix.

Fictional example only (do **not** use these values):

```text
manifest digest : sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
confirm value   : AVAIL-1234@0123456789ab
```

```bash
docker compose run --rm --no-deps -v "$PWD/backend:/app" \
  --entrypoint python backend manage.py remediate_mapping_audit \
  --manifest /tmp/remediate-manifest.json \
  --apply --confirm AVAIL-1234@0123456789ab
```

* `--apply` is required to write anything.
* The confirmation must match the exact manifest being consumed. The v3
  digest is canonical: it covers the plan, every target row's columns, the
  protected mappings and the database identity, and excludes only volatile
  provenance (`generated_at`). Two dry runs over unchanged rows therefore
  produce the same digest. Editing any material field changes the digest
  prefix and invalidates the previous confirmation, even if the file's own
  SHA-256 is recomputed; live rows are revalidated independently at apply time.
* This binds the confirmation to **content**, not to a person; it is not proof
  of who approved the change.
* Everything runs inside one `transaction.atomic()` while the exclusive barrier
  is held: re-validate → update `399` → delete evidence, offers, mappings →
  assert every invariant. Commit happens only if all in-transaction checks pass.

`--confirm` no longer accepts a bare ticket such as `AVAIL-1234`.

Successful output ends with `Mutation committed and post-commit validation
passed.` Before that it prints:

* **In-transaction checks** — evaluated before commit (mapping `399` is
  manual/2996/1.0, deletes are gone, protected mappings unchanged, no orphans,
  unrelated row counts unchanged, no target prices).
* **Post-commit checks** — re-read after commit, including the public local-offer
  read path for the five movies and the two *The Office* seasons.

The audit log line records the manifest digest, ticket id and action counts
(never credentials).

---

## 6. Failure handling: pre-commit versus post-commit

### Pre-commit failure (safe rollback)

Any manifest/identity/barrier mismatch, missing/changed row, failed invariant or
database error **before commit** aborts the whole transaction: the mapping
update and every delete roll back together. The command exits non-zero with
`mutation aborted and rolled back` (or a validation error). Nothing partial is
committed. Fix the cause, regenerate the manifest, and re-review it.

### Post-commit failure (no rollback available)

If a post-commit check fails, the transaction **has already committed**.
Ordinary transaction rollback is gone. The command:

* prints `POST-COMMIT VALIDATION FAILED: <check names>` with non-sensitive
  diagnostics;
* if validation itself raises (for example the connection drops), prints
  `POST-COMMIT VALIDATION COULD NOT COMPLETE: <exception type>` instead; a
  validation error is treated exactly like a failed check, never as a safe
  pre-commit abort;
* states that the transaction is already committed;
* exits **non-zero**;
* does **not** print a success message.

If this happens:

1. **Keep `worker`, `catalogue-worker` and every cron entry paused.** Do not let
   a refresh recreate or further change rows while the state is being assessed.
2. Preserve the command output, the reviewed manifest, database logs and the
   backup/restore-check record.
3. Do **not** retry `--apply`, and do **not** automatically restore a backup.
   The command never performs a second mutation or a restore after a
   post-commit failure.
4. Follow §7 and record every finding before any further write.

A post-commit failure means the committed state is wrong (for example a writer
raced the mutation). Treat it as an incident, not a retryable command error.

---

## 7. Recovery after a post-commit failure

The goal is to reconcile the committed state without silently losing unrelated
work.

1. Keep all writers paused (§4).
2. Compare the committed state with the reviewed manifest and the in-transaction
   check output. Identify which check failed and which row(s) differ.
3. If a backup must be considered, first restore it into a **scratch** database,
   never over the live database. Compare the scratch restore with the live
   database to enumerate unrelated changes made after the backup was taken
   (refreshes, manual edits, scraper runs).
4. A full live restore discards those unrelated changes. If they matter, prefer
   a narrowly reviewed forward repair over a restore. Any repair must be a
   separately approved plan with its own allow-list, manifest and confirmation;
   do not improvise one during this run.
5. Only after the state has been reconciled and reviewed should the workers and
   cron entries be resumed.

---

## 8. Post-commit validation (manual cross-check)

The command prints these; you can also run them by hand.

```bash
docker compose exec -T backend python manage.py shell -c "
from availability.models import ContentMapping, Offer, OfferEvidence, OfferPrice
m = ContentMapping.objects.get(id=399)
print('399 ->', m.tmdb_id, m.match_method, m.confidence)
print('deleted mappings left:', ContentMapping.objects.filter(id__in=[400,386,388,1,4760]).count())
print('deleted offers left:', Offer.objects.filter(id__in=[3504,3505,3506,3507,3508,3509,3510,3511,3512,3513,3514,3515,3516,3517,3518,3519,3455,3457,1,10820]).count())
print('target evidence left:', OfferEvidence.objects.filter(id=1).count())
print('orphan evidence:', OfferEvidence.objects.exclude(offer_identity__in=Offer.objects.values('identity')).count())
print('orphan prices:', OfferPrice.objects.exclude(offer_identity__in=Offer.objects.values('identity')).count())
"
```

Expect `399 -> 2996 manual 1.0` and every other count `0`.

Then confirm the read path no longer surfaces the false offers:

```bash
# The five movies and the two The Office seasons must show no BBC/CBC/Tubi offer.
curl -s localhost:8000/api/movies/10601/ | head
```

Finally, resume the workers and cron entries (`docker compose start worker
catalogue-worker` plus the scheduler) and watch the next catalogue refresh:
`The Office` should re-resolve via the manual mapping to TMDb `2996`, not
`2316`.

---

## Troubleshooting

| Message | Meaning / fix |
| --- | --- |
| `manifest digest mismatch` | The file was edited after review. Regenerate and re-review. |
| `manifest was generated for a different database instance` | Wrong environment or a same-name database on another server. Generate the manifest against the target instance. |
| `cannot prove which PostgreSQL server` | The server exposes no network address and no readable cluster identifier. Connect over TCP or grant permission, then regenerate. |
| `unsupported manifest schema` | A v1/v2 manifest (their digest embeds the generation time). Regenerate a v3 manifest. |
| `manifest does not match the database` | A target/protected row changed or an extra row appeared. Regenerate after pausing workers. |
| `target offer 3519 is missing` / `targeted offer scope does not match` | The database drifted. Do **not** widen the allow-list; investigate the refresh that wrote it. |
| `orphaned OfferEvidence rows already exist` | Unrelated orphan cleanup is no longer part of this command. Investigate and clean it separately; the remediation will not purge it. |
| `the fixed movie matcher is not present` | Deploy the matcher to the applying service (§3). |
| `writer barrier code` / `cannot prove that availability refresh writers are excluded` | Deploy the barrier code and run against PostgreSQL (§3/§4). |
| `another availability writer or maintenance session holds the write barrier` | A refresh or another maintenance run is active. Stop workers and cron, then retry. |
| `other advisory lock(s) are held` | A pre-barrier writer may still be running. Restart every worker/cron entry on this image, then retry. |
| `confirmation must be TICKET@DIGEST_PREFIX` | Supply the ticket, `@`, and the 12-hex digest prefix shown by the command. |
| `confirmation digest prefix does not match` | The manifest content/digest changed. Re-review and use the new prefix. |
| `POST-COMMIT VALIDATION FAILED` | The transaction committed but validation failed. Keep writers paused; follow §6/§7. Do not retry `--apply`. |
