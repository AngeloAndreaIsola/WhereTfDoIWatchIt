# Preferences / Watchlist Refactor Stash Audit

**Scope:** read-only audit of `stash@{2}` (`a37fb657`, “moved language preferences”), with the related `stash@{1}` (`282946115`, series content-type fix) and `stash@{3}` (`f626daa`, remove tabs / provider categories).  
**Baseline inspected:** `master` at `17ac398`. None of the three stash merge commits is an ancestor of `master`; all three trees differ from `master`. No tests were run because this is an inspection-only recovery audit.

## Verdict

Do **not** apply any stash wholesale. `stash@{2}` is a mixed UI relocation plus an incomplete catalog-language filter; `stash@{1}` contains a sound backend series filter but corrupts the test file by dropping four unrelated Link-TMDb tests; `stash@{3}` is a large, incompatible catalog rewrite that overlaps both and should be mined only as an API idea after its consumers are reworked.

## Feature inventory

| Feature | Evidence | Assessment | Recovery disposition |
|---|---|---|---|
| Move viewer-language UI from `/preferences` to Catalog | `stash@{2}` adds `LanguageFilter` and renders it below `FilterBar` on Catalog | UI-only, per-page state; it is never persisted to `Preference.language_rule`. The URL carries a `viewers` JSON value, but the catalog request never sends it. | Keep only after defining whether this is a transient filter or a saved preference. |
| Move viewer-language UI to Watchlist | `stash@{2}` adds the same panel and URL sync to `/watchlist` | Same non-persistence problem; Watchlist does send `viewers`, but the inspected filtering service has no `viewers` handling. Thus it is a no-op at the backend. | Do not recover as functional behavior. May reuse UI after server contract exists. |
| Simple audio/subtitle language filters | `FilterState` gains arrays; Catalog/Watchlist add `audio_languages` and `subtitle_languages` query parameters | Catalog backend accepts only one comma-containing string as one value, then tests exact membership. Multi-select values such as `EN,IT` cannot match a single `EN` offer. Watchlist forwards these parameters, but `watchlist_report` delegates to `apply_composable_filters`, which has no inspected language-param support. | Reimplement server-side with explicit AND/OR semantics and tests; do not cherry-pick. |
| Catalog language filtering implementation | `stash@{2}` modifies `unified_catalog` | Filters happen after all results are materialized and sorted, then before pagination. Official items use only `watchable_offers`, excluding available but non-entitled/nonreachable offers; scraper language data is accepted with inconsistent normalization. No regression tests accompany it. | Rebuild against the current availability/presentation contract. |
| Redirect `/preferences` | `stash@{2}` reduces the page to `router.replace('/catalog')`, removes its form and changes tests to accept a blank render | Removes the only persistence UI for `candidate_countries` and `Preference.language_rule`; navigation still retains a Preferences link. A client-side redirect yields a blank first render and is not a server redirect. | Reject. Current `master` instead leaves a read-only candidate-country page and describes language filters as Catalog/Watchlist UI. |
| Watchlist filter URL persistence | `stash@{2}` restores filters from `window.location.search` and calls `router.replace` | URL key mismatch: initial provider read uses `provider`, while Catalog uses `providers`; this page uses `content_type` on write while pre-stash used `type`. It writes `page=1` and `page_size=24` on every initial load, and reads neither `page` nor actors. It also omits `quality` from both report request and URL serialization despite including it in state. | Reimplement from current Watchlist URL-state behavior; selectively preserve the intent only. |
| Preserve preferences in model/migrations | No model or migration files are changed by any stash | `Preference.language_rule` already exists through migration `0010`; `audio_languages`, `subtitle_languages`, and `candidate_countries` already exist. There is no migration risk from applying this stash alone. But `Preference` has no shown unique constraint on `user_id`, while views call `.filter(user_id=1).first()`, so persistence is ambiguous if duplicates exist. | No migration to recover. Add a data/constraint plan only if changing persistence semantics. |
| Watchlist persistence/data model | No `WatchlistItem` model or migration change | The refactor only changes presentation/filtering. `WatchlistItem` uniqueness is `(movie, source)`, not `(user_id, movie)`, which is a pre-existing single-user seam risk, not introduced by these stashes. | No migration to recover. |
| Series scraper content-type filtering | `stash@{1}` adds `Q` filtering to `unified_catalog` for linked series/movie and unlinked mappings | Correctly treats unlinked scraper mappings as movies and maps `tv` to `series`. It is independent of preferences. | Recover backend hunk plus a clean, additive test. |
| Provider-category API replacing tabs | `stash@{3}` adds `categories` to unofficial catalog/offers endpoints and frontend API helpers | The API concept is useful, but the stash also rewrites 435 lines of Catalog and removes tab-era behavior. It overlaps the same files modified by `stash@{2}` and assumes an older UI/API shape. | Do not apply. Reimplement category query support separately if still desired. |

## Backend and compatibility risks

1. **The Viewer Language Intersection control is not wired end-to-end.** `LanguageFilter` stores a `ViewersRuleNode` locally. Catalog serializes it into the browser URL but does not include `viewers` in `fetchUnifiedCatalog`; Watchlist includes it, but the inspected `apply_composable_filters` implementation does not parse it. The filter therefore cannot enforce its “all viewers” claim.
2. **The relocation removes persistence rather than moving it.** The old Preferences page loaded and PUT `audio_languages`, `subtitle_languages`, `candidate_countries`, and `language_rule`. The stash replacement only redirects. The Catalog preference GET is solely for badge highlighting, not a save path.
3. **Language values have conflicting representations.** The UI normalizes Viewer builder entries to canonical codes, Catalog filter logic uppercases audio but lowercases and underscore-normalizes subtitles, and query arrays are joined with commas. Define the API representation and normalization in one shared backend service before recovery.
4. **Changing availability filtering changes product semantics.** `unified_catalog` filters official entries from `watchability.watchable_offers`, not all discovered offers. That couples language-filter results to subscriptions, country reachability, and the resolver’s definition of watchable. This may be correct, but it must be explicit and tested.
5. **No new model/migration is present.** Applying a frontend-only “preference persistence” story without a proper preference endpoint contract can silently discard existing saved rules. Existing model facts are pre-stash: `language_rule` was added in migration `0010`; no uniqueness constraint for `Preference.user_id` was shown.
6. **The test changes weaken coverage.** `stash@{2}` replaces preference error/load tests with redirect/no-op assertions. It provides no tests for URL encoding, backend language matching, viewer intersection, or preference persistence.

## Stash-specific findings

### `stash@{1}` — series not showing fix (`282946115`)

- **Candidate backend change:** import `Q` and filter scraper mappings by `movie__content_type`, retaining unlinked mappings under movies.
- **Critical test-file loss:** the edit inserts one new unified-catalog test where four `LinkTMDBEndpointTests` methods previously lived. The resulting test inventory loses:
  - `test_links_tmdb_and_updates_movie`
  - `test_uses_title_override_when_provided`
  - `test_requires_tmdb_id`
  - `test_returns_404_when_movie_not_found`
- **Recovery rule:** extract only the `views.py` hunk; add the series test without replacing/deleting the Link-TMDb class.

### `stash@{2}` — moved language preferences (`a37fb657`)

- Its normal worktree delta is 10 tracked files: 180 additions / 282 deletions. A separate untracked-file payload contains `frontend/components/LanguageFilter.tsx` and is required for the altered Catalog/Watchlist imports to compile.
- It overlaps `stash@{1}` in `backend/tracker/views.py` and `backend/tracker/tests/test_views.py`, and `stash@{3}` in Catalog and `FilterBar`.
- `ViewerLanguageBuilder` is imported directly but unused in both modified Catalog and Watchlist pages; the usable wrapper is `LanguageFilter`.
- Its API parameters are not backward-compatible in a reliable way: Catalog uses `providers`; Watchlist serializes `provider`; old Watchlist request used `type`, while this stash emits `content_type`.

### `stash@{3}` — remove tabs / provider categories (`f626daa`)

- Backend: `categories=free,official` support in unofficial catalog and offer endpoints; legacy `include_official=true` remains fallback behavior.
- Frontend: changes `fetchUnofficialProviders` to accept an optional category string and adds categories to the unofficial filter type.
- Risk: the broader Catalog rewrite removes tabs and old state/components while assuming an older FilterBar API. It is not a safe complement to the preference stash; both independently rewrite Catalog and FilterBar.

## Recovery candidates (split, not applied)

| Candidate | Source | Files/hunks | Preconditions | Recommendation |
|---|---|---|---|---|
| A — series scraper content type | `stash@{1}` | `backend/tracker/views.py`: `Q` import and scraper `content_type` query handling | Rebase mentally onto current `unified_catalog`; restore/add test without deleting Link-TMDb tests | **High value, low scope** |
| B — language filter component only | `stash@{2}` untracked payload (`stash@{2}^3`) | `frontend/components/LanguageFilter.tsx` | Confirm the current `ViewerLanguageBuilder` API and decide transient vs persisted rules | **UI seed only; not functional alone** |
| C — URL state affordance | `stash@{2}` | narrow Catalog/Watchlist URL parse/serialize intent | Define one query schema, preserve page/actors/quality, and add browser tests | **Reimplement; do not apply hunk** |
| D — persisted preference editing | pre-stash Preferences page, not the redirect | `/preferences` form and `/api/preferences/` PUT contract | Decide saved defaults vs per-search filters; resolve duplicate-`Preference` risk | **Keep existing capability; do not take stash redirect** |
| E — provider category API | `stash@{3}` | narrow `api_v2.py` category parser and corresponding typed frontend helper | Define allowed categories and test legacy `include_official` compatibility | **Separate feature; no Catalog rewrite** |
| Reject — whole stashes | all | full stash applies | None | **Do not apply** |

## Required verification before implementation

1. Add API tests covering one/multiple audio and subtitle selections, normalization, empty language metadata, official versus scraper offers, and the intended entitlement/reachability semantics.
2. Add a viewer-intersection contract test that proves each named viewer is satisfied; make both Catalog and Watchlist send the same request representation.
3. Add frontend tests for deep-linked URL restoration, page changes, actors, providers, quality, and malformed `viewers` JSON.
4. Preserve and run all Link-TMDb endpoint tests when recovering the series hunk.
5. If preferences become persistent again, test GET/PUT round trips and decide whether to add a unique `Preference.user_id` constraint with a data migration after checking production duplicates.

## Audit limits

- No repository state, stashes, branches, code, migrations, or tests were changed or run.
- Findings are based on Git object inspection against the stated `master` baseline, not runtime verification.
