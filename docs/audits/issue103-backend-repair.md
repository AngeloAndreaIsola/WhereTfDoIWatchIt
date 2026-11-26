# Issue 103 backend repair

PR #222 started at `6667ebcae41bfcad18928f190ddf23ef17f9b3e7` on PR #220's head
`2cfa9204a8d5c8c9ebd85ef4205b68eee81fb5f6`. Master at dispatch was
`8985946b61c24c4b0fa9d6e0b2d19b45fd720cca`. The independent report was read at
`c5dbae111431e30665f252f8d8da87d4dcf95d6e` before edits.

## Reproduction and repair

The database-backed regressions ran against the starting implementation before
any production code edits. The API suite collected 18 tests. Seven new tests
failed, with 14 failing assertions across subtests, zero errors and zero skips.

| Finding | Starting behavior | Repaired behavior |
| --- | --- | --- |
| B1 | Covered package A took English audio from older, unreviewed package B. A became accessible, the title was `available_here`, and `watchable=1&audio_languages=EN` returned it. | Each current streaming rendition uses its own observation and language data. A retains coverage and unknown language, B retains English and unconfirmed access, and the title is `unknown` and absent from the filtered report. |
| B2 | A newer blocked package hid a covered package and removed the title from the filtered report. Reversing freshness hid the blocked package. | Both separately evaluated alternatives appear in detail with `INCLUDED` and `BLOCKED` in either order of freshness. The detail verdict and filtered report select the covered package. The same-observation test also varies language on a second rendition with its own coverage rule. |
| B3 | `current_country=DE&country=NL` with VPN disabled produced `available_elsewhere` for the title and `local` for its NL detail row. | Typed detail uses DE for both the title and detail rows. `country` also works as a fallback when `current_country` is absent. With no selected country, ranking and the source summary retain their existing defaults. |
| B4 | An expired plan submitted at an old revision returned 400. | After structural validation and replay detection, stale writes return 409 with the saved selection. An invalid product at the current revision and a malformed revision still return 400. |
| B5 | Invalid-only language filters kept the official result in both catalogue endpoints. | Nonempty filters with zero recognized codes return zero results for official and unofficial catalogue entries. Mixed recognized and unrecognized values still use the recognized codes. |
| U3 | The saved retired ID could only be read. An update submitting the same ID as unsupported returned 400, and the explicit null option cleared the selection. | Sending `plan_state=unsupported` and the same saved retired `plan_id` with `holding=yes` preserves the product and label while updating channels. Replaying the edit is idempotent. The API rejects arbitrary new unsupported IDs and accepts an explicit null to select the unlisted option. |

The compatibility projection expands verified streaming variants from the
source's current snapshot. Coverage keys, language, availability windows and
price are scoped to the individual rendition. An older snapshot cannot supply
the package for a newer observation. When several renditions share one source
observation, each needs its own declared languages. A single-rendition snapshot
can use its own source observation's language fields when the snapshot omits
them. Scope expansion runs after the existing bounded reconciliation reads.

## Verification

Python 3.12.13, Django 5.2.19, and a fresh PostgreSQL 16.16 UTF-8 cluster on
loopback. Application checks ran with an empty inherited environment, blank
provider credentials and a loopback-only Python socket guard. Typed detail
regressions mocked the title refresh entry point. Stage E used its existing
synthetic transport fixtures. Commands beginning with `python manage.py` ran
from `backend/` with `DJANGO_DEBUG=1`, `POSTGRES_DB=wtdwi_test`,
`POSTGRES_USER=wtdwi_test`, `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=56243` and
`WTDWI_DISPOSABLE_TEST_DB=1`. The final runs set `TMPDIR` to an absolute
directory without a macOS `/tmp` symlink.

| Command | Result |
| --- | --- |
| `python manage.py test tracker.tests.test_issue103_api --keepdb --noinput -v 2` on `6667ebc` with the new tests | 18 collected, 11 passed and 7 failed across 14 assertions. Zero errors and skips. |
| `python manage.py test tracker.tests.test_issue103_fixtures tracker.tests.test_issue103_api tracker.tests.test_issue103_migration tracker.tests.test_access_resolver tracker.tests.test_entitlement_exact tracker.tests.test_addon_channels tracker.tests.test_watchability tracker.tests.test_ranking_wt013 availability.tests.test_issue97_stage_e_app_replay availability.tests.test_offer_prices tracker.tests.test_watched_list availability.tests.test_justwatch_regression tracker.tests.test_unofficial_api tracker.tests.test_reconciliation_read_path tracker.tests.test_prime_endpoints tracker.tests.test_guarded_migrate --noinput -v 2` | 292 passed, zero skips, including the seeded 0063-to-0064 migration and reapplication. |
| `python manage.py test --noinput` | 3,932 passed, zero skips or failures, in 132.507 seconds. |
| `python manage.py migrate --noinput` followed by `python manage.py makemigrations --check --dry-run` | The empty cluster's application database applied through `tracker.0064_market_entitlements`. No model drift. |
| `PYTHONPATH=<socket-guard>:backend:scripts:. python -m pytest -q -rs scripts/test_*.py` | 46 passed, 1 skipped. The optional #97 Stage D check needs retained raw TMDb evidence. |
| `git diff --check` and `sh -n scripts/test-backend-isolated.sh` | Passed. |

An initial focused run of the first 14 modules used `--keepdb` after a
transaction test flushed migration seeds. It had 14 missing-country errors and
one provider seed assertion failure. The fresh run of those modules passed all
244 tests. The first serial full run lost its temporary environment before the
result was captured. The resumed `python manage.py test --noinput --parallel 8`
run aborted after the test environment's host list omitted the Compose `backend`
alias and the parallel runner could not serialize the failure. A serial run with
the default host configuration reached all 3,932 tests and had five failures.
Three exposed the default-market detail regression, now covered by the expanded
focused run. Two came from the macOS symlink in the temporary directory path.

Route `codex / gpt-6.2-sol`, effort `HIGH`, cost `COST_UNKNOWN`. Zero live
provider, first-party, TMDb or playback requests. All data writes and migrations
were confined to disposable PostgreSQL. No grants, tokens, runtime permission
changes, deploys, production access, merge or closure were performed.
