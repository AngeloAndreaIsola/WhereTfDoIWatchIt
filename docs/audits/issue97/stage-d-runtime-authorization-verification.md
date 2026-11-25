# Issue #97 Stage D runtime authorization and target verification

**BLOCKED on current grant, quota, rate and target evidence.** On 2026-11-25, batch 1 preflight on a newly migrated disposable PostgreSQL database returned `readiness=blocked`. It reported 13 blockers, one for each of the 12 missing typed title rows and one for the missing NL-search-only runtime receipt. It emitted no authorization scope. Owner authorization review requires a fresh preflight on the intended retained disposable target.

The audited application revision was `0b6d6325dab00d90baa096a9e529c26c75b41bb4` on `master`. The assigned route was OpenAI Codex / `gpt-6.2-astra` / high (`ASTRA_HIGH_OK` per dispatch), cost `COST_UNKNOWN`. All results below were obtained locally. The audit made zero provider requests and created zero signing keys, tokens, grants or collection batches.

## Findings

| # | Requirement | Status and evidence |
|---|---|---|
| 1 | Exact NL JustWatch scope | **PASS, implementation.** The manifest selects source `justwatch`, HTTP POST to `https://apis.justwatch.com/graphql`, and 12 ordered NL title searches. The payload test checked all 12 for the reviewed title, type, market and single page. Batches contain 2, 5 and 5 titles, one attempt each, zero retries. Coherence and season lookups are excluded. Actual independent permission for this scope is **BLOCKED**. |
| 2 | Approved GetSearchTitles document | **PASS, repository pin.** The SHA-256 of the document in every constructed payload was `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`. The canonical manifest digest was `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727`. The owner still needs independent access evidence naming that document and the ordered scope. |
| 3 | Current receipt, permitted operations and expiry | **BLOCKED.** The rehearsal contained enabled JustWatch and NL rows. The source had no outbound receipt. The actual reviewer, reference, validity window, finite ceiling and interval are unknown. The validator requires exactly `["NL"]` and `["search"]` for the receipt, plus independently reviewed `["GetSearchTitles"]` access tied to the same grant and target. |
| 4 | Reconciled quota, prior spend and balance | **UNKNOWN.** The disposable DB had zero budget and attempt rows. Upstream allowance and prior shared-grant spend remain unknown. For a metered grant the runner takes the lesser of receipt ceiling minus ledger spend and reviewed upstream remaining minus spend since review. A reviewed unmetered classification still requires a finite receipt. Prior spend also needs a reviewed idle time covering the last reservation and response. Historical Notte invocation counts cannot supply an HTTP balance. |
| 5 | Rates, minimum pacing and cooldown | **BLOCKED on current upstream evidence.** The code serializes sends, burst one, with the maximum of the five-second manifest floor, receipt interval and reviewed rate interval from the last response or reviewed idle time. A 429 persists a cooldown of at least ten minutes and honors a longer Retry-After. The audit has no reviewer, check time, expiry, upstream burst/parallelism/interval or current grant cooldown to validate. |
| 6 | Exclusive grant and competing writers | **BLOCKED on operator evidence.** Zero other client backends at the rehearsal isolation check. Preflight saw neither JustWatch nor title-drain lock held and no pending queue or unfinished-run blocker. Source and advisory locks, the shared budget and the Stage D ledger protect cooperating writers on one target. Exclusive use across processes, other databases, schedulers and consumers of the real grant requires a current owner inventory and attestation. |
| 7 | HMAC binding, expiry and replay resistance | **PASS by code inspection on a single retained ledger.** HMAC-SHA256 covers the encoded payload. Scope equality binds target, stable run ID, manifest/document, phase, batch, typed scopes, ceilings, prior checkpoint digest and current evidence/receipt digests. Expiry must follow the check time and be within the earliest receipt, permission, quota, rate or one-hour freshness deadline. Reservation and handoff revalidate authorization. A still-valid approval can resume its batch. Durable ordinals, charged slots and checkpoints prevent duplicate reservations and block an unresolved transmission. This review did not exercise the signing or execution paths. Key custody and retention of the intended ledger remain unverified. |
| 8 | PostgreSQL identity and migrations | **PASS on the rehearsal target.** PostgreSQL 16.15, independent fingerprint `2c65e5557810bc07ae981de824030a624980362c211b54f3451cf937800cb370`, equal to the runner's hash of the database OID and cluster system identifier. All 103 migrations applied. Both 0018 and 0019 and their empty merge 0020 were marked applied. Both 0019 ledger triggers were enabled. Model drift and pending migration checks passed. **BLOCKED** on an identified, retained execution target with matching schema and all 12 exact title rows. |
| 9 | Production DB and worker isolation | **PASS for this audit.** The target used tmpfs, Docker network `none`, zero published ports, loopback in the same network namespace, and a read-only code mount. PostgreSQL reported the dedicated DB and role `wtdwi_test`, zero `tracker` databases and zero other clients at the check. Only PostgreSQL and one-shot audit commands ran there. The separate test wrapper created its own internal network, database and one-shot test process. Neither setup passed host application configuration or started workers. |
| 10 | Read-only Stage D preflight | **PASS for read-only behavior.** Batch 1 ran with the database default transaction mode set to read-only. Its JSON reported zero reservations, handoffs, confirmed transmissions, unknown transmissions and checkpoints. Direct counts afterward were zero for budgets, attempts and Stage D events. A separate regression test exercised the default management command inside a PostgreSQL read-only transaction, captured only SELECT statements and saw zero mocked HTTP calls. The command exited successfully while reporting blocked readiness. |

The source risk-acceptance record applies to the historical canary. JustWatch licensing remains open. Owner risk acceptance provides no independent permission for the Stage D document. The accuracy baseline remains 56 source-reported offers, 7 historical first-party candidates, 0 temporally eligible independent comparisons, 0 confirmed negatives, and accuracy **UNDEFINED**. Historical JustWatch observations are same-source evidence. [Canary decision](../../source-permission/decision-2026-11-24.md) · [Runbook](../../operations/issue97_stage_d_bounded_runner.md)

## Owner review evidence still needed

Provide restricted references for the current runtime receipt and exact independent permission, same-grant quota window and spend reconciliation, effective rate/cooldown evidence, and exclusive use across all writers. Confirm custody of any restricted captures and signing material. Identify the actual dedicated disposable target, attest the fingerprint, verify migrations and typed rows, and review its queues, locks, ledgers and read-only preflight. Each batch needs separate owner review before any approval is issued. The rehearsal cluster was destroyed after verification. Raw evidence stays in restricted off-repo custody.

## Local commands and results

Run from the repository root. The PostgreSQL regression run passed 2 of 2 tests. The manifest suite discovered 6 tests, passed 5 and skipped its optional raw TMDb byte check because the restricted evidence directory was not supplied. Total **7 passed, 1 skipped, 0 failed**. The signing and mocked batch-execution suites were outside this dispatch. Hosted CI is reported separately on the draft PR.

```sh
sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_d_preflight
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py -v
```

The following commands produced the database findings above. Both images were already present locally. The SQL fingerprint query builds the canonical input independently of Django. Repeating this on a fresh cluster produces a different fingerprint.

```sh
set -eu
cluster=$(docker run --pull=never -d --network none --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_USER=wtdwi_test -e POSTGRES_DB=wtdwi_test \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea)
trap 'docker rm -f "$cluster" >/dev/null' EXIT
attempt=0
until docker exec "$cluster" pg_isready -h 127.0.0.1 -U wtdwi_test -d wtdwi_test >/dev/null; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 30 ] || exit 1
    sleep 1
done
app() {
    docker run --pull=never --rm --network "container:$cluster" \
        --mount "type=bind,source=$PWD/backend,target=/app,readonly" \
        -e DJANGO_DEBUG=1 -e POSTGRES_USER=wtdwi_test -e POSTGRES_DB=wtdwi_test \
        -e POSTGRES_HOST=127.0.0.1 -e WTDWI_DISPOSABLE_TEST_DB=1 \
        -e AVAILABILITY_STAGE_D_DISPOSABLE=1 \
        --entrypoint python sha256:b0fcbfe183cb9074a38fde01a487c3e0240b6a28f9cfa3c4b74f5e8e0dd487f4 \
        manage.py "$@"
}
sql() { docker exec "$cluster" psql -X -At -v ON_ERROR_STOP=1 -U wtdwi_test -d wtdwi_test -c "$1"; }
docker inspect --format '{{.HostConfig.NetworkMode}} {{len .HostConfig.PortBindings}}' "$cluster"
sql "SELECT version(), current_database(), current_user"
sql "SELECT count(*) FROM pg_database WHERE datname='tracker'"
sql "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()"
app makemigrations --check --dry-run
app migrate --noinput
app migrate --check
app showmigrations availability
sql "SELECT tgrelid::regclass, tgenabled FROM pg_trigger WHERE tgname='stage_d_ledger_immutable' ORDER BY 1"
sql "SELECT encode(sha256(convert_to(format('{\"actual\":[%s,%s],\"backend\":\"postgresql\"}', d.oid, c.system_identifier),'UTF8')),'hex') FROM pg_database d CROSS JOIN pg_control_system() c WHERE d.datname=current_database()"
sql "SELECT enabled, configuration ? 'outbound_availability_permission' FROM availability_source WHERE slug='justwatch'"
sql "SELECT enabled FROM availability_country WHERE code='NL'"
sql "ALTER DATABASE wtdwi_test SET default_transaction_read_only=on"
sql "SHOW transaction_read_only"
app issue97_stage_d --batch 1
sql "SELECT (SELECT count(*) FROM availability_justwatchrequestbudget), (SELECT count(*) FROM availability_availabilityexperimentattempt), (SELECT count(*) FROM availability_availabilitystagedattemptevent)"
```

The policy and ledger checks above were traced through the [Stage D policy](../../../backend/availability/services/issue97_stage_d_policy.py), [runner](../../../backend/availability/services/issue97_stage_d_runner.py), [receipt validator](../../../backend/availability/services/source_permissions.py), [shared transport budget](../../../backend/availability/sources/justwatch_transport.py), [ledger migration](../../../backend/availability/migrations/0019_stage_d_run_ledger.py) and [merge migration](../../../backend/availability/migrations/0020_merge_stage_d_offerprice_variants.py).
