# Issue 97 acceptance checklist

**READY_FOR_CHATGPT_REVIEW** applies to the draft. **BLOCKED_ON_SPECIFIC_ACCEPTANCE_GATE** applies to production availability accuracy. The dependency gate for #103 remains on hold. [Issue #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97) · [Issue #103](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/103)

## Original acceptance criteria

| Criterion | Rating | Evidence and remaining condition |
|---|---|---|
| Reproducible command, environment, baseline, sanitized evidence | PASS | Fixed code and input revisions, disposable database provenance, read-only execution, full file digests, exact comparison and test commands. The owner sealed a 109-member archive. Offline restore, both comparator replays, frozen title-page checks and a separately guarded clone run passed. Raw-byte replay requires authorized access to the owner-local archive. [Report](issue97-final-verification-report.md) · [Manifest](issue97-evidence-manifest.json) |
| Distinguish catalogue presence, advertised availability, entitlement and playback | PASS | The title/market/class rows distinguish catalogue-only and trailer-only pages, title-specific offers, untested personal access and untested playback. [Accepted](issue97-accepted-observations.csv) · [Rejected](issue97-rejected-observations.csv) |
| Publish accuracy/unknown metrics, limitations and priority defects | PARTIAL | The 34-case clone measurement, 13 known-positive retrieval misses, 12 needs-recheck cases and wrong-edition history link are reproducible. The independent same-window source-fresh denominator is zero. Production precision, recall, entitlement and playback accuracy remain NOT PROVEN. [Metrics](issue97-reconciliation-summary.json) |
| No unapproved outbound probes or metered API usage | PARTIAL | All 26 requests were anonymous public documents within the 40-request budget. Paid and metered calls were zero. One preselected Peacock help-page GET occurred after the title returned a regional refusal. Both responses redirected to unavailable, neither redirect was followed, and the title was never retried. Counting that extra help request conservatively prevents certifying strict stop-on-denial execution as a full pass. [Request ledger](issue97-evidence-manifest.json) |
| Link follow-up bugs and explicitly approve or block readiness | PASS | Exact reproduction for the BBC mapping and the corrected audit-report defect are in the report. Owner-authorized follow-up is documented against existing issues. Production readiness is blocked on independent contemporaneous canonical evidence and broader source coverage. [BBC defect #190](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/190) · [Identity integrity #112](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/112) · [Accuracy #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97) |

## Original scope

| Work item | Rating | Measured extent |
|---|---|---|
| Reconcile Stage 2B against master, current source state and dated countries | PARTIAL | All 30 historical rows have dated dispositions. Current code was run against an authorized November 22 clone. Source flags in the serving system have no current observation. |
| Representative read-only cohort | PARTIAL | 34 cases, 16 titles, six countries, movies, series, subscription, free, rent, buy, add-on, stale/expiry and mistaken identities. Only NL produced valid first-party title/class positives. GB and CA free controls each failed a different evidence gate. |
| Measure false positives, false negatives, unknown, needs-recheck and access discrepancies | PARTIAL | Display retrieval and the independently advertised positive sample are measured. No official feature-level negatives or eligible fresh canonical pairs. No account or playback evidence. |
| Source-specific approval, rate and budget | PARTIAL | One TLS document at a time, three seconds or more between reads within each batch, 26/40 GETs, including two manually followed marketing redirects. Zero login, playback, provider API, paid or JustWatch requests. One additional Peacock help URL as disclosed above. |
| Turn concrete failures into scoped follow-ups | PASS | Offer 3504 and its BBC descendants have a dedicated follow-up. The audit-report defect has a regression. [BBC defect #190](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/190) |

## Execution checklist

| Requested step | Result |
|---|---|
| Record commit, modules, existing behavior and already merged work | PASS. Master `752dc23fd5beb2699612c2a2ee29a1fee0f4824e`. PRs #186–188 and their contributions are identified in the report. |
| Produce task-specific evidence/minimal implementation in isolation | PASS. Eight priority pairs, 16 original capture files, 15 TMDb raw files, 166 historical pilot rows and 34 real clone cases. No synthetic Movie inserts or schema changes. |
| Run automated tests and relevant checks | PASS locally. Focused backend 29, full backend 3,731, standalone comparator 18, boundary and first-party regressions 8. All exited 0. Evidence verifier, pinned comparator, syntax and whitespace checks passed. Hosted CI is NOT PROVEN. |
| Publish paths, commands, results, risks and dependent-issue impact | PASS. All nine named artifacts are in this folder. Follow-ups for data integrity, notification and catalogue consumers are linked below. |
| One focused draft, readiness stated, no merge/close/deploy | PASS for draft delivery. Issue #97 remains open. Production accuracy approval is BLOCKED. |

No observation licenses an entitlement claim for #103 or a verified-availability claim for downstream work. [#147 notifications](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/147) · [#119 homepage](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/119) · [#161 catalogue](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/161)

## Follow-up contract pass — 2026-11-23 (base `e3dd825`)

Verdict vocabulary: `PASS | PARTIAL | FAIL | BLOCKED | NOT PROVEN`. This section is
deliberately separate from the preserved historical checklist above.

| # | Deliverable | Status | Measured evidence / remaining condition |
|---|---|---|---|
| 1 | Stale/unverified display contract | PARTIAL → implementation ready for review | Explicit `Unverified` badge now rendered by the shared `OfferRow` for every `stale`/`unknown` positive; backend qualified status label also says `Unverified` for stale offers. Fresh availability, country/class, last-seen time, entitlement separation and confirmed-unavailable hiding were already present at `e3dd825`. No schema change. |
| 2 | Regression tests | PASS | 12 named contract cases in `backend/tracker/tests/test_issue97_availability_contract.py`; 12/12 pass with 15 existing stale-semantics tests (27 total). Frontend OfferRow + LastKnownOffers 27/27 pass. |
| 3 | Manual verification sheet | PASS (artifact) | `docs/audits/stage2b/manual-verification-sheet.md`: eight NL typed pairs, direct official URLs, classification rules, explicit "no Netflix purchase required". |
| 4 | Automated evidence intake | PASS | `scripts/ingest_owner_observations.py` + 6 offline tests: computes or verifies SHA-256, rejects naive UTC, bad identities/classes, duplicates, manifest-blocked titles, unsanitized URLs and same-source echoes. |
| 5 | Fresh application observations | BLOCKED | Measured 0/11,003 rows within 24h in the only authorized disposable clone; newest cohort observation 2026-10-24. [Report](issue97-fresh-observation-report.md). |
| 6 | Accepted/rejected evidence manifest | PASS (existing) | 109-member sealed archive, 16/16 original files and all decision inputs verified in the prior pass; unchanged here. |
| 7 | Independent reconciliation | PARTIAL | Historical Watchmode and first-party comparisons preserved. 0 fresh independent comparable pairs, so no new accuracy classification. |
| 8 | Accuracy metrics with valid denominators | BLOCKED | FP/FN/TP/TN/precision/recall remain NOT PROVEN; denominators are zero. Display-retrieval misses (13/13) are not accuracy rates. |
| 9 | Source/provider coverage | PARTIAL | 13 advertised NL pairs across 3 provider sites, no entitlement/playback; the six-country panel remains display evidence only. |
| 10 | Focused defect PRs | PARTIAL | Contract implementation prepared as one focused draft; #190 remains a separately traceable defect and was not touched. |

**Terminal state: `BLOCKED_ON_SPECIFIC_ACCEPTANCE_GATE`.** The code and tests are
ready for ChatGPT Web review, but #97 must remain **OPEN** and #103 **ON HOLD**:
there is no fresh, independent, permissioned observation pair and no defensible
negative control. Passing tests and a more honest stale display do not close the
real-world accuracy gap.
