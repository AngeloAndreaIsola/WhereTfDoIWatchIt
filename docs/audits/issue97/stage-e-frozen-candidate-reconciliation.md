# Stage E frozen candidate reconciliation checklist

[Frozen matrix](stage-e-candidate-matrix.csv) SHA-256 `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d`, independently checked at master `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d`. This inventory covers 15 rows, seven NL typed titles and eight first-party documents. Every row's fresh status remains `pending_authorization`. [Batch A plan](stage-e-batch-a-plan.md) · [Adjudicator replay contract](stage-e-adjudication-replay.md)

| Row | Batch and document | NL typed title | Canonical service and class | Historical basis |
| --- | --- | --- | --- | --- |
| E01 | A · E-C1 | Inception movie 27205 (2010) | `netflix / subscription` | Stage C pair, ineligible |
| E02 | A · E-C2 | Inception movie 27205 (2010) | `prime / subscription` | Stage C pair, ineligible |
| E03 | A · E-C2 | Inception movie 27205 (2010) | `prime / rent` | Stage C pair, ineligible |
| E04 | A · E-C2 | Inception movie 27205 (2010) | `prime / buy` | Stage C pair, ineligible |
| E05 | A · E-C2 | Inception movie 27205 (2010) | `max_amazon / addon`, parent `prime` | Stage C pair, ineligible |
| E06 | A · E-C3 | Dune movie 438631 (2021) | `rakuten_tv / rent` | Stage C pair, ineligible |
| E07 | A · E-C3 | Dune movie 438631 (2021) | `rakuten_tv / buy` | Stage C pair, ineligible |
| E08 | A · E-C2 | Inception movie 27205 (2010) | `prime / free` | Shared document only, outcome unassigned |
| E09 | A · E-C3 | Dune movie 438631 (2021) | `rakuten_tv / free_with_ads` | Shared document only, outcome unassigned |
| E10 | B · E-C4 | Breaking Bad series 1396 (2008) | `netflix / subscription` | Historical first-party only |
| E11 | B · E-C5 | Dark series 70523 (2017) | `netflix / subscription` | Historical first-party only |
| E12 | B · E-C6 | Stranger Things series 66732 (2016) | `netflix / subscription` | Historical first-party only |
| E13 | B · E-C7 | The Platform movie 619264 (2019) | `netflix / subscription` | Historical first-party only |
| E14 | B · E-C8 | The Holdovers movie 840430 (2023) | `rakuten_tv / rent` | Historical first-party only |
| E15 | B · E-C8 | The Holdovers movie 840430 (2023) | `rakuten_tv / buy` | Historical first-party only |

## Checked offline

- [x] Recompute the matrix digest and validate unique candidate IDs and typed `(media_type, tmdb_id, market, provider_slug, offer_class)` scopes. Confirm nine Batch A rows across three documents and six Batch B rows across five documents.
- [x] Verify the 13 accepted historical pointers, eight historical capture digests, and typed identity joins. Keep the seven Stage C pairs ineligible and unadjudicated. First-party evidence was recorded November 23, 2026 UTC. The corresponding index observations were recorded November 24 UTC.
- [x] Confirm the two controls share only the reviewed title documents, E08 with E-C2 and E09 with E-C3. Verify the HBO Max add-on retains parent `prime` and transactional classes remain distinct.
- [x] Supply all 15 expected frozen scopes independently of fixture decisions. Exercise each scope and exclude original-time substitutions. Keep missing rows visible and full-panel rates undefined when coverage or denominators cannot be established. [Offline results](stage-e-batch-a-offline-results.md)

## Check after any authorized Batch A run

- [ ] Verify the signed manifest binds the matrix digest, three source decisions or explicitly omitted scopes, both title slots, private allowlist, exact revisions and disposable database. Record recorded/effective NL market evidence and confirm the typed title, edition and native-to-typed binding for each contributor.
- [ ] Reconcile one attempt ledger and artifact receipt per attempted scope with clocks, originals, digest, byte count and custody. Attach the Inception checkpoint before accounting for Dune. Record any stopped scope, spent reservation with unknown transmission, missing document, or authorized reuse with unchanged lineage.
- [ ] Review one disposition per frozen Batch A candidate while keeping all nine in the report, including omitted rows and reasons. Track E10–E15 as outside this batch. Keep title-level series scope and the other eight cohort titles outside the panel under their distinct dispositions. [Panel coverage](stage-e-evidence-protocol.md#candidate-selection)
- [ ] Seal first-party and index extraction inputs with reviewed bindings for title, document, canonical service/class, channel parent, market and time. Record `unknown`, `needs_recheck` or each exclusion explicitly. Route negative assertions, disagreements and price conflicts to second review.
- [ ] Run the app and JustWatch comparisons by named index and the display and projection checks by named surface. Report planned, attempted, completed, eligible and binary-scored counts, first-party positives and negatives, unscored rows, prices and denominators. Verify disclosure by source and confirm the custodian's deletion record.

Baseline for this preparation remains 56 historical JustWatch source-reported offers, seven historical candidate matches, **zero** eligible independent comparisons, **zero** confirmed negatives and `UNDEFINED` accuracy. Entitlement and playback remain untested. [Historical reconciliation](../stage2b/issue97-independent-verification-report.md)
