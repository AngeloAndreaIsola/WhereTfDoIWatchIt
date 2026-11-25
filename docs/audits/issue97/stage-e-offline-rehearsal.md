# Stage E Batch A offline rehearsal

The Batch A fixture rehearsal and historical provenance review passed locally. Nine Batch A rows remain pending. Fresh adjudicated pairs are **0** and all accuracy rates remain **UNDEFINED**. Execution approval is pending. The only writes in the rehearsal were to disposable PostgreSQL, and the only transport events came from mocks.

## What ran on disposable PostgreSQL

The [Batch A test fixture](../../../backend/availability/tests/test_issue97_stage_e_rehearsal.py) uses three synthetic GET scopes and two synthetic POST scopes, ordered across two title slots. It uses the existing PostgreSQL attempt, response and scope-checkpoint tables, plus the shared advisory lock. A second database connection verified that a reservation committed before the mock handoff. A persisted HTTP response counts as a confirmed **fixture** transmission. A spent reservation without a response remains transmission unknown. Each title checkpoint is derived from completion of all its capture scopes.

| Scenario | Spent | Confirmed | Unknown | Scope checkpoints | Stop or restart result |
| --- | ---: | ---: | ---: | ---: | --- |
| All five scopes complete | 5 | 5 | 0 | 5 | After the first three scopes, a recreated connection resumed at the second title. A second restart skipped all five scopes. Two title checkpoints, zero additional sends, and original UTC bounds preserved. |
| Wrong target or order, missing or excessive uncertainty, slot boundary or expired fixture scope | 0 | 0 | 0 | 0 | Refused before transport. |
| First-party pacing after restart | 1 | 1 | 0 | 1 | Second GET refused at a two-second gap. At three seconds the second GET completed, bringing spent, confirmed and scope checkpoints to two each. |
| Timeout after durable reservation | 1 | 0 | 1 | 0 | Restart blocked both the spent scope and the next scope pending reconciliation. |
| Redirect, denial, rate limit, server error or invalid shape | 1 | 1 | 0 | 0 | Per case. One response, zero retries or redirect hops, and restart refused the next scope. |
| Response clock outside the slot, reversed UTC or monotonic time, or excessive expanded span | 1 | 1 | 0 | 0 | Per case. The response stayed charged and the scope remained incomplete. |
| Title span crosses the ten-minute limit | 3 | 3 | 0 | 2 | An expanded span of exactly ten minutes passed. One second over blocked the third scope checkpoint and left the title incomplete. |
| Interruption after response commit and before checkpoint | 1 | 1 | 0 | 0 | Restart required reconciliation and made zero new sends. |

The same local run exercised the existing fixed JustWatch transport, PostgreSQL reservations and guarded preflight, the ordinary reviewed transport, and the app's typed current, source-reported and historical availability rules. The read-only preflight with no current evidence returned `blocked` with four evidence blockers, zero reservations, zero queue overlap and zero unfinished refreshes.

**Integrated Stage E runner preflight remains blocked.** The rehearsal uses synthetic clocks and responses. The checked-in runtime refuses a five-attempt manifest with `manifest maximum must equal the fixed 6-attempt ceiling`, one-attempt title scopes with `manifest differs from the fixed approved scope set`, and a first-party scope with `scope is not an exact member of the approved manifest`. Its fixed client can retry HTTP 503, which the zero-retry Stage E fixture stops after one attempt. An integrated check needs a separately reviewed Stage E entry point that enforces the first-party and index scopes, timing, cooldown and recovery rules, and demonstrates them through that entry point on disposable PostgreSQL. Production configuration, execution tokens and runtime permissions were untouched.

## Independent historical provenance review

The review joined all 15 candidates against the accepted-observation and original matrix blobs at the [Stage C merge](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/commit/7f35a5556d2c03364a6250ca6de4f626e720abce) and the sanitized matrix at the [Stage A/C reconciliation merge](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/commit/5389a6ca3fea89aab3fdac3655b3b2af2792807e). The accepted-observation bytes agree across both merges and this checkout. The sanitized matrix agrees byte-for-byte with the checkout, and every shared field agrees with the original seven-row matrix.

- All 13 accepted observation IDs are referenced exactly once. Each reference matches its typed ID, year, market, canonical service and offer class. They preserve the original November 23 `[15:00, 16:00) UTC` bounds, advertised-only decisions and eight historical capture digests.
- All seven Stage C pair references are in Batch A and point to the same observation IDs, first-party timestamps and recorded prices in both preserved records. The three Stage C capture aliases are retained. All seven keep `temporal_eligible=no`, `independently_adjudicated=no` and `outside_reviewed_window`. The [Stage C report](../stage2b/issue97-independent-verification-report.md) places the index observations on November 24, outside the November 23 reference hour.
- Two Batch A free-class controls have no historical pointers. Batch B has the six remaining historical observations. Historical identity decisions were carried forward as recorded. Fresh independent adjudication remains **0**.

| Reviewed artifact | SHA-256 |
| --- | --- |
| Stage E candidate matrix | `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d` |
| Stage C accepted observations | `560ceaf1f3edf4b2c6b33988dd578acbd76a38c4d18e3a49220e92005961a389` |
| Original Stage C matrix | `f397d59f31279acf9cb8c75951f7fec0257dc28ccac542bf78acfbc6a3ae7ef9` |
| Sanitized Stage C matrix | `ce38077c7ee1d8a16a8c63e6f2652b35d1b9b42e7eaa4b15076e9ffa8d008a56` |

## Reproduce the offline audit

Run from the repository root at this handoff commit or a descendant with both historical Git objects available. Docker must already have `postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` and the Python backend test image used by the [earlier offline preflight](../../operations/issue97_justwatch_collection_preflight.md), `wtdw-issue97-offline:latest`. Set `STAGE_E_TEST_IMAGE` to a locally built image from `backend/Dockerfile` if using a different local tag. The test command uses a unique internal network, tmpfs PostgreSQL storage, a read-only code mount and Django's disposable database identity guard. It uses local images exclusively and cleans up the database container and network on exit. The local run used PostgreSQL 16.15 and Python 3.12.16.

```sh
(
  set -e
  python3 -E scripts/audit_issue97_stage_e.py
  git diff --check 54bd248060880523ce63701b46c031508f99338f HEAD
  git diff --check HEAD
  sh -n scripts/rehearse_issue97_stage_e.sh
  sh scripts/rehearse_issue97_stage_e.sh \
    availability.tests.test_issue97_stage_e_rehearsal \
    availability.tests.test_experiment_runner.GuardedTransportTests \
    availability.tests.test_experiment_runner.PostgreSQLReservationTests \
    availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked \
    availability.tests.test_justwatch_reviewed_transport \
    availability.tests.test_issue97_collection \
    tracker.tests.test_issue97_availability_contract
)
```

Expected results are 15 candidates (9 A, 6 B), 13 observation pointers, seven ineligible and unadjudicated Stage C pairs, two controls, and the four artifact hashes above. The diff and shell syntax checks exit zero with no output. Django reports **57 passed, 0 failed, 0 skipped** for the local run, including 10 Batch A fixture tests and one fixed-runtime boundary test. Hosted PR CI is separately blocked by GitHub billing.

## Owner issuance template — unapproved

This blank template is not authorization. The owner must issue a separate signed record after the protocol gates are met; blank or ambiguous fields prohibit execution.

- Owner, signature and date: __; operator: __; independent reviewer: __; custodian: __.
- Batch A subset and omitted rows with reasons: __; matrix SHA-256: __; private manifest digest: __; expiry UTC: __.
- Each typed title `(media_type, tmdb_id)` and approved half-open 30-minute UTC slot: __; revisions and disposable database identity: __.
- For each source scope: private host/path and method reference, payload/context digest, market, scope ID, attempt ceiling, pacing, quota, spend ceiling, egress and access review: __.
- Custody location/access, retention and deletion dates, permitted sanitized outputs: __; clock and preflight receipts: __.
- Owner explicitly authorizes only the listed sends within these limits: __. Batch B, additional attempts and scope changes require separate authorization.

The owner adopts the protocol's typed-title binding, NL market, canonical service and separate offer-class rules, original observation clocks and maximum uncertainty-expanded ten-minute span. Comparable rent/buy prices require equivalent conditions and exact EUR cents; unknowns remain unscored. This scope tests advertised availability; entitlement and playback remain untested. Metrics require eligible independent binary pairs and established nonzero denominators; full-panel recall is undefined with unresolved positives. Approval does not establish results or accept #97.
