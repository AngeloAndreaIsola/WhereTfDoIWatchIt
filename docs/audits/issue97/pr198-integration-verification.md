# PR #198 integration verification

Review started at `c5d146923e9a292de0d5398d6c975cc85177144c` against master `54bd248060880523ce63701b46c031508f99338f`. The verified application and test revision is `2d894e5888b34d19d4ab72681fc2183ba76a6f08`. This report is a documentation follow-up. [Draft PR #198](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/198) · [Issue #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97)

## Correctness

- Targeted matching keeps movie and series TMDb namespaces distinct. Compatible claims on one confirmed native node contribute their offers. Different usable node IDs and conflicting claims remain unresolved. A fallback with a requested TMDb ID requires an exact normalized title and the requested year when supplied, with no competing stated TMDb ID. Incremental catalogue and targeted refreshes preserve unseen rows.
- Canonical offer IDs and per-source price IDs remain stable across co-observed variants. The open variant that supplies the scalar price also supplies its link, rendition and source offer ID. A previously reported scalar stays in history if only an unpriced variant remains open. Public HTTP variants retain allowlisted display fields and remove query strings, fragments and unsafe links. Offline replay uses the same field projection without variant links. [HTTP verification](http-variant-projection-verification.md)
- The starting head treated an invalid expiry as an open window. Both added regressions failed there. The corrected read and write paths exclude malformed dates or non-string expiries from current offer and price selection while retaining the raw observation internally. If an invalid window is the only remaining indication of availability, reconciliation reports unknown and supplies no current price. An absent expiry still permits an open window, and the existing UTC, offset and date-only parser tests pass.

## Local checks

Backend checks used Python 3.12.16, Django 5.2.19 and disposable PostgreSQL 16.15 in Docker tmpfs on an internal network with zero published database ports. Provider inputs used mocked transport and synthetic fixtures. Frontend checks used Node 22.23.1 and npm 10.9.8.

| Check | Result |
| --- | --- |
| Starting-head focused backend suite | 316 passed in 22.165 s. The two new regressions, run against starting-head code, both failed as expected. |
| Final focused backend suite | 345 passed in 17.560 s, including both malformed-expiry regressions and the migration round-trip. |
| Final full backend suite | 3,804 passed in 157.539 s. Both final suites had zero failures, errors or skips. |
| Django model drift | Passed, no changes detected. |
| Frontend suite | 403 passed across 47 files. The frontend tree is identical at the starting and final code revisions. |
| Frontend lint and build | Both passed. Lint reported the existing `LeavingSoonSection.tsx` hook dependency warning and `UnofficialCatalogItem.tsx` image warning. Build generated 16 of 16 pages. |
| Offline preflight | 15 scopes, two successful synthetic replays, zero live attempts, with networking disabled. Verdict `LIVE_COLLECTION_BLOCKED`. |

Run the frontend commands in `frontend/` and the Django commands in `backend/` inside the isolated container. Start the backend container with `PYTHONPATH=/app/scripts`, `WTDWI_DISPOSABLE_TEST_DB=1`, `DJANGO_DEBUG=1`, PostgreSQL host `db-test` and database/user `wtdwi_test`. Mount `scripts/` and both Compose files at their corresponding `/app` paths as the test runner does. [Disposable test runner](../../../scripts/test-backend-isolated.sh)

```sh
npm ci --no-audit --no-fund
npm test
npm run lint
NEXT_TELEMETRY_DISABLED=1 npm run build
```

```sh
python manage.py test --noinput -v 1 \
  availability.tests.test_issue97_collection \
  availability.tests.test_justwatch_integrity \
  availability.tests.test_justwatch_regression \
  availability.tests.test_justwatch_available_to \
  availability.tests.test_offer_prices \
  availability.tests.test_reconciliation \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_offerprice_migration \
  availability.tests.test_audit_provider_mapping \
  tracker.tests.test_issue97_availability_contract \
  tracker.tests.test_title_availability_cache \
  tracker.tests.test_reconciliation_read_path \
  tracker.tests.test_leaving_soon \
  tracker.tests.test_stale_offer_semantics \
  tracker.tests.test_bulk_refresh \
  tracker.tests.test_refresh_worker \
  tracker.tests.test_season_availability \
  tracker.tests.test_justwatch_title_level \
  tracker.tests.test_guarded_migrate
python manage.py test --noinput -v 1
python manage.py makemigrations --check --dry-run
```

The starting-head focused run omitted `availability.tests.test_justwatch_available_to` and `tracker.tests.test_guarded_migrate`. The red run used `python manage.py test --noinput -v 1 -k malformed_expiry availability.tests.test_justwatch_regression` with only the updated test file mounted on the starting-head backend image.

```sh
docker run --pull never --rm --network none -e DJANGO_DEBUG=1 \
  --mount "type=bind,source=$PWD/docs/audits/stage2b,target=/reviewed,readonly" \
  --entrypoint python wtdwi-pr198-task81d5-final:local manage.py issue97_collection_preflight \
  --cohort /reviewed/nl_pilot_cohort_20261122_v2.csv \
  --identities /reviewed/identity_manifest_20261122.csv \
  --synthetic-fixtures availability/tests/fixtures/issue97_synthetic_justwatch.json
```

## Migration and integration

The migration regression seeds five linked historical rows at availability 0017 and tracker 0063. It compares the IDs and all persisted fields across upgrade, downgrade and re-upgrade. A 0017-model insert fails against 0018 because `variants` is `NOT NULL` and has no database default. A populated variant array disappears on downgrade, and re-upgrade creates an empty array. Scalar price, offer, evidence, run and title-check provenance survive.

The disposable cutover used the pinned master image's `/guarded-migrate-entrypoint.sh` to reach 0017 and restored a pre-cutover `pg_dump -Fc` with `pg_restore --exit-on-error` to a second disposable database. The new `/entrypoint.sh true` was blocked before 0018. The new guarded entrypoint rejected an incorrect confirmation token, then applied 0018 with `--app-label availability --target 0018`. Both `/entrypoint.sh true` and `/worker-entrypoint.sh true` passed. The reverse `python manage.py migrate availability 0017 --plan` showed exactly 0018 to undo. The guarded downgrade with `--app-label availability --target 0017` succeeded, the old `/entrypoint.sh true` passed, and the final guarded re-upgrade succeeded. A preliminary rehearsal initialized only availability and tracker, so its global `migrate --check` correctly reported pending auth, admin, contenttypes and sessions migrations. The final rehearsal started from the fully migrated pinned master. [Writer cutover and recovery runbook](../../operations/issue97_migration_preservation_verification.md#release-runbook-for-availability-0018)

`git merge-tree --write-tree --name-only` found conflicts with draft #192 in reconciliation and refresh, and with draft #205 in refresh and the JustWatch adapter. Draft #206 combined textually, but `python manage.py migrate --plan` rejected the two availability leaves. A disposable graph with 0019 depending on 0018 and a temporary 0020 depending on 0019 printed the order `0018 → 0019 → 0020`. The probe made no changes to those drafts. [Pinned integration heads and required ordering](../../operations/issue97_migration_preservation_verification.md#integration-checks)

## Handoff

`READY_FOR_INDEPENDENT_REVIEW` for Agent E. Hosted CI remains billing-blocked. On the starting head all three hosted jobs stopped before their first step. The release owner still needs to authorize and schedule the coordinated writer cutover. Combined integration needs the conflict and graph checks above. ChatGPT Web owns the final review and merge decision. #97 remains open and #103 remains on hold. [Hosted CI baseline](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45573972544)
