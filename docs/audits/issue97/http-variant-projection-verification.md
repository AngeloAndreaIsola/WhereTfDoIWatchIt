# Issue #97 HTTP variant projection verification

The integration review found that the ordinary movie availability route copied
stored variants into both price fields. An unselected variant could expose a
signed link or private provider identifiers. The review used synthetic evidence
and observed zero live exposure. [Integration review](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/00c8e9bd/docs/audits/issue97/final-completion-integration-review.md#correctness-and-publication-boundary) · [Issue #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97) · [Draft PR #198](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/198)

Current, source-reported and last-known offers now project nested variants
onto provider, country, offer class, quality, amount, currency and a display
link. HTTP(S) display links retain the destination path with query and fragment
removed. Links with embedded credentials or unsupported schemes are omitted.
The same link rule applies when a variant supplies the offer's main display
link. PostgreSQL retains the complete variants for reconciliation. Regression
assertions cover the selected amount, rendition and scalar provenance, stable
offer, price and evidence IDs, and historical price behavior.

The offline replay exports provider, country, offer class, quality, amount and
currency for title, season and unresolved variants. It deduplicates on full
internal observations before projection. The provider mapping audit publishes
only an aggregate digest and count for price rows.

## Local verification

Runs used Python 3.12.16, Django 5.2.19 and disposable PostgreSQL 16.15.
The database used tmpfs on an internal Docker network with zero published
ports. Provider fixtures used mocked or injected transport. The replay command
also ran successfully with networking disabled and its JSON variant fields
were checked for title, season and unresolved rows. There were zero live
provider requests.

- Focused suite including the PostgreSQL migration 0018 regression. **316 passed** in 17.373 seconds.
- Full backend suite. **3,802 passed** in 188.006 seconds. Both suites had zero failures, errors or skips.
- Migration drift check. **Passed**, with no changes detected.

```sh
python manage.py test --noinput -v 1 \
  availability.tests.test_issue97_collection \
  availability.tests.test_justwatch_integrity \
  availability.tests.test_justwatch_regression \
  availability.tests.test_offer_prices \
  availability.tests.test_reconciliation \
  availability.tests.test_justwatch_reviewed_transport \
  availability.tests.test_offerprice_migration \
  availability.tests.test_audit_provider_mapping \
  tracker.tests.test_issue97_availability_contract \
  tracker.tests.test_title_availability_cache \
  tracker.tests.test_reconciliation_read_path \
  tracker.tests.test_leaving_soon \
  tracker.tests.test_stale_offer_semantics \
  tracker.tests.test_bulk_refresh \
  tracker.tests.test_refresh_worker \
  tracker.tests.test_season_availability \
  tracker.tests.test_justwatch_title_level
python manage.py test --noinput -v 1
python manage.py makemigrations --check --dry-run
```

## Hosted CI

The prior hosted run had three jobs stopped before their first step by the
account billing or spending limit. The local results above cover the remediation.
Keep the PR draft for independent review, then rerun hosted checks after the
billing owner clears the block. [Prior hosted run](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45569700694)
