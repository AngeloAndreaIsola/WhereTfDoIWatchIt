# new-sources integration review (2026-11-19)

Integration base: `origin/new-sources` at
`8e227c378896825a985bcf4bf6da7f9d9b28caa9`. Merged `origin/master` at
`b82becb4e6a42c1c7a562fb5dc9b05f8051e4cfe` into the isolated branch,
preserving both histories. This document grants no merge, deployment, migration,
source-access or schedule approval. The PR must remain unmerged for human review.

## Execution safety

Parser registration, domains and `Provider.is_enabled` are metadata, not an
execution grant. The shared `scraper_policy.require_legacy_execution` boundary
blocks unverified, denied, expired and disabled unofficial sources before scraper
creation/dispatch, HTTP, TMDb matching or automatic canonical match writes.
All six sources remain represented: KissKH, HydraHD, AnimeWorld,
StreamingCommunity, CineBlog01 and AltaDefinizione. Unknown imported scraper
providers also fail closed. Changing a known source's category or removing its
registry entry cannot bypass the gate.

The boundary covers the provider refresh, scraper run, v2 scrape and URL-import
routes, `BaseScraper.run`, `fetch_url`, direct session requests/prepared sends,
`ContentMatcher`, and the automatic match persistence seam. Legacy manual
confirmation for these sources also blocks before TMDb or writes. Existing saved
confirmations and observations remain readable. The unresolved-review GET now
returns persisted observations with empty candidate lists, with no implicit TMDb
search for any provider. Candidate discovery needs a separately authorized action.

Even a future verified discovery grant cannot enable legacy scraper transports:
their retries, implicit redirects and mirror fallback lack the reviewed request
envelope. They return `legacy_request_envelope_unavailable`. Reviewed discovery
retains its own permission preflight and `DiscoveryHTTP` transport. Its targeted
parser call uses that installed transport rather than the legacy public `run()`.
The existing named RaiPlay/NHK broadcaster paths and queue-backed official
collectors retain their separately managed behavior; this change establishes no
new broadcaster permission.

Blocked provider cards retain observation counts/history and have no refresh
target. Their saved counts remain labelled active mappings, never playback
availability, even when permission removes the refresh target. Seeding metadata does not authorize execution or change permission grants.
No runtime permission, rate budget, schedule, migration or deployment setting was
enabled by this integration.

## Master compatibility

The catalogue conflict combines master's normalized typed identity lookup and
watched-state helpers with the feature's typed database identity. Unknown media
types never inherit another title's state. Official/unofficial deduplication now
also uses `(tmdb_id, content_type)`, preventing an official movie from hiding an
unrelated series sharing its TMDb number. A persisted-catalogue integration test
covers deduplication, display metadata and watched/unwatched filtering together.
Master's watched-card, mobile navigation and inline language-chip changes and
regression tests are retained. CI instructions keep disposable PostgreSQL guidance
and both backend apps. No migration files were changed during integration.

Parser fixture tests call parser orchestration with mocked fetch seams; executable
route tests assert permission failures. Matching-algorithm and write-hook tests
exercise the retained broadcaster path, while the new permission tests assert
zero DNS, HTTP, TMDb, canonical writes and offers for blocked sources, including
saved manual mappings and checkpoints. No assertion was relaxed or test skipped.

## Verification and data boundaries

The PR records final-commit test counts and CI results. Required local commands:

```sh
# Run only against an explicitly isolated PostgreSQL service, with blank API
# keys and source egress blocked. Do not use a live backend container.
python manage.py test tracker availability -v 1 --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python3 -m unittest discover -s ops/deployment -p test_release.py -v
npm test --prefix frontend
npm run lint --prefix frontend
NEXT_TELEMETRY_DISABLED=1 npm run build --prefix frontend
(cd frontend && ./node_modules/.bin/tsc --noEmit)
npm run typecheck --prefix extension
npm test --prefix extension
npm run build --prefix extension
npm run validate:package --prefix extension
git diff --check
```

Local backend execution uses a new Docker `--internal` network, PostgreSQL 16 on
`tmpfs`, a pre-existing Python 3.12 dependency image, a read-only integration
checkout and explicit test-only environment. No production entrypoint is invoked.
Containers/volumes/worktrees/stashes are retained; the usual test wrapper's
resource-removal trap is deliberately not used under this task's constraints.
Compose validation uses `.env.example`, not live credentials.

Migration rehearsal creates an isolated master-schema database, seeds synthetic
movie/watched/manual mapping/source rows, dumps/restores that disposable database
to another disposable database and applies the integrated migration graph.
Every original fixture field/row is compared after migration; equal numeric
movie/series IDs are then verified. The graph has no conflicting leaves or
inconsistent history, and the resulting schema has no model drift. This is a
synthetic clone rehearsal, not a production-database clone or production approval.
Existing rollback/source-isolation safeguards remain covered by their tests.

No live database is queried or mutated for this review. Historical numbers
(5,095 discoveries, 4,769 associations, 326 deferred, 3,300 legacy mappings,
zero offers) are not claimed as a freshly verified baseline. The task preserves
runtime mounts and unrelated dirty worktrees by working in a separate checkout;
only disposable fixture databases receive writes.

## Incomplete and separately gated work

All unofficial automation grants remain unverified; the live source/TMDb request
budget is zero. HydraHD traversal and KissKH discovery remain partial with no
proof of complete coverage. AnimeWorld RSS is a recent snapshot; historical
restricted records remain restricted. StreamingCommunity, CineBlog01 and
AltaDefinizione retain provenance/permission blockers and unknown coverage.
Observations do not establish playback availability. No audio/subtitle language
or resolution is guessed and no listing observation creates a playback offer.

The catalogue-only bridge remains offline; its production guarded requester is
missing and unreachable (#122). Separate gates cover permission review (#124),
disabled-mode deployment preparation (#125), first bounded live canary (#126)
and eventual scheduler activation (#128). Language extraction remains out of
scope; master's language preference UI is unrelated to source extraction.
