# Issue #103 — Repair Acceptance Matrix and Regression Specifications (Agent C)

| Field | Value |
| --- | --- |
| Repository | `AngeloAndreaIsola/WhereTfDoIWatchIt` |
| Task | #103 REPAIR CYCLE — Agent C, regression matrix and test coverage |
| Branch | `repair/103-matrix-c`, from master `8985946b61c24c4b0fa9d6e0b2d19b45fd720cca` (includes merged #220) |
| Independently reviewed report | PR #226, `docs/audits/issue103-independent-review.md` at `c5dbae111431e30665f252f8d8da87d4dcf95d6e` |
| Affected combined head | #225 `bb438d3d1fd6f2ae1da28e9de43dff7febc37e83` |
| Architecture contract | #220 head `2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6`, published at `docs/contracts/issue103-entitlement.md` |
| Model / provider | `opencode` / `opencode/hy4-flash-free` |
| Reasoning effort | MEDIUM requested, **UNPINNED** |
| Cost | **COST_UNKNOWN** |
| Transport | **Deterministic and mocked only. Zero live provider, first-party, TMDb or playback requests. Disposable PostgreSQL via `scripts/test-backend-isolated.sh`.** |
| Application code | None. This artifact owns only `docs/tests/issue103-repair-acceptance.md` and `docs/tests/issue103-repair-cases.json`. |
| Operations | No grant/token, runtime permission change, production DB write, deploy, merge, issue closure, force-push or history rewrite. PR remains DRAFT. |

## 1. Purpose and method

This matrix independently inspects all ten findings in PR #226 (B1–B5 backend, U1–U5 frontend), verifies each against the merged #220 contract, and turns them into executable regression specifications for Agents A/B and an ordered integration acceptance gate list for Agent D. Machine-readable equivalents, including exact seeds, endpoints, observed-vs-expected payloads and assertions, are in [`issue103-repair-cases.json`](issue103-repair-cases.json).

Method: I read the full audit verbatim, read the #220 contract, inspected the exact code paths at the combined head in a detached worktree, confirmed the #223 coverage gap from its diff, and ran the existing issue #103 backend suite on disposable PostgreSQL as a baseline. The baseline passing with all ten defects present is itself the coverage-gap proof. No application file was modified.

### 1.1 Verdict on each finding

All ten findings are **confirmed real at `bb438d3`** and are consistent with the #220 contract:

- The contract requires "Bind the relationship and qualification to the market and package", "Evaluate entitlement on each exact offer before language, reach, ranking or title aggregation", "A signed URL/raw document never enters a public payload", and "When filters are combined, require one row satisfying all requested offer predicates". B1 violates package binding; B2 collapses exact packages before evaluation; B3 breaks single-country agreement; B4 breaks the specified 409 recovery contract; B5 conflates absent and invalid-only filters.
- U1–U5 are presentation-side violations of the same contract's UI section: independent purchase actions, conflict draft retention, retired-plan persistence, evidence gating and reviewed caveats. U3 has a deliberate backend half (the write contract must accept retention of an existing retired ID).

## 2. Coverage gap versus #223 (confirmed)

`git diff --stat 6667ebcae41bfcad18928f190ddf23ef17f9b3e7 e8280ee95f4209d738ff47ff7253a5f245de72cf` changes exactly two documentation files (`docs/tests/issue103-adversarial-cases.json`, `docs/tests/issue103-adversarial-matrix.md`; 865 insertions). #223 specifies 42 evaluator cases and 12 HTTP cases against `evaluate_entitlement()` / `project_title()` and the API response surface.

- #223 starts **after** database reconciliation: it cannot see group-level language borrowed from a second package (B1), a denied second package suppressing a covered one (B2), or the request-to-row country propagation in `_prime_detail_response` / `_build_prime_offers` (B3).
- #223 pins 409/replay/invalid-write behavior (H02/H05/H06) but does not exercise the *stale-revision plus expired-catalogue-item* ordering that produces B4.
- #223 pins evidence partitions at the API (E30–E32, H09/H10) but does not render React, so it cannot catch U1–U5.
- Baseline run at `bb438d3`: `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration -v 1` → **Ran 16 tests in 3.914s — OK** (0 failures, 0 errors, 0 skips). All ten defects were present in that tree.

Conclusion: the ten cases below are additive; none duplicates an #223 case id or assertion.

## 3. Backend regressions (B1–B5)

Common probe base (from `backend/tracker/tests/test_issue103_api.py:187-233`): `OfferProjectionTests` with `ProductFixture`, rollback-only `TestCase`, `tracker.views.request_title_refresh` mocked, viewer `user_id=1`, `Preference(current_country=NL, audio_languages=["EN"])`, movie `tmdb_id=910003`, source `synthetic-rights`, provider `synthetic-aster`, and the `offer()` / `rule()` / `put()` helpers. Frozen instant `2030-01-15T12:00:00Z`. No live transport.

| ID | Severity | Defect and exact repro | Expected PASS after repair | Cross-PR dependency |
| --- | --- | --- | --- | --- |
| **B1** | High | Seed reviewed, allow-NL package A (no audio/subtitles) and assert title `unknown`, A `is_accessible=false`. Add a 3-minute-older **unreviewed** package B with `audio_languages=["EN"]` in the same provider/country/offer class. Read `GET /api/prime/movie/910003/?current_country=NL` and `GET /api/watchlist/report/?watchable=1&audio_languages=EN`. Today A shows `["EN"]`, `is_accessible=true`, title `available_here`, and the report returns the title. Root cause: group key at `reconciliation.py:205-255` excludes source package/variant; `_language_value` at `:460-470,617-639` picks language from any group observation; `_tracker_offer` at `availability_reads.py:131-166` copies it onto the row while entitlement lineage at `entitlement_reads.py:95-143` is package-bound. | Language used to claim accessibility is bound to the **same package and rendition** as the rights. Detail row, title verdict and filtered report agree: A stays empty-language/inaccessible, B cannot confer EN, title `unknown`, filtered report empty. Reverse observation order is stable. | Corrected **#222** head (reconciliation, availability_reads, entitlement_reads) required. #223 cannot catch it. #224 consumes corrected fields; #225 must refresh the integration replay with two packages. |
| **B2** | High | Seed covered A observed 3 minutes ago (`allow:base`), assert `INCLUDED` / `available_here`. Add current package B of the same provider/country/offer class with a deny rule for **B's exact variant**. A's observation and rule remain valid. Read the detail endpoint. Today only B remains as `BLOCKED` and the title becomes `available_elsewhere`. Root cause: one group collapses both packages; `_offer_lineage` (`availability_reads.py:176-216`) selects a single observation via group-level chosen price/URL, so A's allow rule is lost; rows/title are aggregated from that collapse (`views.py:2857-2916,2990-3040`). | Evaluate each eligible package/variant **before** choosing a display row or aggregating the title. Both alternatives stay visible: A remains `INCLUDED`, B is `BLOCKED`, title stays `available_here`. Also assert the reversed observation order yields identical keyed verdicts. | Corrected **#222** head. #223 E33–E35 cover same-scope allow/deny inside one offer, not two packages in one group. #225 integration fixture must preserve both alternatives. |
| **B3** | Medium | One covered NL offer, DE viewing location, VPN disabled. `GET /api/prime/movie/910003/?current_country=DE` → `available_elsewhere` and inaccessible row. Append `&country=NL`: title stays `available_elsewhere` but the same row becomes `local`/accessible and qualifies for Best Match. Root cause: `_prime_detail_response` uses `_active_country` (`views.py:2789-2804,2919-2934`) while `_build_prime_offers` prefers the `country` query param (`views.py:2882-2886`); rows and title assembled at `:2995-3025` disagree. | Pass one effective viewing country from `_prime_detail_response` into `_build_prime_offers` and use it for every row and the title, or reject contradictory parameters consistently. Row locality, accessibility and title verdict never disagree. | Corrected **#222** head. #224 renders the locality badge/Best Match; #225 must replay a consistent response. Keep `test_surface_country_agreement.py` green (movie 615457 guard). |
| **B4** | Medium | Save base at revision 1, premium at revision 2; let base expire; submit the old but structurally valid base draft at revision 1 to `PUT /api/subscriptions/synthetic-aster/markets/NL/`. Today the server returns **400** during catalogue membership validation before the stale-revision check at `entitlement_selections.py:183`, so the client loses the 409 recovery path. | After structural validation and replay handling, return **409** with `{"code":"revision_conflict","selection":<saved revision-2 selection>}` **before** validating current catalogue membership. Retain 400 for invalid choices at the **current** revision and preserve identical replay semantics (200, no increment). | Corrected **#222** head. #224 `MarketPlanSelector.tsx` consumes the 409 body (shared with U2). #223 H02/H05/H06 must still pass. |
| **B5** | Low | With one synthetic official result, `GET /api/catalog/?audio_languages=not-a-language` returns it just like `?audio_languages=EN`. Root cause: `filtering.py:319-323` turns invalid-only input into an empty set; `:342-343` treats `not audio_filter` as "no predicate". | Distinguish an absent filter from an active filter with zero recognized codes: return a validation error or zero matches, consistently for official and unofficial rows. Absent param behavior is unchanged; invalid-only input never widens results. | Corrected **#222** head. #224 filter/count UI must not double-count when the predicate returns zero. |

## 4. Frontend regressions (U1–U5)

All UI probes use the existing mocked Vitest render setups; no network. Assertions are on rendered output, not just API payloads.

| ID | Severity | Defect and exact repro | Expected PASS after repair | Cross-PR dependency |
| --- | --- | --- | --- | --- |
| **U1** | Medium | Render TV detail with `offers=[]` and one current `purchase_offers` rental; after series metadata loads the provider/action are absent. The `purchase_offers` block at `frontend/app/tv/[tmdb_id]/page.tsx:630-741` is nested inside the `data.offers.length > 0` branch (movie detail renders it independently). | Render TV purchase actions independently of `offers`; cover transaction-only series **and** seasons. Empty-offers state still shows `AvailabilityState`; no Best Match on a transaction-only row; `requires_payment` preserved. | Corrected **#224** head. #225 integration replay must include a transaction-only series fixture. |
| **U2** | Medium | Base at revision 3; draft ads; save → 409 carrying premium at revision 4. Draft still shows ads and the conflict summary is visible. Rerender with the revision-4 catalogue response as subscriptions polling does: input switches to premium and the summary disappears. Root cause: `MarketPlanSelector.tsx:85-101` re-seeds the draft on every `selectionIdentity` change (`:63-72`). | Track the conflict draft independently of catalogue updates for the same market/provider. Refresh the saved comparison while preserving unsaved edits until the viewer saves or discards. A non-conflict catalogue refresh still updates the draft. | Corrected **#224** head plus corrected **#222** 409 body. #223 H02 must still pass. |
| **U3** | Medium | Load a retired selected plan with saved `plan_id=base`, display name `base`, `plan_state=unsupported`. Change only a channel and save. UI sends `plan_state=unsupported, plan_id=null`; replay through the real API changes the saved plan ID and display name to null. Root cause: `MarketPlanSelector.tsx:33-60` (`retiredChoice()` vs `planInput()`) and `entitlement_selections.py:158-194` (writes null plan_id directly; `selection_data` at `:47-63` derives display name only from an active plan). | Preserve the existing retired ID on unrelated edits. Coordinate a **narrow backend exception** retaining that profile's existing retired selection, or require an explicit replacement before saving. Keep rejecting newly submitted arbitrary IDs. Re-read shows `plan_id=base`, display name `base`, state `unsupported`. #223 E18/E23/E24 stay green. | Requires **both** corrected #224 and corrected #222 — the only deliberate two-PR contract change. #225 integration fixture must carry the retired display name across a save. |
| **U4** | Medium | Render an offer with outer `availability_evidence=stale` and inconsistent nested `INCLUDED / PLAN_COVERS_TITLE`. Best Match is suppressed but "Included with your selected plan" still appears beside the stale badge. Root cause: `OfferRow.tsx:87-96` gates only positive/bestMatch; `:240-268` renders `verdictPresentation.label` beside the unverified badge without an evidence gate. | Apply the evidence gate to the verdict badge **and its explanation**. Cover contradictory stale and source-reported payloads as well as the already-tested missing legacy entitlement. Verified `INCLUDED` rows keep their badge. | Corrected **#224** head. #223 E30–E32/H09/H10 cover the API partition, not the rendered badge. |
| **U5** | Low | Provide a hosted channel with nonempty market-specific `channel_options[].caveat`. The selector renders host and country but omits the caveat. Backend already emits it (`entitlement_selections.py:81-88`); `MarketPlanSelector.tsx:214-217` renders only the plan caveat. | Render the reviewed caveat beside its channel control and assert its presence; scope it to its channel/country; leave plan caveat rendering unchanged. | Corrected **#224** head only; no backend change expected. |

## 5. Backend/frontend interactions that need special care

### 5.1 B1/B2 projection lineage (database → read adapter → resolver → API → UI)

The two high-severity defects travel the entire read chain, so a fix at only one layer will leave a visible contradiction:

```
Offer rows (provider_content_id, source package, variant)  reconciliation.py group key
  -> ReconciledOffer.audio_languages / chosen price / single lineage
    -> availability_reads._tracker_offer (row language, lineage) and _offer_lineage (package/variant)
      -> entitlement_reads.EntitlementContext.evaluate (rules keyed by lineage.package_id/variant_id)
        -> classify_offer / resolve_title (verdict, reason, is_entitled, is_accessible)
          -> _prime_detail_response offers / title / watchability + filter projections
            -> OfferRow badge, Best Match, watchable count, "Included" filter
```

Required end-to-end invariants for Agent D: (a) row language and row rights share one package/variant; (b) a deny on package B never removes an allow on package A and vice versa; (c) the title verdict, `watchable=1` report, `included=1` report and catalogue count all derive from the same surviving accessible row; (d) permuting offer insertion order does not change any keyed verdict, reason or title state. A backend-only fix must be checked through the rendered row, and a UI-only fix must be checked against the real API replay.

### 5.2 U3 retired-plan contract (catalogue → draft → write → re-read)

U3 is the only finding that intentionally changes a backend write rule. The #220 contract says "Unsupported plan means a declared plan has no supported mapping for the market/date. Keep these reasons distinct, including after a formerly supported product retires" and "Use null for an unlisted plan rather than accepting arbitrary new catalogue IDs." The repair must reconcile those two clauses:

1. `GET /api/providers/?billing_country=NL` emits `plan_options[]` without the retired plan, plus `selection.plan_state=unsupported`, `plan_id=base`, `plan_display_name=base`.
2. `draftFromSelection` encodes the retired ID as `retired:<id>`; `planInput` must send it back on an unrelated edit instead of `null`.
3. `replace_selection` must accept that exact existing ID for this profile/market while continuing to reject any **new** arbitrary ID, preserving `plan_state=unsupported` and the display name.
4. Any later catalogue poll must not clear the conflict draft (U2) or the retired selection (U3).

Guard: the exception must be profile-scoped and additive; no schema, backfill or legacy-table column may be introduced (migration guard S6).

## 6. Re-regression sentinels — can a repair recreate an earlier defect?

| Sentinel | Existing guard | Risk introduced by the repairs |
| --- | --- | --- |
| S1 | `backend/tracker/tests/test_entitlement_exact.py`, #223 E01–E42 | Package/variant binding changes must not restore substring/prefix/display-name matching (#57) or let a rule/price move between variants (#198/#205). |
| S2 | `backend/tracker/tests/test_surface_country_agreement.py` | The B3 country fix must keep detail, watchlist, catalog and homepage on one effective country (movie 615457). |
| S3 | `test_issue103_api.py`, #223 H02/H05/H06 | B4's earlier 409 must not break identical replay (200, no increment), invalid-choice 400s, or concurrent `[200,409]` outcomes. |
| S4 | `test_detail_availability_state.py`, #223 E30–E32/H09/H10 | B5's filter fix must keep current/source-reported/last-known partitions and stale-only `needs_recheck`. |
| S5 | `frontend/__tests__/availability-status-panel.test.tsx`, `error-states.test.tsx`, `subscriptions-market.test.tsx` | U1–U5 must keep reload, cache invalidation, market-switch ordering, missing-entitlement labels and ordinary 409 recovery. |
| S6 | `test_issue103_migration.py` | U3's backend exception must not add schema, backfill or legacy-table columns; additive storage and read rollback stay intact. |

## 7. Integration acceptance gates for Agent D

Execute in order on the corrected combined tree. All commands use disposable PostgreSQL or mocked transport; keep the PR DRAFT until ChatGPT Web's final review.

| Gate | Command | Pass criterion |
| --- | --- | --- |
| 1 — contract anchor | `git merge-base --is-ancestor 2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6 <corrected-222-head>` and verify final base `8985946` | #220 architecture head is an ancestor; final PR targets master. |
| 2 — backend full | `./scripts/test-backend-isolated.sh --noinput` | No regressions against the 3,928-test baseline recorded in #226. |
| 3 — B1–B5 focused | `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_api tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_migration tracker.tests.test_entitlement_exact -v 2` | All cases pass including the new two-package, country, stale-revision and invalid-filter probes and reversed-order variants. |
| 4 — #223 corpus | `python -m unittest tracker.tests.test_issue103_fixtures` and `python -m pytest -q -rs scripts/test_*.py` | 76 cases / 85 row verdict-reason pairs and 46 script tests with the one documented optional #97 restricted-evidence skip. |
| 5 — U1–U5 frontend | `cd frontend && npm test && ./node_modules/.bin/tsc --noEmit && npm run lint && NEXT_TELEMETRY_DISABLED=1 npm run build` | All tests/typecheck/build pass; lint warnings limited to pre-existing `LeavingSoonSection` / `UnofficialCatalogItem`. |
| 6 — migration/drift | `cd backend && python manage.py makemigrations --check --dry-run && python manage.py migrate --noinput` | No model drift; migration 0064 applies on empty and upgraded disposable databases. |
| 7 — combined refresh | `git diff --check <master> <corrected-combined-head>` and hosted CI | Integration-only fixtures preserved; all three hosted jobs green; PR still DRAFT. |

## 8. Verification performed for this artifact

| Command | Result |
| --- | --- |
| `gh pr view 226 --json ...`; `gh api .../contents/docs/audits/issue103-independent-review.md?ref=c5dbae...` | Full report and pinned heads read; ten findings enumerated. |
| `git fetch origin pull/225/head` / `pull/223/head`; `git worktree add --detach <temp> bb438d3` | Combined head inspected without touching the owned worktree. |
| `git diff --stat 6667ebc e8280ee` | #223 changes exactly 2 documentation files (865 insertions); coverage gap confirmed. |
| `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration -v 1` | **Ran 16 tests in 3.914s — OK**; 0 failures, 0 errors, 0 skips. All ten defects coexist with a green baseline, proving the gap. |
| Source inspection at `bb438d3` of `reconciliation.py`, `availability_reads.py`, `entitlement_reads.py`, `entitlement_selections.py`, `views.py`, `filtering.py`, `frontend/app/tv/[tmdb_id]/page.tsx`, `frontend/components/MarketPlanSelector.tsx`, `frontend/components/OfferRow.tsx` | Ten findings independently confirmed; exact line evidence recorded in the JSON cases. |
| `python3 -m json.tool docs/tests/issue103-repair-cases.json` and `git diff --check` | See the handoff comment on the PR and issue #103 for the final counts. |

The audit's own probes reproduced B1–B5 and U3 against the real API on disposable PostgreSQL and five new UI expectations failed; this artifact does not duplicate those executions, it pins them as acceptance cases. No application code, evidence file or raw secret was added.

## 9. Status and handoff

`READY_FOR_INTEGRATION`. The matrix and machine-readable cases are complete and additive to #223. No live provider/first-party/TMDb/playback request, grant/token, permission change, production DB write, deploy, merge, issue closure, force-push or history rewrite was performed. ChatGPT Web retains final review and merge authority. Issues #103 and #97 remain open.
