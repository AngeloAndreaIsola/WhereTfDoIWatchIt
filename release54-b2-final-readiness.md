# Release 54 — B2 combined integration readiness

Validated on 7 November 2026 (Europe/Amsterdam). Ready for ChatGPT Web's independent review of the integration candidate, subject to the limitations below. This document does not authorize remote merges or deployment.

## Exact revisions

- PR #59 base: `d299fce499e40e61b2d5b5502c487b320260ae97` (`integration/release54-hardening`, current GitHub head when inspected).
- Integration candidate source SHA: `beae804316ea00223a13303865e1cff86136438d`.
- Candidate branch: `codex/release54-b2-final`.
- Isolated managed worktree: `/Users/angeloandreaisola/.codex/worktrees/release54-b2-final/WhereTfDoIWatchIt`.
- This report is packaged in a documentation-only child commit; the report-bearing branch-tip SHA is supplied in the final handoff. All executable source is identical to the tested candidate above.

| Integration order | Input PR | Exact approved input SHA | Local merge commit |
|---|---|---|---|
| 1 | #65 | `0333d613bef5d5088deb863a33d0c746c5795041` | `8c8c16b81288485d716ae9dc34be47e601c33cf6` |
| 2 | #66 | `0dad646618a964734bb70c09044c0f81463c7940` | `60f90cd0884138410479ff5cca0e0be6aa580b52` |
| 3 | #68 | `ed77288ff74bc16fa1be1687d905feb9cc57acc6` | `9a67114e8b189e223dbc82097075669a44808824` |
| 4 | #67 | `668c9d050517698d61b94f64eed51c0574df75a9` | `9bcd6a7490ad797bda92c98be6e33ddd542a0998` |

Integration corrections: `dfa8cfc9d7d91a6e9fc953aa53ffe7a8bbbcf801` (shared sweep supersession, combined regressions, JustWatch/TMDb test isolation), then `beae804316ea00223a13303865e1cff86136438d` (NHK test isolation).

## Overlapping changes and resolutions

Git's three-way merges produced no textual conflicts. The overlapping files were inspected deliberately; no whole-file ours/theirs substitutions were used.

- `availability_report.py`: retained #66's `confirmed`, `fresh`, `trusted`, `trust`, and `playable` metadata together with #68's structured request evidence (`status`, `outcome`, `blocked_reason`, request identity/timestamp), definitive-negative classification, and country-aware supersession. Confirmed fresh scraper evidence may appear in `available_on`, but it does not create a canonical playable offer or resolve scheduling. Unconfirmed/stale evidence remains historical; legacy Availability rows remain display-only with `legacy_unknown` trust.
- `refresh_requests.py`: retained #65's EXPLICIT override and quota/rate guards together with #68's per-market batched permanent negatives and page-snapshot invalidation after definitive answers. Legacy source aliases normalize to the default source; blank-country negatives cannot become global evidence.
- `test_detail_availability_state.py`: retained the trust-aware unknown-state expectation and explicit `NO_OFFER` fixture outcomes. Successful status alone does not manufacture a negative answer.
- `test_metered_gate_policy.py`: retained EXPLICIT semantics and the definitive-negative fixture updates. Gate ON does not override deliberate refreshes or bypass quotas.
- Added `test_b2_combined_integration.py`, including a single report payload containing both trusted mapping metadata and quota-blocked provenance, a legacy-alias supersession regression, a sweep country/supersession regression, and uncapped SQL counting.

## Integration defects fixed

1. **Background sweep ignored negative supersession.** Before the correction, an NL `TITLE_NOT_FOUND` followed by a newer NL `DONE/NO_OFFER` still suppressed the sweep. The queue/report readers correctly superseded it. The new regression failed with `0 != 1` upstream boundary calls in NL. The sweep now reads the shared `_permanent_miss_sources` policy once for its candidate set and selected country, after queue draining. Its compatibility helper also delegates to that reader. An IT permanent miss remains isolated; JP remains eligible.
2. **Three existing tests attempted provider access.** The JustWatch empty-summary test discovered migration-seeded partitions, the scraper import endpoint fell through to TMDb matching, and the NHK no-query test fetched its homepage. All three now use explicit local stubs. Network-disabled containers prevented provider contact throughout discovery of these defects.
3. **Measurement harness corrected.** Django's bounded `queries_log` truncated an exploratory 200-title count. Final sweep measurements use `connection.execute_wrapper`, which counts every executed SQL statement independently of that buffer. Truncated exploratory numbers were discarded.

## Isolation and runtime

New containers only: `release54-b2-final-pg`, `release54-b2-final-backend`, `release54-b2-final-frontend`. All have Docker `NetworkMode=none`; no published ports. PostgreSQL storage is a fresh tmpfs cluster, shared with the backend only through a dedicated Unix-socket volume. No production databases, volumes, credentials, application services, or pre-existing worktrees were changed. Task-owned test containers were stopped after validation; the isolated worktree remains available for review.

The backend's persistent environment has `AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED=0`; ON/OFF changes occur only inside test patch contexts. Provider keys are blank. The backend source mount is read-only. Tests use PostgreSQL, not SQLite and not `config.test_settings`.

Runtime: Python 3.12.16, Django 5.2.19, PostgreSQL 16.15, Node 22.23.4, npm 10.9.9, Next.js 14.2.35, Vitest 4.1.11. Cached frontend package manifests/lockfile and backend requirements were compared with the candidate and match. No dependency installation or image download was performed.

Cached image IDs:

- Backend `b2-3a-mapping-evidence-backend:latest`: `sha256:2efaa2d37c8a371bac7b70fbb3cc89eff6da674639624f96341fda3fbd8b4455`.
- Frontend `b2-3a-mapping-evidence-frontend:latest`: `sha256:cb39a3c4c78a3d4adde6c99c92b33efd93cd4be1287528373a0a7a91657a3e5c`.
- PostgreSQL `postgres:16-alpine`: `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368`.

## Commands and results

Commands below run from the isolated worktree unless an absolute path is shown. Host log files are supporting evidence; this report contains the conclusions and reproduction commands.

### Local integration

```sh
git switch -c codex/release54-b2-final d299fce499e40e61b2d5b5502c487b320260ae97
git merge --no-ff 0333d613bef5d5088deb863a33d0c746c5795041 -m 'Integrate PR #65 explicit refresh and background gate'
git merge --no-ff 0dad646618a964734bb70c09044c0f81463c7940 -m 'Integrate PR #66 trusted scraper mapping evidence'
git merge --no-ff ed77288ff74bc16fa1be1687d905feb9cc57acc6 -m 'Integrate PR #68 blocked provenance and negative evidence policy'
git merge --no-ff 668c9d050517698d61b94f64eed51c0574df75a9 -m 'Integrate PR #67 offline source-policy counterfactuals'
```

The managed worktree was created through Codex's `create_worktree` tool at the exact PR #59 base. The shell branch command above is the equivalent checkout sequence.

### Fresh network-disabled PostgreSQL environment

```sh
B2_ROOT=/Users/angeloandreaisola/.codex/worktrees/release54-b2-final/WhereTfDoIWatchIt
docker volume create release54-b2-final-pgsocket
docker run -d --name release54-b2-final-pg --network none \
  --tmpfs /var/lib/postgresql/data \
  -v release54-b2-final-pgsocket:/var/run/postgresql \
  -e POSTGRES_USER=b2_test -e POSTGRES_DB=b2_integration \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
docker run -d --name release54-b2-final-backend --network none --entrypoint sleep \
  -v "$B2_ROOT/backend:/app:ro" -v "$B2_ROOT/scripts:/integration:ro" \
  -v release54-b2-final-pgsocket:/var/run/postgresql \
  -e POSTGRES_HOST=/var/run/postgresql -e POSTGRES_USER=b2_test \
  -e POSTGRES_DB=b2_integration -e POSTGRES_PASSWORD= \
  -e DJANGO_SECRET_KEY=isolated-b2-integration-test-key \
  -e DJANGO_ALLOWED_HOSTS=testserver,localhost \
  -e AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED=0 \
  -e TMDB_API_KEY= -e RAPIDAPI_KEY= -e WATCHMODE_API_KEY= \
  -e PYTHONDONTWRITEBYTECODE=1 \
  b2-3a-mapping-evidence-backend:latest infinity
docker exec release54-b2-final-pg pg_isready -U b2_test -d b2_integration
docker exec release54-b2-final-backend ln -s /integration /scripts
docker exec release54-b2-final-pg createdb -U b2_test b2_regression
```

Django creates and destroys `test_b2_integration` or `test_b2_regression` for each run. `--keepdb` was not used.

### Backend

The final full suite uses this host bootstrap, copied into the isolated backend. It prevents DNS and non-Unix socket connections and returns failure if any attempt is swallowed by application error handling:

```sh
cat > /tmp/release54-b2-network-free.py <<'PY'
import os
import socket
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, '/app')
network_attempts = []
original_getaddrinfo = socket.getaddrinfo
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex

def blocked_dns(host, *args, **kwargs):
    network_attempts.append(('dns', str(host)))
    raise AssertionError(f'Network DNS forbidden during isolated tests: {host}')

def guarded_connect(sock, address):
    if sock.family != socket.AF_UNIX:
        network_attempts.append(('connect', str(address)))
        raise AssertionError(f'Network socket forbidden during isolated tests: {address}')
    return original_connect(sock, address)

def guarded_connect_ex(sock, address):
    if sock.family != socket.AF_UNIX:
        network_attempts.append(('connect_ex', str(address)))
        raise AssertionError(f'Network socket forbidden during isolated tests: {address}')
    return original_connect_ex(sock, address)

socket.getaddrinfo = blocked_dns
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
from django.core.management import execute_from_command_line
try:
    execute_from_command_line(['manage.py', 'test', *sys.argv[1:], '--noinput', '-v', '2'])
finally:
    print(f'NETWORK_GUARD_ATTEMPTS={len(network_attempts)} {network_attempts}', flush=True)
    if network_attempts:
        raise SystemExit(90)
PY
docker cp /tmp/release54-b2-network-free.py release54-b2-final-backend:/tmp/run_network_free.py
docker exec -e DJANGO_ALLOWED_HOSTS=testserver,localhost,backend \
  -e INTERNAL_FRONTEND_PROXY_HOST= \
  release54-b2-final-backend python /tmp/run_network_free.py \
  > /tmp/release54-b2-backend-accepted.log 2>&1
```

Final result: **2,879 tests passed in 106.562s; exit 0; `NETWORK_GUARD_ATTEMPTS=0 []`**. The complete discovery includes both `tracker` and `availability`, migrations and PostgreSQL concurrency/advisory-lock tests. The final invocation is equivalent to `python manage.py test --noinput -v 2` with the guard installed before discovery.

The proxy hostname is blank only for this isolated invocation; explicit-IP security tests still run. An initial unguarded full run executed 2,875 tests and had one environment-caused failure because `backend` was omitted from `DJANGO_ALLOWED_HOSTS`. Exploratory guarded runs identified the DNS-dependent fixtures described above. No provider connection succeeded. These failed runs are not counted as passing validation.

Focused command, after the sweep fix and before adding the last two combined assertions and NHK stub:

```sh
docker exec -e POSTGRES_DB=b2_regression release54-b2-final-backend \
  python manage.py test \
  tracker.tests.test_b2_combined_integration \
  tracker.tests.test_background_metered_gate \
  tracker.tests.test_negative_evidence_precedence \
  tracker.tests.test_mapping_evidence_trust \
  tracker.tests.test_offline_counterfactual \
  tracker.tests.test_p2_metadata_integrity \
  availability.tests.test_aggregators.JustWatchPartitionTests \
  tracker.tests.test_provider_system --noinput -v 2 \
  > /tmp/release54-b2-focused.log 2>&1
```

Result: **99 tests passed** in 11.922s. The final full suite covers the final versions of all these tests.

Reproduce just the distinct background-sweep measurements with the final guard:

```sh
docker exec -e DJANGO_ALLOWED_HOSTS=testserver,localhost,backend \
  -e INTERNAL_FRONTEND_PROXY_HOST= release54-b2-final-backend \
  python /tmp/run_network_free.py tracker.tests.test_b2_combined_integration
```

### Frontend

```sh
docker run -d --name release54-b2-final-frontend --network none --entrypoint sleep \
  -v "$B2_ROOT/frontend:/candidate:ro" -e NEXT_TELEMETRY_DISABLED=1 -e CI=1 \
  b2-3a-mapping-evidence-frontend:latest infinity
docker exec release54-b2-final-frontend sh -c \
  'mkdir /validation && cp -a /candidate/. /validation/ && ln -s /app/node_modules /validation/node_modules'
docker exec -w /validation release54-b2-final-frontend sh -c \
  'npm test && npm run lint && ./node_modules/.bin/tsc --noEmit && npm run build' \
  > /tmp/release54-b2-frontend.log 2>&1
```

Result: exit 0. **25 test files / 202 unit tests passed**; lint, TypeScript and production build passed. Six existing lint warnings remain: two React hook dependency warnings in catalog/watchlist pages and four `no-img-element` warnings. Build static generation completed. Frontend files were unchanged after this validation.

### Offline counterfactuals

```sh
docker exec -e PYTHONPATH=/app release54-b2-final-backend \
  python /integration/offline_counterfactual_report.py --count 100 --minimal \
  --output /tmp/counterfactual-100.json > /tmp/release54-b2-counterfactual-100.json
docker exec -e PYTHONPATH=/app release54-b2-final-backend \
  python /integration/offline_counterfactual_report.py --count 200 --minimal \
  --output /tmp/counterfactual-200.json > /tmp/release54-b2-counterfactual-200.json
```

Both exited 0 using the standalone dummy database backend. Parity tests compare each NL/IT/JP decision in both gate states with the combined production `decide_sources_for_title`; real PostgreSQL report fixtures cover fresh/stale canonical evidence, unconfirmed mappings and country isolation. Determinism and zero-SQL/zero-network fixture execution are also tested.

| Synthetic titles | NL eligible OFF / ON | IT eligible OFF / ON | JP eligible OFF / ON | NL answers retained / lost if metered evidence removed |
|---:|---:|---:|---:|---:|
| 100 | 100 / 12 | 100 / 21 | 100 / 21 | 48 / 16 |
| 200 | 200 / 32 | 200 / 53 | 200 / 52 | 90 / 27 |

These are synthetic eligibility and evidence-removal projections. Actual provider requests, quota spend, savings and counterfactual SQL/timing metrics remain `not_measured`. The measured sweep SQL below is a separate experiment.

## Background-sweep SQL measurements — gate ON

| Candidate titles | Sweep SQL statements | Policy-gated metered scopes | Queue rows drained | Upstream boundary calls |
|---:|---:|---:|---:|---:|
| 1 | 47 | 1 | 0 | 0 |
| 100 | 3,809 | 100 | 0 | 0 |
| 200 | 7,609 | 200 | 0 | 0 |

Method: fresh NL canonical Watchmode offers for every candidate, no pending queue rows, selected watchlist sizes 1/100/200, gate ON, full `process_watchlist_refresh` entry point. Candidate selection, freshness, negative-history reads, production policy/report computation and run-audit writes execute against PostgreSQL. External `refresh_content` is mocked as a safety boundary and receives zero calls; execution limiters are unlimited test doubles, while report quota reads remain real. Fixture insertion and assertions are outside the measurement window. Counting uses the database execution wrapper, not Django's capped query buffer. The focused and full runs produced the same numbers.

This sweep remains **38N + 9 SQL statements** for these fixtures: per-title report/policy evaluation is still linear. Batched negative-history reads alone do not make the complete sweep constant-query.

For comparison only, #68's queued-worker tests were rerun: **4 / 103 / 203** queries for 1/100/200 queued titles with mocked freshness and unlimited budgets. Its negative-history-only measurements remain **2 / 200 / 400** queries when read individually, versus **2 / 2 / 2** batched. Those are different paths and fixture boundaries; they are not the sweep result.

## Required behavior coverage

| Requirement | Passing coverage in final full suite |
|---|---|
| EXPLICIT single-title and bulk refresh, gate ON | `test_background_metered_gate`: request upgrade/deduplication/execution; explicit quota and rate-window guards |
| Background sweep ON and OFF | `test_background_metered_gate`; combined real-policy sweep SQL fixture |
| Quota exhaustion and retry | `test_event_driven_refresh`, `test_refresh_lifecycle_terminality`, `test_refresh_source_policy_drain`: terminal quota circuits, retryable rate limits, no attempt inflation, source isolation |
| Confirmed/unconfirmed/fresh/stale scraper evidence | `test_mapping_evidence_trust`; combined metadata/provenance payload test |
| Legacy availability display-only | `test_mapping_evidence_trust`: `legacy_unknown`, non-playable, no scheduling promotion |
| Blocked-reason preservation | `test_negative_evidence_precedence`, combined payload test, detail/result-reducer tests |
| Negative supersession and country isolation | `test_negative_evidence_precedence`, combined sweep NL/IT/JP regression and alias compatibility regression |
| Batched progress and deduplication | `test_refresh_status_live_scope`, `test_refresh_lifecycle`, `test_event_driven_refresh`, `test_refresh_result_reducer`, batch isolation in negative evidence tests |
| Offline combined-policy parity | `test_offline_counterfactual`, including every market/gate decision and deterministic 100/200 fixtures |
| Network-free execution | Docker network none for all test containers; final full-suite DNS/socket guard with zero attempts |

## Remaining limitations and remote merge recommendation

- **Remote handoff blocked by automatic approval review.** Publishing `codex/release54-b2-final` to `origin` (`https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt.git`) was rejected because it would export private repository source and this request authorized local preparation rather than this specific remote publication. The push did not execute. No alternate publication mechanism was attempted. Explicit user approval is needed before this local candidate can be inspected by ChatGPT Web through GitHub.

- The background-sweep query count remains linear and high at 200 candidates. No constant-query or production-latency claim is made; further batching of policy/report reads is a follow-up. The measurements intentionally distinguish the sweep from queue-drain benchmarks.
- Providers, live credentials, production data, real quota savings, deployment, and browser end-to-end flows were not exercised. All provider-facing behavior was stubbed or fixture-based. Existing frontend lint warnings remain.
- The candidate's sweep correction must accompany the original PRs; merging only #65/#66/#68/#67 would reintroduce sweep/report supersession disagreement.
- No GitHub Actions polling, independent final PR review, remote PR merge, master change, deployment, paid external model usage, or messages to other people were performed. The candidate branch remains local; no PR was created or merged. All pre-existing worktrees were preserved.

After ChatGPT Web independently reviews the exact handoff SHA and any new CI results, recommended remote sequence is **#65 → #66 → #68 → #67** into PR #59's hardening branch, followed by the two candidate correction commits (or a reviewed equivalent follow-up PR). Verify the resulting source tree matches the candidate before advancing #59. Keep the metered gate OFF outside isolated tests. Any later merge of #59 or deployment requires its own authorization.

Evidence SHA-256 `/tmp/release54-b2-backend-accepted.log`: `7046375416b61f6dee64ba473d53004e555d5df9ff3705c1a1da7f4059aef25e`.

Evidence SHA-256 `/tmp/release54-b2-frontend.log`: `d21c214a857836cbabfee0653049b43e7a57001ad17d53a2816145ee072a44bc`.
