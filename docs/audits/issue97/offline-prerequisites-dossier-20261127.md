# Issue #97 Offline Prerequisites Dossier

**Date:** 2026-11-27 UTC
**Issue:** #97 ([P1] Close the real-world availability accuracy verification gap)
**Status:** DRAFT PR DOSSIER — REQUEST_CHANGES corrections applied; pending ChatGPT Web independent re-review and owner decision; NO EXECUTION AUTHORIZED
**Base Head:** `331dbe2f7731078c93e5e93eb4620c6e7d07e507` (Merge PR #231: #103 stop-writers release readiness plan)
**Model Identity:** `ag-gemini-3.5-flash-lite` (via wrapper/provider omniroute, requested HIGH as unpinned)
**Cost:** JSON cost 0

---

## Executive Summary & Non-Execution Mandate

This dossier aggregates all offline prerequisites required to advance issue #97 without repeating completed Stage E matrix or Agent C identity work. 

**CRITICAL CONSTRAINT:** Live execution is **strictly NOT AUTHORIZED**:
- No live provider, TMDb, first-party, or playback requests.
- No HMAC grants, signing keys, quota changes, or runtime-permission changes.
- No production writes, migrations, deployments, issue closures, or branch merges.

Merging this dossier into master or retaining it as a draft PR grants zero operational approval. All runtime activities remain locked behind owner decision gates.

---

## 1. Exact Runtime-receipt Artifact Requirements (NL Search)

For any future live or recorded search execution against the Netherlands (`nl`) catalog:
- **Required fields are limited to the pinned runner's validated runtime-receipt schema:** operation `GetSearchTitles`, country `NL`, active window, maximum request count, reviewed access confirmation, rate interval, reviewer/check timestamp, and canonical payload/response SHA-256 digests. Do not invent eligibility or identity fields.
- **Optional transport metadata:** TLS cipher, SNI certificate fingerprint and egress routing details are optional context only unless a reviewed runner schema explicitly names them as required. They are not mandatory receipt data here.
- **Custody:** Raw request/response payloads, headers and bodies must stay in private protected off-repository custody with restricted review access and a retention/deletion policy. Git receives only sanitized hashes, counts and provenance metadata; never raw payloads or headers.
- **Fail-Closed Validation:** Any checksum discrepancy, missing required schema field, omitted country code, or unexpected transport error aborts the run immediately without generating availability assertions or updating cache states.

---

## 2. Quota & Balance Evidence Checklist (Offline Satisfiable)

Historical note only: a Streaming Availability / RapidAPI free-tier limit was an earlier source constraint; it is **not** a JustWatch quota and does not establish current balance. Current quota and balance for JustWatch and provider sources remain **UNKNOWN** until owner-supplied evidence is reviewed offline:
- [ ] Owner-supplied account dashboard screenshot or API usage report showing remaining daily/monthly quota.
- [ ] Explicit confirmation of zero active rate-limiting flags or billing alerts.
- [ ] Verification that local rate limiters (`tracker.services.rate_limit` and source-specific budget configs) enforce headroom strictly below plan ceilings.
- [ ] No direct API queries executed by agents; all balance evidence is provided statically.

---

## 3. Exclusive-Use Attestation & HMAC Authorization Request Template

### Exclusive-Use Attestation
> "I attest that all offline rehearsals and future authorized executions use dedicated, single-tenant test accounts and disposable PostgreSQL instances (`WTDWI_DISPOSABLE_TEST_DB=1`) with read-only boundaries. No shared production credentials, concurrency leaks, or unauthorized service principals are employed."

### Non-executable HMAC Authorization Request Templates

These are documentation templates only. They do not generate, sign or authorize any token, and no live endpoint is enabled by them.

**Stage D Batch 1 — approved offline scope only (NL search):**
```yaml
authorization_request_template:
  phase: "Stage D Batch 1"
  operation: "GetSearchTitles"
  countries: ["nl"]
  titles: ["The Godfather / tmdb-238", "Breaking Bad / tmdb-1396"]
  max_first_sends: 2
  max_retries: 0
  fallback_season_queries: "denied"
  additional_operations: "denied"
  status: "PENDING_NON_EXECUTABLE_TEMPLATE"
```

**Stage E — separate per-source decisions only:**
```yaml
authorization_request_template:
  phase: "Stage E Batch A"
  sources: ["netflix", "amazon_prime_video", "rakuten"]
  pinned_titles:
    netflix: "Inception / tmdb-27205"
    amazon_prime_video: "Inception / tmdb-27205"
    rakuten: "Dune / tmdb-438631"
  capture_scope: "per-source decision pending; no endpoint or retry allowance is granted by this template"
  status: "PENDING_OWNER_SOURCE_DECISION_NON_EXECUTABLE"
```
*Note: No token generation, signing key usage, endpoint broadening or live execution is performed by this dossier.*

---

## 4. Per-Source Decision Forms (Netflix, Prime Video, Rakuten TV)

Consistent with the cleaned Stage E matrix, all sources remain **PENDING** owner decision.

| Source | Target Scope | Reviewed Artifacts | Current Status | Owner Decision |
|---|---|---|---|---|
| **Netflix** (`E-C1`) | NL single-title Inception (`tmdb-27205`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |
| **Prime Video** (`E-C2`) | NL single-title Inception (`tmdb-27205`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |
| **Rakuten TV** (`E-C3`) | NL single-title Dune (`tmdb-438631`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |

---

## 5. Accuracy-Denominator Protocol

To prevent unreviewed or zero-denominator inflation in accuracy metrics:
- **Required Adjudicated Independent Binary Pairs:** Every evaluated movie/service combination must have independent positive/negative annotations verified by at least two distinct evidence paths (e.g. crawler observation vs. manual catalog inspection).
- **Nonzero Denominator Rule:** Any assertion category with a denominator of zero is classified as `UNDEFINED` and excluded from aggregate percentages.
- **UNDEFINED Status:** All unreviewed negative or ambiguous matches remain marked `UNDEFINED` until explicitly adjudicated by human review. Self-confirmation is prohibited.

---

## 6. Cross-References

- Comment 7222246863: Cleaned Stage E matrix and source boundaries.
- Integration 7222358595: Disposable PostgreSQL backend integration harness.
- Identity 7222372837: 12 evidence-pinned typed identities provisioning (`issue97_stage_d_identities.py`).
- Repair 7223132616: GraphQL country propagation and offer parsing fixes.
- Decision Tracker 7224642545: Gate control and audit custody preservation.
