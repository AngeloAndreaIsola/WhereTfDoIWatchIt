# Issue #103 integration verification

## Reviewed revisions

| Revision | SHA |
| --- | --- |
| `master` | `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d` |
| Architecture PR #220 | `2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6` |
| Backend PR #222 | `6667ebcae41bfcad18928f190ddf23ef17f9b3e7` |
| QA PR #223 | `e8280ee95f4209d738ff47ff7253a5f245de72cf` |
| Frontend PR #224, original | `3135f960a81ade7c0b806bcbd9734c326963d41e` |
| Frontend PR #224, corrected | `67948f5fe965c5606f032d957c2d5757a6e821a6` |
| Integrated application and tests | `d5cf1bee8c36686ac6a6728271eb0504e32e1347` |

The report-only commit follows the tested application tree. The draft PR and
issue #103 carry the full report-bearing head SHA and its hosted run permalink.
[Integration branch CI runs](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/workflows/ci.yml?query=branch%3Aintegration%2F103-combined)

## Findings and fixes

Four mocked assertions reproduced defects on the original frontend branch.
All four passed on PR #224's corrected head and after its forward merge.

| Reproduction | Result after the fix |
| --- | --- |
| Cache catalogue, watchlist and Leaving Soon, then save a plan and return to a cached page. | A successful save invalidates all three caches. Dependent reads use the updated plan without a source refresh from the save. |
| Start with a held plan and set the legacy provider access mode to `none`. | The selector rereads the revoked revision and the dependent caches are invalidated. The bodyless toggle and source deletion use the same invalidation. |
| Delay the NL catalogue response after a save, switch to DE, let DE finish, then resolve NL. | DE stays visible. A superseded response cannot replace its provider rows or plans. |
| Render a legacy subscription offer with `is_entitled=true`, `is_accessible=true` and an Included label, with the entitlement object omitted. | The row shows “Plan access unconfirmed.” The row and movie/series detail cannot mark it Best Match. Raw offer classes and transaction prices remain visible. |

Backend integration assertions exercise the plan update against the same seeded
title. Switching from a reviewed plan to one without a matching rule changes
the detail verdict to unknown and removes the title from Watchable and
Included results. A local included row, a rental and a foreign purchase
cannot satisfy composed filters across rows. The exact-provider, source
package, region, variant, and season tests run alongside those assertions.

The #223 corpus is executed by the shared fixture test. All 42 evaluator cases
and 48 row verdict/reason expectations are checked, including reverse-order
title projections. The architecture corpus contributes 34 additional cases
and 37 row expectations. H01–H11 are exercised through targeted backend API
and resolver tests. H12 is exercised through mocked offer rows and movie and
series detail pages. For H04, the test uses a current DE offer channel and a
retired NL billing declaration, with an explicit portability rule. That
isolates the billing-channel gate and reproduces `UNKNOWN/HOLDING_UNCONFIRMED`.

Source-reported and historical results retain their own sections and cannot
become current entitlement. A source report preserves null legacy access
flags and `playback_verified=false`. Transactions retain a separate payment
action. Empty-state and refresh-on-view regressions remain covered. The
seeded migration test checks legacy linked, name-only, unmapped, add-on and
rent/buy subscriptions, historical offer identity and price, and migration
reapplication. Legacy bodyless toggling and subscription deletion revoke the
plan through the existing routes.

## Local verification

Node 22.23.1, Python 3.12.13, Django 5.2.19 and a dedicated loopback
PostgreSQL 16.16 cluster were used. Frontend commands ran with outbound
network denied. Python tests ran with the outbound socket guard and the
disposable database settings.

| Command | Result |
| --- | --- |
| `cd frontend && npm test -- __tests__/availability-refresh.test.tsx __tests__/series-page.test.tsx __tests__/subscriptions-market.test.tsx __tests__/entitlement-api.test.ts __tests__/catalog-back-navigation.test.tsx __tests__/subscriptions.test.tsx components/__tests__/OfferRow.test.tsx components/__tests__/OfferRowEntitlement.test.tsx components/__tests__/MarketPlanSelector.test.tsx components/__tests__/LastKnownOffers.test.tsx` | 10 files, 97 tests passed. |
| `cd frontend && npm test` | 51 files, 434 tests passed. |
| `cd frontend && ./node_modules/.bin/tsc --noEmit` | Passed. |
| `cd frontend && npm run lint` | Passed. Two existing warnings in `LeavingSoonSection.tsx:172` and `UnofficialCatalogItem.tsx:99`. |
| `cd frontend && NEXT_TELEMETRY_DISABLED=1 npm run build` | Passed. |
| `cd backend && python -m unittest tracker.tests.test_issue103_fixtures` | Both corpora passed, with 76 evaluator cases and 85 row expectations. |
| `cd backend && python manage.py test tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration tracker.tests.test_access_resolver tracker.tests.test_entitlement_exact tracker.tests.test_addon_channels tracker.tests.test_watchability tracker.tests.test_ranking_wt013 availability.tests.test_issue97_stage_e_app_replay availability.tests.test_offer_prices tracker.tests.test_watched_list availability.tests.test_justwatch_regression tracker.tests.test_unofficial_api tracker.tests.test_surface_country_agreement tracker.tests.test_guarded_migrate --noinput` | 237 tests passed. |
| `cd backend && python manage.py test --noinput` | 3,928 tests passed. |
| `cd backend && python manage.py migrate --noinput` | Empty disposable cluster migrated through tracker 0064. |
| `cd backend && python manage.py makemigrations --check --dry-run` | No changes detected. |
| `PYTHONPATH=backend:scripts:. python -m pytest -q -rs scripts/test_*.py` | 46 passed, 1 skipped. The optional #97 Stage D check requires retained raw evidence outside this task. |
| `git diff --check f9a4431..HEAD` and `sh -n scripts/test-backend-isolated.sh` | Passed. |

The first local full backend attempt reported two subtest failures in the
existing compose-root test. macOS resolves `/tmp` to `/private/tmp`, while the
test compares literal paths. The rerun used a canonical `TMPDIR` under the
same disposable cluster directory. The frontend refresh test now waits for
the button to render after the request starts, removing a race exposed by the
full suite.

## Scope and handoff

The dispatch used `codex/gpt-6.2-sol` at `HIGH` with supplied smoke result
`SOL_HIGH_OK` and cost `COST_UNKNOWN`. Zero live provider, first-party, TMDb,
or playback requests were made. Database writes and migrations used disposable
PostgreSQL only. There were no production changes, runtime permission changes,
deployments, merges of GitHub PRs, issue closures, or history rewrites.

No real reviewed paid-plan rights were seeded. The test results certify the
contract on mocked inputs. Real coverage and account playback remain unverified,
and issue #97 stays open for its separate evidence work.
