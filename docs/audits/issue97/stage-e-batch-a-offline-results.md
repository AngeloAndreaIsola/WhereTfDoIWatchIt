# Stage E Batch A authorization rehearsal results

**Local checks passed at `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d`**, the master revision that merged the [typed display fix](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/216). The only test database was disposable PostgreSQL 16.15. The rehearsal used an internal Docker network, tmpfs database storage, a read-only repository bind mount and mocked provider transport. Backend image ID `sha256:b0fcbfe183cb9074a38fde01a487c3e0240b6a28f9cfa3c4b74f5e8e0dd487f4`. [Batch A plan](stage-e-batch-a-plan.md)

| Check | Result |
| --- | --- |
| Matrix and historical scope audit | 15 frozen candidates, 9 A and 6 B. All 13 historical pointers and eight document digests reconciled. Seven Stage C matches stay outside the temporal window. Both free controls remain unassigned. |
| Adjudication audit | All 15 deterministic synthetic scopes exercised. All 15 substituted original timestamps excluded. The partial-panel run withheld full-panel rates. |
| Historical-binding unit suite | 5 passed, 0 failed, 0 skipped. |
| Adjudicator standalone suite | 27 passed, 0 failed, 0 skipped. |
| Focused PostgreSQL suite | 101 passed, 0 failed, 0 skipped. Includes 27 adjudicator tests, 14 typed app replays, 11 rehearsal and fixed-runtime checks, plus guarded transport, preflight, collection and availability contract checks. The 27 adjudicator cases also ran standalone. |

## Typed HTTP and evaluator outcomes

The four abstention fixtures pair an eligible, independently positive scoped first-party observation with an eligible unknown index verdict. They supply zero binary accuracy cells and the index-to-display projection stays unscored.

| Fixture | Independent display retrieval |
| --- | --- |
| Typed HTTP 200 and a displayed row traced to its sealed, timely source contribution | `displayed`, **1/1**. |
| Typed HTTP 200 and an empty report list | `miss`, **0/1**. |
| Typed HTTP 404 with the supported untracked-title response | `miss`, **0/1**. The series sharing the numerical ID still returned 200. A first-party 404 supplies no independent positive and yields an undefined retrieval rate. |
| Typed HTTP 200 and a displayed row without a matching trace or original observation time | `unresolved`, **0/0 (`UNDEFINED`)**. |

The same-document case with divergent bytes and the identical-payload case returned `same_source_echo`. A sealed source report can show a displayed projection or a projection miss while contributing zero to independent retrieval. The separate displayed-contributor echo case was excluded from independent retrieval even with an abstaining index. Mismatched typed identity, territory, class, lineage, malformed responses, unreviewed conflicts, stale origins and invalid clock/span bounds stayed excluded or unresolved in the relevant comparison. Empty source arrays supplied no verified negative. [Typed app fixtures](../../../backend/availability/tests/test_issue97_stage_e_app_replay.py) · [Adjudication fixtures](../../../backend/availability/tests/test_issue97_stage_e_adjudication.py)

The Batch A fixture exercised durable reservation before send, serial pacing after restart, original clocks, stopping after denial/error and restart reconciliation. The boundary test confirmed that the fixed runner rejects Stage E caps and first-party scopes. The integrated Stage E preflight, real source access decisions, custody and clock receipts, and exact owner and runtime approvals remain outstanding. [Runtime rehearsal](stage-e-offline-rehearsal.md)

The real evidence baseline remains 56 historical JustWatch source-reported offers, seven historical first-party candidate matches, **zero** temporally eligible independent comparisons and **zero** confirmed negatives. Real accuracy and price agreement remain `UNDEFINED`. Entitlement and playback remain `untested`. This preparation sent zero live JustWatch, Notte, Netflix, Prime Video or Rakuten TV requests and issued zero execution grants or tokens. It made zero runtime permission changes, deployments or production data writes or migrations.

## Reproduction commands

Run from the repository root with the local images already available. The stand-alone adjudication audit also reads the historical Git objects referenced by the frozen review.

```sh
shasum -a 256 docs/audits/issue97/stage-e-candidate-matrix.csv
python3 -BE scripts/audit_issue97_stage_e.py
python3 -BE scripts/audit_issue97_stage_e_adjudication.py
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e
PYTHONPATH=backend python3 -B -m unittest availability.tests.test_issue97_stage_e_adjudication
sh -n scripts/rehearse_issue97_stage_e.sh
sh scripts/rehearse_issue97_stage_e.sh \
  availability.tests.test_issue97_stage_e_adjudication \
  availability.tests.test_issue97_stage_e_app_replay \
  availability.tests.test_issue97_stage_e_rehearsal \
  availability.tests.test_experiment_runner.GuardedTransportTests \
  availability.tests.test_experiment_runner.PostgreSQLReservationTests \
  availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_issue97_collection \
  tracker.tests.test_issue97_availability_contract
```
