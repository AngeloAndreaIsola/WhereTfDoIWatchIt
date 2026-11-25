# Issue #97 migration preservation verification

**RESOLVED locally.** The seeded migration probe passed 33 assertions with zero failures at application revision `1b63dc8838f5be24bcfa68eedb6c839a21f45c78`. [PR #198 handoff](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/198#issuecomment-7142261599)

The probe used PostgreSQL **16.15** and Django **5.2.19**, with PostgreSQL on a disposable Docker tmpfs, on an internal network with no published ports. The refresh used one injected JustWatch response, and the real HTTP assertion recorded zero calls. GitHub Actions did not start the application revision's jobs because of an account payment or spending-limit issue. [Hosted CI run](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45471338314)

## Results

| Check | Result |
| --- | --- |
| Fresh migrate | 101 applied, 0 failed. Included `availability.0018_offerprice_variants` and `tracker.0063_title_check_report_provenance`. |
| Seed | 5 assertions passed, 0 failed. The Offer, OfferPrice, OfferEvidence, TitleAvailabilityCheck and linked SyncRun each had ID `1`. |
| Downgrade to availability 0017 | 9 assertions passed, 0 failed. Only availability 0018 was unapplied. Tracker stayed at 0063, and all 91 fields still in the schema matched. |
| Upgrade to head | 8 assertions passed, 0 failed. The 92 fields that matched included 26 Offer, 21 OfferPrice, 17 OfferEvidence, 10 TitleAvailabilityCheck and 18 SyncRun fields. This includes the IDs and identity keys, full-precision timestamps, scalar price, legacy empty variants and provenance run ID. |
| Mock refresh | 11 assertions passed, 0 failed. Two distinct variants were written to OfferPrice ID `1`. Offer ID `1`, OfferEvidence ID `1` and their identity keys stayed stable. Title-check provenance still linked to SyncRun ID `1`. The refreshed Offer linked to the new successful SyncRun ID `2`. |
| Migration drift | `makemigrations --check --dry-run` exited 0 with “No changes detected.” |
| Focused Django regressions | 104 passed, 0 failures, 0 errors, 0 skipped, with `--noinput`. |

The seeded price represented a legacy row with `variants=[]`. The downgrade removes the column. A populated variant list would be lost on downgrade, and the upgrade restores the empty default.

## Release runbook for availability 0018

Migration `availability.0018_offerprice_variants` adds a `NOT NULL` JSONB column and drops the temporary database default once existing rows have been initialized. A process running the 0017 model omits `variants` from new price inserts, and PostgreSQL rejects those inserts. Use a stop-the-writers cutover for this release.

1. Confirm the release revision, the Compose project and the database target. Stage the new images and record the previous application revision. Pause the `refresh_availability` and `refresh_provider_catalogue` cron entries and any other scheduled or manual refreshes. Drain in-flight writes, take the backend out of service and stop `backend`, `worker`, and `catalogue-worker` where deployed. Include any other replicas or standalone writers using the same database.
2. Take a consistent database backup while writes are quiesced. Confirm that it can be restored and that the database is at availability 0017 before applying 0018. Use the new revision's guarded `migrate` service with the exact database and host confirmation in the [deployment guide](../DEPLOYMENT.md#schema-migrations). Check that `availability.0018_offerprice_variants` is applied and the runtime `migrate --check` passes.
3. Recreate the backend and configured worker lanes from the new revision. Check backend health and a cached title response with existing price and provenance. Confirm the worker logs are healthy and check for insert errors before resuming scheduled writers. Use offline fixtures for any smoke test that needs to demonstrate a new variant write.

If migration or startup fails, keep writers paused and inspect the migration state. If 0018 is applied, prefer recovering forward with an 0018-compatible build. If reverting is required, take a fresh backup or a protected export of the populated `variants` and their `(offer_identity, source_id)` keys before the downgrade. Check the reverse plan and use the guarded migration service from the 0018 revision with `--app-label availability --target 0017`. Confirm the schema before restarting 0017 code.

The downgrade **deletes all stored variant payloads**. The scalar price, offer and price identities, evidence, and title-check provenance survive. Reapplying 0018 gives existing rows an empty array. Recover a lost payload from a compatible backup or a separately reviewed import from the protected export. Any restored payload needs to match the source, offer identity and observation represented by the row.

## Integration checks

- Merged PR #195 supplies availability 0017 and tracker 0063. The checked-in migration test seeds five linked rows on the historical 0017/0063 models and compares every stored field after upgrade, downgrade and re-upgrade. It also exercises an old-model INSERT against 0018 and verifies the loss of a populated variant payload on downgrade. The existing JustWatch regression exercises a mocked refresh of the same offer, price and evidence identities.
- Draft PR #192 at `3456ef56fe49ffcd3e180875c1e6389e34e3789b` overlaps `availability/services/reconciliation.py` and `availability/services/refresh.py`. The merge preview reports conflicts in both. Reconciliation needs one observation call after the BBC identity quarantine, carrying title-check provenance, price rows and the reviewed identity flag. In refresh, preserve both the offer variant imports and the BBC identity-policy imports. Exercise #192's BBC identity tests and the #198 price, identity and #195 cache suites together when resolving that draft.
- Season identity is tracked separately in #203 and PR #205.

Run the checked-in migration regression on a disposable database with `./scripts/test-backend-isolated.sh availability.tests.test_offerprice_migration -v 2`. The continuation also reran the original four probe phases described below. All 33 assertions passed, the pre-downgrade and post-upgrade snapshots matched byte for byte, and the mocked refresh wrote both variants to the existing price row.

## Local evidence digests

| Evidence | SHA-256 |
| --- | --- |
| Before migration and after upgrade, byte-for-byte identical | `7780425d51f34966e37a7e898cc9a94298950cef482a6eb7a510743705336d5a` |
| At availability 0017 | `4b9d40c98208db48a8a39d2d2e189e6bb6954ed28778c88a1d2389dd725cc61a` |
| After mocked refresh | `2a78afefda33e5a68093556d4a667205b395151ad6d1b9859c416bc772cdf748` |
| Canonical Offer identity | `d70a1e221b2c937e47274c836b0e71d51fc1dfa230fbce077fceca1ba1bda1df` |
| Persisted variants after mocked refresh | `39bfdb94e571322d30eec09fdbce9087525d6801bd4e0f04eb52026e578709bc` |
| Local `probe.py` | `a3f62a22e47e9440277dd7588778e9f804b1524430e7ba293777b8d7ed993b43` |
| Local `run-backend.sh` | `e2a8a6d04315fa0e9e0e1e216c8c126dd05ee1fa0a4aa1c09d88c2fe35c4ad6f` |

## Commands

Run from the worktree root. The local probe captures every concrete field from each seeded row, including the linked run. It uses historical models for the downgraded snapshot and compares every field after serializing decimal values and timestamps without losing precision.

```sh
docker build --tag wtdw-issue97-probe-c6cb4159:local backend
docker network create --internal wtdw-issue97-probe-c6cb4159
docker run --pull never --detach --name wtdw-issue97-probe-c6cb4159-db \
  --network wtdw-issue97-probe-c6cb4159 --network-alias db-test \
  --tmpfs /var/lib/postgresql/data -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
docker network inspect wtdw-issue97-probe-c6cb4159 --format '{{.Internal}}'
docker inspect wtdw-issue97-probe-c6cb4159-db --format '{{json .HostConfig.PortBindings}}'
docker exec wtdw-issue97-probe-c6cb4159-db pg_isready -U wtdwi_test -d wtdwi_test
docker exec wtdw-issue97-probe-c6cb4159-db psql -X -U wtdwi_test -d wtdwi_test -Atc 'SHOW server_version'
```

The `run-backend.sh` wrapper executes the following command on each invocation.

```sh
docker run --pull never --rm \
  --network wtdw-issue97-probe-c6cb4159 \
  --volume "$PWD:/repo:ro" \
  --volume "/tmp/wtdw-issue97-pr198-probe.Y07flC:/probe" \
  --workdir /repo/backend \
  -e PYTHONPATH=/repo/backend:/repo/scripts \
  -e DJANGO_SETTINGS_MODULE=config.settings -e DJANGO_DEBUG=1 \
  -e POSTGRES_HOST=db-test -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD= -e WTDWI_DISPOSABLE_TEST_DB=1 \
  --entrypoint python wtdw-issue97-probe-c6cb4159:local "$@"
```

```sh
set -o pipefail
probe_dir=/tmp/wtdw-issue97-pr198-probe.Y07flC
sh "$probe_dir/run-backend.sh" manage.py migrate --noinput | tee "$probe_dir/01-fresh-migrate.log"
sh "$probe_dir/run-backend.sh" /probe/probe.py seed | tee "$probe_dir/02-seed.log"
sh "$probe_dir/run-backend.sh" manage.py migrate availability 0017 --noinput | tee "$probe_dir/03-downgrade.log"
sh "$probe_dir/run-backend.sh" /probe/probe.py downgraded | tee "$probe_dir/04-downgraded.log"
sh "$probe_dir/run-backend.sh" manage.py migrate --noinput | tee "$probe_dir/05-upgrade.log"
sh "$probe_dir/run-backend.sh" /probe/probe.py upgraded | tee "$probe_dir/06-upgraded.log"
sh "$probe_dir/run-backend.sh" /probe/probe.py refresh | tee "$probe_dir/07-refresh.log"
sh "$probe_dir/run-backend.sh" manage.py makemigrations --check --dry-run | tee "$probe_dir/08-makemigrations.log"
sh "$probe_dir/run-backend.sh" manage.py test --noinput -v 1 \
  availability.tests.test_models \
  availability.tests.test_reconciliation \
  availability.tests.test_issue97_collection \
  availability.tests.test_justwatch_integrity \
  availability.tests.test_justwatch_regression \
  tracker.tests.test_title_availability_cache 2>&1 | tee "$probe_dir/09-focused-tests.log"
sh "$probe_dir/run-backend.sh" -m django --version
cmp -s "$probe_dir/before.json" "$probe_dir/after.json"
docker rm --force --volumes wtdw-issue97-probe-c6cb4159-db
docker network rm wtdw-issue97-probe-c6cb4159
```
