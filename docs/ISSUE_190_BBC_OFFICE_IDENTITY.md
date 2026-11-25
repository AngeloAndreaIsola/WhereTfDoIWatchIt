# BBC iPlayer Office identity quarantine (#190)

The November 22 disposable snapshot associated GB free offer `3504` with
TMDb `(series, 2316)`, the US *Office* (2005). The URL pointed to the BBC brand
`b00jd68z`, the UK *Office* (2001), TMDb `(series, 2996)`. The published replay
returned an empty current array and included the link in last-known history.
The captured BBC description identifies David Brent and Wernham Hogg in
Slough. The US series identity identifies Michael Scott and Dunder Mifflin in
Scranton. [Issue #190](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/190) ·
[Pinned replay report](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/077bcc78c539cda1294610ad1f4a1e1e955affe2/docs/audits/stage2b/issue97-final-verification-report.md) ·
[Preserved row-level evidence](audits/stage2b/issue97-provider-reconciliation.csv#L216) ·
[Reviewed UK mapping](WATCHMODE_REPAIR_READINESS_REPORT.md#5-mapping-remediation)

## Code path and evidence limits

1. The [BBC collector](../backend/availability/sources/bbc_iplayer.py#L437)
   supplies the brand PID, `series` type, title and no first-air year or TMDb
   ID. Its [series, season and episode offers](../backend/availability/sources/bbc_iplayer.py#L480)
   all use the brand PID for mapping. Season and episode records retain their
   individual scope and links.
2. [Refresh](../backend/availability/services/refresh.py#L628) looks up an
   existing `(provider, provider_content_id)` mapping and, by default, tries the
   local matcher. The [title-only fallback](../backend/availability/services/matching.py#L65)
   accepts a sole same-title series at confidence `0.7` under `title_year`,
   even though it has no year evidence. This creates the wrong mapping if the
   only local homonym is series `2316`. The repository
   [retains equal-confidence conflicting associations](../backend/availability/persistence/repositories.py#L974),
   and refresh [reuses the persisted mapping for every offer with that PID](../backend/availability/services/refresh.py#L684).
   The offline regression reproduces both paths. The existing
   [remediation fixture](../backend/availability/tests/test_remediate_mapping_audit.py#L189)
   identifies mapping `399` as a `title_year` mapping at `0.7`. The exact
   historical writer, the contents of its local catalogue at the time, and any
   serving write that followed are **Not proven**.
3. The [offer repository](../backend/availability/persistence/repositories.py#L380)
   reads the stored typed TMDb identity. Before the patch, the wrong offer and
   wrong mapping agreed, so [reconciliation](../backend/availability/services/reconciliation.py#L264)
   had no conflict to disqualify that observation. The tracker
   [splits effective offers by evidence freshness](../backend/tracker/services/availability_reads.py#L92)
   and the [availability endpoint](../backend/tracker/views.py#L616) serializes
   both arrays. An otherwise valid stale observation surfaced as last-known.

## Scoped code change

The [reviewed identity check](../backend/availability/services/identity_policy.py#L21)
applies to `bbc_iplayer/b00jd68z`. Reconciliation records a mapping conflict
and excludes mismatched observations before combining availability, links or
other offer details. A correctly keyed UK series, season or episode row still
goes through the normal availability-window and freshness checks. The reviewed
programme identity takes precedence over conflicting stored claims. The BBC
catalogue refresh retains provider-native discovery and requires an existing
mapping to series `2996` before projecting offers from that brand. The change
preserves historical rows and evidence in place. The #97 freshness display
contract uses the same existing tracker adapter.

The tests include a synthetic US BBC programme control to exercise isolation in
both directions. A real BBC programme for the US edition is **Not proven**.
The raw repositories and provider catalogue inventory remain useful for
inspecting stored claims, including the old association.

## Separately gated serving-data follow-up

1. On an authorized read-only clone, inventory `(bbc_iplayer, b00jd68z)` and
   both typed series identities. Revalidate the current mapping provenance and
   audit series, season and episode scopes, links, attached evidence and prices.
   The older [fixed fixture plan](../backend/availability/services/mapping_remediation.py#L172)
   names mapping `399`, parent `3504`, seasons `3505` and `3512`, and episodes
   `3506–3511` and `3513–3518`. The supplied report proves the parent link in
   its sampled display. Which descendants exist or remain wrong in the serving
   database is **Not proven**.
2. Use the [read-only impact planner](MAPPING_CORRECTION_IMPACT_PLANNER.md)
   to review exact identities, dependencies, collisions and the replacement
   mapping. Obtain explicit owner and data-integrity authorization for a
   separately reviewed operation. Preserve raw offer/evidence provenance.
   Where a row still needs quarantine, record that decision separately from
   the historical observation. Any approved new UK availability observation
   should have independent evidence and its own typed identity.
3. Validate both editions and all inventoried descendant scopes against the
   authorized clone before a separately approved serving operation. Record the
   expected preconditions and before/after row fingerprints for review.

## Offline verification commands

Run from the repository root with the declared `scripts/test-backend-isolated.sh`.
For the baseline, run the added test module with the three changed service
modules at base `a50305e097fbeab356c79de6bdcfb2f90428949c`. The baseline
keeps the test file from this branch.

```sh
./scripts/test-backend-isolated.sh tracker.tests.test_issue190_bbc_office_identity -v 2
./scripts/test-backend-isolated.sh tracker.tests.test_issue190_bbc_office_identity availability.tests.test_bbc_iplayer availability.tests.test_reconciliation -v 2
./scripts/test-backend-isolated.sh tracker availability -v 1
```

| Local run | Result |
| --- | --- |
| Base services with the seven regressions | Exit `1`. Six methods failed, including two failed subtests in one method. The HTTP replay returned no current offer and exposed the BBC URL in `last_known_offers`. |
| Focused regression, BBC collector and reconciliation | 89 tests, exit `0` |
| Full tracker and availability suites | 3,750 tests, exit `0` |
| Hosted CI | NOT PROVEN |

The local runs used a Compose override with cached images, read-only source
mounts and an internal network. The dependency image was
`sha256:7f2b532c2e9db5f0930f35afc0b466d4dce0d85701901cca3e9ed73f162f8de7`.
Its `requirements.txt` matched the worktree. An egress audit hook rejected and
logged unexpected Python socket destinations. The full suite attempted three
unmocked DNS lookups of the local `frontend` service from the
[candidate-admin proxy check](../backend/availability/candidate_admin_views.py#L56).
The hook blocked all three before resolution. The baseline and focused runs
had no blocked lookups. Zero provider requests escaped mocking and isolation.
Provider and metered calls, new logins, deployments, serving database writes,
and writes to the preserved clone were all zero. The tests used disposable
PostgreSQL databases with the standard Django test schema bootstrap.
GitHub traffic was limited to issue review, branch publication and draft PR
handoff.
