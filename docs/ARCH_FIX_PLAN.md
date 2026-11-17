# Historical Architecture Fix — Implementation Plan

> Historical planning record, not current architecture documentation. The
> candidate work described below was completed or superseded by the canonical
> availability/read-path work. In particular, `tmdb_matcher.py` was never an
> implementation (it was an unused comment-only placeholder) and was removed
> in P3. Consult `docs/AVAILABILITY_REDESIGN_REPORT.md` and the source/tests
> for current behavior; do not execute this plan as a migration guide.

Completed: Candidate 1 (enrichment scatter) — `e6e3ce8`.
Completed: Candidate 4 (presentation seam) — `b21130e`.

---

## Candidate 2 — Unify two offer-ranking implementations [Strong, in-process]

### Problem
`ranking.py:rank_offers` and `watchability.py:evaluate_watchability`
re-implement the same audio/sub/quality scoring. The `elif pref_audio or
pref_subs` branch has a **silent bug** — the score tuples differ:

| path | ranking.py | watchability.py |
|---|---|---|
| `lang_rule` | `(match, quality, audio, sub)` | `(match, quality, audio, sub)` ✅ |
| `elif` | `(match, audio, sub, quality)` | `(match, **audio, sub, quality**)` ✅ |
| `else` | `(match, quality, audio, sub)` | `(match, quality, audio, sub)` ✅ |

(The real divergence is: `ranking.py` puts `quality_rank` before `audio_rank`
in the `else` and `lang_rule` branches; `watchability.py` does the same.
The `elif` branch is where they diverge — ranking.py:96 has
`(match, audio, sub, quality)` while watchability.py:193 also has
`(match, audio, sub, quality)`. **They are actually the same now.** The
review flagged "already differ subtly" — I verified both files and the
tuples match. The risk is future drift.)

### Approach
Extract a single scoring module that both consumers call. Keep output
shapes separate (`RankedOffer` vs `EvaluatedOffer`) since their
downstream consumers differ.

### Files to touch
| File | Change |
|---|---|
| `services/score.py` (new) | `ScoredOffer` dataclass + `score_offer(offer, preference, lang_rule)` returning `(is_match, score_tuple, audio_rank, sub_rank, quality_rank)` |
| `services/ranking.py` | Remove inline scoring logic; call `score_offer` |
| `services/watchability.py` | Remove inline scoring logic; call `score_offer` |
| `tests/test_ranking.py` | No changes needed — test `rank_offers` end-to-end |
| `tests/test_watchability.py` | No changes needed — test `evaluate_watchability` end-to-end |

### Risk
Low. No output-shape changes. Score tuples stay identical. Tests are
regression coverage.

---

## Candidate 3 — Canonical `Offer` dataclass instead of drifting dicts [Worth exploring, local-substitutable]

### Problem
`cache._read_offers` returns `list[dict]` with ad-hoc keys:
`"audio"`, `"subs"`, `"service"`, `"is_scraped_source"`. Every consumer
re-guesses the shape with `.get("audio") or .get("audio_languages") or
[]`. `StreamingOffer` already exists in `streaming_api.py` for the wire
format but is not used internally.

### Approach
Add an internal `Offer` dataclass (or reuse/extend `StreamingOffer`) in
`services/offers.py`. `cache._read_offers` returns `list[Offer]`.
`ranking.py` and `watchability.py` accept `Iterable[Offer]` instead of
`Iterable[dict]`. Dict-key guessing is eliminated.

### Files to touch
| File | Change |
|---|---|
| `services/offers.py` (new) | `Offer` dataclass with canonical fields: `country`, `service`, `offer_type`, `quality`, `url`, `audio_languages`, `subtitle_languages`, `is_scraped_source` |
| `services/cache.py` | `_read_offers` returns `list[Offer]` |
| `services/ranking.py` | `rank_offers` accepts `Iterable[Offer]`; remove `.get("audio") or .get("audio_languages")` |
| `services/watchability.py` | `evaluate_watchability` accepts `Iterable[Offer]`; remove `.get("audio") or .get("audio_languages")` |
| `views.py` `prime_movie` | No change — calls `cache._read_offers` already |
| `api_v2.py` `unofficial_movie_offers` | Minor: builds `Offer` objects instead of dicts |
| `tests/test_cache.py` | Update assertions for Offer type |
| `tests/test_ranking.py` | Pass `Offer` objects instead of dicts |
| `tests/test_watchability.py` | Pass `Offer` objects instead of dicts |

### Risk
Medium. Shape change on the cache→consumer seam. All call sites must be
updated. But `Offer` is internal — no API contract changes. Tests give
regression coverage.

---

## Candidate 4 — One seam for provider presentation [Complete]

### Problem
Three independent paths answer "given a raw offer slug, what does the
UI show?":

1. `views.py:prime_movie` (lines 1130–1177) — builds `provider_map`,
   classifies official/free, falls back country, resolves display name
2. `watchability.py:evaluate_watchability` (lines 123–168) — builds
   `provider_map`, resolves `service_display`, `country_display`
3. `api_v2.py:unofficial_movie_offers` (lines 322–331) — hand-rolls
   `service_display` and `source_indicator` lookup

### Approach
Extract `present_offer(offer, provider_map) -> PresentedOffer` where
`PresentedOffer` carries: `service_display`, `source_indicator`,
`country` (observed on the offer; provider home country is not substituted). Each caller builds `provider_map` once and
passes it in.

### Dataclass
```python
@dataclass
class PresentedOffer:
    service_display: str       # provider.name or service_display_name(slug)
    source_indicator: str      # provider.source_indicator or "Official"
    country: str               # observed offer country; blank stays unknown
    is_official: bool          # provider.category in {OFFICIAL, FREE}
```

### Files to touch
| File | Change |
|---|---|
| `services/presentation.py` (new) | `PresentedOffer` dataclass + `present_offer(offer, provider_map)` |
| `views.py:prime_movie` | Replace lines 1130–1177 with `present_offer` call |
| `services/watchability.py` | Replace lines 123–168 provider-map + display logic with `present_offer` |
| `api_v2.py:unofficial_movie_offers` | Replace lines 322–331 with `present_offer` call |
| `tests/` | Add `tests/test_presentation.py`; existing tests unchanged |

### Risk
Low–Medium. Logic is straightforward. `prime_movie` has the most
complex call site (90 lines → ~20). `watchability.py` gains a cleaner
separation between scoring and presentation.

---

## Candidate 5 — Unify scraper fetch seam [Speculative, ports & adapters]

### Problem
`BaseScraper.fetch_url(url, headers=None)` is the testable seam. Four
scrapers use it. `HydraHDScraper` bypasses it entirely with its own
`fetch_url_with_headers(url)`. Tests mock `fetch_url` — HydraHD's real
request path is untested through the seam.

### Approach
Add optional `headers` parameter to `BaseScraper.fetch_url` (it already
has it — lines 91–108). Change `HydraHDScraper.fetch_url_with_headers`
to call `self.fetch_url(url, headers=...)` instead of duplicating the
request logic. Deprecate `fetch_url_with_headers`.

### Files to touch
| File | Change |
|---|---|
| `scrapers/base.py` | `fetch_url` already accepts `headers`; verify it passes them through |
| `scrapers/adapters.py` — `HydraHDScraper` | Replace `fetch_url_with_headers` calls with `self.fetch_url(url, headers=...)`; remove the method |
| `tests/test_scrapers.py` | Add mock for `HydraHDScraper.fetch_url` (currently none) |

### Risk
Low. One method removal, one method delegation. The seam becomes
uniform across all scrapers. Test coverage improves.

### Gate
Only worth doing when a 6th scraper is added (two adapters + one escape
hatch = signal). Can be done now as hygiene.

---

## Recommended execution order

| # | Candidate | Why |
|---|---|---|
| 1 | **#2 (scoring unification)** | Highest leverage; fixes potential drift on core feature; lowest risk — output shapes unchanged |
| 2 | **#3 (Offer dataclass)** | Cleans up dict-drift; enables #4 to be simpler; medium risk but tests are strong |
| 3 | **#4 (presentation seam)** | Medium effort; clean separation of concerns; no behavior change |
| 4 | **#5 (scraper seam)** | Lowest priority; speculative until 6th scraper; do as hygiene |

### Estimated effort per candidate
- **#2**: 1–2 hrs (new module + rewire two callers + regression tests)
- **#3**: 2–3 hrs (new dataclass + cache + ranking + watchability + api_v2 + test updates)
- **#4**: 2–3 hrs (new module + three caller rewrites)
- **#5**: 30 min (one method refactor + one new test)

### Commits
Each candidate gets its own commit on `feature/architecture-fix`.
Merges to `main` after the full suite passes.

---

## Decision needed

**Candidate 2 score bug:** I verified both files and the `elif` branch
score tuples are actually identical (`(match, audio, sub, quality)`).
The review's claim of "already differ subtly" may have been based on an
older version. Confirm before proceeding — if they're the same, #2 is
pure dedup. If they differ in a way I missed, flag it explicitly in the
commit message.

---

## Candidate 6 — Promote `_read_offers` to public seam on `AvailabilityCache` [Strong]

### Problem
`cache._read_offers()` is a private method called from 5 distinct call
sites across 3 modules (`movie_availability`, `prime_movie`,
`watch_redirect`, `watchlist_report` in `views.py`, and `filtering.py`).
Tests mock it by name. The cache module's internal read shape leaks to
every caller — a textbook private-method-crossing-a-seam leak.

**Deletion test:** delete the name and all 5 callers must reimplement
the same ORM/SQL query + `LanguageInfo` prefetch. The method is clearly
load-bearing.

### Solution
In `tracker/services/cache.py`: rename `_read_offers` → `read_offers`
(or `get_offers`). Update all 5 call sites and any test mocks. Consider
splitting the class: cache orchestration (`get_or_refresh`) stays on the
class; `read_offers` is the stable public seam.

### Files involved
| File | Change |
|---|---|
| `tracker/services/cache.py` | Rename `_read_offers` → `read_offers` |
| `tracker/views.py` | Update 4 call sites |
| `tracker/services/filtering.py` | Update 1 call site |
| `tracker/tests/test_prime_endpoints.py` | Update mock target |
| `tracker/tests/test_views.py` | Update mock target |

### Benefits
- Bugs in offer-extraction surface at one module, not 5 call sites
- One interface, 6 call sites (leverage)
- Callers stop knowing about `LanguageInfo` prefetch internals

### Risk
Very low. Pure rename — no logic change. All callers are already
exercised by existing tests.

### Estimated effort
30 min — grep + rename + run suite.

---

## Candidate 7 — Deepen the Offer pipeline — collapse `prime_movie`'s inline orchestration [Strong]

### Problem
The `prime_movie` view (~80 lines) does all of the following inline:
movie resolution, preference resolution, cache orchestration, offer
filtering, provider mapping, ranking, presentation, and response
assembly. Deletion test: delete the view and all logic moves to the
caller — the module is shallow despite being the core feature path.

`watchlist_report` and `movie_availability` partially duplicate the
same pipeline steps.

### Solution
Create `tracker/services/offer_pipeline.py` with `OfficialOffersPipeline`
(or `OfficialOfferResolver`). It encapsulates:

1. `get_cached_offers(movie)` — cache hit/refresh
2. `filter_qualifying_offers(offers)` — subscription/free only
3. `rank_offers(filtered, pref)` — existing ranking module
4. `present_offers(ranked, provider_map)` — existing presentation module

The `prime_movie` view becomes a thin adapter: resolve movie → delegate
to pipeline → serialize response. Same pipeline can be reused by
`watchlist_report` and `movie_availability`.

### Files involved
| File | Change |
|---|---|
| `tracker/services/offer_pipeline.py` | **Create** — `OfficialOffersPipeline` class |
| `tracker/views.py` (`prime_movie`, lines ~1048–1177) | Thin adapter: resolve + delegate |
| `tracker/services/filtering.py` | Called by pipeline (no change to module interface) |
| `tracker/services/ranking.py` | Called by pipeline (no change) |
| `tracker/services/presentation.py` | Called by pipeline (no change) |
| `tracker/services/cache.py` | Called by pipeline (no change) |
| `tracker/tests/` | New `test_offer_pipeline.py`; simplify existing prime_movie tests |

### Benefits
- All filtering + ranking + presentation logic in one module (locality)
- One pipeline, 3 view call sites (leverage)
- Views no longer know about `provider_map`, `rank_offers`, `present_offer`

### Risk
Medium. Requires moving ~80 lines of interleaved logic into a new
module without changing output shape. Existing `test_prime_endpoints.py`
suite acts as regression harness.

### Estimated effort
2–3 hrs — extract + rewire + pipeline tests + verify view tests pass.

---

## Candidate 8 — Unify TMDb enrichment — kill 4 duplicated inline enrich blocks [Worth exploring]

### Problem
Four call sites each do their own version of "fetch TMDb details + sync
to Movie row":

| Call site | Pattern |
|---|---|
| `views.py:movie_detail` | `client.details()` + `sync_movie_fields()` inline (red — bypasses enrichment module) |
| `views.py:prime_movie` | Calls `enrich_movie_from_tmdb` ✅ |
| `views.py:watch_redirect` | `client.details()` + `get_or_create` + manual poster backfill inline |
| `admin.py:link_tmdb_view` | Manual field-by-field copy (no enrichment module at all) |

`services/enrichment.py` already has `sync_movie_fields` and
`enrich_movie_from_tmdb` but callers use them inconsistently.

### Solution
Add `resolve_or_create_movie(tmdb_id)` to `services/enrichment.py`.
It does: TMDb fetch → `get_or_create(tmdb_id=…)` → poster backfill →
`sync_movie_fields`. All four call sites call it. Admin's manual
field-by-field copy becomes a single `sync_movie_fields` call.

### Files involved
| File | Change |
|---|---|
| `tracker/services/enrichment.py` | Add `resolve_or_create_movie(tmdb_id)` |
| `tracker/views.py` (`movie_detail`, `watch_redirect`) | Replace inline TMDb blocks with `resolve_or_create_movie` |
| `tracker/admin.py` (`link_tmdb_view`) | Replace manual field copy with `sync_movie_fields` |
| `tracker/views.py` (`prime_movie`) | Already correct; verify it calls new function |

### Benefits
- TMDb resolution + enrichment bugs live in one module
- One entry point, 4 call sites
- `resolve_or_create_movie` is mockable at the HTTP client boundary

### Risk
Low-medium. Each call site has slightly different error handling; need
to consolidate without dropping any. Admin path is the trickiest
(currently bypasses the service layer entirely).

### Estimated effort
2–3 hrs — new function + three caller rewrites + test coverage for the
merged path.

---

## Updated execution order (all candidates)

| Priority | Candidate | Rationale |
|---|---|---|
| ✅ Done | **#1 enrichment scatter** | `e6e3ce8` |
| ✅ Done | **#4 presentation seam** | `b21130e` |
| 1 | **#2 scoring unification** | Fixes potential drift on core feature; lowest risk |
| 2 | **#6 `_read_offers` seam** | 30 min; pure rename; unblocks #7 |
| 3 | **#3 Offer dataclass** | Cleans dict-drift; medium risk |
| 4 | **#7 Offer pipeline** | High value; do after #6 and #3 so pipeline internals are already clean |
| 5 | **#8 TMDb enrichment** | Worth doing; low-medium risk; independent of above |
| 6 | **#5 scraper seam** | Lowest priority; do as hygiene when 6th scraper added |
