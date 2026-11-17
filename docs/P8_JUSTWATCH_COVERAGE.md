# P8 — JustWatch Coverage: audit, baseline, design

Everything below is measured, not assumed. Where a suspicion was **not**
confirmed by evidence, it is recorded as disproved rather than quietly dropped —
three of the four "known concerns" turned out differently than expected, and
saying so is what stopped three pointless changes.

## 1. Baseline (persisted state, live DB)

Read from `SyncRun`, `ProviderContent`, `ContentMapping`, `Offer`, `SyncCursor`.
No external request was needed to produce this.

```text
JustWatch sync runs ............ 13 (all succeeded, 0 errors)
titles discovered .............. 906   (cumulative)
titles processed .............. 906
offers created ................ 602
offers updated ................ 304
offers deactivated ............ 0
ProviderContent (justwatch) ... 574
ContentMapping  (justwatch) ... 572
ProviderContent w/o mapping ... 2       (0.3%)
Offers (justwatch) ............ 602
```

**Mapping is not the bottleneck.** 572 of 574 discovered `ProviderContent` rows
carry a real internal mapping, and the live probe returned a TMDB id for 100 of
100 titles. The funnel is losing nothing at the TMDB/IMDb step.

## 2. Where the coverage actually goes

| Stage | Measured | Assessment |
|---|---|---|
| Discovered → ProviderContent | 906 → 574 | titles overlap between partitions; offers are per-country |
| Discovered → ContentMapping | 906 → 572 | **healthy, 99.7% mapped** |
| ProviderContent → Offer | 574 → 602 offers | healthy |
| **Partition plan coverage** | **4 of 72 partitions walked = 5.6%** | **the bottleneck** |
| **Titles per partition** | **100 (one page per run)** | **the bottleneck** |
| Offsets ever reached | `100`, `300` | of a possible 2000 |

### The real limitation

Discovery is starved, not filtered. Concretely:

1. **One page per partition per run.** Every `SyncRun` shows `items_discovered`
   of exactly `100` — the page size. 20 runs are needed to exhaust a partition.
2. **72 partitions, 6 per tick** (`DEFAULT_MAX_PARTITIONS`) → a full pass over
   the plan takes **12 ticks**, and each partition needs 20 visits to complete.
3. **Only 4 partitions have ever been walked** (NL/netflix, NL/apple,
   AU/netflix, AU/abc_iview) out of 72 walkable.
4. **Country coverage is 2 of 34 enabled countries** (NL, AU).

So the coverage ceiling is `72 partitions × 2000 titles`, but the system has
reached **906 titles total**. The fix is walking more of the plan, not changing
the query.

## 3. Concerns investigated — including the disproved ones

### A. `min_year` / `max_year` — **ENFORCED upstream; dropped only in our own adapter**

Path traced end to end:

```text
JustWatchClient.catalogue_titles(min_year=, max_year=)   accepts   ✅
  → _catalogue_variables(...)                             builds    ✅
    → title_filter["releaseYear"] = {"min": .., "max": ..}        ✅
      → variables.popularTitlesFilter                                     ✅
        → GraphQL: popularTitles(filter: $popularTitlesFilter)          ✅ declares it
JustWatchSource._fetch_catalogue(...)                     does NOT pass them ❌
JustWatchSource.fetch(...)                                has no year parameter ❌
justwatch_sync coordinator                                has no year parameter ❌
```

They reach GraphQL correctly — **but only for the search path**
(`search_titles` hardcodes `None`), and **nothing in production ever supplies
them**. They are dead parameters, not broken plumbing.

**Live proof the filter is real** (NL, netflix, MOVIE):

```text
no year filter      → 100 titles, years 1957–2026
min=2020,max=2021   → 100 titles, years 2020–2021 (100% inside the window)
min=1910,max=1915   →   1 title,  year 1913          (filter genuinely applies)
```

Year partitioning yields **new** titles: overlap between the unbounded page and
the 2020–2021 page is **4 titles out of 100**. This is the key measurement —
it means year partitions are complementary, not redundant re-walks.

### B. `sortBy: POPULAR` — hardcoded, but **not the problem**

Hardcoded at two query constants (`POPULAR_TITLES_QUERY`, `SEARCH_TITLES_QUERY`).
Measured behaviour:

```text
offset 0    vs offset 100  → overlap 0 of 200  (0.0% duplicates)
offset 1800 → 100 titles (stable)
```

Adjacent-page duplication is **0%**. `POPULAR` ordering is stable and its pages
are disjoint, which is exactly what offset paging needs. Changing the sort would
risk destabilising paging for **no measured coverage gain** — the ceiling is the
number of pages walked, not their order. **No change made.**

### C. Offset pagination — the ceiling is real, and the code's limit is *safe*

```text
offset   0 → 100 titles      offset 1000 → 100
offset 200 → 100             offset 1200 → 100
offset 400 → 100             offset 1400 → 100
offset 600 → 100             offset 1600 → 100
offset 800 → 100             offset 1700 → 100
                            offset 1800 → 100
                            offset 1900 →   0   ← depth wall
```

The wall arrives as an **empty page, not an error**. `CATALOGUE_MAX_OFFSET=2000`
sits *above* the observed wall (1800–1900), so the limit is a correct safety
bound and not an artificial truncation. **No change made** — raising it would do
nothing.

### D. Date/year partitioning — not currently done, and not the first-order fix

Partition identity today is `(country, provider)` with `content_type` carried
*inside* the cursor value. There is **no year dimension**. Probing shows a year
partition reaches genuinely different titles (§3A), so partitioning is a real
*capability* — but the bottleneck is that partitions are barely walked at all.
It is the second-order fix, and it is implemented here because it is cheap once
years are plumbed and it removes the offset ceiling as a hard stop.

### E. Country propagation — already fixed and preserved

`385c8b4` guarantees explicit country end to end. Verified: the coordinator
requires `Country` rows, the adapter calls `_require_country`, the client
normalises and puts `country` in every GraphQL payload. **Untouched by P8** —
no inference from IP/VPN/locale is introduced.

### F. Provider filtering — correct, and deliberately client-side

`packages` selects *titles*, so offers are filtered to the requested provider
client-side (`only_provider=`). This is intentional and correct: it makes a
provider partition mean "this provider's catalogue". Unmapped
(country, provider) pairs return an empty page rather than a cross-provider
leak. **No change made.**

## 4. One genuine defect found: the cursor key collides across content types

`availability_synccursor` unique key is `(provider_id, source_id, country_id)`.
The partition walk also varies **`content_type`**, which is *not* in the key.
A MOVIE walk and a SERIES walk for the same `(provider, country)` therefore
share one offset and **overwrite each other**:

```text
NL/netflix cursor = '300'   ← written by whichever content_type ran last
```

This is a real correctness bug, not a coverage limit: one content type silently
starves the other. It is also the reason year partitioning cannot simply be
added as another loop dimension — the cursor must become a real partition key.

## 5. Design

### Limitation → evidence → change → expected gain → tradeoff

**1. One page per partition per run**
→ *Evidence:* every `SyncRun.items_discovered` is exactly 100; cursors sit at
100/300 of 2000.
→ *Change:* walk `pages_per_partition` pages in a single partition visit,
bounded, continuing only while pages return titles.
→ *Gain:* up to 20× fewer visits to exhaust a partition.
→ *Tradeoff:* more requests per visit. Bounded and capped; a page that returns
nothing stops the walk immediately, so there is no unbounded loop.

**2. Partition cursor collides across content type (and would collide across years)**
→ *Evidence:* unique key is `(provider, source, country)`, `content_type` lives
in the cursor *value*.
→ *Change:* encode the full partition identity in the cursor **value** and give
each partition its own cursor row keyed by a deterministic partition id.
→ *Gain:* MOVIE/SERIES/year partitions become independently resumable and
idempotent.
→ *Tradeoff:* cursor rows are namespaced rather than reusing the old shared row;
one migration-free change to how the cursor string is built.

**3. No year dimension in discovery**
→ *Evidence:* `releaseYear` is proven to filter upstream (3/3 probes), and year
windows return disjoint title sets (4/100 overlap).
→ *Change:* plumb `min_year`/`max_year` through adapter `fetch()` and the
coordinator, so a partition can be year-bounded. Year windows are generated
deterministically, in both directions, with no gaps and no overlap.
→ *Gain:* removes the 2000-offset wall as a coverage stop; every year window is
its own bounded stream.
→ *Tradeoff:* more partitions to plan. Year windows are opt-in per invocation so
the existing default plan is unchanged.

### Explicitly not changed

`POPULAR` sort, `CATALOGUE_MAX_OFFSET`, provider filtering, country
propagation, source policy, lifecycle, classifier, Tubi, Watchmode, and
Streaming Availability. Each was investigated and either measured to be correct
or out of scope; §3 records the evidence for leaving them alone.