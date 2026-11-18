# Catalogue-only collector bridge: offline rehearsal

Verdict: **OFFLINE_ADAPTER_READY_PERMISSION_PENDING**.
Base: `new-sources` at `a8a0d1670e3d9f6c26f0a849e028e3e70852347a`.
Branch: `prototype/catalogue-only-offline`.
Worktree: `/Users/angeloandreaisola/orca/workspaces/WhereTfDoIWatchIt/catalogue-only-offline`.
The final local implementation SHA is reported at handoff; no merge/push/deploy.

Permission conclusions and primary evidence are in
[catalogue_provider_permission_review.md](catalogue_provider_permission_review.md).
No applicable automation grant was established. Runtime permission remains
fail closed, all schedules remain disabled and the live request budget is zero.

## Architecture and scope

```text
existing TubiClient / TubiSource (unchanged parser, injected fixture requester)
    -> SourceFetchResult.contents + cursor
    -> import_catalogue_batch (permission, normalization, atomic batch/checkpoint)
    -> DiscoveredCatalogueRecord + CatalogueDiscoveryState namespace
    -> reconcile_catalogue_records (bounded explicit IDs, no network option)
    -> existing incremental reconciliation engine / CanonicalVerifier
    -> exact cached search + details, conflict validation, durable outcomes
    -> ProviderContent/currently_available=False + ContentMapping + proof

SourceFetchResult.offers / offer_withdrawals are never consumed.
AvailabilityRefreshService / OfferRepository are never called.
```

The new bridge accepts the existing `DiscoveredContent`/`SourceFetchResult`
interfaces. Its import algorithm is provider-independent; reviewed parser
bindings are explicit code, with Tubi as the only concrete binding. A binding
is **not** an automation approval. The existing permission registry remains
unchanged and contains no verified grant for Tubi. Unknown, denied, expired,
operator-denied and disabled providers fail before writes. There is no approval
CLI flag, JSON override or runtime fixture switch.

Tubi was chosen for explicit movie type/production year, normalized numeric
identity and existing captured HTML fixtures. The unchanged source parser
provides these fields; the bridge does not relabel another provider or manually
promote a weak listing hint. Its real retained series fixture has a leading-zero
native ID normalized to `2185` and no dependable release year; it remains
unmatched with no timed retry until appropriate evidence arrives.

This is a **received-record import boundary**, not a new crawler. Production
code in the bridge performs no HTTP. Only the test fixture requester wires the
existing collector through `DiscoveryHTTP`. The existing legacy Tubi network
client remains unsuitable for a bounded live canary and is not called here.
Production fetch/import orchestration, cooldown persistence and supported source
permission remain prerequisites for live use, not hidden enabling switches.

## Identity, provenance and checkpoint behavior

- Provider slug, collector source slug and country must match the reviewed
  binding. Only title-level movie/series records are accepted; episode/season
  identities are never collapsed into canonical titles. Empty/oversized identity,
  title, malformed release date, mismatched source and unsafe/out-of-scope URL
  fail before any batch writes. The 100-character native-ID limit respects the
  downstream mapping model as well as discovery storage.
- Native ID, source URL, normalized identity URL, URL history, explicit type,
  release date/year, original title, description, external IDs, identity/rights
  evidence, collector source/country and permission evidence are retained.
  Provenance is `PROVIDER_REPORTED`; the observation is not marked independently
  verified. Source IDs are never upgraded to trusted TMDb identifiers.
  `PERMITTED` access comes only after the reviewed-registry check; test approval
  evidence explicitly says it is an offline fixture, not a real grant.
- `observed_at` is aware, cannot be future-dated, and initializes first/last
  observation timestamps. Repeated imports retain first-seen time, monotonically
  advance last-seen time, and leave the matching-input fingerprint unchanged.
  Meaningful source changes become naturally eligible through the existing
  versioned fingerprint/selector. No reconciliation outcome is reset by import.
- Duplicate identical native records coalesce. Contradictory duplicate IDs,
  conflicting URL/native identities, and discoveries owned by another ingestion
  path are rejected. A denied existing discovery cannot be overwritten into
  permission by a replay. Existing classifications/mappings/proofs stay owned
  by the shared reconciliation writer.
- Batches are capped at 250 records and written atomically with the existing
  provider advisory lock, provider/discovery row locks and database uniqueness
  constraints. A valid prefix cannot survive a later validation/write failure.
- The opaque collector cursor is stored under
  `CatalogueDiscoveryState.cursor.catalogue_only_v1`, together with source,
  country, input cursor and normalized batch digest. The caller supplies its
  expected cursor. A different stale batch fails; an identical last batch may
  replay from its original input cursor without duplicates. Other cursor
  namespaces, provider configuration and availability `SyncCursor` are preserved.
  Active discovery leases block imports. Bounded imports do not assert complete
  catalogue coverage or availability.
- Interruption before checkpoint save rolls back the whole imported batch.
  PostgreSQL sequence gaps from aborted inserts are normal; they are not cursor
  advancement or duplicate identities. Reconciliation retains the existing
  per-record commit/lease semantics, so an interruption resumes only remaining
  eligible records. The rehearsal restores disposable-clone sequences explicitly
  after rolling back all scenarios; no live sequence is adjusted.

## Shared canonical verification

The old public `run_incremental_reconciliation` retains exactly its existing
provider allowlist and command behavior. Its body is factored into a private
shared engine; the new entry point adds its own reviewed permission, source
record and bounded-ID checks, then calls that same engine. It exposes no
`allow_network` parameter and forces cached verification only. No CLI, scheduler,
management command, runtime permission grant or provider activation was added.

The type-evidence helper recognizes only explicit title types from the reviewed
Tubi collector marker/source/country. Canonical title/year/type thresholds,
candidate completeness, release-conflict rules and trusted-ID rules are unchanged.
Missing series year defers; ambiguous exact candidates defer; a negative exact
search is unmatched; absent/expired cache evidence stays a cache miss. There is
no title-only/fuzzy auto-association.

Preview and apply both use the existing association conflict validator. An
existing contradictory mapping/proof is preserved and returns `CONFLICTING` in
both modes. A successful preview returns `VERIFIED`; apply commits the same
canonical identity as `ASSOCIATED`. Proof, classification, mapping and outcome
commit through the existing writer. Preview writes no application table;
replay creates no duplicate discovery, mapping, proof or outcome.

## Deterministic request fixture

`tracker/tests/catalogue_only_fixtures.py` uses the **unchanged** real Tubi
collector/parser and captured title HTML. The XML index/window is synthetic,
containing two retained title URLs so the one-record traversal has a resumable
cursor. The recorded HTML is not a current source-access or real TMDb identity
verification. Synthetic TMDb cache IDs are clearly fixture evidence.

The single-page scenario runs through `DiscoveryHTTP` with fixture-only policy,
public-address resolver, `_send` and clock:

| Simulated attempt | Kind | Start (seconds) |
|---|---|---:|
| 1 | robots | 0 |
| 2 | sitemap index metadata | 2 |
| 3 | one child sitemap metadata | 4 |
| 4 | one title page, redirect response | 6 |
| 5 | same title resource, trailing-slash redirect | 8 |

Limits: **one title page, at most five HTTP attempts, zero retries**, shared
minimum two seconds; robots can tighten the interval (tested at four seconds).
Without the redirect, four fixture attempts suffice. Every URL/host/path/DNS and
redirect guard is the existing production HTTP gate. A failed/redirect attempt
consumes budget; invalid/private destinations fail before destination dispatch.
The fixture requester enforces its independent one-page ceiling. Its code lives
under tests and cannot be selected by the runtime source registry.

The new tests and retained discovery regressions exercise request exhaustion,
page ceilings, robots denial/rate policy, mixed private/public DNS, host/scheme/
credentials, cross-host/resource-changing redirects, loops, TLS address pinning,
and direct-session bypass protection. Existing hardening tests retain their
coverage for persisted cooldowns; no new live transport is claimed here.

## Validation

Authoritative logs and clone results are retained privately under
`.catalogue-staging/catalogue-only/`. The adjacent JSON summary records final
exit codes, preservation digests and scenario results without private rows.

Required final checks:

- **269 focused tests**, including **23 new bridge/collector regressions**,
  existing request controls, discovery, incremental/classification/parity,
  canonical bootstrap, TMDb gateway and Tubi collector tests.
- **3,446 complete backend tests** (`tracker availability`).
- **21 deployment safeguard tests**, without Docker mutations.
- `makemigrations --check --dry-run`, `migrate --check`, Django system check,
  base/dev-overlay/test Compose configuration and `git diff --check`.
- New collector-bridge restored-clone rehearsal, plus both existing discovery
  hardening and incremental reconciliation compatibility rehearsals.

The initial adapter-only run exposed two test-fixture issues (a missing test
Source row and a malformed fixture-path expectation). They were corrected;
authoritative final tests include those cases. No application safeguard was
weakened. A later fixture refinement uses the real retained series page rather
than a synthesized series record; focused/full verification was rerun afterwards.

All application testing uses the existing immutable Python image, a copied
source tree, no real external keys, an **internal-only Docker network** and a
Python DNS/socket guard permitting only `wtdw-catalogue-only-db:5432`. No package
installation or image pull is required. Some existing negative tests exercise
denied request paths; no external provider/TMDb connection can be made.

Reproduction on an exact restored disposable clone:

```sh
docker exec wtdw-catalogue-only-runner python \
  /app/scripts/validate_catalogue_only_adapter.py \
  --database incremental_catalogue_only --host wtdw-catalogue-only-db
```

The script refuses another database/host. It verifies the completed inventory,
then imports one captured movie from five simulated attempts, previews exact,
ambiguous and unmatched cases, checks preview/apply mapping-conflict parity,
interrupts an import, applies one exactly fixture-verified association, and
replays without duplicates. All TMDb verification uses synthetic fresh caches,
with **zero TMDb HTTP attempts**. There is no live API credential.

Expected temporary clone changes: 5,095→5,096 discoveries, one Tubi checkpoint,
4,769→4,770 associations and one proof/outcome. Existing 326 deferred rows,
all original associations and zero playback offers are preserved. Preview
changes no tables; conflicting mappings survive unchanged. The outer scenario
rolls back, and all **60 tables / 50 application tables and 57 sequences** must
match the original clone snapshot afterwards.

Regression tests additionally seed an existing playback offer and prove that
both import and reconciliation leave its complete row unchanged. They exercise
per-record reconciliation interruption/recovery and first-observation preservation.

## Live preservation and handoff

The live database was read only: repeatable-read read-only integrity queries,
`pg_dump`, and inspection. It was restored into a separate PostgreSQL container
on a network with no live service attached; no migration, reconciliation or
application command was run against the live database.

Before/after evidence covers:

- Same backend container, image
  `sha256:5ac32c0066a33187345092c3119241df5af8c68f4937f9667100e9a1a5c1592a`
  and deployed revision `09684bae89cf20908ddb4a382452b0efb4a6045e`; healthy,
  no source mounts, unchanged start time and zero restarts.
- Same live PostgreSQL container/start time/restart count and healthy status.
- Identical schema, all 60 public-table counts/full-row digests and all 57
  sequences, including every application table.
- Tracker `0058`, availability `0016`, 5,095 discoveries, 3,300 legacy mappings,
  4,769 associations/proofs/provider identities, 326 baseline deferred outcomes,
  zero offers, zero reconciliation-run/refresh-job rows; provider configuration
  and HydraHD historical checkpoints 132/77 unchanged.
- Empty live `UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES`, no target discovery
  worker, and unchanged disabled default/global/provider/mode switches.

The integrated `new-sources` ref, `master`, unrelated worktrees, five stashes and
backup references were not changed. No service mounts the new worktree. Only
new disposable validation containers are stopped after validation; containers,
network, dump and evidence are retained. Nothing is merged or pushed.

**Next authorization:** provider-specific permission/licensing review and
production bounded-requester work, followed by review of a pinned release and
separate deployment/canary authorization. The precise conditional five-attempt
envelope and data-integrity requirements are in the permission report. No live
source is contractually or technically eligible now. Stop here after offline
validation; do not execute the proposed live canary.
