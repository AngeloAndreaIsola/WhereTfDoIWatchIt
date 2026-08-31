# Architecture Fix — Implementation Plan

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
`country` (with fallback). Each caller builds `provider_map` once and
passes it in.

### Dataclass
```python
@dataclass
class PresentedOffer:
    service_display: str       # provider.name or service_display_name(slug)
    source_indicator: str      # provider.source_indicator or "Official"
    country: str               # offer.country or provider.country_code fallback
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
