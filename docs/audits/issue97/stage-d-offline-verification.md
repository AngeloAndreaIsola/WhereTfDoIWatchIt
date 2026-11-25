# Issue #97 Stage D offline verification

**READY_FOR_OWNER_AUTHORIZATION.** The merged runner passed the scoped offline checks. Live execution awaits separate owner approval and current evidence for the intended disposable target. Batch 1 was exercised only through synthetic tests with a mocked HTTP client. Zero provider requests were sent.

The verified runner and base are [master at `0b6d6325dab00d90baa096a9e529c26c75b41bb4`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/commit/0b6d6325dab00d90baa096a9e529c26c75b41bb4). The verification branch is `stage-d/97-offline-verify`. Its two changed files are this report and [the Stage D runner tests](../../../backend/availability/tests/test_issue97_stage_d_runner.py). The dispatch specified the substituted OpenCode route with `hy4-flash-free`. The effective upstream provider and reasoning effort are `UNPINNED`. Cost is `COST_UNKNOWN`.

## Scope and results

| Check | Local result |
| --- | --- |
| Exactly 12 NL title searches in batches of 2, 5 and 5 | The mock recorded 12 calls, one per ordered scope, with 12 shared debits, 12 checkpoints and 48 immutable ledger events. The runner rejected a 13th reservation and replayed a completed batch with zero additional calls while the synthetic receipt and quota still had capacity. |
| Operation, pacing, retries and concurrency | The mock intercepted only `GetSearchTitles` POSTs. Redirect following, adapter retries and implicit proxy configuration were disabled. The synthetic clock recorded eleven 5-second waits after responses across the three batches. A stricter 8-second limit took precedence. A second Stage D caller and the ordinary JustWatch transport were refused while a Stage D send was in flight. |
| Title identity and market | The full happy-path fixtures matched title, type, TMDb ID, year and NL. Each mismatched field stopped the runner before checkpointing. Coherence (movie TMDb 220289) stayed held for the reviewed year discrepancy. The five optional season lookups stayed outside scope, and signed season-operation tampering was refused. |
| Durable reservation and shared accounting | A separate PostgreSQL connection observed the committed reservation, handoff and charged in-progress grant before each successful mock send. Prior receipt spend reduced available quota. Renewal on the same grant reused the existing ledger. PostgreSQL rejected an attempt update, a handoff deletion and a response evidence update. |
| Interruption and checkpoint recovery | A timeout and a simulated interrupt at handoff each left the reservation spent with unknown transmission and prevented resend. Expiry after reservation prevented handoff. For a durable validated response interrupted before checkpointing, the recovery command on a new connection used the original response time and made zero mock calls. Resume sent only the next unused slot. |
| Target, authorization and stop conditions | Incorrect database identity, missing disposable attestation, expired or mismatched authorization, stale evidence and insufficient quota prevented sends. HTTP 401, 403, 302, 429, 503 and a synthetic HTTP 200 HTML challenge each stopped after one charged call. The 429 fixture persisted a 901-second cooldown from `Retry-After`. |
| Merged migration graph | Model drift check passed. A fresh disposable PostgreSQL database applied both `0018_offerprice_variants` and `0019_stage_d_run_ledger` and their `0020_merge_stage_d_offerprice_variants` merge. The pending migration check passed. |

The evidence baseline remains 56 source-reported offers, seven historical first-party candidates, zero temporally eligible independent comparisons and zero confirmed negatives. Accuracy remains undefined. The historical JustWatch observations are same-source observations. [Stage E evidence protocol](stage-e-evidence-protocol.md)

## Local commands and counts

The backend ran on Python 3.12.16, Django 5.2.19 and PostgreSQL 16.15. The application source was mounted read-only on an internal Docker network with a fresh tmpfs database. The prebuilt backend image supplied dependencies. Its bind mount supplied the reviewed source. Only synthetic fixture data and in-process mocks were used, including the test-only authorization values.

```sh
docker network create --internal wtdw97-stage-d-b-058e8871987b
docker run --pull never -d --name wtdw97-stage-d-b-058e8871987b-db \
  --network wtdw97-stage-d-b-058e8871987b --network-alias db-test \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD=wtdwi_test postgres:16-alpine
docker exec wtdw97-stage-d-b-058e8871987b-db pg_isready -h localhost -U wtdwi_test -d wtdwi_test

docker run --pull never --rm --network wtdw97-stage-d-b-058e8871987b \
  --mount "type=bind,source=$PWD/backend,target=/app,readonly" \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD=wtdwi_test -e POSTGRES_HOST=db-test \
  -e WTDWI_DISPOSABLE_TEST_DB=1 -e DJANGO_SECRET_KEY=wtdwi-test-only \
  -e DJANGO_DEBUG=1 -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint sh wtdwi-stage-d-backend-test:latest -ec '
  python manage.py makemigrations --check --dry-run &&
  python manage.py migrate --noinput &&
  python manage.py migrate --check &&
  python manage.py showmigrations availability &&
  python manage.py test --noinput -v 2 availability.tests.test_issue97_stage_d_runner availability.tests.test_experiment_runner availability.tests.test_justwatch_reviewed_transport availability.tests.test_issue97_collection'

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v
git diff --check

docker rm -f wtdw97-stage-d-b-058e8871987b-db
docker network rm wtdw97-stage-d-b-058e8871987b
```

The four Django test modules passed all 81 tests on the merged base, then all 87 with the verification changes. The final run included 31 Stage D tests and 56 adjacent regression tests. The manifest suite reported five passes and one intentional skip for the restricted raw TMDb input. The input stayed outside this run. The migration commands and whitespace check exited zero. These results are local. Hosted CI is unverified here.

## Evidence still required for execution

- Verify the intended disposable PostgreSQL target and fingerprint, exact catalogue rows, applied migrations, idle locks and queues, and a read-only preflight for the batch.
- Obtain the enabled runtime JustWatch receipt and independent exact access permission for the 12 typed NL `GetSearchTitles` scopes and the pinned document, with current validity and zero retries.
- Obtain current upstream quota and rate evidence, reconcile prior spend and pacing, and verify exclusive grant use across all consumers.
- Approve restricted evidence custody and obtain separate owner-signed authorization tied to the exact target, reviewed batch and prior checkpoint digests before any live send. [Stage D bounded runner runbook](../../operations/issue97_stage_d_bounded_runner.md)

Issue #97 remains open. Issue #103 remains on hold.
