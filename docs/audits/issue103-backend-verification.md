# Issue 103 backend verification

The implementation is stacked on `arch/103-entitlement-c` at
`2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6` (architecture PR #220).
The underlying master commit is `f9a4431ed4db85c29abd29a28ba4b90b82c32e9d`.

## Selection and read behavior

- The provider catalogue exposes the exact billing market's selectable products,
  the explicit unlisted option and the saved selection. Retired selections keep
  their ID and last catalogue label with an unsupported state.
- Selection writes resolve the viewer on the server. They validate exact plan,
  market and host keys, replace child declarations and serialize on the provider
  row with legacy revocation. A repeat of the last successful write returns the
  saved revision. A stale conflicting write returns 409 with the current state.
- The read adapter binds reviewed product evidence to the canonical offer
  identity, source, source package, variant, plan and market. The scope must
  match the current source observation. Display labels, provider-category
  heuristics, coarse holdings and price observations supply no plan coverage.
- When several declarations exist, a declaration in the current market takes
  precedence. Otherwise a sole declaration or an exact offer-market declaration
  supplies the billing profile. A reachable foreign paid offer still needs a
  rule applicable to the selected billing and offer market pair.
- Prime detail preserves separate stream and transaction sections. It also
  preserves source-reported and last-known sections. Source reports retain the
  legacy null compatibility flags and expose an explicit unknown entitlement
  with `SOURCE_REPORTED`. History cannot contribute to current eligibility.
- List and report filters require a single matching row. Accessibility depends
  on the entitlement result, language and reach. Transactional alternatives
  remain visible with their independent payment flag. VPN reach is potential.
- The migration adds storage only. It leaves existing subscription and
  availability rows intact and supplies no real-world product or rights
  assertions. Introducing reviewed source mappings requires separate evidence.

## Offline checks

| Command | Result |
| --- | --- |
| `cd backend && python -m unittest tracker.tests.test_issue103_fixtures` | 34 cases, 37 verdict/reason expectations, title projections and reverse-order checks passed. |
| `python manage.py test tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration tracker.tests.test_access_resolver tracker.tests.test_entitlement_exact tracker.tests.test_addon_channels tracker.tests.test_watchability tracker.tests.test_ranking_wt013 availability.tests.test_issue97_stage_e_app_replay availability.tests.test_offer_prices tracker.tests.test_watched_list availability.tests.test_justwatch_regression tracker.tests.test_unofficial_api --noinput --parallel 8` | 222 tests passed. |
| `python manage.py test --noinput` | Full backend suite passed all 3,925 tests. |
| `python manage.py migrate --noinput` on an empty disposable PostgreSQL cluster and `python manage.py makemigrations --check --dry-run` | Complete migration graph applied through tracker 0064. Model check reported no changes. |
| `python manage.py test tracker.tests.test_issue103_migration --noinput` via the focused and full suites | Seeded upgrade, data and identity preservation, reapply and legacy read checks passed. |
| `PYTHONPATH=backend:scripts:. python -m pytest -q -rs scripts/test_*.py` | 46 passed, 1 skipped. The optional Stage D check requires retained raw TMDb evidence. |
| `./scripts/test-backend-isolated.sh tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration -v 1` | 13 tests passed with disposable PostgreSQL 16 and the read-only corpus mount. |
| `git diff --check` and `sh -n scripts/test-backend-isolated.sh` | Passed. |

The local database runs used independent loopback PostgreSQL 18.7 clusters and
Django 5.2.19. Offline socket guards blocked outbound connections during local
test execution. Fixture tests also use the backend test script's read-only mount
of the shared architecture corpus.

The acceptance checks exercised revision conflicts, concurrent creation and
replay, multiple viewers and markets, legacy revocation, retired products,
exact variant and hosted-channel matches, portability, stale observations,
source reports, price independence, and bounded bulk reads.

Zero live provider, first-party, TMDb or playback requests were made. Database
writes and migrations were limited to the disposable test environments.

Dispatch route `codex/gpt-6.2-sol`, effort `HIGH`, supplied smoke result
`SOL_HIGH_OK`, cost `COST_UNKNOWN`.
