# Stage E first-party source review

**BLOCKED for Batch A owner authorization.** At master `0b6d6325dab00d90baa096a9e529c26c75b41bb4`, the frozen records identify a historical native title document for all 15 candidates. The reviewed materials supply **0 of 3** source-specific decisions covering fresh anonymous collection, retention and publication. The private Batch A allowlist and clock, custody and integrated preflight receipts were unavailable for verification. Fresh capture was outside this review. [Protocol](stage-e-evidence-protocol.md) · [Authorization request](stage-e-authorization-request.md)

The baseline is 56 JustWatch source-reported offers, seven historical first-party candidate matches, zero temporally eligible independent comparisons and zero confirmed negatives. Accuracy is **UNDEFINED**. The seven matches compare November 23 provider evidence with November 24 index observations. A JustWatch-derived app result is a same-source echo. Entitlement and playback are untested. [Historical reconciliation](../stage2b/issue97-independent-verification-report.md)

## Document inventory

Each alias below resolves through the frozen candidate and accepted-observation records to one historical HTTPS title document. Rows sharing a document also share its native identifier, digest and capture time. E08 and E09 inherit only their document assignment. Their class outcomes are open. The matrix SHA-256 is `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d`. [Candidate matrix](stage-e-candidate-matrix.csv) · [Offline verifier](../../../scripts/audit_issue97_stage_e.py)

| Batch | Document | Service and NL typed title | Candidate rows |
| --- | --- | --- | --- |
| A | E-C1 | Netflix · Inception · movie 27205 (2010) | E01 subscription |
| A | E-C2 | Prime Video · Inception · movie 27205 (2010) | E02 base subscription, E03 rent, E04 buy, E05 HBO Max add-on via Prime, E08 free control |
| A | E-C3 | Rakuten TV · Dune · movie 438631 (2021) | E06 rent, E07 buy, E09 free-with-ads control |
| B | E-C4 | Netflix · Breaking Bad · series 1396 (2008) | E10 subscription |
| B | E-C5 | Netflix · Dark · series 70523 (2017) | E11 subscription |
| B | E-C6 | Netflix · Stranger Things · series 66732 (2016) | E12 subscription |
| B | E-C7 | Netflix · The Platform · movie 619264 (2019) | E13 subscription |
| B | E-C8 | Rakuten TV · The Holdovers · movie 840430 (2023) | E14 rent, E15 buy |

## Evidence a reviewer could accept

| Source | Required signal on the one approved title document | Unsupported or inconclusive |
| --- | --- | --- |
| Netflix | Bind the top-level video ID, movie/show type and canonical title or reviewed alias to the typed work. Establish anonymous request country NL. Require the same title's affirmative availability plus a reviewed title-specific subscription action or plan signal. | A general sign-up action and a search login form leave the class unknown. A paid plan with ads still counts as subscription. For Batch B series, use the independently reviewed native ID and first-air year binding. The recorded display year can refer to the latest season. |
| Prime Video | Bind the detail page ID and film GTI to Inception (2010). Require recorded and effective territories to agree on NL. Read only that GTI's base Prime benefit and action, its individual rental and purchase actions, and its separately identified HBO Max channel benefit under parent Prime. E08 needs an explicit full-film, zero-payment offer without membership or ads. | A generic trial, channel-only subscription or unrelated edition cannot confirm base Prime. A missing action leaves its class unknown. Prices need matching edition, quality, promotion, access conditions and EUR cents on both observations. |
| Rakuten TV | Bind the movie card, native ID, title, director and year to the edition. For Dune use the 2021 Villeneuve film. Require the canonical NL storefront plus independent NL context on that document, such as the historical Netherlands-specific chart. Read rental and purchase actions separately. E09 needs an explicit full-film, zero-payment ad-funded offer without a subscription. | Language, EUR pricing or a route slug alone cannot establish market or identity. A trailer, client shell or absent purchase button leaves the class unknown. Record comparable rent and buy prices separately. |

Use the reviewed canonical year or a separately reviewed native-to-typed binding when a field is absent. Record direct or inferred identity. Territory conflicts, edition ambiguity and changed signal semantics preclude admission. Read embedded data offline. Seal the first-party extraction before disclosing index results to its reviewer. A second reviewer must check any negative, disputed mapping or conflicting evidence. [Historical signal interpretation](../stage2b/issue97-final-verification-report.md) · [Typed identity decisions](../stage2b/issue97-identity-decisions.md)

A negative needs an explicit, current assertion with reviewed semantics covering the exact title, service, NL market, class and relevant variants. Generic search results, login walls, empty pages, missing fields, shells and HTTP 404 responses yield **unknown**. Historical rejections include a Netflix search login wall, a Netflix market redirect and a Rakuten client shell. Current reachability is untested. For a redirect, denial, challenge, rate limit or unexpected response shape, charge the attempt, stop the batch and reconcile. Preserve a valid document's inconclusive class as unknown. [Rejected observations](../stage2b/issue97-rejected-observations.csv) · [Stop conditions](stage-e-authorization-request.md#safeguards-and-stop-conditions)

## Missing access and custody decisions

| Service | Exact decision still required from the source reviewer |
| --- | --- |
| Netflix | Identify dated, applicable evidence covering an automated anonymous NL GET of E-C1, extraction of title and plan fields, storage of the returned document and disclosure of approved derived results. Specify the permitted limit and retention period. |
| Prime Video | Establish those conditions for E-C2, including storage and use of embedded film, store-price and channel-benefit data from the single detail response. Identify any restrictions on sharing offer or price decisions. |
| Rakuten TV | Establish those conditions for E-C3, including storage and use of the NL movie document, title actions and displayed prices. Identify permitted retention and publication of derived availability and price decisions. |

For each service, retain the applicable terms or permission reference, version/effective date, review date, reviewer, permitted method, territory, rate/cooldown, reuse limits and expiry privately. The review lacks sufficient evidence to assign a source-approved allowance or retention period to any of them. Historical public access documents feasibility only. If a condition cannot be substantiated, leave the affected rows pending or identify them as omitted in a reduced request before signing.

The custodian must identify restricted owner-controlled storage, permitted readers, retention deadline, deletion date and responsible person consistent with each source decision. Record a sealed response digest, byte count, UTC bounds, reviewer and custody reference. Keep originals, private manifests, headers and link inventories outside Git and GitHub. Publish only reviewed aliases, hashes, counts and structured decisions. The historical canary inventory describes a different collection. [Custody precedent](evidence-custody-verification.md)

## Separate Batch A request for owner decision

**Request status: pending prerequisites, zero authorized sends.** The requested maximum is three first-party documents (one anonymous HTTPS GET per E-C1, E-C2 and E-C3) and two exact NL JustWatch title operations (one each for Inception and Dune), with zero retries and zero paid calls. Reuse an independently authorized, eligible index observation at its original clock and ledger entry where possible. App reads use isolated disposable PostgreSQL with egress disabled. Batch B requires a later review and its own decision. [Owner issuance template](stage-e-offline-rehearsal.md#owner-issuance-template--unapproved)

1. Attach the three source decisions and custody sign-off. Bind each approved scope alias to the exact private HTTPS origin, path, method, native ID, typed key, expected title-document format, NL context and limits. Pin the matrix and private allowlist/manifest digests. A live browser, subrequests, discovery, redirects, login, challenge solving, entitlement and playback are outside scope.
2. Supply absolute half-open 30-minute UTC slots, one per title. For each acquisition host, record a dated synchronization check with its reference, measured offset and bounded uncertainty of at most five seconds. Capture UTC start/end and monotonic elapsed time for every contributing observation, including local reads. Uncertainty-expanded bounds must fit the slot and the combined span must be at most ten minutes. Record cache age and original assertion times. Unknown clocks, stale evidence and an end at the excluded slot boundary are ineligible.
3. Attach exact runner and app revisions, verified NL egress and disposable database identity, current JustWatch receipt/quota and a successful integrated Stage E preflight at those revisions. The existing test fixture exercises durable reservation before send, serial pacing, clocks and restart accounting. Its fixed-runtime boundary test demonstrates rejection of Stage E scopes. Record real preflight evidence separately from the synthetic rehearsal. [Offline rehearsal](stage-e-offline-rehearsal.md)
4. Name owner, operator, independent reviewer and custodian. Record expiry, any omitted rows and their reasons. Only after the supporting evidence is reviewed may the owner separately issue an exact-scope decision. Use one in-flight attempt across tracks, at least three seconds between first-party sends or the stricter source limit. Reconcile Inception before Dune.

## Offline verification

From the repository root, the following commands passed locally. The scope audit resolved 15 candidates to eight historical documents (three A, five B). Four unit tests covered the frozen bindings, misassigned controls and changed capture identity. Eleven Django tests passed on disposable PostgreSQL 16.15 with mocked transport. Live provider requests were zero. Hosted CI is a separate result.

```sh
python3 -BE scripts/audit_issue97_stage_e.py
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e
sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal
```
