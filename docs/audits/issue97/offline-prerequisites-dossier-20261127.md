# Issue #97 Offline Prerequisites Dossier

**Date:** 2026-11-27 UTC
**Issue:** #97 ([P1] Close the real-world availability accuracy verification gap)
**Status:** DRAFT PR DOSSIER / PENDING OWNER DECISION / NO EXECUTION AUTHORIZED
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
- **Required Capture:** Complete raw request payloads, exact response headers, JSON body, timestamp (ISO-8601 UTC), client version, and egress IP routing fingerprint.
- **Retention:** Immutable append-only local storage under `docs/audits/issue97/receipts/` with SHA-256 checksum verification.
- **Hash/Fingerprint Fields:** Every receipt must include:
  - `payload_sha256`: SHA-256 digest of the canonical JSON payload.
  - `response_sha256`: SHA-256 digest of the received response body.
  - `transport_fingerprint`: TLS cipher suite and SNI certificate fingerprint.
- **Fail-Closed Validation:** Any discrepancy in checksums, missing variables (e.g. omitted country code), or unexpected transport errors must abort the run immediately without generating availability assertions or updating cache states.

---

## 2. Quota & Balance Evidence Checklist (Offline Satisfiable)

The project operating constraint is the Streaming Availability API (movieofthenight via RapidAPI) **100 requests/day free tier**, which applies exclusively to cached lookups (`last_checked_at`). For external sources (JustWatch, Netflix, Prime Video, Rakuten TV), live quotas are verified offline through owner-provided telemetry:
- [ ] Owner-supplied account dashboard screenshot or API usage report showing remaining daily/monthly quota.
- [ ] Explicit confirmation of zero active rate-limiting flags or billing alerts.
- [ ] Verification that local rate limiters (`tracker.services.rate_limit` and source-specific budget configs) enforce headroom strictly below plan ceilings.
- [ ] No direct API queries executed by agents; all balance evidence is provided statically.

---

## 3. Exclusive-Use Attestation & HMAC Authorization Request Template

### Exclusive-Use Attestation
> "I attest that all offline rehearsals and future authorized executions use dedicated, single-tenant test accounts and disposable PostgreSQL instances (`WTDWI_DISPOSABLE_TEST_DB=1`) with read-only boundaries. No shared production credentials, concurrency leaks, or unauthorized service principals are employed."

### HMAC Authorization Request Template
```yaml
authorization_request:
  target_system: "WhereTfDoIWatchIt Streaming Availability / Source Inception"
  scope_boundaries:
    countries: ["nl"]
    providers: ["netflix", "amazon_prime_video", "rakuten"]
    max_requests: 3
    max_retries: 0
    concurrency: 1
    allowed_endpoints: ["GetSearchTitles", "GetOffers"]
  hmac_signature_request:
    key_id: "PENDING_OWNER_KEY"
    nonce: "PENDING_NONCE"
    validity_window_seconds: 300
  status: "PENDING"
```
*Note: No token generation or signing key usage is performed within this dossier.*

---

## 4. Per-Source Decision Forms (Netflix, Prime Video, Rakuten TV)

Consistent with the cleaned Stage E matrix, all sources remain **PENDING** owner decision.

| Source | Target Scope | Reviewed Artifacts | Current Status | Owner Decision |
|---|---|---|---|---|
| **Netflix** (`E-C1`) | NL single-title Inception (Inception / `tmdb-27205`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |
| **Prime Video** (`E-C2`) | NL single-title Inception (The Godfather / `tmdb-238`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |
| **Rakuten TV** (`E-C3`) | NL single-title Inception (Dune / `tmdb-438631`) | `docs/audits/issue97/stage-e-source-review.md` | PENDING | **[ ] Authorize / [x] Hold** |

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
