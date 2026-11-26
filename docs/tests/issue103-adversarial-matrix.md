# Issue #103 — Adversarial Test Matrix (Agent B)

| Field | Value |
| --- | --- |
| Repository | `AngeloAndreaIsola/WhereTfDoIWatchIt` |
| Task | #103 FRONTEND / TEST PHASE — Agent B (adversarial test design) |
| Branch | `qa/103-adversarial-tests` |
| Commit under test | `6667ebcae41bfcad18928f190ddf23ef17f9b3e7` (backend PR #222 head) |
| Architecture contract | `2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6` (PR #220 head) |
| Base | `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d` (`master`) |
| Intended PR base | `impl/103-entitlement-backend` |
| Owned files | `docs/tests/issue103-adversarial-matrix.md`, `docs/tests/issue103-adversarial-cases.json` |
| Model / provider | `opencode` / `opencode/hy4-flash-free` |
| Reasoning effort | MEDIUM requested, **UNPINNED** |
| Cost | **COST_UNKNOWN** |
| Transport | **Mocked/deterministic only. Zero live provider, first-party, TMDb, or playback requests were made.** |
| Application code | No `frontend/**` or `backend/**` changes; no production DB, deploy, permission, merge, closure, force-push, or history rewrite |

## 1. Scope and guardrails

This matrix is a specification for a future resolver/API/UI harness. It consumes the #220 contract (`docs/contracts/issue103-entitlement.md`, `docs/contracts/issue103-entitlement-examples.json`) and the #222 implementation (`backend/tracker/services/entitlement.py`, `entitlement_reads.py`, `entitlement_selections.py`, `views.py`, `tests/test_issue103_api.py`). It deliberately does **not** overlap Agent A's frontend work; browser assertions are stated as contracts on the API responses Agent A consumes.

All names, rights, plans, packages and provenance references are synthetic. No raw documents, account identifiers, signed URLs, or private lookup keys appear in the fixtures. The only network activity was `docker compose` pulling/building local test images for the disposable PostgreSQL harness; no application endpoint called an external service.

## 2. Method

- Frozen instant `asOf = 2030-01-15T12:00:00Z`; current observation `observedAt = asOf - 1 day`; rule validity is half-open `[observedAt, ruleUntil)`.
- Merge each case's `viewer` over `viewerDefaults` and each offer over `offerDefaults` independently, **retaining explicit nulls**. All rows share `titleId = 900001`.
- `scope=season:N` uses the parent series ID with season N; `scope=series` uses the parent series with no season; `scope=movie` uses the movie type. A rule with a different scope contributes nothing.
- Locality: `market == current → local`, `market ∈ vpn → vpn`, `market = null → unknown`, otherwise `out_of_reach`.
- Language: `languageMatch: true → true`, `false → false` (**not null**), `null → null`. This distinction changes the title projection (see F03).
- Per-row expectation is `(verdict, primary reason)` before sorting; title expectation comes from `project_title()` over `(entitlement, locality, language)` triples. Permuting offer order must preserve every keyed result and the title state.
- Extended fixture fields (`sourceSupported`, `publicConditionsMet`, `conflicted`, `observedAt`, `startsAt/endsAt`, `ruleObservedAt/ruleFreshUntil/ruleStartsAt`, `portability: "conflicting"`, `noSelection`) are defined in `issue103-adversarial-cases.json → extendedFields`. They add no application code.

## 3. Required-scenario coverage map

| Required scenario | Case IDs | Core expected outcomes |
| --- | --- | --- |
| Base vs premium plan | E01, E02, E03, H01 | base `INCLUDED/PLAN_COVERS_TITLE`; premium without its own rule `UNKNOWN/NO_APPLICABLE_RULE`; premium with `allow:premium` `INCLUDED` |
| Ad-supported exclusions | E04, E05, E06 | paid-ads is still a subscription; `deny:ads` → `BLOCKED/PLAN_EXCLUDED`; a "with ads" label never grants another plan |
| Hosted channel needing base + add-on | E07–E12, H04 | `REQUIRES_ADDON/CHANNEL_NOT_HELD` → `INCLUDED/CHANNEL_AND_BASE_COVER_TITLE`; missing base `BLOCKED/BASE_NOT_HELD`; unanswered child and retired channel `UNKNOWN/HOLDING_UNCONFIRMED`; unresolved package `UNKNOWN/PACKAGE_UNRESOLVED` |
| Same provider, different countries | E13–E17 | separate country rows; no portability → `UNKNOWN/PORTABILITY_UNVERIFIED`; denial → `BLOCKED/REGION_EXCLUDED`; conflicting portability → `UNKNOWN/EVIDENCE_CONFLICT`; VPN on → `watchable_vpn` |
| Unsupported / expired plans | E18, E23, E24, H03 | `UNKNOWN/PLAN_UNSUPPORTED`, `UNKNOWN/NO_APPLICABLE_RULE` at half-open boundary and for a future rule; retired plan persists as `unsupported` |
| Missing selection | E19–E22 | `PLAN_UNSPECIFIED`, `HOLDING_UNCONFIRMED`, `BASE_NOT_HELD` kept distinct |
| Rent/buy vs subscription | E25, E26, H08, H11 | `RENT_OR_BUY/RENTAL` and `PURCHASE`, `requires_payment=true`, `is_entitled=false`; no cross-row borrowing |
| Free with ads | E27–E29 | `FREE_WITH_ADS/PUBLIC_AD_FUNDED`; unconfirmed condition → `UNKNOWN/PUBLIC_CONDITIONS_UNCONFIRMED`; explicit `free` stays `INCLUDED/PUBLIC_FREE` |
| Stale / source-reported evidence | E30–E32, H09, H10 | `SOURCE_REPORTED` (null `is_entitled`), `STALE_EVIDENCE`/`needs_recheck`; late stale evidence cannot move a current verdict |
| Multiple conflicting offers | E33–E35 | `EVIDENCE_CONFLICT` for same-scope allow+deny; season/series rules cannot qualify another typed scope |
| Invalid API responses | E36–E39, H05, H12 | `OFFER_CLASS_UNKNOWN`, `EVIDENCE_UNVERIFIED`, `SOURCE_UNSUPPORTED`, `TERRITORY_UNKNOWN`; 400 on malformed writes; old payload → "Plan access unconfirmed" |
| Concurrent edits / 409 | H02, H06 | distinct concurrent writes → `[200, 409]`; identical concurrent writes → `[200, 200]` with one increment |
| Browser refresh / persisted selection recovery | H01, H02, H03, H07 | GET catalogue reflects saved revision after refresh; stale PUT returns 409 with current selection; per-viewer/market isolation |
| Unknown entitlement, no false-positive badge | E40–E42, H11 | `is_entitled=false`, `is_accessible=false`, `is_user_subscribed=false`, zero watchable/included counts; language mismatch and worldwide distribution never badge watchable |

## 4. Per-row expected outcomes

| Case | Offer 1 | Offer 2 | Title |
| --- | --- | --- | --- |
| E01 | INCLUDED / PLAN_COVERS_TITLE | — | watchable_here |
| E02 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E03 | INCLUDED / PLAN_COVERS_TITLE | — | watchable_here |
| E04 | INCLUDED / PLAN_COVERS_TITLE | — | watchable_here |
| E05 | BLOCKED / PLAN_EXCLUDED | — | available_elsewhere |
| E06 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E07 | REQUIRES_ADDON / CHANNEL_NOT_HELD | — | available_elsewhere |
| E08 | INCLUDED / CHANNEL_AND_BASE_COVER_TITLE | — | watchable_here |
| E09 | UNKNOWN / HOLDING_UNCONFIRMED | — | unknown |
| E10 | BLOCKED / BASE_NOT_HELD | — | available_elsewhere |
| E11 | UNKNOWN / HOLDING_UNCONFIRMED | — | unknown |
| E12 | UNKNOWN / PACKAGE_UNRESOLVED | — | unknown |
| E13 | INCLUDED / PLAN_COVERS_TITLE | UNKNOWN / PORTABILITY_UNVERIFIED | watchable_here |
| E14 | BLOCKED / PLAN_EXCLUDED | INCLUDED / PLAN_COVERS_TITLE | available_elsewhere |
| E15 | BLOCKED / PLAN_EXCLUDED | INCLUDED / PLAN_COVERS_TITLE | watchable_vpn |
| E16 | BLOCKED / REGION_EXCLUDED | — | available_elsewhere |
| E17 | UNKNOWN / EVIDENCE_CONFLICT | — | unknown |
| E18 | UNKNOWN / PLAN_UNSUPPORTED | — | unknown |
| E19 | UNKNOWN / PLAN_UNSPECIFIED | — | unknown |
| E20 | UNKNOWN / HOLDING_UNCONFIRMED | — | unknown |
| E21 | UNKNOWN / HOLDING_UNCONFIRMED | — | unknown |
| E22 | BLOCKED / BASE_NOT_HELD | — | available_elsewhere |
| E23 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E24 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E25 | INCLUDED / PLAN_COVERS_TITLE | RENT_OR_BUY / RENTAL | watchable_here |
| E26 | UNKNOWN / PLAN_UNSPECIFIED | RENT_OR_BUY / PURCHASE | unknown |
| E27 | FREE_WITH_ADS / PUBLIC_AD_FUNDED | — | watchable_here |
| E28 | UNKNOWN / PUBLIC_CONDITIONS_UNCONFIRMED | — | unknown |
| E29 | INCLUDED / PUBLIC_FREE | — | watchable_here |
| E30 | UNKNOWN / SOURCE_REPORTED | — | unknown |
| E31 | UNKNOWN / STALE_EVIDENCE | — | needs_recheck |
| E32 | INCLUDED / PLAN_COVERS_TITLE | UNKNOWN / STALE_EVIDENCE | watchable_here |
| E33 | UNKNOWN / EVIDENCE_CONFLICT | — | unknown |
| E34 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E35 | UNKNOWN / NO_APPLICABLE_RULE | — | unknown |
| E36 | UNKNOWN / OFFER_CLASS_UNKNOWN | — | unknown |
| E37 | UNKNOWN / EVIDENCE_UNVERIFIED | — | unknown |
| E38 | UNKNOWN / SOURCE_UNSUPPORTED | — | unknown |
| E39 | UNKNOWN / TERRITORY_UNKNOWN | — | unknown |
| E40 | UNKNOWN / PLAN_UNSPECIFIED | — | unknown |
| E41 | INCLUDED / PLAN_COVERS_TITLE | — | available_elsewhere |
| E42 | INCLUDED / PLAN_COVERS_TITLE | — | available_elsewhere |

All 42 rows above were executed against the actual `evaluate_entitlement()` / `project_title()` implementation in this worktree; see §7.

## 5. Exact request/response fixtures and assertions

Full machine-readable fixtures are in `issue103-adversarial-cases.json → httpCases`. The decisive ones are reproduced here.

### H01 — catalogue market scoping (`GET /api/providers/?billing_country=NL`)

Seed: provider `synthetic-aster`; plans `base`/`premium` in NL, `paid-ads` in DE; channels `orbit`/`comet` in NL, `foreign` in DE; one legacy `Subscription`.

```json
{
  "slug": "synthetic-aster",
  "plan_options": [
    {"plan_id": "base", "display_name": "base", "class": "base", "billing_country": "NL", "caveat": ""},
    {"plan_id": "premium", "display_name": "premium", "class": "premium", "billing_country": "NL", "caveat": ""}
  ],
  "channel_options": [
    {"package_id": "comet", "parent_provider": "synthetic-aster", "country": "NL", "caveat": ""},
    {"package_id": "orbit", "parent_provider": "synthetic-aster", "country": "NL", "caveat": ""}
  ],
  "unlisted_plan_option": {"plan_id": null, "plan_state": "unsupported", "display_name": "I don't know / my plan isn't listed"},
  "selection": {"holding": "yes", "plan_state": "unspecified", "plan_id": null, "plan_display_name": null, "channels": [], "revision": 0}
}
```

Assertions: DE products absent; legacy row seeds only coarse `holding=yes`; unlisted option has `plan_id=null`; `GET .../billing_country=XYZ` is 400.

### H02 — first write, refresh persistence, 409 and replay

```http
PUT /api/subscriptions/synthetic-aster/markets/NL/
{"holding":"yes","plan_state":"selected","plan_id":"base",
 "channels":[{"package_id":"orbit","holding":"yes"},{"package_id":"comet","holding":"no"}],"revision":0}
```

200 response:

```json
{"holding":"yes","plan_state":"selected","plan_id":"base","plan_display_name":"base",
 "channels":[{"package_id":"comet","holding":"no"},{"package_id":"orbit","holding":"yes"}],"revision":1}
```

- Simulated browser refresh: `GET /api/providers/?billing_country=NL` → `selection.revision === 1`, `plan_id === "base"`, both children sorted by `package_id`.
- Stale write `plan_id:"premium", revision:0` → **409** `{"code":"revision_conflict","selection":{ ...the saved revision-1 body... }}`; client reloads saved selection and keeps draft visible.
- Identical replay of the revision-0 body → **200**, saved `revision` stays 1.
- Two-step-old replay (`revision:0`, changed `channels`) → **409**.

### H05 — invalid writes (all 400, no profile created)

`PUT /api/subscriptions/synthetic-aster/markets/NL/` with each of: missing fields; `selected` + null plan; `selected` without `holding=yes`; `unsupported` + non-null plan; DE plan `paid-ads` on NL; `"Base"`; `"base "`; `"revision":"0"`; `"revision":true`; DE channel `foreign`; duplicate `orbit` declared twice. Expected: 400 for each, `ViewerMarketSelection.objects.exists() === false`.

### H06 — concurrency

Two threads PUT `base` and `premium` at revision 0 → sorted statuses `[200, 409]`, final revision 1. Two threads PUT an identical revision-1 state → `[200, 200]`, final revision 2.

### H08 / H09 / H10 — detail projections

- Mixed: `GET /api/prime/movie/910003/?current_country=NL` → `offers` has subscription `INCLUDED/PLAN_COVERS_TITLE`, `purchase_offers` has rent `RENT_OR_BUY` with `requires_payment=true, is_entitled=false`; DE buy is a separate row; `watchability.verdict = available_here`; `source_package_id` preserved; no `provenance_reference` in payload.
- Source-reported rewrite: `offers=[]`, `purchase_offers=[]`, `watchability.verdict=unknown`, `source_reported_offers[0].entitlement.reason=SOURCE_REPORTED`, `is_entitled=null`, `playback_verified=false` — even when a matching reviewed product exists.
- Stale: `offers=[]`, `watchability.verdict=needs_recheck`, `last_known_offers[0]` has `is_entitled=false` and `entitlement.reason=STALE_EVIDENCE`.

### H11 — composed filters

With NL-included, NL-rent, DE-buy rows: `current_country=NL` and `current_country=NL&included=1` each yield one report row; `watchable=1&access=rent`, `watchable=1&country=DE`, `included=1&access=buy`, `country=NL&access=buy` each yield no row. One row must satisfy every predicate.

### H12 — older payload without a v1 entitlement object

Plan-dependent row displays "Plan access unconfirmed"; no "Included" string or watchable badge may be derived from a legacy boolean; raw offer classes and prices remain visible.

## 6. Findings for the reviewer

| ID | Severity | Finding |
| --- | --- | --- |
| F01 | informational | Identical replay is accepted for the saved revision **and one revision behind** (`data.revision in {revision, revision-1}`); a two-step-old replay is 409. A 200 replay is not a new revision. |
| F02 | informational | A declared child channel whose `(host, billing_country, package_id)` is no longer in the active reviewed channel set is silently dropped from the selection by `entitlement_reads.py:140-143`, producing `UNKNOWN/HOLDING_UNCONFIRMED` rather than `INCLUDED`. This is the desired no-false-positive result but needs an explicit regression assertion (H04). |
| F03 | informational | In the shared harness, `languageMatch:false` is passed as the boolean `false`, not `None`; `project_title()` only treats `None` as missing language info. A harness that coerces `false → None` will mis-score E41 (`available_elsewhere` vs `unknown`). |
| F04 | needs-evidence | No real reviewed source/product mapping or first-party title-plan assertion ships with #222. Every real paid positive stays `UNKNOWN` until independently reviewed evidence exists; no live call was made to probe this. |

No blocking defect was found in the entitlement decision order, the 409/revision rules, or the no-false-positive projections within the mocked scope.

## 7. Verification (commands, counts, results)

| Command | Result |
| --- | --- |
| `python3 -c "import json; d=json.load(open('docs/tests/issue103-adversarial-cases.json')); ..."` | Parsed: 42 evaluator cases, 12 HTTP cases, 4 findings |
| `python3 /var/folders/dg/jx2907jd1cx9q2dhyljb8fxm0000gn/T/opencode/validate_issue103_cases.py` (temporary offline harness importing `tracker.services.entitlement` with `PYTHONPATH=backend`) | **42/42 expected verdict/reason and title outcomes passed; 0 failures**; reverse order title projection stable for every case |
| `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration -v 1` | **Ran 13 tests in 1.702s — OK** (ephemeral PostgreSQL via Docker, read-only `docs/contracts` mount) |
| `git diff --check` | Passed (no whitespace errors) |

The HTTP cases (H01–H12) are specified from the #222 API test surface and were **not re-executed as a new harness** in this worktree; the 13-test isolated run above confirms the underlying behaviors they pin. The temporary validator is not committed because this task owns only the two documentation files.

## 8. Status

`READY_FOR_INDEPENDENT_REVIEW`. No application code was changed. No live provider/first-party/TMDb/playback request, production DB write, deploy, permission change, merge, issue closure, force-push, or history rewrite was performed.
