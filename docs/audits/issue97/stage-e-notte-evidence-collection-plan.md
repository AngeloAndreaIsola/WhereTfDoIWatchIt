# Issue #97 Stage E fresh independent evidence preparation with Notte — collection plan

**Status: DRAFT PLAN / NOT AN EXECUTION AUTHORIZATION. Gate `97-E-NOTTE` = `BLOCKED_ON_SPECIFIC_EVIDENCE`.**

Exact base used for this draft: `3e439e6a7cab7a72834e710352da7cbf0f0b739f` (current `origin/master` re-fetched at dispatch time, merge of #238). Isolated forward-only documentation branch: `docs/97-stage-e-notte-evidence-plan`. This document performs **zero** live provider capture and grants **zero** sends. Owner authorization of Notte-assisted *investigation and evidence preparation* does **not** include a source-specific collection authorization for Netflix, Prime Video or Rakuten TV; acquiring one is a prerequisite to any execution. Issue #97 stays open; #103 stays on hold.

## 1. Route, model identity, effort and cost

A fresh in-session probe was run with native OpenCode on the exact base worktree:

```sh
opencode --version
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
```

| Field | Observed value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual model in reply | `opencode/hy4-flash-free` — `MODEL_IDENTITY_OK opencode/hy4-flash-free` |
| Provider | `opencode` (native OpenCode CLI `1.19.6`) |
| Effort | **UNPINNED** — MEDIUM/HIGH requested by owner; OpenCode exposes no reliable effort flag in this session, so no override was set |
| Cost | Probe JSON `step_finish.cost: 0`; session total **$0.00** |
| Deviation | Effort could not be reliably pinned to MEDIUM/HIGH. No silent substitution, no Sol/Astra, no paid escalation. |

## 2. Frozen inputs and exact documents required for comparison

Frozen Stage E matrix and protocol digests, verified on the exact base:

| Document | SHA-256 | Role |
| --- | --- | --- |
| `docs/audits/issue97/stage-e-candidate-matrix.csv` | `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d` | Frozen 15-row panel (A=9, B=6) |
| `docs/audits/issue97/stage-e-evidence-protocol.md` | `aaf2baf2f604b0110fb3f750a81f3db33743f929a3df05e4bffb9f5046910a93` | Temporal, identity, market, service, class and denominator rules |
| `docs/audits/issue97/stage-e-source-authorization-readiness.md` | `0eae1ac9b3ef5b0a8519ce96414cb905a7b180246196e562508e9550277fbd71` | N1–N11 / P1–P11 / R1–R11 / X1–X9 checklist |
| `docs/audits/issue97/stage-e-authorization-request.md` | `10640ce4abbc5e8bd5714510d4924b58cac45cdae21f22d73cf2464bc5c0d045` | Ceilings, approval fields, stop conditions |
| `docs/audits/issue97/stage-e-source-review.md` | `d4aad68a32d9bf3c9f54dc0168df2fea23b133f3922ffb65ba390ccc2b33f452` | Three document aliases and missing source decisions |
| `docs/audits/issue97/stage-e-adjudication-replay.md` | `05f027fe3c947a29555b968d6d7f6550bffa2eccc49b5a47176f036b80bfb2db` | Invalid-observation exclusion design |
| `docs/audits/issue97/stage-e-authorization-readiness-package-20261127.md` | `d20c06b399fc3172b6a5a80ff4220506ac3857b52d728bc921629449fe825a66` | Integrated approval package |

Batch A independent comparison needs exactly three native first-party title documents, each paired with its own index observation:

| Capture scope | Service · typed title | Frozen rows | Documents needed on each side |
| --- | --- | --- | --- |
| `E-C1` | Netflix · Inception, movie 27205 (2010), NL | E01 subscription | First-party title document (native video ID, type, title, anonymous NL context, title-specific affirmative availability plus subscription action/plan signal); separate JustWatch source-claim index row; separate app current-advertisement index row |
| `E-C2` | Prime Video · Inception, movie 27205 (2010), NL | E02 subscription, E03 rent, E04 buy, E05 `max_amazon` addon, E08 free control | First-party detail document (detail-page ID and film GTI, recorded and effective NL territory, base Prime benefit/action, individual rent and buy actions, distinct HBO Max channel benefit); index rows per class |
| `E-C3` | Rakuten TV · Dune, movie 438631 (2021, Villeneuve), NL | E06 rent, E07 buy, E09 `free_with_ads` control | First-party NL movie document (movie card/native ID, title, director, year, canonical NL storefront, title-level rent/buy actions, prices); index rows per class |

The two indices are frozen separately: **JustWatch source claims** and **application current advertisement claims**. A comparison between those two indices measures projection fidelity only; an app row derived from the comparator payload or an untraceable source is `SAME_SOURCE_ECHO` and excluded from the independent score. JustWatch is never independent confirmation of JustWatch.

## 3. Current access conditions (retained records only, no live probes)

Verified exclusively from already-authorized retained records at the exact base and from permitted local tool metadata. **No Notte provider call, no HTTPS GET and no browser navigation was made.**

| Source | Retained access condition | Exact unresolved decision |
| --- | --- | --- |
| Netflix | Historical document alias `E-C1` exists; prior Netflix searches hit a login wall and a market redirect. Historical access supports feasibility only; current reachability untested. | **0/3 source decisions substantiated.** N1 anonymous GET, N2 legitimate NL context, N3 typed binding, N4 extraction conditions, N5 retention/deletion, N6 publication fields, N7 private allowlist, N8 allowance/pacing/cooldown, N9 UTC slot and clock, N10 custody, N11 separate owner approval — all pending. |
| Prime Video | Historical `E-C2` alias exists with embedded film/price/channel fields. Recorded and effective territories must agree on NL; missing actions yield unknown. | **0/3 substantiated.** P1–P11 pending. P4 additionally requires validating base Prime benefit, rent/buy and the separate HBO Max channel benefit on the same GTI; E08 needs an explicit full-film zero-payment offer with no membership or ads. |
| Rakuten TV | Historical `E-C3` alias exists; a historical client-shell observation was rejected. | **0/3 substantiated.** R1–R11 pending. R3 must bind the 2021 Villeneuve film; E09 needs an explicit full-film zero-payment ad-funded offer without subscription. |

Cross-source gates X1–X9 are all pending: no signed allowlist digest, no signed scoped manifest digest, no verified integrated preflight at pinned revisions, no named owner/operator/reviewer/custodian, no UTC slot or clock receipt, no verified serial accounting, no binding of 3 GET + 2 JustWatch ceilings, and no current JustWatch runtime/quota receipt. The fixed runtime rejects Stage E first-party scopes and batch caps in rehearsal.

**Notte metadata scope actually available in this session:** no Notte capture tool, skill or credential is exposed through the permitted interface (`orca tools list`, local skill directories and environment inspection returned no Notte entry). Therefore zero Notte requests could be made even if authorized, and no provider capture was attempted.

## 4. Executable collection scope, per source (proposed, NOT authorized)

The following is a specification to be signed, not permission to run. The lower of reviewed source limit and stated ceiling always applies. No login automation, CAPTCHA solving, region/access circumvention, private endpoints, checkout or playback is included.

| Field | Netflix `E-C1` | Prime Video `E-C2` | Rakuten TV `E-C3` |
| --- | --- | --- | --- |
| Permitted method | One anonymous HTTPS GET of the exact title-document URL pinned in the private allowlist; redirects, retries, JS, subresources, cookies and credentials disabled | Same, for the exact detail document | Same, for the exact NL movie document |
| Permitted endpoints | Exact private origin/path + native video ID only; no search, help or diagnostic pages | Exact private origin/path + detail-page ID/GTI only | Exact private origin/path + native movie ID only |
| Authentication boundary | Anonymous; no account, login, subscription entitlement or playback check | Anonymous; no account, channel trial, checkout or entitlement | Anonymous; no account or transaction |
| Regional constraint | Verified anonymous NL context; no location spoofing | Recorded and effective territory must agree on `NL` | Canonical NL storefront plus independent NL evidence on the document |
| UTC capture window | Inception absolute half-open 30-minute slot (historical proposal: 2026-12-02 `[14:00,14:30)` UTC), subject to fresh owner approval | Same Inception slot as `E-C1` | Dune absolute half-open 30-minute slot (historical proposal: 2026-12-02 `[15:00,15:30)` UTC), subject to fresh owner approval |
| Request limit | 1 first-send, 0 retries, 0 redirects | 1 first-send, 0 retries, 0 redirects | 1 first-send, 0 retries, 0 redirects |
| Clock/span | Host sync check, offset and uncertainty ≤5 s; expanded bounds inside slot; combined span ≤10 min | Same | Same |
| Spacing/custody | ≥3 s between first-party sends (or stricter reviewed limit), one in-flight attempt across tracks; raw bytes sealed in restricted owner-controlled custody outside Git | Same | Same |
| Retention | Deletion deadline and custodian must be substantiated by N5/N10 before any capture | P5/P10 | R5/R10 |
| Stop conditions | Stop on expired/changed approval, accounting or identity mismatch, clock failure, budget exhaustion, transport ambiguity, unexpected shape, redirect, challenge, rate limit or denial; charge the attempt, honor `Retry-After`, report unknown/`needs_recheck` | Same | Same |

Batch A ceiling remains **3 first-party GETs + 2 bounded JustWatch `GetSearchTitles` attempts**, zero retries, redirects, fallback calls, paid requests, Watchmode/Streaming Availability calls, seasons or episodes. These are planning ceilings, not permission. Batch B is a separate decision.

## 5. Temporal eligibility and denominator reconciliation

- Approve one absolute half-open 30-minute UTC slot per typed title before capture. A comparison qualifies only when first-party capture and index observation fall in that slot and the **uncertainty-expanded combined span is ≤10 minutes**.
- Record UTC start/end and monotonic elapsed time for every contributing request or local read; record clock synchronization, offset and ≤5 s uncertainty. Unknown original observation time, unordered timestamps, unverifiable clock or failed response leaves the row ineligible.
- Record response age separately from request time; reject known-stale documents and assertions whose origin predates the slot. A fresh app response carrying historical offers stays `last_known`, not a fresh observation.
- `needs_recheck` on a stated start/end/price/promotion change or equally scoped conflict; never widen the window or substitute a historical row.
- One row per frozen `(media_type, tmdb_id, market, provider_slug, offer_class)`. Score only rows passing identity, market, source-independence, scope and time gates with both verdicts binary.
- Denominators: agreement/conflict over `TP+TN` / `FP+FN`; accuracy `(TP+TN)/N` with `N=TP+FP+FN+TN`; precision/recall/FPR/FNR on their stated eligible subsets only. Report `UNDEFINED` with numerator/denominator whenever N or the subset is zero.
- Entitlement and playback remain `untested` for every Stage E row. Advertised availability is never equated with account entitlement or successful playback.
- Current baseline on the exact base: **0 fresh adjudicated pairs, 0 confirmed negatives, all accuracy metrics `UNDEFINED`**; seven historical Stage C pairs are ineligible (November 23 provider vs November 24 index observations).

## 6. Mocked capture/scoring rehearsal — verification results

All checks below ran locally on the exact base with mocked/static inputs; none made a live request.

| Command | Result |
| --- | --- |
| `python3 -BE scripts/audit_issue97_stage_e.py` | `CSV PASS` 15 candidates (A=9, B=6); `Provenance PASS` 8 digests, 7 ineligible/unadjudicated; `Scope PASS` 8 documents (A=3, B=5); fresh pairs 0; accuracy `UNDEFINED`. Exit 0 |
| `python3 -BE scripts/audit_issue97_stage_e_adjudication.py` | Historical panel: 7 outside window, 6 without paired index, 2 unassigned controls; **2030 synthetic smoke: all 15 scopes exercised, all 15 original-time substitutions excluded**; baseline 56 source-reported offers, 0 eligible comparisons, 0 confirmed negatives; accuracy/price `UNDEFINED`. Exit 0 |
| `python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e` | 5 tests, `OK` (frozen controls/channel share reviewed documents; banned cross-title document borrowing; changed capture identity) |
| `PYTHONPATH=backend python3 -B -m unittest availability.tests.test_issue97_stage_e_adjudication` | 27 tests, `OK` |

Conclusion: the adjudication rehearsal confirmed a 2030 synthetic original-time substitution is rejected for all 15 scopes, so invalid/out-of-window observations cannot enter binary accuracy denominators; historical positives with no paired index and the two unassigned controls produce zero scored cells.

## 7. Notte request accounting (every call actually made)

| Field | Value |
| --- | --- |
| Notte function/version | **None exposed or invoked** — no Notte tool/skill/credential available in permitted scope |
| Title / country | Not applicable (no call) |
| Operation | Metadata-only capability inspection: `orca tools list`, local skill-directory listing, environment variable-name inspection |
| Request count | **0** |
| Retries | 0 |
| Runtime duration | Local metadata inspection only; no provider request |
| Cost | $0.00 (no upstream call) |
| Response status | `orca tools list` returned no Notte entry; skill directories and env names returned no Notte entry |
| Evidence digest | Not applicable — no raw evidence produced; identity-probe output retained outside Git at `.scratch` temp path |
| Deviation | None. No exploratory provider calls outside reviewed scopes. |

No JustWatch, Netflix, Prime Video, Rakuten TV, TMDb or playback request was made.

## 8. Per-source decision and gate

| Source | Decision | Exact unresolved owner input |
| --- | --- | --- |
| Netflix | **Reduced panel candidate — no executable scope.** `E01` cannot be collected until N1–N11 are substantiated and N11 signed. | Dated terms/permission evidence for an anonymous NL GET and extraction; retention/deletion deadline; publication field list; private allowlist digest; allowance/pacing; clock and custody receipts; separate exact-scope signature |
| Prime Video | **Reduced panel candidate — no executable scope.** `E02`–`E05`, `E08` cannot be collected until P1–P11 are substantiated and P11 signed. | Same for `E-C2`, plus validated base Prime vs channel benefit on the same GTI and explicit E08 zero-payment offer terms |
| Rakuten TV | **Reduced panel candidate — no executable scope.** `E06`, `E07`, `E09` cannot be collected until R1–R11 are substantiated and R11 signed. | Same for `E-C3`, plus 2021 Villeneuve edition binding and explicit E09 ad-funded zero-payment terms |

If any source remains unsubstantiated at signing, that source's rows must be explicitly omitted from a reduced request before signature; no silent substitution or historical reuse is allowed.

**Gate `97-E-NOTTE` = `BLOCKED_ON_SPECIFIC_EVIDENCE`.** The specific missing evidence is: 0/3 source-specific access/extraction/retention/publication decisions (N1–N11, P1–P11, R1–R11), all X1–X9 cross-source prerequisites, no exact source-specific collection authorization in this package, and no exposed Notte collection capability. This is not a claim that the sources are technically unreachable; it is a refusal to execute without substantiated scope. A reduced panel is the defined fallback, never an invented panel.

## 9. Validation commands and results (exact base)

```sh
git rev-parse HEAD
# 3e439e6a7cab7a72834e710352da7cbf0f0b739f

shasum -a 256 docs/audits/issue97/stage-e-candidate-matrix.csv
# ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d

python3 -BE scripts/audit_issue97_stage_e.py
python3 -BE scripts/audit_issue97_stage_e_adjudication.py
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e
PYTHONPATH=backend python3 -B -m unittest availability.tests.test_issue97_stage_e_adjudication
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'
```

All listed commands exited 0; probe reported `cost: 0`.

**Draft PR and hosted CI (verified with `gh api` / `gh pr view`):** draft PR [#239](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/239), base `3e439e6a7cab7a72834e710352da7cbf0f0b739f`, reviewed head `495bcb768811bf7f7906a5e1ca871f076a4cadda`, state `open`, `draft: true`. Hosted CI for the reviewed head, [run 46071757828](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46071757828), completed with conclusion `success`: Backend Tests `success`, Frontend Lint/Build/Test `success`, Browser Extension Build/Typecheck/Tests `success` (3/3 jobs). PR metadata remains authoritative for the current head; this paragraph records the reviewed head only and deliberately cites no superseded head or run. The PR is not merged; the ChatGPT Web review request is posted on #97.

## 10. Forbidden operations statement

**FORBIDDEN — none performed and none authorized by this document:** live provider capture; Notte capture calls; login automation; CAPTCHA or authentication-barrier bypass; region/access circumvention; JustWatch self-confirmation; HMAC grant generation; quota or permission changes; runtime permission/configuration changes; raw evidence in Git; production or retained database writes; deployments; branch cleanup; PR merges; issue closure. Only sanitized digests, counts and provenance are in Git. No credentials or raw restricted evidence are exposed. #97 remains open; #103 remains on hold.
