# Streaming Tracker — Build Plan

## Stack

- **Frontend**: Next.js + Tailwind CSS
- **Backend**: Django + Django REST Framework (admin panel doubles as the manual data-entry UI for `LanguageInfo`)
- **Database**: PostgreSQL 16
- **Hosting**: small self-hosted VPS via `docker-compose`
- **Auth**: none in v1; single-user, but data model leaves room to add it later

## Current status as of 2026-09-30

Phase 0 through Phase 10 are implemented and merged. Phase 11 (the dedicated Prime Video source) and Phase 12 (the dedicated RaiPlay source) are implemented and tested locally but not yet committed. The original checklist is retained below as the build history, with current status reflected in the checkboxes.

- Docker Compose, Django, Next.js, and PostgreSQL scaffolding are in place.
- Core data model and Django admin support are in place.
- Single-movie search, TMDb matching, cache-first availability lookup, subscription/free filtering, and strict-priority ranking are implemented.
- Cron-driven background refresh and audit logging are implemented with a default budget of 90 requests/day.
- Letterboxd CSV upload, ambiguous TMDb match resolution, quota-aware bulk refresh, watchlist report, and report analytics are implemented.
- Frontend smoke tests now cover the search, preferences, watchlist report, and ambiguous-match resolver flows.
- Unofficial sources are implemented and merged.
- Provider catalogue refresh (`refresh_provider_catalogue`, CBC Gem first) is operationalized as a scheduled background workflow: capability-driven source discovery, per-source advisory locking, declared-country initialization, per-source failure isolation, exit codes `0`/`1`/`2` for cron, and scheduling docs in `docs/CRON.md` / `docs/DEPLOYMENT.md`.
- Provider-native catalogue persistence (`availability.ProviderContent`) is in place: every entry a source publishes is stored under the provider's own identity even when no TMDb mapping resolves it — unresolved discovery is kept, not discarded, while offers stay exactly as strict as before.
- Provider catalogue query layer (`ProviderCatalogueQueryService` → `ProviderContentRepository`) is in place: the local catalogue is readable independently of TMDb, with mapped and unresolved rows as first-class results and zero network access on the read path.
- BBC iPlayer is the second catalogue source (`availability/sources/bbc_iplayer.py`): the same `AvailabilitySource` → `DiscoveredContent` → `ProviderContent`/`ContentMapping`/`Offer` pipeline runs against a materially different catalogue shape (public HTML pages with an embedded JSON state instead of a JSON API, per-brand episode *slices*, no external ids, no language metadata), so the provider-native architecture is no longer a single-provider design.
- Tubi is the third catalogue source (`availability/sources/tubi.py`) and the first region-locked, ad-supported one: sitemap discovery walks `tubitv.com/sitemap.xml` down to the title sitemaps (round-robin across sitemaps, bounded by `max_contents`, always `authoritative=False`), each title page is parsed for its `window.__data` SSR state (a JS literal, not JSON), movies and series become `ProviderContent` rows with season/episode offers keyed to the series id, and a 200 SPA shell is logged as `tubi_content_missing` rather than mistaken for "unavailable".
- Prime Video is the fourth catalogue source (`availability/sources/prime_video.py`, Phase 11) and the first that walks a storefront instead of a sitemap or partner API: `/categories` plus the `/store`, `/movie`, `/tv` and genre index pages are the seeds, each index page's `body.pagination.url` chains the walk (bounded by `max_contents`, round-robined, `authoritative=False`), and one SSR detail page per title yields a `DiscoveredContent` with offers at MOVIE/SERIES/SEASON/EPISODE granularity under `provider_id="prime"` (the source slug stays `prime_video` for provenance). It is bound to the single territory it was researched against (`COUNTRY == "NL"`) and raises `SourceFetchError` on a `currentTerritory` mismatch instead of persisting another country's catalogue, publishes no TMDb ids (local title/year matching in the refresh service), and is exercised only by recorded fixtures (`backend/availability/tests/fixtures/prime_video/`) — no live calls in tests.
- RaiPlay is the fifth catalogue source (`availability/sources/rai_play.py`, Phase 12) and the second discovered purely from a sitemap index: `https://www.raiplay.it/sitemap.xml` → only its `sitemap.raiplay.programmi.MM.YYYY.xml` children (~2.2k unique `/programmi/` URLs; the editorial, `dirette`, `collezioni` and `video` children are filtered out by URL shape and never requested) → one programme page per entry (round-robined across months, de-duplicated on the canonical URL, bounded by `max_contents` = 400) → the page's `window.WashiContext` hydration object (strict JSON) → `first_item` → the video's `schema.potentialAction.actionAccessibilityRequirement`. One observation yields one `DiscoveredContent` — MOVIE for `layout == "single"`, SERIES for `layout == "multi"`, keyed by the provider's own `uniquename` — plus at most one offer under `provider_id="raiplay"` (source slug `rai_play`, so provenance never merges with the Streaming Availability rows). The `.json` `first_item` URL answers either with the rendered player page or with a bare JSON record (the shape served live since 2026-09-30) that carries no access model, in which case the source follows the record's own observed `weblink` to the player page. It publishes no TMDb ids (local title/year matching), never deactivates anything (`authoritative=False`), and is exercised only by recorded fixtures under `backend/availability/tests/fixtures/rai_play/` — no live calls in tests. Live verification stays deliberately small: captured pages and sitemaps during research, plus two bounded live probes (6 titles and 100 titles, the latter to measure runtime) — a full ~2.2k-title live walk has never been run.
- Cross-source availability reconciliation (Phase 13, `backend/availability/services/reconciliation.py`) is implemented locally and not yet committed: raw `Offer` / `ProviderContent` / `ContentMapping` / `SyncRun` observations are grouped per (content, provider, country) and merged into one effective offer, disagreements between sources are surfaced as conflicts instead of being resolved (there is no source ranking), and `get_local_offers` now serves that reconciled view. It adds no table, no migration, no network call, and no write; the raw `AvailabilityQueryService.get_for_*` boundary is unchanged.
- Unified catalogue read-path migration off `ScrapedContentMapping` (Phase A) is implemented: `GET /api/catalog/unified/`'s scraper half is served through `ProviderCatalogueQueryService.list_unofficial` → `ProviderContentRepository.list_unofficial` over canonical `ProviderContent`/`ContentMapping` rows (`tracker/services/unified_catalogue.py` shapes the response, preserving Movie-display-for-resolved-rows, dedupe, filters, pagination and the no-network read). Legacy rows reach the canonical tables through the write-side bridge `availability/services/legacy_ingest.py` (`provider_content_id = legacy-<pk>`, source `legacy_scraper`, no `Offer` rows written), which is invoked from `ContentMatcher._upsert_mapping`, the `scraper_run`/`scraper_confirm_match` views, and a `post_delete` mirror on `ScrapedContentMapping` (`tracker/signals.py`); rows that predate the bridge are backfilled once with `python manage.py ingest_legacy_scraper_catalogue`. `ScrapedContentMapping` itself stays as the scrapers' write/review table — remaining legacy read consumers left in place for later phases: `views._build_prime_offers`, the api_v2 unofficial endpoints, `tracker/services/cache.py::read_offers`, `scraper_unresolved`, and `ScrapedContentMappingSerializer`.

## Availability architecture (`backend/availability`, Phases 1–8)

Internal numbering used by the `availability` app's docstrings and tests —
distinct from the build-plan phase checklist below.

1. **Domain foundation** — canonical models and identity helpers (`Offer`,
   `ContentMapping`, `Source`, `SyncRun`, `Country`, `tracker.Provider`).
2. **Local-only reads** — `AvailabilityQueryService` answers from the `Offer`
   table; the HTTP path never calls an external API.
3. **Write/refresh boundary** — `AvailabilityRefreshService` owns
   fetch → normalize → persist, single-transaction offer writes, and
   authoritative-snapshot-only deactivation.
4. **Provider/source architecture** — *where the user watches*
   (`tracker.Provider`) vs *where the information came from* (`Source`), with
   `SourceCapabilities` expressing behaviour instead of slug checks.
5. **CBC Gem collector** — a catalogue-driven source plus local title/year
   matching: confidence tiers, manual mappings win, fuzzy never overwrites.
6. **Refresh scheduling** — `refresh_provider_catalogue`: capability-driven
   discovery, per-source advisory lock, failure isolation, cron exit codes.
7. **Provider-native content catalogue** — `ProviderContent` persists every
   discovered entry under the provider's own key, with or without a TMDb
   mapping, and `ContentMapping` optionally links the row it resolved.
8. **Provider catalogue query** — `ProviderCatalogueQueryService` reads the
   local catalogue through `ProviderContentRepository` and returns the
   `ProviderContentView` read model: provider slug, provider-native identity,
   metadata, observation lifecycle, optional `tmdb_id`, and active-offer
   presence — entirely offline.
9. **BBC iPlayer collector** — second catalogue source, proving the pipeline
   against a different shape: HTML pages carrying an embedded JSON state
   instead of a JSON API, per-brand episode *slices* (brand pid → `SERIES`,
   single pid → `MOVIE`, no per-episode content rows), pid-based identity with
   no external ids, no language metadata, and a public A-Z index whose 200-item
   per-letter cap is observed as a warning rather than treated as authoritative.
10. **Tubi collector** — sitemap-driven catalogue source for a geo-restricted,
   ad-supported catalogue: XML index → title sitemaps → interleaved detail
   pages, `window.__data` SSR state decoded as a JS literal, provider-native
   id normalization (`02185` → `2185`), series/season/episode offers derived
   from the one observed season, and offer-window gating so a closed window is
   a missing offer rather than a stale one.

Phase 7 contracts, since they touch earlier phases:

- **Unresolved != discarded**: content with no mapping still gets a
  `ProviderContent` row; only offers require a TMDb id (`Offer.tmdb_id` is
  non-null), and `Offer` itself is unchanged — its value-level link stays
  `Offer.provider_content_id`.
- **One transaction per phase**: discovery (`ProviderContent` upserts) and
  mapping commit together before any `SyncRun` exists, so a fetch failure, a
  mapping failure, or an offer failure each leave nothing behind.
- **Observation, not deactivation**: `first_seen` never moves, `last_seen`
  advances, sparse metadata is never erased, and a disappearing entry keeps
  its row — catalogue deactivation stays reserved for authoritative,
  complete snapshots (CBC Gem never declares one).

Phase 8 contracts:

- **Read/write split**: reads go `application → ProviderCatalogueQueryService
  → ProviderContentRepository → Postgres`; writes stay with the scheduled
  refresh (`AvailabilitySource → ProviderContent/Offer persistence`). The
  query path imports no source, HTTP client, TMDb/Streaming Availability
  client, or refresh module, and a stale catalogue is still readable.
- **Three concepts, three lifecycles**: `ProviderContent` ("the provider
  catalogue entry was observed"), `ContentMapping` ("optional provider-native
  → TMDb resolution"; manual/high-confidence precedence unchanged), and
  `Offer` ("a currently active access offer") are read together but never
  collapsed — a row can be listed with `tmdb_id=None`, zero offers, or a
  mapping that changes later.
- **Lifecycle meaning (formalized)**: `last_seen` = "a successful source
  refresh observed this entry", not a provider availability guarantee;
  `currently_available` is never flipped by a non-authoritative (or
  incremental) refresh that simply did not return the entry, and re-observation
  reuses the same primary key. Offer deactivation stays exclusively with the
  Phase 3 authoritative-snapshot rules.
- **Offer presence is summarized**: views expose `has_offers`/`offer_count`
  (active offers keyed on `Offer.provider_content_id`), never the Offer rows
  themselves, and never write back to either table.

Phase 9 contracts (BBC iPlayer):

- **Shape independence**: the source contract is only `fetch → DiscoveredContent
  → Offer`; HTML parsing, state extraction, and pid identity all stay inside
  `bbc_iplayer.py`, so nothing outside that module knows BBC URLs, slice ids,
  or `__NEXT_DATA__`-style payloads.
- **Titles, not episodes**: one `ProviderContent` per brand/single pid;
  season/episode offers key to the brand pid with the episode pid kept in
  `Offer.source_record_id`, so episodes are transactable offers without
  becoming catalogue rows.
- **No language metadata is honest**: `audio_languages=None`,
  `subtitle_languages=None`, `supports_languages=False` — the source never
  invents a language, and ranking falls through to quality for such offers.
- **A-Z truncation is never authoritative**: a letter capped at 200 entries is
  logged (`bbc_iplayer_letter_truncated`) and marked `authoritative=False`,
  `complete_snapshot=False`, so a truncated walk can never deactivate rows.

Phase 10 contracts (Tubi):

- **Discovery is the sitemap, never a guess**: only `<loc>` values that match
  a title URL (`/movies/<id>` or `/series/<id>`) are requested; editorial
  children of the index, season URLs, and episode URLs are never fetched, and
  a season/episode `provider_url` is taken from the page's own links or left
  empty — never constructed.
- **Absence is never evidence of unavailability**: a 200 shell, an entry
  missing from the state, a type mismatch, a closed or not-yet-open offer
  window, and a series with no observable episode all skip *that row only*
  (logged: `tubi_content_missing`, `entry_not_in_state`, `type_mismatch`,
  `tubi_offer_expired`, `tubi_offer_not_yet_available`,
  `tubi_content_only`), while the walk continues. Only an undecodable state,
  a missing marker, a malformed sitemap, or a transport failure raises
  `SourceFetchError` — discovery failures are failures, empty results are not.
- **The walk is bounded, interleaved, and non-authoritative**: entries are
  round-robined across sitemaps before `max_contents` caps them (so a small cap
  still mixes movies and series), truncation is logged
  (`tubi_catalogue_truncated`), and the result always reports
  `authoritative=False`, `complete_snapshot=False`, `sync_mode=FULL`,
  `cursor=None` — no offer and no content row is ever deactivated by it.
- **Series identity outranks season granularity**: movies and series are the
  only content rows; season and episode offers share the series' normalized
  id with `season_number`/`episode_number` on the offer, and a series with no
  episode on its page yields a content row and zero offers. Series carry
  `release_date=None` (Tubi's series `year` is not a dependable first-air
  year), movies carry Jan 1 of their production year.

Phase 11 contracts (Prime Video):

- **Discovery walks Prime's own storefront, not a sitemap**: the index queue is
  seeded from `/categories` plus `/store`, `/movie` `/tv` and the genre paths
  the category page publishes, index pages chain through
  `body.pagination.url` (joined against the base URL when the server returns a
  path), and only `/detail/<compactGTI>`-linked Movie/TV Show/Short Film cards
  are fetched — collections and `/genre/` tiles never are. Genre indices do not
  chain, so a genre seed contributes page 1 only.
- **Identity**: `atf.state.pageTitleId` indexes `atf.state.self` (title type,
  compact gti, link) and `btf.state.detail.detail` (metadata). Prime has no
  series-level id — every TV page is a season page — so the SERIES content row
  uses the canonical first season's `compactGTI` from `atf.state.seasons`, and
  the SERIES, SEASON and EPISODE offers all share it, keeping the season/episode
  slots of the offer identity to stay distinct rows. `provider_id` is `"prime"`
  (the seeded `tracker.Provider`); `source_id`/`source_version` remain
  `prime_video` / `prime-ssr-v1`, so provenance never merges with the
  Streaming Availability rows.
- **A loud failure is not an empty catalogue**: a missing hydration marker, a
  payload that is not the expected shape, a transport error, or a
  `currentTerritory` mismatch raises `SourceFetchError` (and the refresh
  records it on `RefreshRun.error`) — only genuinely absent rows skip
  individually. The one non-fatal discovery step is `/categories`
  (`prime_video_categories_unavailable`); everything else failing fails the
  source.
- **Territory is served by egress IP**: `/region/<cc>` does not select the
  catalogue, so the source declares `COUNTRY == "NL"` and verifies
  `currentTerritory` on every page rather than reporting a country it was not
  researched against — the same loud-failure contract as Tubi's geo-lock.
- **The walk is bounded and never authoritative**: index pages are round-robined
  before `max_contents` (default 500 detail pages) caps them, truncation is
  logged (`prime_video_catalogue_truncated ... pending_index_pages=`), and the
  result always reports `authoritative=False`, `complete_snapshot=False`,
  `sync_mode=FULL`, `cursor=None` — absence never deactivates.
- **Commercial state is what the page says, not what it implies**: `buyBox` is
  empty in SSR and `seasonSelectorIcon` is always `"Unentitled"`, so offer types
  come from the page's `focusMessage` vocabulary together with `isPrime`
  (detail-page cue wins over card cue); pre-order wording (*"Available to
  pre-order"*, observed live with a *"Pre-order movie UHD €17.99"* buy action)
  classifies as a purchase, and anything unclassifiable becomes `unknown`
  rather than being dropped. The page's `isPrime` is per-page, never
  per-episode, so episode offers inherit the season page's offer types.
- **Honesty about missing metadata**: `audio_languages`/`subtitle_languages`
  are `None` when the record ships an empty list (never `[]`, which would claim
  "no languages exist"), quality maps `isUhd`/HDR flags onto `4K`/`HDR`/`""`,
  windows are always `None` (Prime publishes none here), and
  `supports_tmdb_ids=False` leaves mapping to the refresh service's local
  title/year matching (series resolve through the `TITLE_ONLY` tier).

Phase 12 contracts (RaiPlay):

- **Discovery is the sitemap, never a guess**: the index's programme children
  are matched by URL shape (`PROGRAMME_SITEMAP_RE`), entries are round-robined
  across the monthly sitemaps and de-duplicated on the canonical URL before
  `max_contents` (400) caps them, and only `/programmi/…` entries are fetched —
  live channels and other editorial paths are logged
  (`rai_play_live_content_skipped`, `rai_play_unsupported_content`) and never
  requested. Truncation logs `rai_play_catalogue_truncated`; nothing is
  constructed from a pattern.
- **The access model lives on the player page**: `first_item` points at a
  `.json` video URL that answers either with the rendered player page
  (hydration object embedded) or with a **bare JSON record** (no `data_type`,
  no `schema`), which is the shape served live since 2026-09-30. Only the
  second case triggers a follow-up to the record's own observed `weblink`
  (the `.html` player page), so a rendered player page still costs one
  request; a record without a `weblink` costs the offer, never the content.
- **Identity is the provider's own**: the programme `uniquename`
  (`ContentItem-<uuid>`) keys the content row, and the video's `uniquename`
  keys `Offer.source_record_id`; `first_item` must equal the video `path_id`
  (observed on every captured pair) or the video is skipped. `provider_id` is
  `raiplay` (the seeded `tracker.Provider`), `source_id`/`source_version`
  stay `rai_play` / `rai-play-ssr-v1`, and mapping goes through local
  title/year matching because RaiPlay publishes no TMDb ids.
- **Numbering is a label, not a number**: `season`/`episode` on video pages
  are strings (`"1"`, `"2026/27"`, `""`), so the source publishes **no**
  SEASON/EPISODE offers at all — movies and series only — and a series offer
  carries no window and no subtitles (they describe one representative
  episode). Series also carry `release_date=None` and `duration=None`; movies
  carry Jan 1 of their production year and the video's runtime.
- **Free by vocabulary, region by statement**: `category` `free` and
  `nologinrequired` both normalize to `OfferType.FREE`, an unrecognized
  category inside an existing spec becomes `UNKNOWN` (logged), and a missing
  spec skips the offer (`reason=no_access_spec`). `eligibleRegion.name` must
  be the declared `IT`; a different region or a missing region skips the offer
  rather than assuming coverage.
- **Windows are movie-only and current-only**: naive timestamps are read as
  UTC (the compact `+0000` is normalized), and a window that has expired or
  has not opened skips that offer (`reason=expired` / `reason=window_not_open`)
  so the read path never publishes a closed window. Language honesty:
  `subtitlesArray` `{}` → `[]`, populated → normalized (`["IT"]`), absent →
  `None`; `audio_languages` is always `None` (no audio field is published),
  and quality maps a truthy `uhd` to `"4K"` (never observed live) else `""`.
- **Loud failures, quiet absences**: a transport error, a non-200 (except
  404/410, which are stale entries), a payload with neither the
  `window.WashiContext` marker nor a leading JSON object, or a malformed
  hydration value raises `SourceFetchError` — the whole run fails and nothing
  is written. Individual absences (retired programme, dead video, unsupported
  layout, page that is not a programme) skip that row with a
  `rai_play_*` log line, and a walk that observes nothing at all logs
  `rai_play_catalogue_nothing_observed`.
- **Never authoritative**: every run reports `authoritative=False`,
  `complete_snapshot=False`, `sync_mode=FULL`, `cursor=None`, and the source
  never calls the deactivation path — a capped or partially observed walk
  deactivates no offer and removes no catalogue row.

Phase 13 contracts (reconciliation):

- **Raw stays raw**: a read never rewrites `Offer`, `ProviderContent`,
  `ContentMapping` or `SyncRun`. Reconciliation is a derived view — the
  frozen DTOs in `backend/availability/domain/reconciliation.py` computed at
  read time by `AvailabilityReconciliationService` — so no model, field or
  migration was added and `makemigrations --check` stays clean.
- **The key is (content, provider, country)**: observations group by
  `(content_type, tmdb_id, season_number, episode_number, provider, country)`
  and, inside a group, by `offer_type`. `source` and `provider_content_id` are
  provenance, not identity: two sources describing the same provider's offer
  for the same content become one `ReconciledOffer` carrying both (`sources`,
  `offer_ids`, ordered per-source `observations`, `source_urls`,
  `sync_run_ids`).
- **Disagreement is reported, never resolved**: with no source ranking, a
  conflicting value widens to unknown and raises a `ReconciliationConflict`
  of kind `availability`, `offer_type`, `mapping`, `window`, `quality`,
  `language` or `freshness`, each carrying the `offer_ids`, `sources` and
  per-source `facts` behind it. Conflicts are deduplicated at the result level
  and attached to every offer they affect. Language comparison is
  order-insensitive (`EN,IT` and `IT,EN` agree), and an unknown value never
  beats a known one — only two *different* known values conflict.
- **Availability is conservative**: an observation counts toward availability
  only when its raw `currently_available` flag is set, its
  `available_from`/`available_until` window is open at the evaluation instant,
  and its provider content resolves to this TMDB id (a `ContentMapping`
  pointing elsewhere, or the same `(provider, provider_content_id)` active
  under another TMDB id, is a `mapping` conflict that excludes it). The
  effective offer is available when any observation is, and only available
  offers reach `tracker.services.availability_reads.get_local_offers`.
- **Freshness is declared, never assumed**: a source may set
  `Source.configuration["expected_refresh_interval_seconds"]`; then each of
  its observations is `fresh`/`stale` against `now` and a group mixing states
  reports a `freshness` conflict. Without the key there is no deadline to
  measure against, so the state is `unknown` — no universal timeout exists.
  `sync_status` comes from the latest `SyncRun` per (provider, source,
  country); a failed latest run shows up in diagnostics and hides no offer.
- **Two read shapes coexist**: the raw `AvailabilityQueryService.get_for_*`
  methods (observation boundary, deactivation applied) are untouched; the new
  `get_reconciled_for_content` / `get_reconciled_for_movie` /
  `get_reconciled_for_series` equivalents answer with `ReconciliationResult`,
  and the HTTP path consumes those through `get_local_offers`.
- **Read budget is fixed**: one observation query plus one bulk lookup each
  for mappings, cross-content claims and sync runs — four queries per
  reconcile call regardless of row count, no per-offer query, no network, no
  refresh, no write. Freshness, window and mapping rules take an explicit
  `now` so reads are deterministic and testable without a clock object.

## Phase 0 — Project setup

- [x] Provision `docker-compose` with Postgres + Django + Next.js services
- [x] Django project + app scaffolding, Postgres connection
- [x] Configure API-key seams for TMDb and Streaming Availability API (RapidAPI)
- [x] Confirm and document cache-first Streaming Availability API usage constraints

## Phase 1 — Data model & admin

- [x] Django models: `Movie`, `Region`, `Availability`, `LanguageInfo`, `LanguageEntry`, `Subscription`, `Preference`, `WatchlistItem`
- [x] Django admin config for manual `LanguageInfo` entry
- [x] Seed/admin path for single-user `Subscription` and `Preference` data

## Phase 2 — Single-movie search

- [x] TMDb search integration (title → `tmdb_id`)
- [x] Streaming Availability API client with Postgres caching (`last_checked_at`) and refresh-if-stale behavior
- [x] Filter offers to `subscription`/`free` type only, matched against configured subscriptions; rent/buy offers stay excluded
- [x] Ranking logic: strict priority (audio → subtitles → quality)
- [x] API endpoints for search and per-movie ranked availability results
- [x] Next.js search page with ranked results, best match highlighting, and missing-language-info entry path

## Phase 3 — Background refresh job

- [x] Django management command run by cron to refresh the stalest `Availability` rows
- [x] Default scheduled budget is 90 requests/day, leaving reserve under the 100 requests/day free-tier cap
- [x] Basic logging and refresh audit records for observability

## Phase 4 — Letterboxd watchlist free-to-watch check

- [x] CSV upload endpoint for Letterboxd watchlist export
- [x] Title+year → TMDb ID matching, with review/confirm screen for ambiguous matches
- [x] Bulk availability fetch path that respects the daily rate cap and cache-first architecture
- [x] Report endpoint: per movie, which region(s) have it free via subscriptions; unavailable movies flagged
- [x] Next.js upload/report flow, including summary analytics by country and provider

## Release-hardening plan Claude Code can execute next

Work through this in order. Do not make live Streaming Availability API calls during verification; tests should use mocks or cached/local test data.

1. **Verify documentation alignment**
   - Confirm `docs/API.md` and `docs/examples/api/watchlist_report_response.json` include the `summary` object returned by `GET /api/watchlist/report/`.
   - Confirm `docs/CRON.md` and `docs/DEPLOYMENT.md` use `--budget 90`, not `--budget 100`.
   - Confirm `docs/DELEGATION.md` clearly states Tasks 1–15 are historical and no longer active assignments.

2. **Run frontend verification**
   - `npm --prefix frontend test`
   - `npm --prefix frontend run lint`
   - `npm --prefix frontend run build`
   - If lint/build fails because the local dependency install or Next.js config is stale, fix the source/dependency mismatch and rerun.

3. **Refresh backend container dependencies**
   - Rebuild the dev stack so source dependencies such as WhiteNoise are installed in the backend image:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
     ```
   - Then run backend tests:
     ```bash
     docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python manage.py test tracker -v 2
     ```

4. **Inspect final diff before commit**
   - `git diff --stat`
   - `git diff -- docs frontend backend`
   - Check that no generated build artifacts, logs, secrets, or local environment files were added.

5. **Commit when verification is clean**
   - Suggested commit title: `Add frontend smoke tests and align release docs`
   - If backend tests still fail because a local container is stale or unavailable, report the exact command/output instead of marking the work fully verified.

## Prime Video MVP hardening (active — Claude Code, as of 2026-08-21)

The Prime Video MVP (`/api/prime/popular/`, `/api/prime/movie/<id>/`, homepage grid, movie detail, greyed-out
preferences) is merged, but two issues block it from rendering real data. This is the core integration path and
stays with Claude Code (not delegated). See `docs/PRIME_MVP_HARDENING.md` for the full plan.

- [X] **CC-1** Add `image.tmdb.org` to `frontend/next.config.mjs` `images.remotePatterns` — homepage posters
  currently crash at runtime (`next/image` with an unconfigured host); the build passes so it was missed.
- [X] **CC-2** Reconcile `services/streaming_api.py` parsing + the Prime provider id against one real
  movieofthenight response. `DEFAULT_PREFERENCE_PROVIDER` is the literal `"amazon"`, but the API's Amazon
  service is id `"prime"` / name `"Prime Video"`, so real lookups currently match nothing. Standardize on the
  service **id**; keep tests mocked (no live calls in tests).
- [X] **CC-3** Verify the click → auto-create Movie → live fetch (`PRIME_LIVE_FETCH`) → cache → ranked-offers
  path end-to-end; document `TMDB_API_KEY`/`RAPIDAPI_KEY` in `.env.example`; optional `seed_prime_fixture`
  command so the UI renders from cached sample data without spending quota.

## Phase 5 — Polish (active queue as of 2026-08-21)

Current post-Prime-MVP polish items. Delegatable ones are briefed in `docs/DELEGATION.md` (Task 16–21); the
Prime MVP hardening above and editable preferences stay with Claude Code.

- [x] Add CI to run backend tests and frontend test/lint/build on every pull request — **delegated: Task 16**
- [x] Homepage search box (find a specific movie, not just browse trending) — **delegated: Task 17**
- [x] Enhance ambiguous TMDb match review with posters and richer metadata — **delegated: Task 18**
- [x] Add broader frontend coverage for unhappy paths and API error states — **delegated: Task 19**
   - [ ] Add manual poster matching and title editing for movies — **delegated: Task 20**
   - [ ] Add detail view for movies with rating, synopsis, actors, and director — **delegated: Task 21**
- [ ] Fix search by actor and category — **delegated: Task 22**
- [ ] Add series import and display — **delegated: Task 23**
- [ ] Fix page jump on subscription provider click — **delegated: Task 24**
- [ ] Fix Free Streaming Providers and Major Streaming Providers display — **delegated: Task 25**
- [ ] Show only watchable titles on home page — **delegated: Task 26**

- [x] Phase C: Integration & UI — unofficial sources catalog tab, language badges, scrape panel — **done**
- [x] Review npm audit output and update vulnerable frontend dependencies deliberately — **delegated: Task 20**
- [x] Improve manual language-entry UX beyond the current functional form/admin paths — **delegated: Task 21 (backlog)**
- [x] Editable preferences end-to-end (un-grey UI, `PATCH /api/preferences/`, ranking reads the DB `Preference` row instead of `DEFAULT_PREFERENCE`) — **Claude Code (not delegated)**

## Explicitly deferred (not v1)

- Notifications
- Availability history / leaving-soon predictions
- IMDb / Trakt sync
- Browser extension
- Real user accounts / multi-tenancy
- Monetization
- Personal library sources (Plex/Jellyfin/custom archives)
- "Watch Together" synchronized playback + public movie rooms/timeline chat — a large, separate future direction (see SPEC.md "Future Directions"); would need its own phased plan (sync infra, provider adapters, browser extension, moderation) if ever pursued

## Open question for future ranking changes

The current ranking priority is audio language match → subtitle language match → quality. Confirm with the user before changing that priority order or treating a different order as fixed.

---

## Unofficial Sources Feature (Streaming Tracker v2)

### Overview

Add unofficial streaming sites as possible viewing options in the catalog. These sources are scraped for titles and language metadata, then matched to TMDB IDs for integration with the existing availability system.

### Sources to Support

**Priority sources (full catalog scraping):**
- RaiPlay (Italy)
- BBC (UK)  
- NHK (Japan)

**Secondary sources (from uncommitted unofficial_provideres.txt):**
- sflix.to, sflix2.to
- tinyzone-tv.com
- anime-related sites (animeunion.tv, meikyou-monogatari etc.)

### Implementation Plan

#### Phase A: Technical Foundation

1. **Create async scraper framework**
   - Build `backend/tracker/scrapers/` directory with base `BaseScraper` class
   - Implement async HTTP client using `aiohttp` with retry logic
   - Add language detection using `langdetect` library

2. **Data model extensions**
   - Add `UnofficialSource` model (name, base_url, country_code, active)
   - Add `UnofficialAvailability` model (source, tmdb_id, title, year, is_available, languages_json, last_scraped)
   - Create `LanguageTag` lookup table for audio/subtitle languages

3. **TMDB matching service**
   - Create `backend/tracker/services/tmdb_matcher.py` for fuzzy title matching
   - Implement confidence scoring (title match + year + genre overlap)
   - Add manual confirmation flow via Django admin or CLI prompt

#### Phase B: Source Scrapers

4. **RaiPlay scraper**
   - Handle Italian-language content
   - Map Italian language tags to `LanguageTag`
   - Extract available titles + streams

5. **BBC scraper**
   - Handle UK English content
   - Extract iPlayer streaming availability

6. **NHK scraper**
   - Handle Japanese-language content
   - Map Japanese language tags (JP, ja)

7. **Remaining unofficial sources**
   - Prioritize by traffic in unofficial_provideres.txt
   - Implement as time allows

#### Phase C: Integration & UI

8. **API integration**
   - Add `/api/v2/unofficial/` endpoints
   - Implement filtering by user preferences (language, country)
   - Add caching with staleness tracking

9. **Frontend updates**
   - Update catalog search to include unofficial results
   - Add source indicator badges (Unofficial / RaiPlay / etc.)
   - Maintain ranking: audio match → subtitle match → quality

#### Phase D: Automation

10. **Cron-scheduled refresh**
    - Daily scrape of all unofficial sources
    - Rate limit management: distribute 100 req/day across sources
    - Automatic TMDB ID matching; flag ambiguous matches for manual review

### Critical Constraints

- **100 API requests/day hard cap**: Scrapers operate on cached data, not live API calls. TMDB matching uses cached results.
- **Language handling**: Scraped titles in any language must map to TMDB's English/Japanese/etc. language codes.
- **Unofficial ToS**: Scraping must use public pages only; no login-required or rate-limited endpoints that violate terms.

### Success Criteria

- [ ] All three priority sources (RaiPlay, BBC, NHK) have working scrapers
- [ ] Movies from these sources appear in catalog search results
- [ ] Language preferences filter works for unofficial sources
- [ ] Daily cron job successfully updates availability data
- [ ] Total API requests stay under 100/day

### Key Files to Create/Modify

**New files:**
- `backend/tracker/scrapers/__init__.py`
- `backend/tracker/scrapers/base.py`
- `backend/tracker/scrapers/rai_play.py`
- `backend/tracker/scrapers/bbc.py`
- `backend/tracker/scrapers/nhk.py`
- `backend/tracker/services/tmdb_matcher.py`

**Modified files:**
- `backend/tracker/models.py` - Add new models
- `backend/tracker/admin.py` - Register new models
- `backend/tracker/api/views.py` - Add unofficial API endpoints
- `frontend/src/components/Catalog.tsx` - Display unofficial sources
- `frontend/src/api/client.ts` - API methods

### Timeline Estimate

- Phase A: 3-4 days
- Phase B: 5-7 days (parallelize across languages)
- Phase C: 2 days
- Phase D: 1 day
