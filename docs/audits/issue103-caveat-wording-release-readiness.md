# Release-Readiness and Caveat-Wording Report for #103

| Attribute | Value |
| --- | --- |
| Task | #103 Caveat-wording audit and release-readiness review |
| Target Issue | #103 ([P2] Make subscription plan tiers, channel add-ons, and offer entitlement verdicts accurate) |
| Merged Base Commit | `488654a22b1837acdd0acbcd810fc495449fc66d` (Merge #225) |
| Branch | `AngeloAndreaIsola/wtdw3-caveat` |
| Original Audit Route | `gpt-6.2-sol` was requested for the earlier audit and was unavailable; the earlier audit used `omniroute/ag-gemini-3.8-flash-tiered` via pi agent at HIGH effort (`PI_REASONING_EFFORT=high`). |
| Repair-Cycle Requested Route | `opencode/hy4-flash-free` (native OpenCode) |
| Repair-Cycle Actual Model / Identity | `opencode/hy4-flash-free` (self-reported by fresh in-session probe; provider OpenCode) |
| Repair-Cycle Effort | MEDIUM (self-reported `EFFORT=medium`; OpenCode exposes no reliable HIGH flag, so HIGH is not claimed) |
| Repair-Cycle Cost | JSON telemetry `cost: 0`; self-reported textual cost `unknown` (`opencode stats`: $0.00 total) |
| Live Network / DB Calls | Strictly **ZERO**. No live provider, first-party, TMDb, or playback calls. Disposable ephemeral PostgreSQL used exclusively. |

---

## 1. Executive Summary & Verification Findings

The merged #103 implementation (`488654a22b1837acdd0acbcd810fc495449fc66d`, which integrates #220, #222 `aa6db9b6`, #224 `351e2816`, #223 `e8280ee9`, and #227 `87c62385`) was systematically audited across frontend and backend services for:
1. Every user-facing string regarding country/market, provider name, subscription tier, add-on channel, unsupported/retired plan, stale/source-reported/last-known evidence, and UNKNOWN entitlement.
2. Complete fidelity to the reviewed contract (`docs/contracts/issue103-entitlement.md`, `2cfa9204`).
3. Alignment with upstream architectural constraints: **no real-world entitlement assertions are made**; all positive assertions remain strictly framed as coverage under the viewer's declared selection, with unverified account status and playback explicitly stated.

### Result of Audit
The audited user-facing strings are **consistent with the reviewed application contract** (`docs/contracts/issue103-entitlement.md`) and are properly guarded against upgrading unverified observations to positive verdicts. This is a claim about contract consistency and evidence gating only: **it is not a claim that all market-specific copy is objectively correct about the real world**. Real-world provider/package mappings remain unverified until independently corroborated, and account status and playback are not confirmed. Owner wording sign-off is still required before production activation.
Specifically:
- Non-verified observations (`stale`, `source_reported`, `unknown`) are strictly prevented from rendering positive verdict badges, Best Match indicators, or positive entitlement caveats (`frontend/components/OfferRow.tsx`).
- Stale offers are segregated into `LastKnownOffers` and badged with "Unverified · last seen — [date]".
- Third-party source observations are badged "Source-reported", labeled "Reported by availability sources", and cautioned with "Your subscription access and playback are unverified."
- Unsupported plans are kept marked unsupported with clear user guidance that plan-dependent offers remain unconfirmed. A missing catalogue entry is **not** treated as evidence that the provider discontinued a real-world plan; the notice is scoped to our catalogue.
- Cross-market / billing portability caveats explicitly state that coverage in another market requires reviewed portability.

---

## 2. Comprehensive Inventory of Market-Specific Caveat & Verdict Wording

| Category | UI Element / Identifier | Exact User-Facing Copy | Evidence Gating & Context | Contract & Truth Assessment |
| --- | --- | --- | --- | --- |
| **Entitlement Verdict** | `VERDICT_LABELS.INCLUDED` | `"Included with your selected plan"` | Gated by `positive && !unverified && !legacyPlan`. Requires verified fact and active coverage rule matching viewer's plan. | **Accurate.** Accurately describes plan coverage under declared selection. |
| **Entitlement Verdict** | `presentVerdict (channel)` | `"Included with your channel"` | Positive rule matching viewer's declared base plan and channel. | **Accurate.** Accurately describes combined coverage. |
| **Entitlement Verdict** | `VERDICT_LABELS.REQUIRES_ADDON` | `"Requires channel add-on"` | Fact requires child channel that is not declared held. | **Accurate.** Distinguishes base from add-on. |
| **Entitlement Verdict** | `VERDICT_LABELS.RENT_OR_BUY` / `presentVerdict` | `"Rent or buy"` / `"Rent"` / `"Buy"` | `key.offer_type` in `{"rent", "buy"}`. Separated from subscription. | **Accurate.** Prevents conflation of TVOD with SVOD. |
| **Entitlement Verdict** | `VERDICT_LABELS.FREE_WITH_ADS` | `"Free with ads"` | `key.offer_type == "free_with_ads"`. Explicitly AVOD/FAST, not paid ads plan. | **Accurate.** |
| **Entitlement Verdict** | `presentVerdict (free)` | `"Free"` | `key.offer_type == "free"`. | **Accurate.** |
| **Entitlement Verdict** | `VERDICT_LABELS.BLOCKED` | `"Blocked for your selection"` | Explicit negative rule, excluded plan, or unheld base. | **Accurate.** |
| **Entitlement Verdict** | `VERDICT_LABELS.UNKNOWN` / `effectivePresentation` | `"Plan access unconfirmed"` | Unspecified plan, unsupported plan, unconfirmed holding, stale/unverified evidence, conflicting evidence. | **Accurate.** Never claims watchability when unconfirmed. |
| **Positive Caveat** | `entitlement.caveats` (`PLAN_COVERS_TITLE`, `CHANNEL_AND_BASE_COVER_TITLE`) | `"Coverage applies to your declared selection. Account status and playback are unverified."` | Only emitted on positive verdict with verified evidence. Rendered in `OfferRow` under `data-testid="entitlement-caveat"`. | **Accurate.** Essential boundary marker required by contract. |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PLAN_UNSPECIFIED` | `"Confirm your exact plan and holding to resolve plan-dependent access."` | Emitted when viewer selected provider but has not specified plan. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PLAN_UNSUPPORTED` | `"The declared plan has no supported mapping for this market."` | Emitted when viewer has an unsupported or unlisted plan choice. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.HOLDING_UNCONFIRMED` | `"The required holding is unconfirmed — it is not counted as access."` | Emitted when holding is not `yes`. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.NO_APPLICABLE_RULE` | `"Coverage under the selected plan is unconfirmed for this title."` | Verified offer exists, but no reviewed rule covers this plan/title combination. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.CHANNEL_NOT_HELD` | `"The channel is not marked as held. Select the exact child channel on its host to confirm."` | Emitted when add-on is required but channel is not declared held. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.BASE_NOT_HELD` | `"The required base subscription is marked as not held."` | Base provider is declared `no`. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PLAN_EXCLUDED` | `"The selected plan explicitly excludes this offer."` | Explicit exclusion rule (e.g. tier excludes 4K/HDR or title). | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.REGION_EXCLUDED` | `"The selected product excludes this market."` | Coverage rule excludes this geographic market. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PORTABILITY_UNVERIFIED` | `"This title is not confirmed for playback outside your billing country."` | Title offer is outside declared billing country. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.TERRITORY_UNKNOWN` | `"Territory could not be resolved for this offer."` | Offer missing valid country. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.SOURCE_REPORTED` | `"A source reported this offer; access and playback are unverified."` | Source-reported evidence state. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.STALE_EVIDENCE` | `"This offer was last seen in an earlier check and needs a recheck."` | Stale evidence state. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.EVIDENCE_CONFLICT` | `"Applicable offer evidence conflicts; no positive can be asserted."` | Conflicting facts observed. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.OFFER_CLASS_UNKNOWN` | `"The monetization class could not be resolved."` | Unrecognized offer monetization type. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PACKAGE_UNRESOLVED` | `"The source package has no reviewed identity for this offer."` | Unresolved package/variant. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.EVIDENCE_UNVERIFIED` | `"The observation does not satisfy the current evidence gate."` | Unverified observation state. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.SOURCE_UNSUPPORTED` | `"This source/market mapping has not passed review for positive results."` | Collector source not approved for positive outputs. | **Accurate.** |
| **Negative / Unconfirmed Reason** | `REASON_COPY.PUBLIC_CONDITIONS_UNCONFIRMED` | `"Required conditions for this public offer remain unconfirmed."` | Public conditions unmet. | **Accurate.** |
| **Region Caveat (Market Mismatch)** | `OfferRow.tsx:399` | `"Billing {billing_country} · offer {offer_country} — plan coverage in another market needs reviewed portability."` | Gated by `!isContradictoryUnverified && billing_country != offer_country`. | **Accurate.** Warns user of cross-border ambiguity. |
| **Region Caveat (Reason Gate)** | `OfferRow.tsx:407` | `"Region caveat: this market is not confirmed by your selected plan."` | Gated by `reason` in `{"PORTABILITY_UNVERIFIED", "REGION_EXCLUDED", "TERRITORY_UNKNOWN"}`. | **Accurate.** |
| **Selector Unsupported Plan Notice** | `MarketPlanSelector.tsx` (`data-testid="retired-plan-notice"`) | `"We can’t currently confirm coverage for your saved plan “{name}” in {billing_country}. It remains unsupported in our catalogue, so plan-dependent offers are unconfirmed."` | Gated by `savedState.plan_state === "unsupported" && savedState.plan_id`, which also occurs when the plan row is merely absent from our curated catalogue. | **Consistent with the reviewed application contract.** Catalogue-scoped only; does not assert that the provider discontinued the plan. The saved plan ID and existing selection remain intact. |
| **Selector Conflict Notice** | `MarketPlanSelector.tsx:139` | `"Not saved: this selection changed elsewhere. The current saved state is shown below and your edits are still here — review and save again."` | HTTP 409 revision conflict. | **Accurate.** Explicit concurrency guidance. |
| **Selector Isolation Rule** | `MarketPlanSelector.tsx:296` | `"A channel held on its own does not grant a channel hosted by another provider. Unanswered channels stay unconfirmed."` | Rendered under channel selector. | **Accurate.** Explains parent-host containment semantics. |
| **Last Known Availability Header** | `LastKnownOffers.tsx:31` | `"🕓 Last known availability — needs recheck"` / `"Not re-confirmed recently, so not counted as watchable now."` | Separates stale offers from current watchability. | **Accurate.** |
| **Source Reported Header** | `SourceReportedOffers.tsx:15` | `"Reported by availability sources"` / `"Your subscription access and playback are unverified."` | Isolates third-party listing rows from verified catalog. | **Accurate.** |

---

## 3. Verification & Test Evidence

### A. Test Additions
Added dedicated unit tests in `frontend/components/__tests__/OfferRowEntitlement.test.tsx` under describe block `caveat and non-verified evidence safety guarantees`:
1. `never renders positive verdict, Best Match, or positive copy when evidence is stale` — asserts that stale offers suppress "Included with your selected plan", "Best Match", and positive caveat text, presenting "Plan access unconfirmed" and "needs a recheck".
2. `never renders positive verdict, Best Match, or positive copy when evidence is source_reported` — asserts that source-reported offers suppress positive verdicts and caveat text, displaying "Source-reported" and "Plan access unconfirmed".
3. `never renders positive verdict, Best Match, or positive copy when evidence is unknown/unverified` — asserts that unverified offers suppress positive indicators and display unconfirmed access.
4. `strictly bounds positive caveat rendering to verified evidence with positive verdict` — verifies that caveat copy is only rendered when evidence is strictly verified and verdict is positive.
5. Added focused regression tests in `frontend/components/__tests__/MarketPlanSelector.test.tsx` under describe block `catalogue-scoped unsupported-plan wording (review regression)`:
   - a missing catalogue entry produces no discontinued/no-longer-offered/retired claim;
   - the previously saved plan ID (`base`) remains intact;
   - plan-dependent entitlement stays unconfirmed and no positive verdict or Best Match is rendered;
   - the user can retain the saved selection on save (submitted with `plan_state: "unsupported"`, `plan_id: "base"`);
   - existing HTTP 409 conflict handling is unchanged (draft preserved, saved state shown, revision advanced).

### B. Test Execution Results
- **Focused Entitlement Tests:**
  `cd frontend && npx vitest run components/__tests__/OfferRowEntitlement.test.tsx`
  -> **22 passed (22)**
- **Focused Market Selector Tests:**
  `cd frontend && npx vitest run components/__tests__/MarketPlanSelector.test.tsx`
  -> **14 passed (14)** (9 pre-existing + 5 new catalogue-scoped wording regressions)
- **Focused Suite (Series, Market Selector, OfferRow, Subscriptions):**
  `cd frontend && npx vitest run __tests__/series-page.test.tsx components/__tests__/MarketPlanSelector.test.tsx components/__tests__/OfferRowEntitlement.test.tsx __tests__/subscriptions-market.test.tsx`
  -> **55 passed (55)**
- **Full Frontend Suite:**
  `cd frontend && npm test`
  -> **51 test files passed, 454 passed (454)**
- **Production Next.js Build:**
  `cd frontend && npm run build`
  -> **Build succeeded with 0 errors**.
- **Frontend Linter:**
  `cd frontend && npm run lint`
  -> **Pass (0 errors)**.
- **Backend Entitlement and Surface Tests:**
  `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_api tracker.tests.test_entitlement_exact -v 2`
   -> **45 passed in 2.301s** (ephemeral PostgreSQL).
   `./scripts/test-backend-isolated.sh tracker.tests.test_surface_country_agreement -v 2`
   -> **2 passed in 0.143s** (ephemeral PostgreSQL).

---

## 4. Release-Readiness Package

### What is Verified by Contract, Tests, and CI (contract consistency only):
1. **Contract Validity:** Architectural contract #220 (`2cfa9204`) and adversarial test matrix #223 (`e8280ee9`) are fully met.
2. **Deterministic Mechanics:** Evaluator engine (`backend/tracker/services/entitlement.py`) and API projections (`backend/tracker/services/availability_reads.py`) correctly yield UNKNOWN verdicts for unverified, stale, conflicting, unsupported, or unspecified plans.
3. **Frontend Presentation Integrity:** Components (`OfferRow`, `MarketPlanSelector`, `LastKnownOffers`, `SourceReportedOffers`) communicate the boundaries of declared selections consistently with the reviewed contract and never upgrade unverified observations to watchable states. Unsupported-plan copy is catalogue-scoped and makes no real-world discontinuation claim.
4. **Zero Regressions:** Full test suite (454 frontend unit tests, 47 backend integration/API tests) passes cleanly.

### What Remains Unverified Real-World Evidence:
1. **Real-World Provider & Plan Mapping Corpus:** The engine uses synthetic fixture data and seed mappings. Real-world provider/package mappings — including whether a plan actually exists, which benefits it grants, and which titles it covers in each territory — remain unverified until independently corroborated. A missing catalogue entry is not evidence of real-world discontinuation.
2. **Real-World Playback & Account Status:** As stated in the primary positive caveat: "Account status and playback are unverified." Actual viewer subscription validity and live streaming playback are not confirmed and are out of band. No claim in this report confirms account status or playback.

### Owner Sign-off Required Before Production Activation:
1. **Approval of Final User-Facing Copy:** The repository owner requires human sign-off on the exact user-facing caveat table above prior to production deployment.
2. **Review of Technical Assessment:** Technical review by ChatGPT Web of the wording audit, safety guarantees, and release-readiness package.
