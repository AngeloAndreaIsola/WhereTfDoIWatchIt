# Issue 103 frontend verification

## Stack and ownership

- Base: `impl/103-entitlement-backend` (PR #222 head
  `6667ebcae41bfcad18928f190ddf23ef17f9b3e7`, stacked on architecture PR #220
  `2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6`, master
  `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d`).
- Head branch: `impl/103-entitlement-frontend-free`. The exact head SHA is the
  one published in the PR and issue #103 comments and in the worker_done mail.
- Files changed: `frontend/lib/api.ts`, `frontend/lib/entitlement.ts`,
  `frontend/components/MarketPlanSelector.tsx`,
  `frontend/components/ProviderGrid.tsx`, `frontend/components/OfferRow.tsx`,
  `frontend/components/CountrySelect.tsx`, `frontend/app/subscriptions/page.tsx`,
  `frontend/components/__tests__/MarketPlanSelector.test.tsx`,
  `frontend/components/__tests__/OfferRowEntitlement.test.tsx`,
  `frontend/__tests__/entitlement-api.test.ts`,
  `frontend/__tests__/subscriptions-market.test.tsx`,
  `frontend/__tests__/catalog-back-navigation.test.tsx` (one pre-existing
  strict-TS cast fixed so `tsc --noEmit` is clean), and this audit file.
  No `backend/**` or contract-doc files were modified.

## What was implemented

- `lib/api.ts` adds the six-value `EntitlementVerdict`, stable `EntitlementReason`
  codes, `OfferEntitlement` (the backend `Entitlement.to_dict()` projection),
  market catalogue/selection types, `fetchProviderMarketCatalogue(billing_country)`,
  and `saveMarketSelection()` with an exact-key `PUT`, optimistic `revision`, and
  a typed `MarketSelectionConflictError` for HTTP 409. An absent entitlement
  object remains explicitly unconfirmed.
- The subscriptions page exposes a billing-country selector, re-reads the
  provider catalogue for that market, and passes the reviewed `plan_options`,
  `channel_options` and saved selection into each card. The grid renders the
  new `MarketPlanSelector` with a holding control (yes/no/unanswered), base /
  paid-ads / premium plan options from the catalogue, an explicit unlisted choice,
  host-labelled channel add-on controls, and the last catalogue label for a
  retired (`unsupported`) selection.
- Saving sends the displayed revision. A 409 keeps the user's draft visible,
  reloads the server's saved selection into a read-only summary, adopts the new
  revision for the next explicit save, and never silently overwrites. A
  successful write only refetches the provider catalogue — no source refresh.
- `lib/entitlement.ts` is a pure presentation map. `OfferRow` renders the
  server's verdict and reason verbatim for all six cases, the declared-selection
  caveat, billing/offer region caveat, and the separate rent/buy action. An
  UNKNOWN verdict is never labelled "Included", never crowned Best Match, and
  its link becomes "Why can't I watch?". Paid "with ads" plan text is distinct
  from the `FREE_WITH_ADS` "Free with ads" label. Source-reported and last-known
  rows keep their existing "Source-reported"/"Unverified" evidence labels.
- No real-world coverage is hardcoded and no entitlement is inferred from a
  provider name, logo, price, category or coarse ownership flag.

## Offline checks

All commands ran from `frontend/` with the local `node_modules` install and
mocked `fetch` transports.

| Command | Result |
| --- | --- |
| `npx tsc --noEmit` | Exit 0, no errors. |
| `npm test` (`vitest run`) | 51 files passed, 430 tests passed, 0 failed. |
| `npm run lint` (`next lint`) | Exit 0; 2 pre-existing warnings (`LeavingSoonSection.tsx:172` exhaustive-deps, `UnofficialCatalogItem.tsx:99` `<img>`). No errors. |
| `npm run build` (`next build`) | Compiled successfully, 16/16 static pages generated; no live API calls during the build. |
| `git diff --check` | Passed (no whitespace errors). |

New focused coverage: 4 API-client tests (market URL, exact PUT body/revision,
409 conflict payload, 400 validation), 6 selector tests (catalogue-only options,
host-labelled channel, exact write, unlisted/null plan, 409 draft preservation
and revision adoption, retired selection, held-base requirement), 11 offer-row
tests (all six verdicts, public free vs paid ads, channel/host explanation,
paid action, unconfirmed row, legacy missing object, caveat, region caveat,
source-reported and last-known preservation), and 2 page tests (billing-market
reload, save without source refresh).

## Constraints and blockers

No blockers. No live provider, first-party, TMDb or playback request was made;
all transport is mocked in tests. No production database, deployment or
permission change, no merge or issue closure, no force-push or history rewrite,
and no raw secrets or evidence were handled.

Dispatch route `opencode / hy4-flash-free` (OpenCode Zen free tier). Reasoning
effort `UNPINNED` — OpenCode cannot pin effort. Cost is free/zero where
measurable (`COST_UNKNOWN` where the platform does not report it).
