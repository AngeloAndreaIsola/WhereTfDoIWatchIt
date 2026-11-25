# Issue #97 final integration

**READY_FOR_CHATGPT_REVIEW** on `integration/97-phase3-final`. The five draft PRs are integrated for ChatGPT Web's final review. Merge eligibility requires successful hosted CI. Issue #97 remains open and #103 stays on hold.

Base master `54bd248060880523ce63701b46c031508f99338f`. Tested application revision `9df0eae6ebe0ed88d9c7e2c3a808e3310b81bb72`. Its next commit adds only this report. The integration PR and handoff comments identify the published head. The dispatch specified Codex / gpt-6.2-astra at XHIGH, with the `XHIGH_OK` route receipt. Cost is `COST_UNKNOWN`. The prior quota-stalled attempt is excluded from these results.

## Forward merges

| Order | Draft | Verified source head | Integration merge | Focused Django tests |
| ---: | ---: | --- | --- | ---: |
| 1 | #198 | `85774f40ba31f283a148df625f80c0f1d4418188` | `7671f7647e558274f02b8c8b8baee8942cc32b93` | 143 passed |
| 2 | #205 | `7be721b62bf04408ddd67802eaff1ea4951d4e9f` | `e56cdea5b4370fd8af1ced90390985067ea903c0` | 144 passed |
| 3 | #192 | `2a832ed86ab09ee1bb7b25441a9feeed47a5b3ec` | `da5cbaec6632b7fbe0535aaf112490172cfa78cc` | 113 passed |
| 4 | #206 | `3256aaeca04137e7a19e1c72cb3fdc5191425e9e` | `8742f6706fafcde4db95c1e6fcd3dc3a85e3506c` | 74 passed |
| 5 | #204 | `454278610ac35b219da9038912042674c9804908` | `9df0eae6ebe0ed88d9c7e2c3a808e3310b81bb72` | 60 passed |

Verified each source against GitHub's pull ref and ran `git merge-base origin/master <source-head>` for each row. All five returned the full base above. Every integration commit has the previous integration revision as its first parent and the pinned source as its second parent.

- **#205, JustWatch adapter.** Kept per-provider season discovery and the asserted series ID, including the child-only catalogue guard and typed search filters. Preserved #198's cross-edge validation, offer variants and shared timestamp parser.
- **#192, refresh and reconciliation.** Combined the price-identity and BBC identity imports in refresh. Verified a single observation after the BBC identity check carries source prices, title checks and reviewed identity.
- **#206, migration 0018.** The two added migrations differed only in their generated header. Kept #198's file byte-for-byte and verified a single `OfferPrice.variants` field. Both ledger models and #206's 0019 and empty 0020 were preserved. #198 and #204 merged cleanly.

## Local results

The full backend run passed all **3,861 tests** in 198.887 seconds. The standalone tracker run passed all **2,173 tests** in 131.035 seconds. Focused and tracker counts overlap the full suite.

All 403 frontend tests in 47 files passed on Node 22.23.1 and npm 10.9.8. Lint and production build exited 0. Both reported existing warnings at `LeavingSoonSection.tsx:172` (hook dependency) and `UnofficialCatalogItem.tsx:99` (image element). The build generated 16/16 pages.

All five `makemigrations --check --dry-run` runs reported **No changes detected**. The Stage D manifest suite ran six cases, five passed and one skipped because it requires retained raw TMDb evidence. The Stage E offline audit verified 15 candidates, 13 historical observations, seven historical pairs and two controls. Fresh adjudicated pairs remain zero and accuracy metrics are undefined. The shell syntax check and base-to-head whitespace check passed.

### Commands

From the repository root, target the row's integration merge for each focused run. Backend tests ran with Python 3.12.16, Django 5.2.19 and PostgreSQL 16.15. Compose supplied a tmpfs database, an internal Docker network and the repository's disposable identity guard. The temporary Compose override used these mounts and network settings.

```yaml
services:
  backend-test:
    volumes:
      - ./backend/availability:/app/availability:ro
      - ./backend/tracker:/app/tracker:ro
      - .:/repo:ro
networks:
  default:
    internal: true
```

In the commands below, `override` points to that YAML. The Python entry point is provided by the `backend-test` service. The first launch attempt used a read-only mount over all of `/app`, which prevented Docker from creating nested mount points. It ran zero tests. The narrower mounts above were used for every reported result.

```sh
project=wtdw97-phase3-d04eb28b
dc() { docker compose -p "$project" -f docker-compose.test.yml -f "$override" "$@"; }
backend() { dc run --rm --no-deps backend-test "$@"; }
dc up -d --wait db-test
dc build backend-test

# After #198, 143 passed
backend manage.py test --noinput availability.tests.test_issue97_collection availability.tests.test_justwatch_integrity availability.tests.test_justwatch_regression availability.tests.test_offer_prices availability.tests.test_offerprice_migration availability.tests.test_reconciliation tracker.tests.test_issue97_availability_contract
backend manage.py makemigrations --check --dry-run

# After #205, 144 passed
backend manage.py test --noinput availability.tests.test_justwatch_season_identity availability.tests.test_justwatch_integrity availability.tests.test_justwatch_regression availability.tests.test_offer_prices tracker.tests.test_season_availability tracker.tests.test_issue97_availability_contract
backend manage.py makemigrations --check --dry-run

# After #192, 113 passed
backend manage.py test --noinput tracker.tests.test_issue190_bbc_office_identity availability.tests.test_justwatch_reconciliation_refresh availability.tests.test_reconciliation availability.tests.test_justwatch_season_identity availability.tests.test_offer_prices tracker.tests.test_title_availability_cache
backend manage.py makemigrations --check --dry-run

# After #206, 74 passed
backend manage.py test --noinput availability.tests.test_issue97_stage_d_runner availability.tests.test_experiment_runner availability.tests.test_justwatch_reviewed_transport availability.tests.test_offerprice_migration
backend manage.py makemigrations --check --dry-run

# After #204, 60 passed
backend manage.py test --noinput availability.tests.test_issue97_stage_e_rehearsal availability.tests.test_experiment_runner.GuardedTransportTests availability.tests.test_experiment_runner.PostgreSQLReservationTests availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked availability.tests.test_justwatch_reviewed_transport availability.tests.test_issue97_collection tracker.tests.test_issue97_availability_contract
backend manage.py makemigrations --check --dry-run

# After #206, 5 passed and 1 skipped
dc run --rm --no-deps -e PYTHONPATH=/repo/backend backend-test /repo/scripts/test_issue97_stage_d.py -v

# After #204
python3 -E scripts/audit_issue97_stage_e.py
sh -n scripts/rehearse_issue97_stage_e.sh
git diff --check 54bd248060880523ce63701b46c031508f99338f 9df0eae6ebe0ed88d9c7e2c3a808e3310b81bb72
backend manage.py test --noinput -v 1

# Dedicated tmpfs cluster for the standalone tracker run
project=wtdw97-phase3-tracker-d04eb28b
dc up -d --wait db-test
dc build backend-test
backend manage.py test --noinput -v 1 tracker

(cd frontend && npm ci --no-audit --no-fund && npm test && npm run lint && NEXT_TELEMETRY_DISABLED=1 npm run build)
```

## Migration graph and database results

**Both orders passed on fresh, separate databases.** PostgreSQL recorded `0017 0018 0019 0020` on the first and `0017 0019 0018 0020` on the second. The actual graph forks at 0017. Both 0018 and 0019 depend on 0017, and 0020 depends on both siblings. Django identified 0020 as the sole availability leaf and returned a single model field named `variants`. The sibling dependencies permit both rehearsals. The release runbook specifies 0018 first. Each database passed global `migrate --check`, with one required JSONB variants column and two enabled ledger immutability triggers.

```sh
project=wtdw97-phase3-d04eb28b
for order in '0018 0019' '0019 0018'; do
    set -- $order
    db="phase3_order_${1}_${2}"
    dc exec -T db-test createdb -U wtdwi_test "$db"
    manage() { dc run --rm --no-deps -e POSTGRES_DB="$db" backend-test manage.py "$@"; }
    sql() { dc exec -T db-test psql -XAt -v ON_ERROR_STOP=1 -U wtdwi_test -d "$db" -c "$1"; }
    for target in 0017 $order 0020; do
        manage migrate availability "$target" --noinput
        applied=$(sql "SELECT string_agg(left(name,4), ' ' ORDER BY applied,id) FROM django_migrations WHERE app='availability' AND left(name,4) BETWEEN '0017' AND '0020'")
        case "$target" in
            0017) expected=0017 ;;
            "$1") expected="0017 $1" ;;
            "$2") expected="0017 $order" ;;
            0020) expected="0017 $order 0020" ;;
        esac
        echo "$db $applied"
        test "$applied" = "$expected"
    done
    manage migrate --noinput
    manage migrate --check
    test "$(sql "SELECT count(*) FROM information_schema.columns WHERE table_name='availability_offerprice' AND column_name='variants' AND data_type='jsonb' AND is_nullable='NO'")" = 1
    test "$(sql "SELECT count(*) FROM pg_trigger WHERE tgname='stage_d_ledger_immutable' AND tgenabled='O'")" = 2
done
```

## Review and operational gates

The integration PR's checks and handoff comments carry the hosted result for the published head. The upstream #206 run stopped before executing jobs because GitHub reported a billing or spending-limit issue. Successful hosted checks on the integration PR remain a merge gate. [Upstream CI run](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45594385430)

Local verification used fixtures and disposable databases. Stage D/E live execution, Stage E runner authorization, production writer cutover and any historical data repair require separate owner action. ChatGPT Web owns final review and merger authority.

## Changed files

Base-to-published-head inventory includes 36 backend files, 14 documentation files (including this report), and three scripts.

```text
backend/availability/domain/enums.py
backend/availability/domain/models.py
backend/availability/domain/reconciliation.py
backend/availability/management/commands/issue97_stage_d.py
backend/availability/migrations/0018_offerprice_variants.py
backend/availability/migrations/0019_stage_d_run_ledger.py
backend/availability/migrations/0020_merge_stage_d_offerprice_variants.py
backend/availability/models.py
backend/availability/persistence/repositories.py
backend/availability/services/experiment_runner.py
backend/availability/services/identity_policy.py
backend/availability/services/issue97_collection.py
backend/availability/services/issue97_stage_d_observation.py
backend/availability/services/issue97_stage_d_policy.py
backend/availability/services/issue97_stage_d_runner.py
backend/availability/services/issue97_stage_d_titles.json
backend/availability/services/normalize.py
backend/availability/services/offer_variant_projection.py
backend/availability/services/reconciliation.py
backend/availability/services/refresh.py
backend/availability/sources/justwatch.py
backend/availability/sources/justwatch_client.py
backend/availability/sources/justwatch_transport.py
backend/availability/sources/streaming_availability.py
backend/availability/tests/test_issue97_collection.py
backend/availability/tests/test_issue97_stage_d_runner.py
backend/availability/tests/test_issue97_stage_e_rehearsal.py
backend/availability/tests/test_justwatch_integrity.py
backend/availability/tests/test_justwatch_reconciliation_refresh.py
backend/availability/tests/test_justwatch_regression.py
backend/availability/tests/test_justwatch_season_identity.py
backend/availability/tests/test_offer_prices.py
backend/availability/tests/test_offerprice_migration.py
backend/availability/tests/test_reconciliation.py
backend/tracker/services/availability_reads.py
backend/tracker/tests/test_issue190_bbc_office_identity.py
docs/DEPLOYMENT.md
docs/ISSUE_190_BBC_OFFICE_IDENTITY.md
docs/audits/issue97/final-integration-report.md
docs/audits/issue97/http-variant-projection-verification.md
docs/audits/issue97/justwatch-season-identity.md
docs/audits/issue97/justwatch-season-inventory.sql
docs/audits/issue97/pr198-integration-verification.md
docs/audits/issue97/stage-e-authorization-request.md
docs/audits/issue97/stage-e-candidate-matrix.csv
docs/audits/issue97/stage-e-evidence-protocol.md
docs/audits/issue97/stage-e-offline-rehearsal.md
docs/operations/issue97_justwatch_collection_preflight.md
docs/operations/issue97_migration_preservation_verification.md
docs/operations/issue97_stage_d_bounded_runner.md
scripts/audit_issue97_stage_e.py
scripts/rehearse_issue97_stage_e.sh
scripts/test_issue97_stage_d.py
```
