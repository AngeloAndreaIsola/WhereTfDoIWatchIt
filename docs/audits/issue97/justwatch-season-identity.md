# JustWatch season identity and historical mappings

See [issue #97], [issue #203], and [draft PR #205].

## Discovery and offers

A JustWatch catalogue edge can identify a native season or episode and report its own TMDb ID without identifying the parent series. Valid child-only discoveries remain in `ProviderContent`, with the provider-native ID, typed slot and reported external IDs. They produce no exact canonical mapping or offer. A season discovered under a matched series uses that series' TMDb ID as its canonical key, including season 0. The child's own TMDb ID stays in discovery metadata.

## Season reads with an existing child-keyed mapping

The original draft wrote each season offer under the parent series ID. When a mapping already pointed at the child with equal confidence, refresh kept both that mapping and the parent-keyed offer. Reconciliation treated the differing IDs as a conflict, so the offer was absent from the season response even after a successful title check.

An exact typed JustWatch series match now records the series and child TMDb IDs together in the linked native season's `identity_evidence`. Reconciliation can recognize an existing JustWatch `EXTERNAL_ID` season mapping as the reported child of the offer's series when the provider, native ID, type and slot also agree. The offer needs a successful targeted run and still goes through the usual freshness and title-check rules. A catalogue-only refresh cannot supply this parent assertion. Manual mappings and unrelated or competing active claims still control conflict handling. Refresh retains the stored mapping, including its target and revision, and uses the same offer and discovery identities.

## Owner decision for existing data

The regression uses disposable data. The number of affected serving rows and the condition of their provenance are **NOT PROVEN**. Historical rows without corroboration can still be withheld. No data migration or repair command is included. The owner must decide whether to leave historical mappings in place for ordinary authorized verification or approve a separate read-only inventory and reviewed repair plan. Any repair would need independently verified parent/child/slot evidence for each candidate, explicit preservation of manual mappings, and approval of the exact mutation and rollback scope.

[issue #97]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97
[issue #203]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/203
[draft PR #205]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/205
