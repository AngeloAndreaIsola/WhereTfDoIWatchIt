# Independent incremental reconciliation integration review

Reviewed feature: `ac30782f7f3c8483d54338f8471d62ecaa188a62` on
`feature/incremental-catalogue-reconciliation`.
Target: freshly fetched `origin/new-sources` at
`a7ce47f5a15107ea8df1c39c740e8699f0f6cd68`.

The target is the feature's direct ancestor; there are no integration conflicts.
The feature branch exists locally; its named remote ref was not present at
review time. The original Markdown and JSON audits are actually committed in
the feature, verified with `git show ac30782:docs/audits/incremental_catalogue_20261118.md`
and its JSON counterpart. The worktree directory name does not identify its
checked-out branch. The two untracked bulk audit documents were left untouched.

## Findings and fixes

| Finding | Impact | Resolution |
| --- | --- | --- |
| Network previews ignored durable cooldowns and leases | A preview could spend TMDb requests during an active provider cooldown | Read persisted guards for previews without creating/updating state or caches |
| Mapping conflicts were detected after canonical identity/proof creation | An operator's existing mapping survived, but a contradictory new canonical identity and proof could remain | Lock/check existing mappings before persisting proof; roll back the entire record transaction if a conflict appears during association |
| Scheduler used truthiness for enable flags and network access | Quoted JSON `"false"` could enable execution or HTTP | Require explicit JSON `true` for schedule switches; reject non-boolean `allow_network` |
| Early lock/cooldown returns copied the report before final accounting | Refused jobs omitted request counters | Return the finalized report and include zero-attempt accounting for advisory-lock rejection |

Regression tests reproduced the first three issues before their fixes. Added
coverage also proves expired owners cannot commit/checkpoint, a late mapping
conflict rolls back canonical proof, and refused jobs report zero attempts.
The expanded clone rehearsal caught and verified the accounting fix.

## Safety review

| Requirement | Result |
| --- | --- |
| New discoveries | No reconciliation state means eligible; tested through HydraHD collection and AnimeWorld RSS parsing/persistence |
| Meaningful edits | Versioned input fingerprint reselects older IDs; collector timestamps/pagination noise do not cause matching |
| Unchanged observations | Immediate replay selects zero records and issues zero requests |
| Previous 326 unresolved observations | Baseline status has zero attempts and no retry date; none eligible now or ten years later without new evidence/inputs/review |
| Existing canonical associations | Conflicting edits and operator mappings preserve associations; rejected candidates leave no new proof/identity |
| Movie/TV IDs | Composite identities and separate caches retain both media types with one numeric TMDb ID |
| Request/retry bounds | Every attempt, including retries, consumes the hard budget; budget interruption resumes from cached search |
| Persistent cooldown | HTTP 429 stops after one attempt, persists 900-second cooldown, and blocks subsequent apply/preview requests |
| Provider locking | A second PostgreSQL session cannot enter the active provider; a durable live lease also rejects reentry |
| Expired leases | Expired lease recovers; expired/replaced owner cannot commit |
| Interrupted jobs | Successful record outcome, association, evidence and checkpoint commit together; interrupted/failed record resumes without duplicating prior commits |
| Availability | No offers created; catalogue identity creation remains unavailable and does not infer playback |

## Migration compatibility

Only two migrations are introduced: tracker `0057` creates the durable outcome
and provider run tables; `0058` acknowledges the completed inventory without
matching or canonical writes. Dependencies match the live tracker `0056` and
availability `0016` baseline. No additional migration was needed for the fixes.

A fresh live custom-format `pg_dump` was taken with
`default_transaction_read_only=on`, then restored into `incremental_review` in
a new PostgreSQL 16 container on an internal Docker network. A separate runner
uses fixture-only TMDb credentials and that clone's connection settings.
No application startup/worker entrypoint was run against live data.

`scripts/validate_incremental_migrations.py` guards the exact disposable
database/host and verifies the migration plan, before/after row hashes,
reverse/reapply, and deferred eligibility. Results:

- All **48 existing application tables** preserved byte-equivalent row hashes.
- Exactly **50 application tables** after migration.
- **4,769 ASSOCIATED** and **326 BASELINED** outcomes, all with zero attempts.
- Zero immediate or ten-years-later eligible observations; zero offers.
- Reverse to `0056` restores the original application-table snapshot.
- Reapply reproduces the same migrated snapshot.

A subsequent PostgreSQL-enforced read-only live audit matched all 48 original
application tables and migration leaves to the restored pre-review snapshot.
Live remains on tracker `0056` and availability `0016`.

## End-to-end rehearsal

`scripts/validate_incremental_catalogue.py` passed on the migrated clone. All
HTTP was intercepted; the internal network also isolates the runner from live
providers and TMDb. Scenarios include:

1. New HydraHD discovery: one discovery request, two TMDb requests, one association.
2. New AnimeWorld RSS GUID: replay preserves identity; missing year causes zero HTTP.
3. Later RSS year evidence: two TMDb requests verify a TV association sharing the
   HydraHD movie's numeric ID, with distinct movie/series canonical records.
4. Ambiguous candidates: one search, no association, no immediate repeat.
5. Changed metadata: conflicting identity preserves the old mapping and proof.
6. Interruption: committed first record/checkpoint survives; only remaining work resumes.
7. Repeated jobs: zero eligible records and requests with unchanged inputs.

Additional cases verify cross-session exclusion, expired leases/proof, budget
exhaustion and cached recovery, atomic rollback, and durable rate-limit cooldown
for both apply and preview. All **50 application tables** match after rehearsal
rollback. **Zero playback offers** are generated.

## Scheduler readiness

Historical discovery, incremental discovery and reconciliation have independent
enable flags, budgets, intervals and persisted checkpoints/cooldowns. Empty
environment configuration disables all modes. No schedules were enabled and
no worker was started for this review.

The [conservative canary template](../sources/INCREMENTAL_RECONCILIATION_CANARY.json)
is valid and entirely disabled, including network access. It proposes daily
intervals, two-second spacing and no retries. Reconciliation is limited to five
records/ten requests per provider. HydraHD discovery budgets are one historical
request and two incremental requests; AnimeWorld is limited to one permitted
RSS request and has no historical archive configuration. Prefer one explicitly
authorized provider/record selection before any recurring execution.

## Validation and integration

See the [machine-readable results](incremental_catalogue_review_20261118.json)
for final counts and status. **160 focused PostgreSQL tests and all 3,382
isolated backend tests passed** against the final implementation. Migration,
system, Compose and diff checks also passed. Verdict:
`INCREMENTAL_RECONCILIATION_READY_TO_MERGE`. Commands exercised:

```sh
./scripts/test-backend-isolated.sh tracker availability -v 1
python manage.py test tracker.tests.test_incremental_catalogue_reconciliation \
  tracker.tests.test_catalogue_discovery_scheduler \
  tracker.tests.test_catalogue_reconciliation tracker.tests.test_canonical_bootstrap \
  tracker.tests.test_canonical_rollout tracker.tests.test_catalogue_discovery \
  tracker.tests.test_catalogue_identity tracker.tests.test_canonical_identity \
  tracker.tests.test_kisskh_catalogue_discovery availability.tests.test_candidate_gateway \
  --noinput -v 1
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
python manage.py check
docker compose config --quiet
git diff --check
```

Both management-command verification and migration/rehearsal commands target
the isolated database only. Private dump, row hashes and complete logs remain
under ignored `.catalogue-staging/incremental-review/`; no row data or credentials
are committed. Reproduction scripts require a fresh restored clone, with the
migration rehearsal run before the workflow rehearsal.

Merge preparation targets `new-sources` only. No bulk canonical reconciliation,
live migrations/writes/crawls/schedule changes, merge, or push was performed.
No implementation or integration blocker remains after successful final checks.
Live migration and any canary execution still require separate authorization;
the baseline migration is intentionally tied to the audited inventory boundary,
so repeat the disposable-clone checks against a fresh dump before deployment.
