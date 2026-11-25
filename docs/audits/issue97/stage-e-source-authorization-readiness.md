# Stage E first-party source authorization readiness

**Status: READY_FOR_OWNER_AUTHORIZATION**. All three Batch A first-party sources have been reviewed offline. This document itemizes the exact outstanding requirements for each source before the owner can issue Batch A execution authorization. Zero live first-party or provider requests were made during this preparation. Zero documents were fetched. Zero permissions were changed.

Prepared 2026-11-25 on branch `stage-e/97-source-auth`, base `0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea` (master, post-merge of PR #212). Dispatch route: omniroute/pool-claude via OpenCode, effort UNPINNED, COST_UNKNOWN. ChatGPT Web owns final review and merge. Issue #97 remains OPEN, #103 remains on HOLD.

References: [Protocol](stage-e-evidence-protocol.md) · [Authorization request](stage-e-authorization-request.md) · [Source review](stage-e-source-review.md) · [Candidate matrix](stage-e-candidate-matrix.csv) · [Offline rehearsal](stage-e-offline-rehearsal.md) · [Custody verification](evidence-custody-verification.md) · [Independent readiness review (PR #212)](stage-de-independent-readiness-review.md)

---

## Batch A summary

Batch A covers 2 typed titles (Inception tmdb:27205, Dune tmdb:438631), 9 candidate pairs across 3 first-party documents, and 1 market (NL). Maximum 3 first-party HTTPS GETs plus 2 JustWatch attempts. Zero retries. Batch B requires separate approval after Batch A review.

| Capture scope | Source | Title | TMDB ID | Candidate rows | Offer classes covered |
| --- | --- | --- | --- | --- | --- |
| E-C1 | Netflix | Inception | 27205 | E01 | subscription |
| E-C2 | Prime Video | Inception | 27205 | E02–E05 | subscription, rent, buy, addon |
| E-C3 | Rakuten TV | Dune | 438631 | E06–E07 | rent, buy |

---

## Source 1: Netflix (capture scope E-C1)

**Candidate rows**: E01 (Inception, NL, subscription)
**Evidence rule**: `netflix_subscription`
**Historical document**: Netflix NL title page for Inception (tmdb:27205), captured 2026-11-23 during `[15:00, 16:00) UTC`

### Outstanding requirements for owner authorization

| # | Requirement category | Specific requirement | Current status | What the owner must decide or supply |
| --- | --- | --- | --- | --- |
| N1 | Anonymous native-document access | Confirm that the Netflix NL title page for tmdb:27205 is accessible via anonymous HTTPS GET without authentication, cookies, or session tokens. | NOT VERIFIED — historical capture exists but fresh access conditions are undetermined | Owner must confirm or test (outside this preparation) that a single anonymous GET to the pinned Netflix host/path returns a parseable HTML document without requiring login. If the page redirects unauthenticated visitors, document the redirect behavior. |
| N2 | NL territorial context | Confirm that the Netflix title page returns NL-specific availability when accessed from a Netherlands IP or that the URL path encodes NL territory (e.g. `/nl/title/...`). | ASSUMED from historical URL structure — not independently verified against fresh page | Owner must confirm the URL path includes `/nl/` or `/nl-en/` territory prefix, OR confirm the request will originate from a Netherlands-located host. No VPN or geo-spoofing is authorized. |
| N3 | Typed title identity | Pin the exact Netflix internal title identifier (numeric ID in URL path) corresponding to tmdb:27205 (Inception, 2010). Confirm it matches the identity manifest entry. | Historical identity exists in `identity_manifest_20261122.csv` — freshness unverified | Owner must verify that the Netflix title ID in the allowlist resolves to the correct title (Inception, 2010) and has not been reassigned. Cross-reference with the typed identity manifest. |
| N4 | Extraction conditions | Define what constitutes a positive subscription-availability signal in the Netflix HTML response. Specify the DOM element, attribute, or text pattern that confirms "available to stream with subscription in NL". | NOT DEFINED — the evidence rule `netflix_subscription` references no extraction spec | Owner must specify (or approve a proposed specification for) the exact signal: e.g., presence of a play button, absence of a "not available" banner, or a specific metadata tag indicating subscription availability. |
| N5 | Derived-publication conditions | Confirm whether the presence/absence determination (binary yes/no) may be recorded in the repository audit trail. No verbatim page content, screenshot, or Netflix-proprietary data is published. | NOT DECIDED — source review shows 0/3 publication decisions | Owner must explicitly authorize recording: (a) the binary availability determination (available/not-available), (b) the capture UTC timestamp, (c) a content hash (SHA-256) of the response for integrity, and (d) HTTP status code. No other response content is retained or published. |
| N6 | Retention and deletion | Define the maximum retention period for the raw HTTP response stored in restricted private custody (`~/.hermes/private/`). Define deletion trigger (e.g., after adjudication, fixed calendar date, or batch review). | NOT DECIDED — existing custody policy covers historical artifacts but not fresh captures | Owner must set: (a) maximum days to retain the raw response in private storage, (b) whether deletion is automatic after adjudication or requires manual confirmation, (c) whether the integrity hash survives deletion of the raw response. |
| N7 | Host/path allowlist | Pin the exact HTTPS host and path prefix for the Netflix NL title document in the private Batch A allowlist. No wildcards. | NOT PINNED — the private allowlist was unavailable for verification per the source review | Owner must add the exact entry: `https://<netflix-host>/<nl-path-prefix>/<title-id>` to the private allowlist file. The allowlist must be signed (digest recorded) before execution. |
| N8 | Source-specific request limits | Confirm the Netflix-specific limit: exactly 1 HTTPS GET for capture scope E-C1, zero retries, zero follow-up requests regardless of response status. | DEFINED in protocol (1 GET per scope, zero retries) — not source-specifically acknowledged | Owner must confirm acceptance of the protocol's zero-retry rule for Netflix specifically. A non-200 response is recorded as-is and does not trigger any additional request. |
| N9 | UTC capture slot and clock check | Assign the exact UTC time window `[start, end)` during which the Netflix GET may execute. Confirm the operator's system clock is NTP-synchronized and within ±2 seconds before execution. | NOT ASSIGNED — protocol requires pre-assigned slot but none exists | Owner must assign a specific UTC window (e.g., `[HH:MM, HH:MM+05) UTC` on a specific date). The operator must run an NTP clock check and record the offset before the window opens. |
| N10 | Private evidence custody | Confirm the custody path, directory permissions (0700), and file permissions (0600) for the raw Netflix response artifact. | ESTABLISHED for historical artifacts at `~/.hermes/private/issue97-canary-ff15058/` — fresh capture path not assigned | Owner must designate the exact subdirectory under `~/.hermes/private/` for Batch A Netflix artifacts and confirm the permission model (dir 0700, files 0600). |
| N11 | Batch A owner authorization (separate) | Issue the formal execution authorization: operator identity, reviewer identity, matrix digest, manifest digest, batch ID, expiry, and the signed allowlist digest. | NOT ISSUED — this document prepares the requirements; execution authorization is a separate act | Owner issues authorization only after all N1–N10 requirements are satisfied, the offline preflight passes with the integrated allowlist, and the operator confirms clock synchronization. |

---

## Source 2: Prime Video (capture scope E-C2)

**Candidate rows**: E02 (subscription), E03 (rent), E04 (buy), E05 (addon/Max via Amazon channel)
**Evidence rules**: `prime_subscription`, `prime_transaction`, `prime_channel`
**Historical document**: Prime Video NL title page for Inception (tmdb:27205), captured 2026-11-23 during `[15:00, 16:00) UTC`

### Outstanding requirements for owner authorization

| # | Requirement category | Specific requirement | Current status | What the owner must decide or supply |
| --- | --- | --- | --- | --- |
| P1 | Anonymous native-document access | Confirm that the Prime Video NL title page for tmdb:27205 is accessible via anonymous HTTPS GET without Amazon account login, cookies, or session tokens. Prime Video pages frequently require or redirect to authentication. | NOT VERIFIED — historical capture exists but Prime Video is known to gate content behind login | Owner must confirm or test that a single anonymous GET returns a parseable page with offer information. If Prime Video requires authentication, this source may need to be flagged as inaccessible for anonymous capture and the candidate rows E02–E05 would need an alternative evidence path or explicit exclusion. |
| P2 | NL territorial context | Confirm that the Prime Video URL encodes NL territory or that the request from a Netherlands IP returns NL-specific pricing and availability (not a global or US-defaulted page). | ASSUMED from historical URL — not independently verified | Owner must confirm the URL path includes an NL locale marker (e.g., `/dp/...` with NL region parameter) or that Netherlands IP geolocation produces NL-specific results. No VPN or geo-spoofing is authorized. |
| P3 | Typed title identity | Pin the exact Prime Video ASIN or title identifier corresponding to tmdb:27205 (Inception, 2010). Confirm it matches the identity manifest. | Historical identity in manifest — freshness unverified | Owner must verify the Prime Video identifier (ASIN) in the allowlist resolves to Inception (2010) and has not been reassigned or region-forked. |
| P4 | Extraction conditions — multi-class | Define the extraction signals for all four offer classes on a single Prime Video page: (a) subscription ("Included with Prime"), (b) rent (price present in rent section), (c) buy (price present in buy section), (d) addon channel ("Watch with Max" or equivalent channel badge). | NOT DEFINED — four evidence rules reference no extraction spec | Owner must specify or approve extraction criteria for each of the four offer classes. A single page response supports multiple determinations. Each must have a defined positive and negative signal. |
| P5 | Derived-publication conditions | Confirm whether the four binary availability determinations may be recorded. No verbatim content, pricing, or Amazon-proprietary data is published. | NOT DECIDED — 0/3 publication decisions per source review | Owner must explicitly authorize recording for Prime Video: (a) binary determinations for each of the four offer classes, (b) capture timestamp, (c) SHA-256 content hash, (d) HTTP status code. No pricing data, HTML fragments, or screenshots are retained or published. |
| P6 | Retention and deletion | Define retention period and deletion policy for the raw Prime Video HTTP response in private custody. | NOT DECIDED | Same structure as N6: maximum retention days, automatic vs. manual deletion after adjudication, hash survival policy. |
| P7 | Host/path allowlist | Pin the exact HTTPS host and path for the Prime Video NL Inception page in the private Batch A allowlist. | NOT PINNED | Owner must add the exact entry. The allowlist must be signed before execution. |
| P8 | Source-specific request limits | Confirm: exactly 1 HTTPS GET for capture scope E-C2, zero retries. One response covers all four candidate rows (E02–E05). | DEFINED in protocol — not source-specifically acknowledged | Owner must confirm the zero-retry rule for Prime Video. A non-200 or login-redirect response is recorded as-is. |
| P9 | UTC capture slot and clock check | Assign the exact UTC window for the Prime Video GET. Must not overlap with Netflix slot (sequential execution per protocol). | NOT ASSIGNED | Owner must assign a slot after the Netflix capture completes and the operator confirms the Netflix artifacts. Clock check must be re-verified if more than 5 minutes elapsed. |
| P10 | Private evidence custody | Designate custody subdirectory and permissions for Prime Video artifacts. | NOT ASSIGNED | Same model as N10 under `~/.hermes/private/`. |
| P11 | Batch A owner authorization (separate) | Issue formal execution authorization covering E-C2 scope after P1–P10 are satisfied. | NOT ISSUED | Part of the unified Batch A authorization or a per-scope sub-authorization, at owner's discretion. |

---

## Source 3: Rakuten TV (capture scope E-C3)

**Candidate rows**: E06 (rent), E07 (buy)
**Evidence rule**: `rakuten_transaction`
**Historical document**: Rakuten TV NL title page for Dune (tmdb:438631), captured 2026-11-23 during `[15:00, 16:00) UTC`

### Outstanding requirements for owner authorization

| # | Requirement category | Specific requirement | Current status | What the owner must decide or supply |
| --- | --- | --- | --- | --- |
| R1 | Anonymous native-document access | Confirm that the Rakuten TV NL title page for tmdb:438631 is accessible via anonymous HTTPS GET without login. Rakuten TV catalog pages are generally public. | NOT VERIFIED — historical capture exists; Rakuten TV is expected to be publicly accessible but not freshly confirmed | Owner must confirm or test that an anonymous GET returns the Dune title page with offer pricing information visible. |
| R2 | NL territorial context | Confirm that the Rakuten TV URL returns NL-specific content (pricing in EUR, NL availability status). | ASSUMED from historical URL structure (e.g., `rakuten.tv/nl/...`) — not independently verified | Owner must confirm the URL path includes `/nl/` locale prefix or that Netherlands IP geolocation produces NL-specific results. |
| R3 | Typed title identity | Pin the exact Rakuten TV content identifier corresponding to tmdb:438631 (Dune, 2021). Confirm it matches the identity manifest. | Historical identity in manifest — freshness unverified | Owner must verify the Rakuten TV slug or ID in the allowlist resolves to Dune (2021). |
| R4 | Extraction conditions — rent and buy | Define the extraction signals for both transaction offer classes: (a) rent (price/button visible in rent section), (b) buy (price/button visible in buy section). | NOT DEFINED — `rakuten_transaction` rule references no extraction spec | Owner must specify or approve extraction criteria for rent and buy determinations. Each must have defined positive and negative signals. |
| R5 | Derived-publication conditions | Confirm whether the two binary availability determinations may be recorded. No verbatim content, pricing, or Rakuten-proprietary data is published. | NOT DECIDED | Owner must authorize recording: binary determinations, timestamp, SHA-256 hash, HTTP status code. |
| R6 | Retention and deletion | Define retention and deletion policy for Rakuten TV raw response. | NOT DECIDED | Same structure as N6/P6. |
| R7 | Host/path allowlist | Pin the exact HTTPS host and path for the Rakuten TV NL Dune page. | NOT PINNED | Owner adds exact entry to signed allowlist. |
| R8 | Source-specific request limits | Confirm: exactly 1 HTTPS GET for capture scope E-C3, zero retries. One response covers both candidate rows (E06–E07). | DEFINED in protocol — not source-specifically acknowledged | Owner confirms zero-retry rule. |
| R9 | UTC capture slot and clock check | Assign the exact UTC window for the Rakuten TV GET. Must follow Prime Video slot (sequential). | NOT ASSIGNED | Owner assigns slot, clock check re-verified. |
| R10 | Private evidence custody | Designate custody subdirectory and permissions for Rakuten TV artifacts. | NOT ASSIGNED | Same model as N10/P10. |
| R11 | Batch A owner authorization (separate) | Issue formal execution authorization covering E-C3 scope after R1–R10 are satisfied. | NOT ISSUED | Part of unified Batch A authorization. |

---

## Cross-source requirements

| # | Requirement | Status | Action |
| --- | --- | --- | --- |
| X1 | Unified Batch A allowlist — all 3 host/path entries pinned and digest signed | NOT COMPLETE | Owner pins all entries, signs, records digest in authorization |
| X2 | Matrix digest — SHA-256 of `stage-e-candidate-matrix.csv` frozen and recorded | AVAILABLE — matrix is frozen at 15 rows | Owner records digest in authorization |
| X3 | Manifest digest — SHA-256 of identity manifest recorded | AVAILABLE | Owner records in authorization |
| X4 | Offline integrated preflight — all 3 scopes pass with mock transport on the signed allowlist | NOT RUN — rehearsal passed but integrated preflight with actual allowlist pending | Owner or operator runs integrated preflight after allowist is signed |
| X5 | Operator identity and reviewer identity recorded | NOT ASSIGNED | Owner designates operator and reviewer |
| X6 | Authorization expiry — calendar datetime after which the batch authorization lapses | NOT SET | Owner sets expiry |
| X7 | Execution order — Netflix (E-C1) → Prime Video (E-C2) → Rakuten TV (E-C3), sequential, with checkpoint between each | DEFINED in protocol | Owner confirms sequence |
| X8 | Batch A ceiling — 3 first-party GETs + 2 JustWatch attempts, zero retries | DEFINED in authorization request | Owner confirms ceiling |
| X9 | JustWatch — 2 bounded `GetSearchTitles` attempts for Inception NL and Dune NL using existing approved operation | DEFINED — reuse of eligible captures preferred | Owner confirms or assigns JustWatch slots within the same window |

---

## Blocker summary

| Source | Outstanding items | Critical blocker |
| --- | --- | --- |
| Netflix | 11 (N1–N11) | N1 (anonymous access unverified), N4 (no extraction spec), N5 (no publication decision) |
| Prime Video | 11 (P1–P11) | P1 (anonymous access — likely requires authentication), P4 (no extraction spec for 4 classes), P5 (no publication decision) |
| Rakuten TV | 11 (R1–R11) | R1 (anonymous access unverified), R4 (no extraction spec), R5 (no publication decision) |
| Cross-source | 9 (X1–X9) | X1 (allowlist unsigned), X4 (integrated preflight not run), X5 (operator unassigned) |

**Total outstanding items: 42** (33 source-specific + 9 cross-source). Of these, the most critical are the 3 anonymous-access verifications (N1, P1, R1), 3 extraction specifications (N4, P4, R4), and 3 publication decisions (N5, P5, R5).

---

## Verification commands (no live requests)

```sh
# Verify matrix integrity
sha256sum docs/audits/issue97/stage-e-candidate-matrix.csv

# Run offline audit script
python3 -BE scripts/audit_issue97_stage_e.py

# Run offline unit tests
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e

# Run offline rehearsal on disposable PostgreSQL
sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal
```

---

## Decision

**READY_FOR_OWNER_AUTHORIZATION**. The 42 outstanding requirements are enumerated above. No requirement depends on a live request. The owner can review and supply decisions in any order; the integrated preflight (X4) must run last, after the allowlist (X1) is signed. Upon satisfying all items, the owner may issue the separate Batch A execution authorization. Batch B requires its own review after Batch A completes.

No live first-party or provider requests were made, no documents were fetched, no permissions were changed, and no grants or tokens were created during this preparation.
