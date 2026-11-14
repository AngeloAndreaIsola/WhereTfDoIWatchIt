# Stage 2H — Execution readiness and proposed manifest

## Readiness matrix

“Unknown” refers to the confirmed Docker-volume database, which was
unreachable. The local host PostgreSQL snapshot is not substituted for it.

| Scope | Proposed cap | Evidence/identity status | Remaining blockers | Readiness |
|---|---:|---|---|---|
| Inception (27205), movie, NL, JustWatch | 3 attempts | Stage 2E records historical Netflix/Max/Prime canonical subscription evidence and a JustWatch NL search that reports subscription plus TVOD. Providers are not paired to the aggregator listing; Stage 2D classified app evidence as stale. | PRs #78–#81 not merged; current target DB/migration/source config/Movie/Country/offer/freshness/queue/locks unknown; fresh quota evidence absent; no primary-provider evidence URL. | Blocked |
| Dune (438631), movie, NL, JustWatch | 3 attempts | Stage 2E records historical Prime base/add-on and Max offers; its saved external-evidence row says NL was not checked. Stage 2D canonical evidence was stale. Proposed JustWatch matcher rejects conflicting IDs and ambiguous title/year results. | PRs #78–#81 not merged; current target DB and queue state unknown; fresh quota evidence absent; no saved NL external URL or primary-provider URL. | Blocked |
| Dune (438631), movie, IT, Mediaset Infinity | 2 attempts | Stage 2E aggregate notes historical Mediaset Infinity subscription and Max offers, but no GUID or year-verifying primary record was preserved; Stage 2D evidence was stale. | All shared blockers above; persisted target mapping unknown; provider GUID unverified and the Stage 2G guard blocks this scope while it is null; no official evidence URL; quota/source row/config unknown on target. | Blocked |
| Independent provider evidence | 3 page requests maximum | No exact official page URL was preserved for any selected case. The saved JustWatch URLs are secondary. | Requires a separate evidence-collection authorization, exact approved destinations, provider/offer-class mapping, and no-redirect request policy. | Blocked |

## Passed with evidence

- Proposed limits add up to 8 source attempts (6 JustWatch, 2 Mediaset) plus
  at most 3 independent page requests, total ceiling 11.
- The Stage 2G branch has explicit source/operation limits, immutable attempt
  reservations, a source-isolated runner, read-only preflight, dry-run and
  digest/database/expiry-bound authorization. It denies Streaming Availability,
  Watchmode and `legacy_scraper`.
- Its JustWatch adapter rejects conflicting identity and ambiguous title/year
  matches. The experiment proxy forces non-authoritative incremental behavior,
  discards withdrawals and unrelated catalogue content, and only accepts
  manifest-matching offers. Thus stale prior rows or a source omission do not
  become confirmed positive/negative evidence. The historical Stage 2D audit
  also reports zero fresh canonical offers at its observation time.
- In the separate host database only, exact-scope pending queue count was zero,
  there were no pending requests at all, no unfinished refresh runs, and no
  advisory locks at query time. This is not target-database evidence.

## Failed / not satisfied

- Prerequisite merge gate: all four PRs are still open.
- Current master lacks the Stage 2G command and migration. The reachable host
  `tracker` database lacks migration 0015 and the experiment ledger; it is not
  the confirmed Docker-volume database.
- No exact primary-provider URL exists in the saved records for these cases.
- No verified Mediaset GUID exists in the saved records; the runner correctly
  blocks that scope.

## Unknown / authorization required

- Actual Docker-volume identity and target database contents, migration state,
  source rows/configuration, Movie/Country anchors, offer freshness, queue,
  active workers, cron state, advisory locks, and experiment reservations.
- Current provider quota/operational rate state. Historical “no billed quota”
  notes and empty local counters are not current quota evidence.
- Applying migration 0015 to the actual database requires separate
  authorization after the code prerequisites merge.
- Capturing exact primary-provider pages requires a separate authorization.
- Any live source execution, token creation, or source activation requires
  separate explicit authorization for the exact manifest, database and cap.

## Proposed inactive execution manifest

This is a planning record only. It is not the active Stage 2G authorization
manifest and must not be used to issue a token or execute requests.

```yaml
schema_version: 1
experiment_id: stage2e-availability-refresh
maximum_attempts: 11
scopes:
  - scope_id: inception-nl-justwatch
    tmdb_id: 27205
    media_type: movie
    country: NL
    source_slug: justwatch
    operation: title_refresh
    endpoint: https://apis.justwatch.com/graphql
    maximum_attempts: 3
    expected_offer_type: subscription
  - scope_id: dune-nl-justwatch
    tmdb_id: 438631
    media_type: movie
    country: NL
    source_slug: justwatch
    operation: title_refresh
    endpoint: https://apis.justwatch.com/graphql
    maximum_attempts: 3
    expected_offer_type: subscription
  - scope_id: dune-it-mediaset
    tmdb_id: 438631
    media_type: movie
    country: IT
    source_slug: mediaset_infinity
    operation: title_refresh
    endpoint: https://frontier.api-graph.mediaset.it/
    maximum_attempts: 2
    expected_offer_type: subscription
    verified_provider_id: null # BLOCKED; do not execute
provider_evidence:
  maximum_attempts: 3
  exact_destinations: [] # BLOCKED; separate authorization required
```

## Per-source request budget

| Source/operation | Maximum outbound attempts | State |
|---|---:|---|
| JustWatch `GetSearchTitles`, Inception NL | 3 | Proposed; not authorized |
| JustWatch `GetSearchTitles`, Dune NL | 3 | Proposed; not authorized |
| Mediaset `MediasetSearch` + `MediasetMovie`, Dune IT | 2 total (1 + 1) | Blocked pending verified GUID |
| Independent provider evidence | 3 total | Blocked pending exact destinations and separate authorization |
| Streaming Availability | 0 | Denied |
| Watchmode | 0 | Denied |
| `legacy_scraper` | 0 | Denied |

## Conclusion

**INCONCLUSIVE** — do not execute. The required PRs have not merged and the
actual Docker-volume database could not be inspected. Independent primary
evidence URLs are missing, current quota evidence is absent, and Mediaset
identity is unresolved. The local host PostgreSQL snapshot cannot establish
readiness for the application volume.
