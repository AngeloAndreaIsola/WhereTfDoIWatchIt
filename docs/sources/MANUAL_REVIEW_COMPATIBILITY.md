# Manual matching investigation and compatibility repair

## Investigation before implementation (2026-11-19)

Inspected head: `922c44b18b2ea2741f24c65cb68a3c0fcc879224`, matching PR #130
and its clean, isolated integration worktree. The mounted original checkout,
existing containers, live databases and five stashes are not repair targets.

Findings:

1. `scraper_unresolved` now discards suggestions for every provider. Earlier
   code searched TMDb on GET through `ContentMatcher` and never persisted those
   transient search results. `ContentMatcher` itself still performs automatic
   local/TMDb association and must not be used for manual review.
2. The project already stores typed/dated `TmdbSearchCache` and
   `TmdbDetailsCache` entries, plus `ProviderContentCandidate` records with scoring
   evidence and review state. `TMDbCandidateGateway` supports cache-only reads,
   finite attempt budgets, disabled redirects, retry ceilings and Retry-After.
   These are sufficient to restore suggestions without any network on GET.
   Persistent suggestions must still match current source identity/type/year;
   expired or rejected evidence is not verification. Review timestamps cannot
   rejuvenate old candidate evidence; use a fresh search cache instead.
3. The current frontend has **no consumer** of `/api/scrapers/unresolved/` or
   `/api/scrapers/confirm-match/`. `/candidate-review` instead reads the separate
   ProviderContent candidate-admin endpoints, shows evidence/conflicts and uses
   explicit accept/reject modals. A small legacy-review section can expose the
   restored contract without replacing that existing workflow.
4. The established RaiPlay/NHK legacy manual review paths predate this change.
   Collection eligibility was incorrectly reused as manual-confirmation policy.
   There is no retained, application-specific server-side TMDb network grant for
   a new interactive lookup. API-key presence and a request's authorize_network
   field are not grants. New network lookup must therefore be disabled by default.
5. Legacy confirmation currently fetches TMDb details, creates a typed Movie,
   changes the saved legacy mapping, projects ProviderContent/ContentMapping,
   and **automatically queues availability refresh**. It does not validate source
   title/year against details or protect existing manual mappings from replacement.
   Confirmation must become cache-only, conflict-checked and free of refreshes.
6. Candidate-admin acceptance is a distinct existing operation with a local
   operator/CSRF boundary and cached-details support. Its direct service must
   also refuse the six restricted unofficial providers, so the legacy repair
   does not leave an alternative manual-acceptance bypass. Other existing admin
   workflows must retain their contracts.
7. Generic URL imports were deliberately blocked because imported domain/category
   metadata cannot authorize speculative crawling. No reviewed grant for a
   generic importer was found. This remains a documented security restriction,
   not a workflow to re-enable. The six collection restrictions remain unchanged.
8. The incremental reconciliation lane owns the historical deferred records and
   checkpoints. Manual legacy review must never enumerate or mutate that lane.

Implementation boundary selected after investigation: restore bounded cached or
reliably persisted suggestions with explicit state; introduce a local-operator
POST lookup using the existing gateway only behind an empty reviewed TMDb-grant
registry plus shared cooldown controls; retain cache-only manual confirmation
for RaiPlay/NHK with strict typed/title/year/conflict checks. No permissions are
granted. New HTTP functionality remains unreachable under current configuration.

## Implemented behavior and limits

- GET keeps the legacy list/observation/candidate fields and adds
  `candidate_state` (`cached`, `persisted`, `not_searched`, `no_results`, `stale`,
  or `blocked`), independent manual-review and candidate-search capability/code
  objects, an observation version, and ambiguity information. Suggestions are
  bounded to five, typed and unverified. Fresh negative cache results remain
  distinguishable from a missing search; stale evidence is never offered as fresh.
- POST `/api/scrapers/candidates/lookup/` requires the existing local operator/
  CSRF guard plus a separate reviewed TMDb grant and a configured shared
  `manual_review` database/Redis cache for pacing, mutual exclusion and cooldown.
  No grant or cache configuration is installed by this change. The grant registry
  is empty. An optional `tmdb_id` explicitly requests typed detail-cache evidence
  instead of a search. It uses the existing gateway, one counted HTTP attempt,
  zero retries/redirects, existing daily/monthly quota accounting, shared minimum
  spacing and persisted Retry-After cooldown. Only candidate caches and quota/
  cooldown accounting may change; there is no automatic association or enqueue.
- Confirmation is a separate explicit operator POST using fresh cached details.
  RaiPlay/NHK retain stored-evidence review. Type, positive numeric ID, exact
  normalized title/original title, exact known year, saved observation version,
  canonical mapping and metadata conflicts are checked. Legacy decimal-string
  JSON IDs remain accepted; booleans, floats and out-of-range IDs are rejected. Truncated/ambiguous
  searches and rejected candidates cannot silently become verified identity.
  Existing confirmed decisions are idempotent; replacing one remains blocked
  pending a separately reviewed correction. No existing Movie metadata is enriched
  or overwritten. The canonical projection must already exist and agree with the
  saved observation; missing projections require separately approved offline
  preparation, not an implicit source/provenance creation during confirmation.
- Successful confirmation writes only the chosen legacy association, its canonical
  mapping, a Movie identity if absent, and an append-only review audit. It never
  writes provider/source records, content observations, offers, schedules, cache,
  discovery/reconciliation state or availability refresh requests.
- The separate candidate-admin acceptance service also refuses all six restricted
  providers before gateway access and validates the full typed identity on details
  verification and existing-mapping conflict checks. Other existing admin APIs and
  their operator/network-budget contracts are retained.
- `/candidate-review/legacy`, linked from the existing review page, loads saved
  evidence only. Lookup/detail caching require button clicks and authorization;
  manual confirmation requires a review dialog and a second explicit click.
  Missing permissions and stale/conflicting evidence remain visible as blocked
  states. Generic URL crawling/import is intentionally still blocked; arbitrary
  domains and imported metadata confer no collection or manual-review grant.
- No new migration files, source permissions or network grants are introduced.
  The 326 historical deferred records and their processing lane are untouched.
  Validation uses only synthetic fixtures and a newly created isolated PostgreSQL
  test runtime; no existing running container or live database is operated on.
