# Entitlements, language and territorial access audit

Baseline: `4332732977ac414b051c0e2308e142af9c5803af`. No user preference/subscription rows were inspected. Findings below are from code paths and existing deterministic tests; no live service catalogue was consulted.

## Decision path

`tracker/services/access.py::resolve_title` takes canonical current offers → `build_user_access` or explicit access → `build_reach` → `classify_offer` → shared ranking. Watchlist/report and detail use this resolver. `tracker/services/score.py::score_offer` applies audio/subtitle rules and returns `is_match=None` if the offer has no language fields. Ranking orders classified evidence; it cannot add entitlement or make language match.

### Subscription and access model

- Subscription rows are bucketed by explicit `access_mode`: included, add-on, free, rent/buy, none. Unknown mode grants nothing (`access.py:329-368`).
- Provider matching uses exact normalized slug equality plus curated, explicit aliases; no substring match (`:128-263,282-304`). Prime channel/store and Viaplay Sports identities are intentionally separate from base subscriptions.
- Add-on provider with `addon_parent_slug` requires both base subscription and add-on channel entitlement (`:586-617`).
- `rent`/`buy` are excluded from accessible included offers; they become purchase-only only when the user declares a rent/buy account. Confirm UI labels explain that account requirement.
- Enabled BVOD/AVOD/FAST providers are automatically added to the free bucket; enabled SCRAPER category rows are auto-added as included (`:370-396`). Verify that provider category data is correct because this is a positive entitlement shortcut.
- Free and free-with-ads offers can be accessible without a paid subscription when the provider/category is known and country/language gates pass.

## Required scenario audit

| Scenario | Code expectation | Baseline conclusion |
|---|---|---|
| Netflix offer, user not subscribed | Provider may be present, but `is_entitled=False`; not watchable included | Exact resolver enforces this. Existing test coverage should be kept in regression suite. |
| Provider missing from registry | Unmapped source ID is skipped during persistence; classifier will not infer a user subscription | Fail closed at ingest; registry absence does not silently grant access. |
| Free official broadcaster | Free offer can be watchable without paid subscription if provider category/access is free and territory permits | Supported via access modes/auto-free categories; title-level free assertion still needs source evidence. |
| Paid add-on | Base subscription and explicit add-on required | Enforced by `addon_parent_slug`. |
| Rental/purchase | Separately labeled; not included subscription | Enforced by access type. |
| Unsupported country | Offer in nonlocal country is elsewhere unless explicit VPN allows | Enforced for known country; Watchmode country scope is NL/CA/JP; adapter rejects unsupported request. |
| VPN disabled | Foreign offer is unreachable | Enforced if no configured regions and VPN disabled. Explicit candidate regions themselves activate VPN reach (`access.py:435-438`), even with flag false; this is an intentional but surprising rule. |
| Unknown audio/subtitles | Language result is unknown and cannot qualify as accessible | `score_offer` returns `None`, and `classify_offer` requires truthy match (`score.py:164-168`; `access.py:619-624`). |
| Language mismatch | Not eligible; source ranking cannot turn mismatch into match | Resolver classifies before sorting. |
| Stale/uncertain evidence | Not in current offers; displayed as last-known/recheck only | `availability_reads.py:90-103`; availability report uses freshness state. |
| Offer country absent | Should be unknown without source assertion of global territory | Baseline mapped blank to `ANYWHERE`, hence reachable (A1, P0); audit fix now returns unknown and does not claim local availability. |

## Cross-surface consistency

- Watchlist passes `refresh_states_for` to `apply_composable_filters` and the resolver (`tracker/views.py:1380-1393`).
- Detail uses the same resolver but currently omits `refresh_state` (`tracker/views.py:2524-2545`). Empty offers can therefore show unknown in detail and unavailable/checking in watchlist (A4).
- The detail endpoint includes both `watchability` and a source-level `availability` report. Frontend uses the former for the user verdict; the latter explains evidence state.
- Catalog uses the canonical catalogue read boundary and may filter by provider/subscription/language; do not equate catalogue membership with watchability. A separate endpoint/API comparison still needs live endpoint tests after fixes.

## Verified vs inferred

This pass verified code semantics for exact subscription matching, add-on base requirements, free/rent/buy type separation, language-unknown behavior, and shared ranking/access classification. It did not inspect a real user's subscription records, query provider title pages, or confirm that source metadata is correct for any particular title. Actual live watchability remains unverified.
