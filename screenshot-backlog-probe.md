# WTDW — Historical Screenshot Backlog Verification Probe

**Repository:** `AngeloAndreaIsola/WhereTfDoIWatchIt`, `master` @ `ff091ad` (PR #74 merge).
**Probe date:** 2026-11-17. **Mode:** read-only inspection + disposable-DB offline tests.
**Source of the 21 items:** Apple Reminders list `WhereTfDoIWatchIt` has 23 entries, 2 completed ("add an apply filter button", "Duration filter") — exactly 21 open requests, matching the investigation scope. Enumerated read-only via `remindctl list "WhereTfDoIWatchIt" --json`.
**WT backlog source:** `hermes-ai-setup-help/docs/audits/WTDW_FEATURE_AUDIT.md`, `docs/durable/WHERETF-SHORTLIST.md`, `orchestrator/WhereTfDoIWatchIt_Jules_Development_Pack/IMPLEMENTATION_PLAN.md`.
**Issue source:** `gh issue view/list --repo AngeloAndreaIsola/WhereTfDoIWatchIt` (read-only), issues #93–#104.

## Method and safety record

- Static evidence: `grep`/file reads; line numbers refer to `master` at `ff091ad`.
- Dynamic evidence: 8 deterministic Django `TestCase`s in a scratch module outside the repo (`~/.hermes/cache/scratch/wtdw-verify/probe_tests.py`), run against a **disposable** local Postgres database `wtf_probe_scratch`; Django created and destroyed `test_wtf_probe_scratch`. Command:
  `cd backend && DJANGO_SETTINGS_MODULE=config.settings POSTGRES_DB=wtf_probe_scratch POSTGRES_HOST=localhost POSTGRES_USER=tracker POSTGRES_PASSWORD=tracker_dev DJANGO_SECRET_KEY=offline-probe-only TMDB_API_KEY= RAPIDAPI_KEY= WATCHMODE_API_KEY= ../.venv/bin/python manage.py test probe_tests --noinput -v 2`
- `config/test_settings.py` (SQLite) was tried first and **fails**: `tracker.0001_initial` uses Postgres `ArrayField` → `sqlite3.OperationalError: near "[]": syntax error`. Local Postgres with a scratch DB was used instead; the project's `tracker` DB was never read or written.
- All TMDb/refresh seams were mocked in the final run (`request_availability_refresh`, `request_import_refresh`, `enrichment.resolve_or_create_movie`, `tmdb.requests.get`). **Disclosure:** two earlier iterations of the probe (before the seam was patched) issued two unauthenticated `GET https://api.themoviedb.org/3/movie/981001` requests with an empty key; both returned HTTP 401, no key was used and no metered quota was consumed. No other live provider calls were made.
- Final run: `Ran 8 tests ... FAILED (failures=6)`; 6 failures are the intended counterfactual assertions (bugs present), 2 passes prove import dedup and watched-history semantics. Log: `~/.hermes/cache/scratch/wtdw-verify/run5.log`.
- Three verification subagents were dispatched in parallel but all three failed before returning results (free-model period ended / provider rate-limit 429). **No finding in this report relies on them** — every verdict below was measured directly in this session.
- No issues, PRs, commits, branches, worktrees, credentials or user data were modified.
- **Pre-existing dirty state, not created by this probe:** modified `backend/availability/services/legacy_ingest.py` (adds `unverified_discovery` guard against automatic canonical TMDb association — relevant to S19, but uncommitted, so NOT counted as master behavior); untracked `.worktrees/`, `docs/audits/stage2q/`, `docs/audits/stage2r/`, `worktree-feature-consolidation-report.md`.

**Verdict vocabulary:** `IMPLEMENTED_VERIFIED` | `PARTIALLY_IMPLEMENTED` | `CONFIRMED_BUG` | `FEATURE_MISSING` | `NEEDS_REPRODUCTION` | `ALREADY_TRACKED` | `INTENTIONAL_BEHAVIOR`. "Not proven" marks evidence this probe did not measure.

## 21-item matrix

| Item | Implemented status | Code evidence | Test evidence | Existing issue/WT | Recommendation | Priority |
|---|---|---|---|---|---|---|
| S01 "If checking availability don't hide old results" | `IMPLEMENTED_VERIFIED` | `frontend/components/LastKnownOffers.tsx:16`; `frontend/components/AvailabilityState.tsx:73-87`; `frontend/components/OfferRow.tsx:82` retain stale evidence as "last seen", never fresh | `frontend/components/__tests__/LastKnownOffers.test.tsx:28,42,51`; `OfferRow.test.tsx:186`; `backend/tracker/tests/test_stale_offer_semantics.py` | #97 (wider accuracy verification, not this UI) | No new issue; fold into #97 verification | P2 |
| S02 "localization" | `PARTIALLY_IMPLEMENTED` | i18n skeleton complete: `frontend/lib/i18n/`, `frontend/messages/{en,it,ja}.ts`, `LocaleSwitcher`; no translated-title cache/alias provenance in `backend/tracker/services/tmdb.py` | `frontend/__tests__/i18n.test.tsx`, `i18n-layout.test.tsx` (PR #71 merged); localized-title matching NOT tested | WT-006 COMPLETE; WT-007/008/009 BLOCKED | Design decision first; do not duplicate WT-007/008 | P2 |
| S03 "Tubi fails" | `PARTIALLY_IMPLEMENTED` | `backend/availability/sources/tubi.py:16,300-320,321-370` — catalogue-driven, US egress required, EU egress raises `SourceFetchError`; `docs/CRON.md` "Tubi scheduling notes" (500 pages/run, checkpoint, never authoritative) | `availability/tests/test_tubi.py`, `tubi_fixtures.py`, `test_existing_source_checkpoints.py` (offline fixtures); no live DB queried, so current failure counts NOT PROVEN | WT-071 PARTIAL, WT-060, #97 | Operator decision on US egress; no code ticket until RefreshRun evidence | P2 |
| S04 "Add 'leaving soon' and 'new arrivals'" | `ALREADY_TRACKED` | No `expiresSoon`/`availableSince`/calendar code in tracked app code (only CBC "COMING SOON" index filtering at `cbc_gem.py:188`); `refresh_lifecycle.py` is refresh-run lifecycle, not offer lifecycle | WT audit rows 47,101-108; no feature test | WT-018/019, WT-073–080 (rights/quota gates), WT-079 PARTIAL | Do not open duplicate; unblock gates first | P2 |
| S05 "multiple same movies in watchlist when you import it" | `CONFIRMED_BUG` | `views.py:3350-3361` creates `source="manual"`; `views.py:1521-1525` creates `source="letterboxd_resolved"`; `models.py:496-499` uniqueness is `(movie, source)` only | `run5.log`: manual→resolve `['manual','letterboxd_resolved']`; resolve→manual `['letterboxd_resolved','manual']` (2 rows, HTTP 201 both); Letterboxd reimport stays 1 row | #96 + WT-002/003/004 (ownership/concurrency adjacent; this cross-source symptom not named) | New P1 issue (Ticket 1), cross-reference #96 | P1 |
| S06 "Language chips (2-char → full language chip)" | `PARTIALLY_IMPLEMENTED` | `frontend/components/LanguageBadge.tsx:18-46` renders `getLanguageDisplayName(code)`; `LanguagePicker.tsx` exists; compact mode deliberately shows raw code | No focused chip-contract test located in this probe (NOT PROVEN); no frontend suite run | none | Add focused test; low-cost cleanup | P3 |
| S07 "Add hyperlink for actors and expand the show more" | `PARTIALLY_IMPLEMENTED` | `frontend/app/movie/[tmdb_id]/page.tsx:506-530`, `tv/[tmdb_id]/page.tsx:535-558`: first 8 cast names in `<span>` badges, static `+N more`, no `<a>` and no expand handler | No targeted test located (NOT PROVEN) | none | New P2 issue (Ticket 3) | P2 |
| S08 "Add like, dislike and rate of a movie" | `PARTIALLY_IMPLEMENTED` | `models.py:1100-1107` stores `UserMovieData.rating`; `views.py:3490-3544` accepts `rating` on mark-watched; movie/TV pages show it (`movie/[tmdb_id]/page.tsx:456-458`); no like/dislike field or UI anywhere | `backend/tracker/tests/test_letterboxd.py:255` (imported ratings); no like/dislike test (NOT PROVEN) | none | Ticket 4 for rating-edit UI; likes/dislikes need product decision | P2 |
| S09 "Instagram saved videos about movies" | `ALREADY_TRACKED` | No Instagram importer in app code (`grep` negative); the user's local export is not a product integration | none | WT-053 BLOCKED (`real_export_schema` gate), WT-054 MANUAL MEDIA GATE, WT-055 candidate | Do not duplicate; gate first | P3 |
| S10 "Extension to import Netflix/Prime library" | `ALREADY_TRACKED` | `extension/` manifest/background/popup, `backend/tracker/extension_views.py`, migration `0048`, PR #74 merged at HEAD | `docs/verification/pr74/` E2E evidence; `extension` tests | #100 explicitly covers real-site adapters/progress; WT-049 COMPLETE, WT-050 BLOCKED | Covered by #100 | P2 |
| S11 "Cron" | `PARTIALLY_IMPLEMENTED` | `docs/CRON.md`: daily `refresh_availability --budget 90`, catalogue every 6h plus per-source slots, `worker`/`catalogue-worker` services at `docker-compose.yml:79,108`; no in-repo crontab; cleanup command not scheduled | Documentation inventory only (no scheduler test) | #104 (abandoned `RefreshRun` cleanup unscheduled), #98 | #104 covers the gap; no new issue | P2 |
| S12 "Profile page with import pages + recurring cron" | `ALREADY_TRACKED` | No profile directory/page linked imports; `google_interests.py` and watchlist import UI exist but linked-list sync absent | none | WT-025/026/027/085 BLOCKED (auth/owner gates WT-044/WT-005) | Do not duplicate; product/auth decision | P3 |
| S13 "Movie card fetch poster only when you click detail" | `FEATURE_MISSING` | `MovieCard.tsx:44-57` renders poster from list API via `next/image` (lazy transport, **not** on-demand metadata enrichment); detail pages also use `next/image` | Static inspection; no browser network trace (NOT PROVEN) | none; contradicts S20 | Product decision (ticket 6) | P3 |
| S14 "Subscription page refresh only what you subscribe to" | `PARTIALLY_IMPLEMENTED` | `frontend/app/subscriptions/page.tsx:384-392` per-card `onRefresh`; bulk buttons only for free BVOD (`406-425`) and scraper sources (`497-511`); `views.py:911-950` `provider_refresh` targets exactly one source | `backend/tracker/tests/test_provider_refresh.py`; no subscribed-only bulk test | none | New P2 issue (Ticket 5) | P2 |
| S15 "Add Rotten Tomatoes score?" | `ALREADY_TRACKED` | No RT/IMDb rating fetch in code; `IMPLEMENTATION_PLAN.md:665,674,702` forbids scraping without rights | none | WT-022 MANUAL RIGHTS GATE; WT-023/024 BLOCKED | Do not duplicate; rights decision | P3 |
| S16 "Mayu Netflix ads-tier regions" | `ALREADY_TRACKED` | `backend/tracker/services/access.py` `AccessType`/`UserAccess.entitles()`; PR #57 canonical provider matching; subscription UI has access-mode control | `tracker/tests/test_access_modes_api.py`, `test_access_resolver.py` | #103 explicitly covers tiers/add-ons/country caveats; WT-016/017 | Covered by #103 | P2 |
| S17 "Prime scraper error on VPN region discrepancy" | `PARTIALLY_IMPLEMENTED` | `backend/availability/sources/prime_video.py:760-779` checks `currentTerritory`, raises explicit `SourceFetchError` naming served vs declared territory and egress cause; fails closed; `docs/CRON.md` NL-egress note | `availability/tests/test_prime_video.py` + `fixtures/prime_video` (offline); user-facing UI wording NOT PROVEN | #97 (accuracy) | Reproduce UI message before ticket | P2 |
| S18 "Import watched also imports everything into watchlist" | `INTENTIONAL_BEHAVIOR` | `letterboxd.py:752-759`: only `file_type == "watchlist"` creates `WatchlistItem`; watched/ratings/reviews/diary set `UserMovieData.is_watched=True` | `run5.log` watched-history test OK (0 watchlist rows, `is_watched=True`); `test_letterboxd.py:806` | none / WT-002 adjacent | Document semantics; no issue | P3 |
| S19 "Import still doesn't work when clear which item is right" | `CONFIRMED_BUG` | `letterboxd.py:830-847`: `_select_match`/`_is_ambiguous` compare **year only**, never title similarity; exact-title candidates with no same-year match, or two same-year candidates, are pushed to ambiguous | `run5.log`: exact title "The Matrix" (no year) → 0 resolved / 1 ambiguous; "Fight Club" + director's cut same year → 0 resolved / 1 ambiguous | WT-003/004 adjacent; existing test `test_import_rows_preserves_myanimelist_source` uses `MagicMock` and does not pin search kwargs | Combine with S21 into Ticket 2 | P1 |
| S20 "Load all posters in a page" | `IMPLEMENTED_VERIFIED` | `MovieCard.tsx:44-57`, `frontend/app/watchlist/page.tsx:434-445`, `frontend/app/catalog/page.tsx:449-495`: list APIs already supply `poster_path`, all cards render it; explicit "No Image"/"No Poster" fallback | Static inspection; no browser request-count measurement (NOT PROVEN) | none | No new issue; decide jointly with S13 | P3 |
| S21 "Import does not match against series" | `CONFIRMED_BUG` | `letterboxd.py:702` `client.search(row.title, year=row.year)` — no `content_type`, while `tmdb.py:78-98` supports `movie/series/tv/all` and `views.py:567,1297` pass it; `models.py:24-28` has `content_type` | `run5.log`: captured `('Anime Show', {'year': 2009})`; series stored with `content_type='movie'`, 2 failures | #101 is season-level availability (different); WT-003/004 | Ticket 2 (same root cause as S19) | P1 |

## Confirmed-bug detail (executed tests)

Final run `run5.log`: `Ran 8 tests — FAILED (failures=6)`. The six failures are assertions written against the *desired* contract; two passing tests pin correct behavior already present.

| Probe assertion | Measured result | Verdict |
|---|---|---|
| manual add + ambiguous resolve must leave one row | HTTP 201/201, `['manual', 'letterboxd_resolved']` (2 rows) | CONFIRMED_BUG |
| resolve + manual add must leave one row | HTTP 201/201, `['letterboxd_resolved', 'manual']` (2 rows) | CONFIRMED_BUG |
| Letterboxd import + reimport over an existing manual row | `['manual']` (1 row), both runs OK | INTENTIONAL_BEHAVIOR (import path already reuses any source) |
| watched-history row creates no `WatchlistItem`, sets `is_watched` | OK, 0 watchlist rows | INTENTIONAL_BEHAVIOR |
| series search must pass `content_type` in `('series','tv','all')` | `('Anime Show', {'year': 2009})` | CONFIRMED_BUG |
| series-only title must be stored as `content_type='series'` | stored `movie` | CONFIRMED_BUG |
| exact title, missing year, must resolve | `resolved=0, ambiguous=1` | CONFIRMED_BUG |
| exact title + same-year collision must resolve | `resolved=0, ambiguous=1` | CONFIRMED_BUG |

## Work already covered (do not duplicate)

- **#96 / WT-002** — per-user ownership, `(movie, source)` uniqueness, authenticated scoping. It covers the *schema* half of S05 but its acceptance criteria do not mention cross-source membership reuse; Ticket 1 is a distinct, testable symptom and should cross-reference #96 rather than be closed by it.
- **#100 / WT-049–WT-050** — extension foundation merged (PR #74); real-site adapters/progress remain open. S10 fully covered.
- **#101** — season/episode availability and movie/TV ID collisions; distinct from S21's import-time content-type bug (though both mention series).
- **#103 / WT-016–WT-017** — entitlement tiers, add-ons, country caveats. S16 fully covered.
- **#104** — scheduling of `cleanup_availability_history` for abandoned `RefreshRun` rows; part of S11's gap.
- **#98** — re-benchmark bulk refresh and metered-gate activation criteria; adjacent to S14, not a subscribed-only-refresh feature.
- **WT-006** i18n skeleton (S02 display side); **WT-018/019/073–080** lifecycle (S04); **WT-022–024** ratings rights (S15); **WT-025/026/027/085** recurring linked imports (S12); **WT-053/054/055** Instagram (S09). All already in the WT registry — no new issue.

## Issue-ready tickets

### Ticket 1 — [P1][Bug] Cross-source watchlist add/resolve creates duplicate rows for the same movie

**Description:** Adding a title manually and then resolving the same TMDb id from an ambiguous import (or the reverse) produces two `WatchlistItem` rows with different `source` values. `UniqueConstraint(fields=["movie", "source"])` cannot prevent this, and both endpoints check existence only within their own source.

**Current behavior (measured):** `POST /api/watchlist/add/` writes `source="manual"` (`backend/tracker/views.py:3358-3362`); `POST /api/watchlist/resolve/` uses `get_or_create(..., source="letterboxd_resolved")` (`views.py:1521-1525`). Test run: 2 rows in both orders, both requests HTTP 201. Letterboxd CSV import itself already reuses any existing row (`services/letterboxd.py:754-763`), so the defect is specific to the add/resolve pair.

**Expected behavior:** One canonical watchlist membership per `(user, movie)` regardless of which UI path created it; provenance may be recorded separately without multiplying membership rows.

**Acceptance criteria:**
- [ ] Adding a movie manually, then resolving the same `tmdb_id` (and vice versa), yields exactly one `WatchlistItem` row and the second action is idempotent (documented response, not a 201 duplicate).
- [ ] Reimport, ambiguous resolution and manual add all share one membership-lookup helper; source provenance is preserved without using `source` as an identity key.
- [ ] The decision on `(user, movie)` vs `(user, movie, source)` uniqueness is recorded against #96/WT-002 before any schema change; no unbackfilled constraint migration.
- [ ] Regression tests cover both directions, repeat calls, and concurrent inserts on a disposable Postgres DB.
- [ ] Cross-references #96, WT-002/003/004; no overlapping change to auth scoping.

### Ticket 2 — [P1][Bug] Letterboxd/MyAnimeList import is movie-only and rejects unambiguous matches

**Description:** `import_rows()` searches TMDb without `content_type`, so series can only match movies; the matcher then compares years only and sends exact-title matches to manual review.

**Current behavior (measured):**
- `backend/tracker/services/letterboxd.py:702` calls `client.search(row.title, year=row.year)`; `TMDbClient.search` defaults to movie (`services/tmdb.py:85`) although it supports `series`/`tv`/`all` (`:78-98`). Captured kwargs: `{'year': 2009}`.
- A series-only candidate is stored as `content_type="movie"`, colliding with film namespaces.
- `_select_match`/`_is_ambiguous` (`letterboxd.py:830-847`) never compare titles: "The Matrix" with no year alongside "The Matrix Reloaded" → `resolved=0, ambiguous=1`; "Fight Club" alongside "Fight Club Director's Cut" with the same year → `resolved=0, ambiguous=1`.
- Existing test `test_import_rows_preserves_myanimelist_source` passes because its `MagicMock` hides the missing search kwarg.

**Expected behavior:** MyAnimeList series use series-aware search and retain `content_type="series"`; an exact (normalized) title match that is unique after title-then-year narrowing is resolved automatically; genuinely conflicting candidates still require manual confirmation and never create a false canonical association.

**Acceptance criteria:**
- [ ] Import passes a content type derived from row provenance (`myanimelist` → series); mocked tests assert the exact kwargs.
- [ ] Series/film name collisions and missing-year rows resolve to the correct `tmdb_id` and `content_type`, with fixtures for both.
- [ ] Exact-title precedence cannot downgrade a clear match to ambiguous; a test pins the negative case (two genuinely equal titles/years remain ambiguous).
- [ ] Original/localized title aliases may be *candidates* only; no automatic canonical mapping without explicit acceptance (respect the uncommitted `legacy_ingest.py` guard's intent).
- [ ] Watched-history rows still do not create watchlist items; reimport remains idempotent.
- [ ] Update the MagicMock-based test to assert search kwargs so the bug cannot silently return.

### Ticket 3 — [P2][Feature] Detail page: link cast members and make the full cast expandable

**Current:** `frontend/app/movie/[tmdb_id]/page.tsx:506-530` and `tv/[tmdb_id]/page.tsx:535-558` render at most eight cast badges as plain `<span>`s with a static `+N more` label; nothing is clickable and the remainder cannot be revealed.
**Expected:** Each cast member links to a relevant person page (using the existing `/api/search/person` data shape), and "show more" expands/collapses the list with an accessible control.
**Acceptance criteria:** links use canonical person ids where available; expand/collapse works by mouse and keyboard; movie and TV detail pages behave identically; component tests cover >8 cast, missing character, and empty cast.

### Ticket 4 — [P2][Feature] Expose user rating editing in the UI (like/dislike needs product decision)

**Current:** `UserMovieData.rating` exists (`models.py:1104`), is writable through `POST /api/watched/<tmdb_id>/` (`views.py:3490-3544`), and is displayed read-only on detail and watched pages (`movie/[tmdb_id]/page.tsx:456-458`). There is no in-page edit control and no like/dislike state.
**Expected:** Signed-in owner can edit/clear their own rating from the detail page; TMDb rating stays visually distinct. Like/dislike semantics are an explicit product decision (binary flag vs 1–10 rating, reversibility, export behavior) before any schema work.
**Acceptance criteria:** rating edit/clear persists and round-trips; TMDb rating is never overwritten; tests cover invalid ranges and null; a separate decision note covers like/dislike.

### Ticket 5 — [P2][Feature] Bulk refresh limited to subscribed services

**Current:** `frontend/app/subscriptions/page.tsx` offers per-card refresh for every provider (`:384-392`), bulk refresh for free BVOD (`:406-425`) and scraper sources (`:497-511`); `POST /api/providers/<slug>/refresh/` is single-source by construction (`views.py:911-950`). No "refresh only my subscriptions" action.
**Expected:** One explicit bulk action refreshes only providers with `is_subscribed=True`, reusing the same per-source queue/lock/error reporting, without touching subscription state or consuming metered quotas.
**Acceptance criteria:** selection is derived from the saved subscription set; action is idempotent and reports per-source outcomes; tests prove non-subscribed sources are never enqueued and subscription flags are unchanged; quota policy unchanged (#98).

### Ticket 6 (product decision, not ready to implement) — poster loading policy

S13 asks to defer poster fetches until detail click; S20 asks to load all posters in a page. Current code already loads all list posters from the list API using `next/image` lazy transport, with "No Image" fallbacks; this is lazy loading, **not** on-demand poster metadata enrichment. Decide whether to (a) keep current behavior, (b) stop requesting posters on cards, or (c) add explicit enrichment-on-click. No browser network trace was captured in this probe, so request-count claims are NOT PROVEN.

## Ranked groups — issues awaiting approval

**Group 1 — ready to create (issue-ready text above):**
1. Ticket 1 — cross-source watchlist duplication (P1, deterministic evidence, cross-reference #96).
2. Ticket 2 — movie-only/ambiguous import matcher (P1, deterministic evidence, combine S19+S21).
3. Ticket 3 — actor links + expandable cast (P2).
4. Ticket 4 — user rating editing UI (P2; like/dislike split out by decision).
5. Ticket 5 — subscribed-only bulk refresh (P2).

**Group 2 — awaiting reproduction or a product decision:**
1. S03 Tubi — need live `RefreshRun` evidence and a US-egress decision (#97 adjacent).
2. S17 Prime territory UX — backend error is explicit; need the actual user-facing message reproduced.
3. S02 localized titles / naming convention — WT-007/008 design gate.
4. S04 lifecycle "leaving soon"/"new arrivals" — WT-018/019/073–080 rights/quota gates.
5. S09 Instagram saved posts — WT-053/054 real-export and media gates.
6. S15 Rotten Tomatoes — WT-022 rights gate.
7. S12 recurring imports / profile page — WT-025/026/027 + auth gates.
8. S06 / S07 / S08 / S13 / S20 — focused frontend test coverage and the poster-policy decision above.
9. S01, S10, S16, S18 — verified/tracked; no new issue.

PROBE_COMPLETE — AWAITING_ISSUE_APPROVAL
