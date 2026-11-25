# Stage E first-party source authorization preparation

**`STAGE_E_AUTHORIZATION_PREPARATION_COMPLETE`** records completion of the offline checklist. **`STAGE_E_LIVE_AUTHORIZATION_BLOCKED`** records the execution decision. The 42 checklist entries include outstanding evidence, operational prerequisites and separate approvals. The packet authorizes zero live sends.

The frozen NL Batch A panel covers nine candidates across three native title documents. The proposed ceilings are three first-party GETs and two JustWatch attempts, with zero retries. Batch B requires a separate review and decision. [Evidence protocol](stage-e-evidence-protocol.md) · [Authorization request](stage-e-authorization-request.md) · [Source review](stage-e-source-review.md)

## Batch A candidates

E-C1 is Netflix's Inception (2010, movie 27205) document. E-C2 is Prime Video's document for the same typed title. E-C3 is Rakuten TV's Dune (2021, movie 438631) document. Historical bindings identify candidate documents for review. Every class awaits fresh adjudication. [Frozen matrix](stage-e-candidate-matrix.csv)

| Candidate | Capture scope | Offer class | Fresh status |
| --- | --- | --- | --- |
| E01 | E-C1 | subscription | pending_authorization |
| E02 | E-C2 | subscription | pending_authorization |
| E03 | E-C2 | rent | pending_authorization |
| E04 | E-C2 | buy | pending_authorization |
| E05 | E-C2 | addon | pending_authorization |
| E06 | E-C3 | rent | pending_authorization |
| E07 | E-C3 | buy | pending_authorization |
| E08 | E-C2 | free | pending_authorization |
| E09 | E-C3 | free_with_ads | pending_authorization |

E08 tests a Prime Video free offer. E09 tests a Rakuten TV free-with-ads offer. Both controls have unknown outcomes and share only their historical document assignments. Missing actions, login walls, empty pages and generic errors yield unknown. A confirmed negative requires an explicit current assertion with reviewed semantics for the exact title, service, NL market and offer class. Fresh independently adjudicated pairs and confirmed negatives remain zero.

## Source-specific requirements

For each source, the source reviewer must substantiate access, extraction, retention and derived publication independently. Keep dated applicable terms or permission references, effective dates, scope, method, territory, limits, reviewer and expiry in restricted custody. The offline review established zero of three complete source decisions. Historical access supports feasibility only. Owner approval cannot substitute for missing source evidence. Leave affected rows pending or identify them in an explicitly reduced request before signing. [Source review](stage-e-source-review.md#missing-access-and-custody-decisions)

| Requirement | Netflix · E-C1 | Prime Video · E-C2 | Rakuten TV · E-C3 |
| --- | --- | --- | --- |
| Source access evidence | N1. Substantiate one anonymous HTTPS GET for the exact NL title document. | P1. Substantiate one anonymous HTTPS GET for the exact NL title detail document. | R1. Substantiate one anonymous HTTPS GET for the exact NL movie document. |
| NL context | N2. Establish legitimate anonymous NL request context. | P2. Require recorded and effective territories to agree on NL. | R2. Require the canonical NL storefront and independent NL evidence on the document. |
| Typed identity | N3. Bind the top-level video ID, work type and title or reviewed alias to Inception (2010). | P3. Bind the detail page ID and film GTI to Inception (2010). | R3. Bind movie card, native ID, title, director and year to the 2021 Villeneuve Dune. |
| Extraction conditions | N4. Substantiate extraction conditions and review the title-specific affirmative availability and subscription action or plan signal. | P4. Substantiate use of embedded film, price and channel fields. Validate the same GTI's base Prime benefit, rent and buy actions and separate HBO Max channel benefit under Prime. E08 requires an explicit full-film zero-payment offer without membership or ads. | R4. Substantiate use of title actions and prices. Review rent and buy signals independently. E09 requires an explicit full-film zero-payment ad-funded offer without a subscription. |
| Retention and deletion | N5. Establish permissible raw Netflix document retention and its deletion deadline. | P5. Establish permissible retention of the raw Prime detail document with embedded fields and its deletion deadline. | R5. Establish permissible raw Rakuten movie document retention and its deletion deadline. |
| Derived publication | N6. Establish which sanitized availability decisions and provenance fields may be published. | P6. Establish which sanitized offer or price decisions and provenance fields may be published. | R6. Establish which sanitized availability or price decisions and provenance fields may be published. |
| Private allowlist | N7. Pin exact HTTPS origin and path, native ID, method, typed key and expected document format for E-C1. | P7. Pin those exact fields for E-C2. | R7. Pin those exact fields for E-C3. |
| Source limit | N8. Establish source allowance, pacing and cooldown for at most one GET and zero retries. | P8. Establish the allowance, pacing and cooldown for E-C2 within the same ceiling. | R8. Establish the allowance, pacing and cooldown for E-C3 within the same ceiling. |
| UTC slot and clock | N9. Bind E-C1 to Inception's slot and attach the acquisition host clock check. | P9. Bind E-C2 to the same Inception slot and attach the acquisition host clock check. | R9. Bind E-C3 to Dune's slot and attach the acquisition host clock check. |
| Custody | N10. Designate restricted owner-controlled custody, permitted readers and deletion custodian for Netflix evidence. | P10. Designate custody, readers and deletion custodian for Prime evidence. | R10. Designate custody, readers and deletion custodian for Rakuten evidence. |
| Separate owner approval | N11. Sign the exact scope covering E-C1 after review of the evidence and preflight. | P11. Sign the exact scope covering E-C2 after review of the evidence and preflight. | R11. Sign the exact scope covering E-C3 after review of the evidence and preflight. |

The extraction descriptions are review criteria. Source-specific terms, the exact approved extraction fields, retention and publication decisions must still be substantiated. Apply the existing protocol for prices, temporal eligibility, unknowns and independent review. Keep raw documents, private manifests, headers and private identifiers outside Git and GitHub. [Evidence protocol](stage-e-evidence-protocol.md) · [Custody verification](evidence-custody-verification.md)

## Cross-source requirements

| ID | Requirement | Current disposition |
| --- | --- | --- |
| X1 | Bind approved scopes to the exact private allowlist and sign its digest. | Pending. |
| X2 | Record the frozen candidate matrix digest in the scope. | Matrix frozen. Binding pending. |
| X3 | Record the signed scoped manifest digest, with typed identity bindings. | Pending. |
| X4 | Verify the exact runner and app revisions, NL egress and disposable DB identity. Run the integrated preflight against the reviewed allowlist and exact scope. | Pending. Offline fixtures exercise controls with mocked transport. Integrated live readiness remains unverified. |
| X5 | Identify the owner, operator, independent reviewer and custodian. | Pending. |
| X6 | Bind expiry and one absolute half-open 30-minute UTC slot per title. Attach dated clock synchronization evidence with measured offset and bounded uncertainty of at most five seconds. Keep the combined span at most ten minutes within each slot. | Pending. |
| X7 | Verify serial accounting, at most one in-flight attempt, at least three seconds between first-party sends or stricter reviewed limits, and reconciliation of Inception before Dune. | Protocol defined. Exact-scope verification pending. |
| X8 | Bind the ceilings of three first-party GETs and two JustWatch attempts, with zero retries, redirects or paid requests, subject to stricter reviewed limits. | Proposed study ceilings. Exact-scope approval pending. |
| X9 | Verify current JustWatch runtime receipt, endpoint and operation binding, quota and accounting controls, manifest/database/expiry bindings and clean read-only preflight. Pin any separately authorized eligible reuse to its original clock and ledger. | Runtime authorization remains blocked. Historical captures and mocked rehearsal supply zero new runtime authority. |

The checklist has 33 source-specific entries and nine cross-source entries. Review the supporting records and exact-scope preflight before a separate owner decision. Live execution also requires current runtime authorization verified for the approved scope and slot. The fixed runtime rejects Stage E first-party scopes and batch caps. This preparation made zero live provider requests and changed zero permissions. [Offline rehearsal and owner issuance template](stage-e-offline-rehearsal.md)

## Offline verification

```sh
shasum -a 256 docs/audits/issue97/stage-e-candidate-matrix.csv
python3 -BE scripts/audit_issue97_stage_e.py
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e
sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal
```
