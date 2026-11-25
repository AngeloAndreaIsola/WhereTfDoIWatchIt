# Issue #97 Stage D offline verification

**STAGE_D_OFFLINE_TESTS_PASS.** **STAGE_D_LIVE_PREFLIGHT_BLOCKED.**

The merged runner passed the strengthened offline checks with mocked transport and synthetic fixtures. Operational readiness is NOT established: synthetic tests are not operational readiness, and synthetic title rows populated for tests cannot substitute for actual retained target identity inventory. Independent read-only preflight on an isolated target found **13 blockers**: 12 missing typed title identities + 1 missing current NL-search runtime receipt. Live execution remains strictly blocked pending resolution of all 8 remaining gates detailed below. Zero live provider requests were sent, zero execution tokens or grants were issued, and zero real batches were executed.

The verified runner and base are [master at `0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea`](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/commit/0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea) (merging PRs #198, #204, #205, #206, #208, #209, #212). This report and test suite replace the misleading `READY_FOR_OWNER_AUTHORIZATION` wording from PR #210 head `abaf501f8743cc4a48d80e36061e554117e1efac`. The dispatch route is `ag-claude-sonnet-4-6` / `HIGH` (verified smoke). Cost is `COST_UNKNOWN`.

The signing-scope repair preserved the initial Pi edits and used OpenAI Codex (`gpt-6.2-sol`, `HIGH`, `COST_UNKNOWN`) to verify the published text and rerun the offline checks.

## Scope and offline test results

| Check | Offline test result |
| --- | --- |
| Exactly 12 NL title searches in batches of 2, 5 and 5 | The mock recorded 12 calls, one per ordered scope, with 12 shared debits, 12 checkpoints and 48 immutable ledger events. The runner rejected a 13th reservation and replayed a completed batch with zero additional calls while synthetic receipt and quota still had capacity. |
| Published Batch 1 signing scope | The offline test compares the report's signing gate against the pinned manifest's type, TMDb ID, territory and order. |
| Operation, pacing, retries and concurrency | Pinned `GetSearchTitles` POSTs verified against document SHA-256 `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`. Redirect following, adapter retries (`max_retries.total == 0`), and implicit proxy configuration were disabled. Pacing enforced eleven 5-second waits after responses across three batches; stricter 8-second pacing took precedence when configured. A second Stage D caller and the ordinary JustWatch transport were refused while a Stage D send was in flight (burst 1, maximum in flight 1). |
| Title identity and market | Strict matching enforced for title, media type, TMDb ID, year and NL country market. Mismatched response fields stopped the runner before checkpointing. Mismatched database target inventory rows (`Movie`) stopped execution before reservation. Coherence (`movie-220289-nl`) stayed held for reviewed year discrepancy (2013 vs 2014). The five optional season lookups stayed outside scope, and signed season-operation tampering was refused. |
| Durable reservation and shared accounting | An independent PostgreSQL connection observed committed reservation, handoff and charged in-progress grant before each mock send. Prior receipt spend reduced available quota. Renewal on the same grant reused the existing ledger. Target database triggers rejected attempt update/delete, handoff update/delete, reservation delete, and run delete (`append-only`). |
| Interruption and checkpoint recovery | A timeout and simulated interrupt at handoff each left the reservation spent with unknown transmission and prevented resend. Expiry after reservation prevented handoff. For a durable validated response interrupted before checkpointing, offline checkpoint recovery command on a new connection used the original response time (`observed_at`) and made zero mock calls. Batch resume sent only the next unused slot without duplicate transmission. |
| Target, authorization and stop conditions | Incorrect database identity (in `execute` and `recover_checkpoint`), missing disposable attestation, expired or outliving authorization, stale evidence and insufficient quota prevented sends. Tampered token HMAC payloads and corrupted tags were rejected. HTTP 401, 403, 302, 429, 503 and synthetic HTTP 200 HTML challenge each stopped after one charged call. The 429 fixture persisted a 901-second cooldown from `Retry-After`. |
| Preflight 13-blocker verification | On a clean target without synthetic `Movie` rows and without a runtime receipt, read-only preflight verified exactly 13 blockers: 12 missing typed title identities + 1 missing current NL-search runtime receipt, with `readiness="blocked"` and `authorization_scope` withheld. Synthetic title rows populated for offline tests cannot substitute for actual retained target identity inventory. |
| Merged migration graph | Model drift check passed. Fresh disposable PostgreSQL database applied both `0018_offerprice_variants` and `0019_stage_d_run_ledger` and their `0020_merge_stage_d_offerprice_variants` merge. The pending migration check passed. |

The evidence baseline remains 56 source-reported offers, 7 historical first-party candidates, 0 temporally eligible independent comparisons and 0 confirmed negatives. Accuracy remains undefined. Historical JustWatch observations are same-source observations and do not constitute independent verification. [Stage E evidence protocol](stage-e-evidence-protocol.md)

## Exact remaining gates required before live execution

All 8 gates below remain strictly required before any live provider transmission or owner authorization of Batch 1:

1. **Runtime receipt**: Enabled, current JustWatch source configuration receipt covering NL search only, specifying operation `search` / `GetSearchTitles`, country `NL`, valid active time window, and maximum requests bound to the reviewed scope.
2. **Independent operation access evidence**: Dated, independently reviewed access permission evidence matching the pinned `GetSearchTitles` GraphQL query document SHA-256 (`997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`), endpoint, exact scope IDs, zero retries, and country NL.
3. **Quota and prior spend**: Current upstream quota evidence with verified remaining quota and reconciled prior spend (`spent_at_review`) matching shared ledger state.
4. **Rate and cooldown**: Current upstream rate policy evidence confirming minimum 5-second pacing (or stricter), burst 1, maximum 1 in flight, and no active cooldown window.
5. **Exclusive grant use**: Explicit attestation of exclusive grant use across all workers and consumers, with zero overlapping or unmanaged JustWatch traffic on the target.
6. **Retained disposable target fingerprint and identities**: Attested isolated disposable PostgreSQL cluster with exact database OID and cluster system identifier fingerprint; all 12 exact typed `Movie` titles and release years populated and verified; applied migrations through 0020; clean advisory locks; empty refresh queues; and a clean read-only preflight returning 0 blockers.
7. **Signed Batch 1 manifest and expiry**: Separate owner-signed HMAC authorization token covering only Batch 1 (the first 2 scopes: `movie-238-nl`, `series-1396-nl`), bound to the exact disposable target identity, prior checkpoints digest, manifest digest, evidence digest, receipt digest, and valid expiry.
8. **Restricted custody approval**: Approved custody protocol for all restricted observation evidence, ensuring no raw provider payloads, signed URLs, or private tokens leak into Git or public issue trackers. [Stage D bounded runner runbook](../../operations/issue97_stage_d_bounded_runner.md)

## Local reproduction commands and counts

The backend runs on Python 3.12.16, Django 5.2.19 and PostgreSQL 16.15. The application source is mounted read-only on an internal Docker network with a fresh tmpfs database. Only synthetic fixture data and in-process mocks are used, including test-only authorization values.

```sh
docker network create --internal wtdw97-stage-d-test-net
docker run --pull never -d --name wtdw97-stage-d-test-db \
  --network wtdw97-stage-d-test-net --network-alias db-test \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD=wtdwi_test postgres:16-alpine
docker exec wtdw97-stage-d-test-db sh -c 'until pg_isready -h localhost -U wtdwi_test -d wtdwi_test; do sleep 0.1; done'

docker run --pull never --rm --network wtdw97-stage-d-test-net \
  --mount "type=bind,source=$PWD,target=/repo,readonly" --workdir /repo/backend \
  -e POSTGRES_DB=wtdwi_test -e POSTGRES_USER=wtdwi_test \
  -e POSTGRES_PASSWORD=wtdwi_test -e POSTGRES_HOST=db-test \
  -e WTDWI_DISPOSABLE_TEST_DB=1 -e DJANGO_SECRET_KEY=wtdwi-test-only \
  -e DJANGO_DEBUG=1 -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint sh wtdwi-stage-d-backend-test:latest -ec '
  python manage.py makemigrations --check --dry-run &&
  python manage.py migrate --noinput &&
  python manage.py migrate --check &&
  python manage.py showmigrations availability &&
  PGOPTIONS="-c default_transaction_read_only=on" AVAILABILITY_STAGE_D_DISPOSABLE=1 python manage.py issue97_stage_d --batch 1 > /tmp/stage-d-preflight.json &&
  python manage.py test --noinput -v 2 availability.tests.test_issue97_stage_d_runner availability.tests.test_issue97_stage_d_preflight availability.tests.test_experiment_runner availability.tests.test_justwatch_reviewed_transport availability.tests.test_issue97_collection'

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v
git diff --check

docker rm -f wtdw97-stage-d-test-db
docker network rm wtdw97-stage-d-test-net
```

The five Django test modules passed all 91 tests (33 runner tests, 2 Stage D preflight tests and 56 adjacent regression tests, zero skips and zero failures). The manifest suite reported 6 passes and 1 intentional skip for the restricted raw TMDb input. The direct read-only preflight returned 13 blockers and withheld the authorization scope, with zero reservations and handoffs. The migration and git diff checks exited 0. These results are local with synthetic fixtures and mocked transport. See PR #214 for hosted CI status.

Issue #97 remains open. Issue #103 remains on hold.
