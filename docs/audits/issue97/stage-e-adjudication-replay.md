# Stage E offline adjudication and application replay — issue #97

**Status: READY_FOR_OWNER_AUTHORIZATION for the offline adjudication package. Live collection, entitlement and playback remain BLOCKED.** All work below is offline: mocked fixtures, sanitized structured inputs and disposable PostgreSQL only. No provider request, execution token, production write, migration, deploy or permission change was made. No raw first-party document, header, credential, signed link or private path is recorded here or in Git.

This report extends the frozen [Stage E protocol](stage-e-evidence-protocol.md), [candidate matrix](stage-e-candidate-matrix.csv), [authorization request](stage-e-authorization-request.md) and [Batch A rehearsal](stage-e-offline-rehearsal.md). It adds an executable, transport-free adjudication engine and a replay of the application's display claim against independent source claims. Accuracy remains **UNDEFINED**.

## What was added

| Path | Purpose |
| --- | --- |
| `backend/availability/services/issue97_stage_e_adjudication.py` | Pure adjudication engine. Gates for typed-title identity, market, service/class, contemporaneous window, freshness and source independence; scoped positive/negative/unknown/needs-recheck verdicts; EUR-minor price comparison; TP/FP/FN/TN outcomes; denominator-aware metrics; display retrieval accounting. No I/O. |
| `backend/availability/tests/test_issue97_stage_e_adjudication.py` | 32 offline tests over sanitized fixtures. Exercises all 15 frozen rows and every gate, verdict, price and metric rule. No transport, no database. |
| `scripts/audit_issue97_stage_e_adjudication.py` | Standalone Python check of the real 15-row CSV through the engine. No Django, no database, no network. |
| `docs/audits/issue97/stage-e-adjudication-replay.md` | This report. |

## Verification of all 15 candidate rows

The engine was run against the authoritative frozen matrix. Batch A = 9 rows (3 first-party documents: Netflix/Inception, Prime/Inception, Rakuten/Dune; the `max_amazon` add-on row reads the Prime document). Batch B = 6 rows (5 documents: four Netflix titles and one Rakuten title). The seven typed titles and the single NL market match the matrix. The proposed ceilings are 3 first-party + 2 JustWatch for A and 5 + 5 for B, combined 8 + 7, all subject to separate approval; no attempt was spent.

Every row reaches the time gate with historical evidence and is therefore **not eligible** for a binary score: the accepted first-party observations are in the November 23 `[15:00, 16:00) UTC` reference hour and the index observations are dated November 24, so neither the per-title slot nor the ten-minute expanded span can be satisfied. Outcome per row is `abstention`/`unresolved`, never TP/FP/FN/TN. Independence of the JustWatch index from each named first-party service is confirmed structurally; no historical row is re-adjudicated. Fresh adjudicated pairs remain **0**; confirmed negatives remain **0**.

## Rules rehearsed

| Area | Rehearsed behavior |
| --- | --- |
| Typed-title identity | Exact `(media_type, tmdb_id)` binding, canonical year/title and native-id agreement; unresolved, missing or conflicting identity stays unknown. |
| NL territory | Exact `NL`; explicit basis required for first-party, reviewed `request_scoped` admitted only for the index; unknown or conflicting territory rejected. |
| Provider and offer class | Exact canonical slug and provenance-bound id; `netflix`, base `prime`, `max_amazon` (add-on through Prime, parent retained) and `rakuten_tv` kept distinct; rent and buy scored separately. |
| Rent/buy prices and variants | Localized EUR parsing to minor units (`1.299,00` → `129900`, `12,99` → `1299`), exact cents equality on matching variant and comparable basis; conflicting amount or variant set → `needs_recheck`; ranges, unknown currency and unmatched variants stay unknown. Subscription/free prices unscored. |
| Fresh vs last-known | Only `fresh` observations pass; a current application read carrying only historical offers is `last_known` and does not support a present-availability claim. |
| Same-source echo | JustWatch against JustWatch, and an app row whose provenance is JustWatch, are `SAME_SOURCE_ECHO` and excluded from the independent score; unattributed provenance is `NOT_PROVEN`. |
| Outcomes | Rule-specific first-party positives; affirmative scoped negatives; `unknown` for login walls, empty shells, 404s and missing rows; `needs_recheck` for straddled changes or conflicting scoped signals. Index absence is never a negative. |
| Denominators | Six rates computed from TP/FP/FN/TN with numerator/denominator recorded; every rate is `UNDEFINED` when its denominator is zero, and recall is `UNDEFINED` while any independent positive abstains. |
| Display vs source accuracy | Display retrieval is counted only over independently positive pairs with a completed, correctly scoped read; retrieval misses are logged separately; transactional pairs are excluded when the surface intentionally omits them; echoed and unresolved reads are not scored as misses. These counts never enter the accuracy cells. |

Entitlement and playback are left `untested` on every row. Catalogue presence and advertised availability are recorded separately from access.

## Commands and exact results

Run from the repository root at this branch head. Docker used the locally pinned PostgreSQL image and the local `wtdw-issue97-offline:latest` backend image; the harness creates an internal-only network, tmpfs database, read-only code mount and the disposable-database identity guard. Local PostgreSQL 16 / Python 3.12.16 were reported by the earlier rehearsal.

```sh
python3 -E scripts/audit_issue97_stage_e.py
python3 -E scripts/audit_issue97_stage_e_adjudication.py
git diff --check
sh -n scripts/rehearse_issue97_stage_e.sh
sh scripts/rehearse_issue97_stage_e.sh \
  availability.tests.test_issue97_stage_e_adjudication \
  availability.tests.test_issue97_stage_e_rehearsal \
  availability.tests.test_experiment_runner.GuardedTransportTests \
  availability.tests.test_experiment_runner.PostgreSQLReservationTests \
  availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_issue97_collection \
  tracker.tests.test_issue97_availability_contract
```

Observed results:

- `audit_issue97_stage_e.py` — CSV PASS (15 candidates A=9/B=6, 13 accepted observations, 7 Stage C pairs, 2 controls), Provenance PASS, fresh adjudicated pairs **0**, accuracy **UNDEFINED**, four artifact digests unchanged (`ac1d37…`, `560cea…`, `f397d5…`, `ce3807…`).
- `audit_issue97_stage_e_adjudication.py` — PASS: 15 rows with no binary outcomes, independence confirmed, all six rates UNDEFINED, entitlement/playback untested; zero provider calls and zero database writes.
- `git diff --check` — clean; `sh -n` — clean.
- Django focused suite — **Ran 92 tests, OK (skipped=1)**: 60 pre-existing tests plus 32 new adjudication tests. The one skip is the optional check that reads the repo-level CSV; the image mounts only `backend`, so the authoritative CSV check is the standalone script above. The new module alone: **Ran 32 tests, OK (skipped=1)**.

These are local results. Hosted PR CI remains blocked before its first step by GitHub billing; local PASS is not hosted CI PASS.

## Blockers and exact missing evidence

Execution and #97 acceptance remain blocked on evidence not available offline:

1. Owner-signed exact-scope authorization for Batch A (manifest digest, matrix digest, per-title half-open UTC slots, per-source attempt ceilings, source revisions, expiry) and a separate decision for Batch B. No approval is issued by this package.
2. A separately reviewed Stage E runner entry point that enforces first-party/index scopes, timing, cooldown and recovery through the checked-in runtime. The existing fixed six-attempt runner refuses the Stage E shape, as recorded in the Batch A rehearsal.
3. First-party source/access and custody review for the named public documents, plus a current runtime receipt, quota/rate evidence, reconciled attempt ledger and clean read-only preflight for the JustWatch scope.
4. Fresh, contemporaneous, independent observations inside approved slots. Without them the time gate cannot pass, denominators stay zero and all accuracy rates stay UNDEFINED.
5. A successful hosted CI run on the review head. Entitlement and playback would require separate authorized viewer tests and are explicitly out of scope here.

## Handoff

Model/provider/reasoning effort: opencode / hy4-flash-free, UNPINNED. Cost: **COST_UNKNOWN**. Approved capturers and reviewers are unchanged from the protocol. Keep #97 open and #103 on hold; ChatGPT Web owns final review and merges. No production write, deployment, merge, issue closure or force-push was performed.
