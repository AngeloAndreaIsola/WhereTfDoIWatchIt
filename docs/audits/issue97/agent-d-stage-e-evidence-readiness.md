# Issue #97 — Agent D Stage E evidence-readiness report

**Date:** 2026-11-27 UTC
**Owner-approved mission:** /orchestration, Agent D — #97 Stage E evidence readiness
**Route / identity:** native OpenCode `opencode/hy4-flash-free` (verified fallback); see §0
**Scope:** evidence readiness only. No live capture, no collection, no gate opening.
**Gate 97-E assignment:** **BLOCKED_ON_ACCESS** (see §11)

This report prepares, but does not grant, Stage E execution. It reviews the frozen Stage E
protocol and comparison matrix, independently verifies first-party source access conditions
from retained documents and non-automated, already-authorized public information, assigns a
legally and operationally supported decision to each of the three sources, confirms custody
and retention requirements, defines contemporaneous UTC observation windows, and rehearses
capture and scoring with mocked evidence only. It deliberately does not rerun completed
implementation or documentation work, does not execute live capture, and does not expand scope.

---

## 0. Model, provider, effort and cost record

A fresh in-session identity probe was run exactly as instructed. The first attempt returned a
transient server error (`err_1f48ae74`); the retry succeeded and is the authoritative record.

```sh
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
```

| Field | Value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual self-report | `MODEL_IDENTITY_OK opencode/hy4-flash-free` |
| Provider | `opencode` (native OpenCode CLI) |
| Effort | **UNPINNED** — no effort override set; no HIGH/MEDIUM claim is made |
| Probe cost | `0` (`step_finish.cost: 0`) |
| Session cost | `0` — no paid escalation |

No silent substitution occurred. The preferred `omniroute/ag-gemini-3.5-flash-lite` route was
not used by this agent (coordinator probe reported repeated 503 `ALL_TARGETS_SKIPPED`). No
Sol, Astra or paid escalation was used. The probe text part was the only model output recorded.

---

## 1. Exact base and provenance

| Item | Value |
| --- | --- |
| Coordinator dispatch base | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` |
| Re-fetched remote master (`gh api …/commits/master`) | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` — **exact match** |
| `git rev-parse origin/master` after `git fetch origin` | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` — **exact match** |
| Report branch | `agent-d/stage-e-evidence-readiness-20261127` |
| Branch base (forward-only) | `6af4cd8421ec9c85e4fff724fded60d5cce86c85` |

The branch was created with `git checkout -b agent-d/stage-e-evidence-readiness-20261127
6af4cd8421ec9c85e4fff724fded60d5cce86c85`. It is forward-only from the recorded base; no
completed implementation or documentation work was restarted, amended, rebased or merged.
The workspace's pre-existing local branch (`8985946`) is the merge-base ancestor of this
base and was left untouched.

---

## 2. Frozen protocol and comparison matrix (task 1)

The authoritative frozen artifacts were read and reproduced, not rewritten:

| Artifact | SHA-256 reproduced | Role |
| --- | --- | --- |
| `docs/audits/issue97/stage-e-candidate-matrix.csv` | `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d` | 15 frozen candidates: 9 Batch A, 6 Batch B; 2 controls |
| Stage C accepted observations | `560ceaf1f3edf4b2c6b33988dd578acbd76a38c4d18e3a49220e92005961a389` | 13 historical pointers; zero fresh observations |
| Stage C original matrix | `f397d59f31279acf9cb8c75951f7fec0257dc28ccac542bf78acfbc6a3ae7ef9` | historical seven-row matrix |
| Stage C sanitized matrix | `ce38077c7ee1d8a16a8c63e6f2652b35d1b9b42e7eaa4b15076e9ffa8d008a56` | reconciled matrix |

Protocol facts carried forward from `stage-e-evidence-protocol.md`,
`stage-e-source-review.md`, `stage-e-source-authorization-readiness.md` and the newer
`stage-e-authorization-readiness-package-20261127.md` / `offline-prerequisites-dossier-20261127.md`:

- The panel is **purposive**, covers at most 7/15 titles, and is not a statistical sample.
  All current accuracy rates are **UNDEFINED**.
- Baseline: 56 JustWatch source-reported offers, seven historical first-party matches, all
  with `temporal_eligible=no` and `independently_adjudicated=no`; zero confirmed negatives;
  zero fresh independently adjudicated pairs. Entitlement and playback are `untested`.
- Batch A is three anonymous first-party GETs (E-C1 Netflix/Inception, E-C2
  Prime/Inception, E-C3 Rakuten TV/Dune) plus at most two bounded JustWatch attempts,
  zero retries. Batch B is a separate future decision.
- Ranking is strict priority (audio match → subtitle match → 4K/HDR quality); no change made.

---

## 3. Independent access-condition verification (tasks 2 and 3)

### Method and evidence boundary

Access conditions were checked **only** against retained repository documents and the
permission-review methodology already recorded in `docs/audits/catalogue_provider_permission_review.md`
and `docs/source-permission/decision-2026-11-24.md`. No source webpage, endpoint, robots,
sitemap, API, TMDb, playback or login surface was requested by this agent. No automated
collection was performed or assumed. A page being public, browsable, login-free or previously
reached once does **not** establish permission for automated collection — this is the explicit
precedent of the retained permission review ("A website being public, free, browsable without
login or described by a sitemap is not a grant").

### Documented facts (retained, independently checkable)

Retained first-party captures exist for all three sources, dated 2026-11-23 within
`[15:00,16:00) UTC`, with off-repository digests:

| Source | Historical document | Digest (retained) | Retained context |
| --- | --- | --- | --- |
| Netflix · E-C1 | `inception_nl.html`, `/nl/title/70131314` | `4876c83e5df2779f00a79152ec20594afae4c8de8cd9e268be6b1664cf5ef3c5` | "Anonymous request country NL"; title-level `isAvailable`/`isPlayableOnAdsPlan` true |
| Prime Video · E-C2 | `primevideo_inception_nl.html`, detail `0FFJSDLK6HUWC5T8XJUQODMFJ3` | `e61a628945ad4a81bf19a345b7252f3695087d3a7059dd8fd76bbce7589f6997` | `recordTerritory` and `currentTerritory` NL from detail page |
| Rakuten TV · E-C3 | `rakuten_dune_nl.html`, `/nl/movies/dune-2021` | `c6126e31be57907d0208202b948bf5700d9618963dedd957840277ed1538819f` | canonical NL storefront + Netherlands-specific chart |

These establish **feasibility of a prior observation only**. They are advertised-only
evidence, temporally ineligible for Stage E, and their raw bytes are held in restricted
custody, not Git. Rejected historical captures (Netflix search login wall, Netflix market
redirect, Rakuten client shell) confirm that a successful HTTP status alone does not yield an
offer verdict.

### Per-source decision

| Source | Access evidence | Method / endpoint / auth / region | Decision | Reason and exact evidence needed |
| --- | --- | --- | --- | --- |
| **Netflix (E-C1)** | Historical anonymous NL title GET exists; no current permission record | Proposed: one anonymous HTTPS GET, exact NL title document; no login, no playback; NL context must be established, not assumed | **BLOCKED_ON_ACCESS** | N1–N11 unresolved. Need dated applicable terms/permission reference; permitted method and exact origin/path; NL context basis; extraction scope; finite rate/cooldown; raw retention + deletion deadline; sanitized publication fields; private allowlist digest; clock slot; named custodian and readers; separate owner approval |
| **Prime Video (E-C2)** | Historical anonymous NL detail GET exists; no current permission record | Proposed: one anonymous HTTPS GET, exact NL detail document; embedded film/price/channel fields; recorded and effective territories must agree on NL | **BLOCKED_ON_ACCESS** | P1–P11 unresolved. Need the same evidence class plus a reviewed decision on using embedded film, store-price and HBO-Max-under-Prime channel data, and restrictions on sharing derived offer/price decisions |
| **Rakuten TV (E-C3)** | Historical anonymous NL movie GET exists; no current permission record | Proposed: one anonymous HTTPS GET, exact NL movie document; canonical NL storefront plus independent NL evidence; no trailer/shell inference | **BLOCKED_ON_ACCESS** | R1–R11 unresolved. Need the same evidence class plus retention/publication decisions for displayed rent/buy prices and the free-with-ads control (E09) |

Documented facts versus assumptions are separated throughout: the **facts** are the retained
historical captures and the retention/custody precedent; the **assumptions still requiring
proof** are current permission, anonymous NL egress legitimacy, extraction allowance, finite
rate, retention deadline, publication scope and the separate owner signature. Owner approval
cannot substitute for missing source evidence. All 42 checklist entries (33 source-specific
+ 9 cross-source X1–X9) remain pending; **0 of 3** source decisions are complete.

---

## 4. Available documents for independent comparison (task 4)

All independently comparable documents are retained **outside Git** in restricted custody.
No raw restricted evidence is reproduced in this report or committed.

| Document class | Retention location | Provenance | Git exposure |
| --- | --- | --- | --- |
| Historical first-party title captures (Netflix, Prime, Rakuten + other Stage C pages) | `~/.hermes/private/issue97-canary-ff15058/` — directory mode `0700`, files `0600` | 20 artifacts from commit `ff15058`, SHA-256 and byte counts in `docs/audits/issue97/evidence-custody-verification.md`; 20/20 matched on last independent check | Hashes/counts only; never raw bytes |
| Sanitized Stage C canary evidence | `docs/audits/issue97/jw-canary-2026-11-24-evidence/` | Sanitized JSON + `SHA256SUMS` + `verify_evidence.py`; separate JustWatch canary, not Stage E first-party | Sanitized fields only |
| Frozen matrix and observations | Repository `docs/audits/issue97/` and `docs/audits/stage2b/` | Digests in §2 | Safe, already public |
| Typed identity manifest | `docs/audits/stage2b/identity_manifest_20261122.csv` (SHA `ae78a757…`) | Native-to-typed bindings; raw TMDb payloads deliberately absent from cleaned checkout | CSV only; raw payloads off-repo |

Custody verification performed here: the restricted directory exists with the expected
protection (`0700` directory, `0600` files). This agent did **not** open, copy, modify,
delete or re-hash raw payload contents; it only checked directory metadata and relied on the
retained custody record. No cleanup was performed.

---

## 5. Legally and operationally supported access decisions (task 5)

All three sources are **BLOCKED_ON_ACCESS**, not "authorized" and not "REQUEST_CHANGES"
at the protocol level: the evidence design is sound and already reviewed; what is missing is
source-specific access evidence that only a dated, applicable permission record can supply.
A reduced panel is therefore required before any Batch A decision (§10).

Exact evidence needed, common to all three sources, before a source could move to
"authorized":

1. Dated, applicable terms or permission reference with effective date, version, reviewer,
   review date and expiry, retained privately.
2. Permitted method and exact scope: one anonymous HTTPS GET, pinned origin/path, method,
   native ID, expected title-document format, no redirects/retries/subrequests/credentials.
3. Territory basis: legitimate anonymous NL context (Netflix), agreeing recorded/effective
   territory (Prime), canonical NL storefront plus independent NL evidence (Rakuten).
4. Extraction allowance: which title-specific fields may be read from the response and
   embedded state, and whether derived offer/price decisions may be published.
5. Finite source limit: allowance, pacing and cooldown for at most one GET, zero retries,
   with the lower of scope and reviewed source limits applying.
6. Retention and deletion: permissible raw-document retention period and deletion deadline.
7. Custody: restricted owner-controlled storage, permitted readers and deletion custodian.
8. Cross-source X1–X9: signed allowlist/manifest digests, exact runner/app revisions, NL
   egress, disposable DB identity, integrated preflight, named operator/reviewer/custodian,
   UTC slots with clock receipts, serial accounting and ceilings.
9. Separate exact owner approval for the signed scope after all of the above.

"No HMAC grant, quota/permission change, runtime change or production write was performed."

---

## 6. Evidence retention and private-custody requirements (task 6)

Confirmed from `evidence-custody-verification.md`, `stage-e-source-review.md` and
`stage-e-authorization-request.md`:

- Raw captures and raw manifests stay in restricted owner-controlled storage; historical
  precedent is mode `0700` directory / `0600` files off-repository.
- Record sealed digest, byte count, UTC bounds, reviewer and custody reference per original.
- Publish only reviewed aliases, hashes, counts and structured decisions.
- Keep raw documents, cookies/headers, signed links, private identifiers and private path
  inventories out of Git and public handoffs.
- Treat source bytes as inert data during offline parsing.
- Per-source retention period and deletion deadline are **not yet substantiated** — this is
  part of the access block, not a satisfied condition.
- This report adds no raw evidence and changes no permissions.

---

## 7. Contemporaneous UTC observation windows (task 7 — defined, not executed)

Per `stage-e-evidence-protocol.md`, the windows below are proposed definitions only; no live
slot is approved and no capture will be run under this report.

- Exactly one **absolute half-open 30-minute UTC slot** per typed title, approved in advance.
  Batch A: Inception (movie 27205) and Dune (movie 438631). Batch B: five further titles.
- Each contributing observation records UTC start/end, monotonic elapsed time and a
  synchronization check with clock uncertainty **≤5 s per host**.
- Expanded bounds must fit inside the slot; combined observation span must be **≤10 minutes**.
- An application comparison also includes the current local response read in the same span.
- Cache age and original assertion times are recorded separately; known stale documents,
  pre-slot assertions and the excluded end boundary are ineligible.
- Out-of-window captures remain in the attempt ledger; a new capture needs a separately
  approved slot and budget.

No slot values are filled in here because slot approval is part of the missing exact-scope
owner decision.

---

## 8. Mocked capture and scoring rehearsal (task 8)

Rehearsal used mocked transport only, on disposable PostgreSQL inside an internal Docker
network with zero external routing; the backend source was mounted read-only. No live
provider, TMDb, first-party or playback request was made.

```sh
STAGE_E_TEST_IMAGE=wtdw-issue97-offline:latest sh scripts/rehearse_issue97_stage_e.sh \
  availability.tests.test_issue97_stage_e_rehearsal \
  availability.tests.test_issue97_stage_e_adjudication \
  availability.tests.test_issue97_stage_e_app_replay \
  tracker.tests.test_issue97_availability_contract
```

**Result:** `Ran 64 tests in 8.717s — OK`, disposable test database destroyed on exit.
The rehearsal covers durable reservation before send, serial pacing and cooldown, stop on
denial/challenge/rate-limit/clock/identity mismatch, restart accounting, and original-clock
preservation on replay. The integrated live Stage E runner entry point remains blocked by the
fixed runtime boundary documented in `stage-e-offline-rehearsal.md`; a reviewed entry point
and clean exact-target preflight are still required before authorization.

---

## 9. Invalid observations cannot enter accuracy denominators (task 9)

Explicit mocked checks confirm the exclusion rules. Passing tests include:

- `test_unreviewed_negative_never_enters_binary_denominators`
- `test_negative_requires_review_and_cannot_be_inferred_from_an_empty_class`
- `test_both_roles_reject_unsuccessful_or_non_title_responses`
- `test_conflicting_territories_and_unestablished_context`
- `test_incomplete_reviewed_scope_cannot_be_self_confirmed`
- `test_free_controls_require_separate_full_feature_classes`
- `test_boundaries_apply_to_expanded_bounds_and_originals`
- `test_fresh_read_never_refreshes_old_underlying_time`
- `test_price_only_for_two_eligible_current_transactional_positives`
- `test_one_positive_is_conditional_when_other_candidates_or_manifest_are_missing`
- `test_abstention_does_not_admit_unresolved_read_or_wrong_scope`

All are in `backend/availability/tests/test_issue97_stage_e_adjudication.py` (27 test methods)
and ran within the 64-test mocked suite above. The supported semantics are: empty class,
login wall, shell, 404, redirect, unknown schema, rate limit, missing control, stale evidence,
wrong typed title/provider/service and out-of-window or negative-uncertainty observations
yield `unknown`, `needs_recheck`, abstention or `UNDEFINED` — never TP/FP/FN/TN. A zero
denominator yields `UNDEFINED`, and full-panel recall stays undefined while any eligible
positive abstains or a frozen row is missing.

---

## 10. Reduced-panel recommendation (task 10)

Without expanding scope, Authorization Batch A should be **reduced to zero live sends** until
at least one source decision is substantiated. The defensible ordering is:

1. **No source authorized now.** Keep E01–E09 `pending_authorization`; do not substitute a
   historical capture for a fresh observation.
2. If Netflix N1–N11 are later substantiated first, the smallest reviewable panel is the
   historical-anchor subset **E01 (Netflix/Inception subscription)** for Batch A, with all
   Prime (E02–E05, E08) and Rakuten (E06–E07, E09) rows explicitly omitted and reasons
   recorded. This is a reduction, not a scope expansion.
3. Any omission must be listed before owner signing: source name, rows omitted and reason
   (`BLOCKED_ON_ACCESS`), plus the retained evidence reference for the decision.
4. Batch B (E10–E15) remains a separate future decision; no series or season work is added.
5. JustWatch attempts follow the same rule: no current receipt/quota evidence, so zero
   attempts are authorized here; reuse of a separately authorized eligible capture must keep
   its original clock and ledger entry.
6. Entitlement, playback and paid classes remain outside scope.

---

## 11. Gate 97-E assignment

**`BLOCKED_ON_ACCESS`**

Rationale: the protocol, frozen matrix, custody model, scoring rules and mocked rehearsal are
ready and verified; the blocking condition is source-specific access evidence, which is
absent for all three sources (0/3 decisions complete). This is not a specification change
(`REQUEST_CHANGES`), not a lack of specific evidence already in custody (`BLOCKED_ON_SPECIFIC_EVIDENCE`),
not ready for exact approval, and the work product here is complete as a readiness report —
but the gate itself cannot be `COMPLETE` while live access is blocked. Gate 97-E requires
separate exact source approvals; none exist.

---

## 12. Validation commands and results

Run from the repository root at the report branch base. Results are from this session.

| # | Command | Result |
| --- | --- | --- |
| 1 | `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` | `MODEL_IDENTITY_OK opencode/hy4-flash-free`; `cost: 0` |
| 2 | `python3 -BE scripts/audit_issue97_stage_e.py` | exit 0; 15 candidates (A=9, B=6), 13 observations, 7 Stage C pairs, 2 controls, 8 documents; matrix SHA `ac1d37ec…a4891d`; 0 fresh pairs, UNDEFINED |
| 3 | `python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e` | `Ran 5 tests — OK` |
| 4 | `sh -n scripts/rehearse_issue97_stage_e.sh` | exit 0 |
| 5 | `git diff --check 6af4cd8421ec9c85e4fff724fded60d5cce86c85 HEAD` | exit 0 |
| 6 | `STAGE_E_TEST_IMAGE=wtdw-issue97-offline:latest sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal availability.tests.test_issue97_stage_e_adjudication availability.tests.test_issue97_stage_e_app_replay tracker.tests.test_issue97_availability_contract` | `Ran 64 tests in 8.717s — OK` (mocked transport, disposable PostgreSQL 16) |

Local results are separate from hosted CI; the hosted link is recorded in §14 after push.

---

## 13. Prohibitions observed

No live source collection; no login automation; no provider/TMDb/first-party/playback request;
no HMAC grant; no quota or permission change; no raw evidence in Git; no production write or
migration; no issue closure; no cleanup. Issues #97 and #103 remain open/on hold as applicable;
the PR is a draft and is not merged. No raw restricted payload, credential, signed URL or
private path inventory appears in this report.

---

## 14. Publication and verification record

Filled in after publication: exact PR head, hosted CI link and `gh api`/`gh pr view`
verification are recorded in the issue #97 handoff and in the PR body. This section is
completed on the final branch commit so the report does not contain a self-referential head
claim.

**Review request:** ChatGPT Web review is explicitly requested for this Stage E
evidence-readiness report.
