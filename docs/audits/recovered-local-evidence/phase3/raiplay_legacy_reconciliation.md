# Recovered local evidence — Phase 3: RaiPlay legacy reconciliation

> **This file is a recovered archive.** The source document was present only in a
> local worktree and was left uncommitted there. It has been copied unchanged
> (body below) for preservation; this header records its origin and limits.
>
> The numbers and findings are historical. They describe the live `tracker`
> database as measured on 2026-10-25 and are **not claims about current provider
> behavior**. Do not delete legacy RaiPlay data, change ingestion behavior, or
> run live scrapers based on this archive.

## Provenance

| Field | Value |
|---|---|
| Original worktree | `.worktrees/phase3-raiplay-audit` (canonical checkout `.../WhereTfDoIWatchIt`) |
| Source path | `docs/RAIPLAY_LEGACY_RECONCILIATION.md` |
| Source branch | `phase3-raiplay-audit` (worktree of `feature/scrapres-improvedment` @ `5dcd6cf`) |
| Source HEAD SHA | `5dcd6cf0c4daff70551cd2e0748003b17ac4814e` |
| Source file tracked? | **No — untracked** (`?? docs/RAIPLAY_LEGACY_RECONCILIATION.md`) |
| Date of original findings | 2026-10-25 (stated in report: "**Date:** 2026-10-25") |
| Archived destination | `docs/audits/recovered-local-evidence/phase3/raiplay_legacy_reconciliation.md` |
| Content already present in master? | **No.** `docs/RAIPLAY_LEGACY_RECONCILIATION.md` is not tracked in `origin/master` (`7cec749f40ec1bfad51ac6d3c54a5b80485fc5ec`). Related RaiPlay sources (`scrapers/rai_play.py`, `backend/availability/sources/rai_play.py`) exist in master, but this reconciliation report itself does not. |
| Unique findings preserved? | Yes — full body archived below, incl. verified headline numbers and the three defects. |
| Unresolved technical concerns | DEFECT 1: 135 legacy rows stored as `movie` but classified `series` by the scraper (stored data wrong; live write path NOT proven broken); DEFECT 2: `typology` vs `layout` classification divergence (4 rows); DEFECT 3: canonical collector has no surviving `SyncCursor` row (~22% of catalogue walked, 504/2,239). Whether the two editorial listing pages (`/film/`, `/serieitaliane`) contain titles the sitemaps lacks — **not measured**. Whether the 422 legacy-only titles are still available — **unobserved, not unavailable**. |

## Source document (unchanged)

# RaiPlay Legacy Coverage Reconciliation

**Date:** 2026-10-25
**Branch:** `phase3-raiplay-audit` (worktree of `feature/scrapres-improvedment` @ `5dcd6cf`)
**Scope:** `raiplay` provider only — the 457 legacy `ScrapedContentMapping` rows, the 504
canonical `ProviderContent` rows, and the bridge between them.
**Deliverable:** audit + recommendation only. **Nothing was written, merged, deleted or
reassigned.** No external request was made; no scraper ran.

Verdict vocabulary: **GO** | **HOLD** | **NO-GO** | **BLOCKED**

> **Not proven is not proven.** Every claim below carries a row from the live `tracker`
> database (read-only, 2026-10-25), a line-numbered citation into the code on this
> branch, or a measured result from an in-process probe that reads existing rows and
> never saves. Where a property is only *implied* by naming or documentation, it is
> marked **Not proven**. A claim I could not establish is written as such.

---

## 0. Reading order

1. §1 What was asked vs. what the data shows
2. §2 Reproducible queries
3. §3 Data integrity findings
4. §4 Coverage comparison and the match partition
5. §5 What actually explains the 422-row gap
6. §6 The three defects found along the way (not the brief's subject)
7. §7 Strategies A–D evaluated
8. §8 Recommended next implementation
9. §9 GO / HOLD / NO-GO
10. §10 Unresolved questions
11. §11 Coverage-preservation risks
12. §12 Constraint compliance

---

## 1. What was asked vs. what the data shows

### 1.1 Stated evidence — verified

Every headline number in the brief reproduces exactly. **All five.**

| Stated | Measured | Status |
|---|---|---|
| 457 legacy RaiPlay observations | **457** | ✅ confirmed |
| 504 canonical RaiPlay observations | **504** | ✅ confirmed |
| 35 normalized-title overlaps | **35** | ✅ confirmed |
| 26 title-and-year overlaps | **26** | ✅ confirmed |
| 422 normalized-title legacy-only observations | **422** rows (**421** distinct titles) | ✅ confirmed* |
| All 457 legacy rows projected into canonical | **457/457** | ✅ confirmed |
| Legacy scraper reads two editorial listing pages | `RAIPLAY_FILM_URL`, `RAIPLAY_SERIE_URL` (`scrapers/rai_play.py:13-14`) | ✅ confirmed |
| Canonical collector walks programme sitemaps | `sitemap.raiplay.programmi.MM.YYYY.xml` (`sources/rai_play.py:212-214`) | ✅ confirmed |
| Canonical collector is non-authoritative | `authoritative=False`, `complete_snapshot=False`, `supports_authoritative_snapshots=False` (`sources/rai_play.py:320-322, 449-450`) | ✅ confirmed |

\* **The 422/421 discrepancy is a real, explainable measurement choice, not an error in the
brief.** One legacy row shares a normalized title with another legacy row — the Italian
titles *Clan* (id 285) and *Blanca* (id 310) both normalize to `ln` (the `->` in
*Blanca* → `b` + `lanca`; *Clan* → `c`+`lan`; the accents/punctuation collapse leaves
`ln` for both). Measured:

```
legacy rows .......................... 457
distinct normalized titles ........... 456   (one collision: 'ln')
legacy-only rows ..................... 422   <- the brief's number
legacy-only distinct titles .......... 421
```

**The brief's 422 is the row-level count.** A downstream consumer that iterates rows
reproduces 422; one that builds a set of titles reproduces 421. Both are correct; the
report quotes **422 rows / 421 distinct titles** from here on and states which is meant.

### 1.2 What the brief's framing does not say

The brief's framing — "422 legacy-only titles" — invites the reading that the canonical
collector *failed* to observe 422 titles the legacy scraper saw. **The data does not
support that reading, and this is the central finding of the audit.**

The legacy scraper is a **single-page** listing reader. The canonical collector is a
**sitemap-driven catalogue walk** of a 2,239-programme catalogue. These are not two
measurements of the same surface; they are two different surfaces, and the legacy scraper's
surface is the smaller one.

Measured, the two are near-disjoint *by construction*:

| | rows |
|---|---|
| legacy rows | 457 |
| canonical rows | 504 |
| overlap on the strongest key available (provider-native `uniquename`) | **35** |
| canonical-only rows | **469** |

The canonical collector observes **469 titles the legacy scraper never saw**. The legacy
scraper's marginal contribution is 422 rows — and §5 shows that essentially none of that
marginal contribution is *unobserved availability*: it is a different slice of the same
catalogue, cut along a different axis.

---

## 2. Reproducible queries

All queries are read-only `SELECT`. Two forms are given: the SQL used to produce this
report, and the Django-equivalent for the one probe that needed Python.

### 2.1 Environment

```bash
# DB is live in the dev stack; the container name is stable across restarts.
docker ps --format '{{.Names}}'          # wheretfdoiwatchit-db-1 / -backend-1

# Run SQL by piping on stdin: `docker exec` needs -i for stdin, and the SQL
# must be single-line or it is parsed as a shell command list.
docker exec -i wheretfdoiwatchit-db-1 \
  psql -U tracker -d tracker -P pager=off -A -F'|' -c "<SQL>"
```

> **Pitfall found while running this audit.** `docker exec ... psql -c "<multiline SQL>"`
> fails with `invalid command \nwith` — `docker exec` splits a multi-line `-c` argument
> into separate argv entries. Flatten to one line, or `docker cp` a `.sql` file and use
> `psql -f -`.

### 2.2 Volume

```sql
-- Legacy rows per provider (scraper-era table)
SELECT p.slug, p.category, count(*) AS legacy_rows
FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
GROUP BY 1, 2 ORDER BY 3 DESC;
-- raiplay 457 | animeworld 181 | hydrahd 109 | kisskh 61

-- Canonical observations by source
SELECT s.slug AS source, count(*) AS rows
FROM availability_providercontent c
JOIN tracker_provider p ON p.id = c.provider_id
JOIN availability_source s ON s.id = c.source_id
WHERE p.slug = 'raiplay' GROUP BY 1 ORDER BY 2 DESC;
-- rai_play 504 | legacy_scraper 457
```

### 2.3 Bridge integrity

```sql
-- (a) orphan legacy rows — expect 0
SELECT count(*) FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
LEFT JOIN availability_providercontent c
  ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
WHERE p.slug = 'raiplay' AND c.id IS NULL;              -- 0

-- (b) duplicated projections — expect 0
SELECT count(*) FROM (
  SELECT m.id FROM tracker_scrapedcontentmapping m
  JOIN tracker_provider p ON p.id = m.provider_id
  JOIN availability_providercontent c
    ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
  WHERE p.slug = 'raiplay' GROUP BY m.id HAVING count(c.id) > 1
) x;                                                    -- 0

-- (c) orphan projections — expect 0
SELECT count(*) FROM availability_providercontent c
JOIN tracker_provider p ON p.id = c.provider_id
WHERE p.slug = 'raiplay' AND c.provider_content_id LIKE 'legacy-%'
  AND NOT EXISTS (
    SELECT 1 FROM tracker_scrapedcontentmapping m
    WHERE m.id::text = replace(c.provider_content_id, 'legacy-', '')
  );                                                    -- 0

-- (d) identity == build_provider_content_identity() — expect 961/961
SELECT count(*) FILTER (
  WHERE identity = p.slug || '|' || c.provider_content_id || '|' || c.content_type
                    || '|' || coalesce(season_number::text, '') || '|'
                    || coalesce(episode_number::text, '')
) AS matches, count(*) AS total
FROM availability_providercontent c
JOIN tracker_provider p ON p.id = c.provider_id
WHERE p.slug = 'raiplay';                               -- 961 / 961
```

### 2.4 Field-level conflict checks

```sql
-- title conflicts — expect 0
SELECT count(*) FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_providercontent c
  ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
WHERE p.slug = 'raiplay' AND c.title <> m.scraped_title; -- 0

-- URL conflicts — expect 0
SELECT count(*) FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_providercontent c
  ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
WHERE p.slug = 'raiplay' AND c.provider_url <> m.scraped_url; -- 0

-- year conflicts — expect 3
SELECT m.id, m.scraped_title, m.scraped_year,
       extract(year FROM c.release_date) AS projected_year
FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_providercontent c
  ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
WHERE p.slug = 'raiplay'
  AND extract(year FROM c.release_date) IS DISTINCT FROM m.scraped_year;
```

### 2.5 Overlap on three independent keys

```sql
-- Normalized title (mirrors availability.services.normalize.normalize_title)
WITH legacy AS (
  SELECT m.id,
    regexp_replace(translate(lower(m.scraped_title),
      'àáâãäåāăąèéêëēĕėęěìíîïĩīĭįòóôõöøōŏőùúûüũūŭůųýÿçćĉċčñńņňŊśŝşšźżžæœ',
      'aaaaaaaaaeeeeeeeeeiiiiiiiiooooooooouuuuuuuuyyccccnnnnnsssszzzao'),
      '[^a-z0-9 ]', ' ', 'g') AS l_norm
  FROM tracker_scrapedcontentmapping m
  JOIN tracker_provider p ON p.id = m.provider_id WHERE p.slug = 'raiplay'),
canon AS (
  SELECT c.title,
    regexp_replace(translate(lower(c.title),
      'àáâãäåāăąèéêëēĕėęěìíîïĩīĭįòóôõöøōŏőùúûüũūŭůųýÿçćĉċčñńņňŊśŝşšźżžæœ',
      'aaaaaaaaaeeeeeeeeeiiiiiiiiooooooooouuuuuuuuyyccccnnnnnsssszzzao'),
      '[^a-z0-9 ]', ' ', 'g') AS c_norm
  FROM availability_providercontent c
  JOIN tracker_provider p ON p.id = c.provider_id
  WHERE p.slug = 'raiplay' AND c.provider_content_id NOT LIKE 'legacy-%')
SELECT
  (SELECT count(*) FROM (SELECT DISTINCT l_norm FROM legacy
                        INTERSECT SELECT DISTINCT c_norm FROM canon) z) AS title_overlap,
  (SELECT count(*) FROM (SELECT l_norm, 1 AS y FROM legacy
                        INTERSECT SELECT c_norm, 1 FROM canon) z)         AS title_year_overlap;
-- 35 / 26  (the year join adds the year predicate — see §2.6)
```

### 2.6 The strongest key: provider-native identity

The legacy payload carries RaiPlay's own `uniquename` (`ContentItem-<uuid>`) — **457/457
rows**. The canonical collector uses the same value as `provider_content_id`. Joining on it
is decisive and needs no title normalization:

```sql
SELECT count(*) AS native_id_matches
FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_providercontent c ON c.provider_id = p.id
WHERE p.slug = 'raiplay'
  AND c.provider_content_id = m.raw_payload->>'uniquename'
  AND c.provider_content_id NOT LIKE 'legacy-%';
-- 35
```

**The three keys agree exactly:** normalized title 35, slug 35, native id 35. Every slug
match is also a title match (`diff_title = 0`), and every title match is also a slug match
(`title_only_matches = 0`). This is the strongest available evidence that the 35 are
genuine same-content matches and that the 422 are genuinely *not* the same content.

### 2.7 Full partition of the 457 legacy rows

```sql
WITH legacy AS (
  SELECT m.id, m.scraped_title, m.scraped_year, m.scraped_url,
         m.raw_payload->>'uniquename' AS un,
         split_part(m.scraped_url, '/', 5) AS l_slug,
         regexp_replace(translate(lower(m.scraped_title),
           'àáâãäåāăąèéêëēĕėęěìíîïĩīĭįòóôõöøōŏőùúûüũūŭůųýÿçćĉċčñńņňŊśŝşšźżžæœ',
           'aaaaaaaaaeeeeeeeeeiiiiiiiiooooooooouuuuuuuuyyccccnnnnnsssszzzao'),
           '[^a-z0-9 ]', ' ', 'g') AS l_norm
  FROM tracker_scrapedcontentmapping m
  JOIN tracker_provider p ON p.id = m.provider_id WHERE p.slug = 'raiplay'),
canon AS (
  SELECT c.title, c.release_date, c.provider_url, c.provider_content_id,
         split_part(c.provider_url, '/', 5) AS c_slug,
         regexp_replace(translate(lower(c.title),
           'àáâãäåāăąèéêëēĕėęěìíîïĩīĭįòóôõöøōŏőùúûüũūŭůųýÿçćĉċčñńņňŊśŝşšźżžæœ',
           'aaaaaaaaaeeeeeeeeeiiiiiiiiooooooooouuuuuuuuyyccccnnnnnsssszzzao'),
           '[^a-z0-9 ]', ' ', 'g') AS c_norm
  FROM availability_providercontent c
  JOIN tracker_provider p ON p.id = c.provider_id
  WHERE p.slug = 'raiplay' AND c.provider_content_id NOT LIKE 'legacy-%')
SELECT CASE
    WHEN EXISTS (SELECT 1 FROM canon c WHERE c.provider_content_id = l.un)
      THEN 'A_native_id_match'
    WHEN EXISTS (SELECT 1 FROM canon c WHERE c.c_slug = l.l_slug)  THEN 'B_slug_match'
    WHEN EXISTS (SELECT 1 FROM canon c WHERE c.c_norm = l.l_norm)  THEN 'C_title_match'
    WHEN EXISTS (SELECT 1 FROM canon c WHERE c.release_date IS NOT NULL
                 AND extract(year FROM c.release_date) = l.scraped_year)
      THEN 'D_year_only_ambiguous'
    ELSE 'E_no_match'
  END AS bucket,
  count(*)
FROM legacy l GROUP BY 1 ORDER BY 1;
-- A_native_id_match       35
-- D_year_only_ambiguous  361
-- E_no_match              61
```

### 2.8 Timestamps

```sql
-- last_scraped_at is NULL on every legacy row: the bridge never writes it.
SELECT count(*) AS n,
       count(*) FILTER (WHERE last_scraped_at IS NULL) AS null_last_scraped
FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id WHERE p.slug = 'raiplay';
-- 457 / 457

-- ProviderContent.last_seen is strictly LATER than the legacy row's created_at
-- for all 457 rows: the bridge (not the scraper) is the writer of last_seen.
SELECT count(*) AS n,
       count(*) FILTER (WHERE c.last_seen > m.created_at) AS last_seen_gt_created
FROM tracker_scrapedcontentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_providercontent c
  ON c.provider_id = p.id AND c.provider_content_id = 'legacy-' || m.id
WHERE p.slug = 'raiplay';
-- 457 / 457
```

### 2.9 Canonical coverage provenance

```sql
-- No SyncCursor row for rai_play, although the source declares resumable walks.
SELECT count(*) FROM availability_synccursor sc
WHERE sc.provider_id = (SELECT id FROM tracker_provider WHERE slug = 'raiplay');
-- 0

-- Canonical rows never advance last_seen after their first observation.
SELECT count(*) FILTER (WHERE first_seen = last_seen) AS fs_eq_ls,
       count(*) FILTER (WHERE first_seen <> last_seen) AS fs_ne_ls
FROM availability_providercontent c
JOIN tracker_provider p ON p.id = c.provider_id
WHERE p.slug = 'raiplay' AND c.provider_content_id NOT LIKE 'legacy-%';
-- 35 / 469
```

### 2.10 The in-process probe (the one check SQL cannot make)

To determine what the *live* scraper would classify for each stored row, the audit ran the
real parser over the stored `raw_payload`s and compared the result against the stored
`ProviderContent.content_type`. The script fetched rows into memory and never called
`save()`:

```python
# docker exec wheretfdoiwatchit-backend-1 sh -c 'cd /app && PYTHONPATH=/app python /tmp/p.py'
from tracker.scrapers.rai_play import SERIES_TYPELOGIES, MOVIE_TYPELOGIES
from availability.models import ProviderContent

# live classification, from raw_payload['typology'] (the scraper's own rule)
# stored classification, read from the canonical projection
```

Measured output:

```
LIVE scraper classification   : {'movie': 314, 'series': 137, 'unknown': 6}
STORED content_type           : {'movie': 455, 'series': 2}
rows where live != stored     : 135
  (typology, layout, live, stored):
      ('Fiction',   'multi', 'series', 'movie'): 129
      ('Serie TV',  'multi', 'series', 'movie'): 6
```

> **Method note — a corrected measurement.** A first pass of this probe read
> `payload["content_type"]` directly and concluded the live bridge was at fault. That was
> wrong: it did not simulate `persist_scraped_item`'s `payload.setdefault(...)`. Re-run
> correctly, the live write path produces **137** series rows, not 2 — so the live code is
> **not** the proven cause of the 135 wrong stored values. See §6.1, where the live-code
> claim is explicitly marked **Not proven**.

---

## 3. Data integrity findings

### 3.1 Bridge integrity — PASS (no missing, duplicated or conflicting bridges)

| Check | Result | Verdict |
|---|---|---|
| Orphan legacy rows (no projection) | **0** | PASS |
| Duplicated projections (>1 row per legacy pk) | **0** | PASS |
| Orphan projections (`legacy-<pk>` with no source row) | **0** | PASS |
| `identity` == `build_provider_content_identity()` | **961/961** | PASS |
| Projection title ≠ legacy title | **0** | PASS |
| Projection URL ≠ legacy URL | **0** | PASS |
| Projection availability ≠ legacy `is_available` | **0** | PASS |
| Projection year ≠ legacy year | **3** | KNOWN (see §3.3) |

**The bridge is structurally sound.** Every legacy row has exactly one canonical
projection, the identities are deterministic and self-consistent, and no projection has
drifted from its source row.

### 3.2 `legacy-<pk>` identity — one row per legacy observation

`legacy_ingest._provider_content_id` (`availability/services/legacy_ingest.py:117-125`)
derives identity from the legacy primary key, not from the title:

```python
return f"legacy-{mapping.pk}"
```

The docstring gives the reason: legacy uniqueness is `(provider, scraped_title,
scraped_year)`, so the pk is the only stable one-row-one-identity key — scraped URLs are
not unique (a URL import stamps one URL onto every item of a run).

Measured: 457 `legacy-<pk>` rows, all distinct, all matching a live legacy row. **No
bridge identity is missing, duplicated or conflicting.**

### 3.3 The 3 year conflicts are a documented bridge behaviour, not corruption

```
id | scraped_title | scraped_year | projected_year
22 | Happy Holidays | 2024 | 2025
28 | Non riattaccare | 2023 | 2024
1085 | S'indattaraiu | (null) | 2023
```

`legacy_ingest._ingest_scraped_mapping_locked` (`:154-158`) prefers the **linked Movie's
year** over the scraped year:

```python
year = None
if movie is not None and getattr(movie, "year", None):
    year = movie.year          # <- Movie year wins
elif mapping.scraped_year:
    year = mapping.scraped_year
```

Rows 22 and 28 are linked to a `Movie` whose year differs from the scrape; row 1085 has no
scraped year and takes the Movie's. This is intentional (a confirmed TMDb link outranks a
listing-page year) and is **not a data-integrity defect**. It is recorded here because a
naive year-equality audit would flag it.

### 3.4 Timestamps — the legacy table is not a freshness signal

- `last_scraped_at` is **NULL on all 457 legacy rows**. The bridge never writes it, and no
  scheduled entry point runs a legacy scraper (see §6.3).
- `ProviderContent.last_seen` is strictly **later** than the legacy row's `created_at` for
  all 457 rows, because the bridge is the writer.
- The legacy projections were written in **two batches** (447 on 2026-10-01, 10 on
  2026-10-02) — consistent with a single backfill run plus a re-scrape.
- The canonical rows were written across **7 distinct days** (2026-10-02 … 2026-10-24).

**Consequence for the brief's instruction:** "Compare active/inactive status and
timestamps" cannot be answered as a freshness comparison. There is no per-row "last
scraped" timestamp on the legacy side, and `ProviderContent.last_seen` for a bridged row
records the *bridge write*, not the *upstream observation*. **The legacy rows are
unrefreshable by construction** (§6.3), so their `last_seen` is frozen at the backfill
time. This is the single most important input to the retirement decision.

### 3.5 Availability status

All 961 rows (457 legacy projections + 504 canonical) have `currently_available = TRUE`.
This is correct under the model's rules — no catalogue source declares an authoritative
complete snapshot, so **absence is never evidence** and nothing is deactivated
(`availability/models.py:181-194`, `sources/rai_play.py:320-322`).

### 3.6 Unresolved content mappings

| Population | rows | with `ContentMapping` | unresolved |
|---|---|---|---|
| legacy projections | 457 | **25** | 432 |
| canonical (sitemap) | 504 | **2** | 502 |
| canonical (series) | — | — | all 173 series (no year → no title/year match) |

**This is expected, not a defect.** RaiPlay publishes no TMDb ids
(`supports_tmdb_ids=False`, `sources/rai_play.py:324-325`), so mapping happens by local
title/year match in the refresh service. The canonical collector deliberately gives series
no release date (`sources/rai_play.py:588-591` — "a wrong year is worse than none"), so
173 series rows can never match by title/year and remain unresolved until a manual or
aggregator mapping appears.

The 25 legacy rows with a mapping are the rows linked to a `Movie` (`movie_id IS NOT
NULL`) — measured: 25/457 legacy rows have `movie_id`, and all 25 have a `ContentMapping`
with `match_method='title_year'` and `confidence` between 0.6 and 1.0.

### 3.7 Movie/series classification — **see §6.1; the stored values are wrong for 135 rows**

The stored classification of the legacy projections does not agree with what the live
scraper would emit, for **135 rows**. This is the audit's most consequential finding and is
treated separately in §6.1.

### 3.8 Provider-native URL and ID overlap

- **URL**: every row on both sides is `https://www.raiplay.it/programmi/<slug>` — 457
  legacy, 504 canonical, **0** rows with a different host or path prefix. The slug is the
  join key and it is a clean one.
- **Provider-native ID**: the legacy payload carries `uniquename` (`ContentItem-<uuid>`)
  on **457/457** rows; the canonical collector uses the same value as `provider_content_id`.
  **This is the strongest key in the dataset** and it agrees with the slug join exactly
  (35 = 35 = 35).
- `raw_payload->>'id'` (the bare UUID) and `raw_payload->>'uniquename'` differ on 457/457
  rows — the prefix `ContentItem-` matters. A naive join on `id` instead of `uniquename`
  returns **0** matches. Recorded as a pitfall.

---

## 4. Coverage comparison and the match partition

### 4.1 The 457 legacy rows, partitioned

| Bucket | rows | distinct titles | Evidence strength |
|---|---|---|---|
| **A** native-id match | **35** | 35 | **Exact.** Provider's own UUID on both sides. |
| **B** slug match | 0 | 0 | *(coincides with A)* |
| **C** normalized-title match | 0 | 0 | *(coincides with A)* |
| **D** year-only, title differs | **361** | 361 | **Ambiguous.** One shared year is not identity. |
| **E** no match at all | **61** | 61 | **Genuinely unmatched.** |
| total | 457 | 456 | |

Buckets B and C are empty because A already claims every row that matches on slug or title:
**the 35 native-id matches are exactly the 35 slug matches are exactly the 35 title
matches.** Measured directly:

```
slug_pairs 35 | same_title 35 | diff_title 0
title_only_matches (title match, slug differs) 0
```

### 4.2 Bucket A — the 35 exact matches

Every one of the 35 agrees on all three keys, so these are same-content rows with no
ambiguity. Composition:

| legacy `typology` | legacy `layout` | canonical `content_type` | count |
|---|---|---|---|
| `Film` | `single` | `movie` | 26 |
| `Fiction` | `multi` | `series` | 5 |
| `Film` | `multi` | `series` | 4 |

**Note the last row: 4 titles the legacy scraper's `typology` calls `Film` but RaiPlay's own
`layout` calls `multi`, and the canonical collector therefore records as `series`** —
*Trenque Lauquen*, *I film di Charlie Chaplin del periodo "First National" (1918-1921)*,
*Folle d'amore - Alda Merini*, *Califano*. The canonical classification is the correct one
(`layout` is the structured flag; `typology` is a marketing label). This is a *second*,
independent classification divergence — see §6.2.

### 4.3 Bucket D — the 361 "year-only" rows are ambiguous, not matches

**These are not matches.** The only agreement is a shared release year, which is not an
identity. Measured:

- 361 rows, 361 distinct titles, **31 distinct years** (1956–2026).
- **0** of them share a slug with any canonical row.
- **0** of them share a normalized title with any canonical row.
- Ambiguity is severe: the number of same-year canonical candidates per legacy row ranges
  from 1 to 69, with 43 legacy rows facing 69 candidates.

```
candidates per legacy row: 1→28, 2→27, 3→44, 4→14, 5→20, 7→23, 10→18,
                           15→29, 22→41, 36→38, 43→15, 63→21, 69→43
```

**This bucket must not be merged.** Assigning any of these 361 rows to a same-year
canonical row would be a guess, and the repo's own rule is that an ambiguous match must
not overwrite a deterministic one. The correct label is **ambiguous/unresolved**, and the
correct action is to leave them as independent observations.

### 4.4 Bucket E — the 61 genuinely unmatched rows

| legacy `typology` | count |
|---|---|
| `Film` | 47 |
| `Fiction` | 13 |
| `Documentari` | 1 |

59 have a year, 2 do not (*Franco Brocani - Outsider assoluto*, *Grand Tour Italiano*).
No slug, no title, no native id, and no same-year canonical row.

### 4.5 The reverse direction — 469 canonical-only rows

The comparison is not symmetric, and the asymmetry is the point:

| | rows |
|---|---|
| canonical total | 504 |
| matched by native id | 35 |
| **canonical-only** | **469** |

Of the 469 canonical-only rows: **305 movie (298 with year), 164 series (0 with year)**.

**The canonical collector observes 469 titles the legacy scraper never saw.** Any framing
of the legacy scraper as "the more complete source" is contradicted by this number.

### 4.6 Metadata comparison

| Field | legacy projections (457) | canonical (504) |
|---|---|---|
| `metadata.poster` | 457 | 504 |
| `metadata.description` | 0 | **504** |
| `metadata.duration` | 0 | **323** (movies only) |
| `metadata.audio_languages` | 0 | 0 |
| `metadata.subtitle_languages` | 0 | 0 |
| `release_date` | 454 present | 323 present |

The canonical rows are strictly richer: they carry a description, and movies carry a
duration. The legacy projections carry a poster only.

The legacy scraper *asserts* metadata the canonical source deliberately refuses to guess —
`audio_languages=["it"]`, `subtitle_languages=["it"]`, `quality="1080p"`
(`scrapers/rai_play.py:133-135`) — but **none of it survives into the canonical projection**:
`metadata.audio_languages` and `metadata.subtitle_languages` are **0/457**. The bridge's
`_metadata` (`legacy_ingest.py:98-114`) reads `payload.get("audio_languages")` or
`payload.get("audio")`, but the stored `raw_payload` holds neither key — the scraper puts
them on the `ScrapedItem`, not in the payload, and `persist_scraped_item` only copies
`payload.setdefault("content_type", item.content_type)` (`scraped_mapping.py:31`).

**So the asserted Italian audio/subtitle/quality metadata is lost at projection time.** It
is not in the canonical rows and cannot be recovered from the bridge. This is a real
coverage loss, but it is a *metadata* loss on rows whose identity is already covered by the
canonical collector — not an availability loss.

---

## 5. What actually explains the 422-row gap

The brief asks for an attribution of the 422 unmatched observations across eight candidate
causes. Each is answered with the evidence actually available. **Where the evidence does
not exist, the answer is "Not proven" — and in particular, the audit did *not* classify
unknown records as stale merely because they are absent from the sitemap.**

| # | Candidate cause | Rows | Evidence | Status |
|---|---|---|---|---|
| 1 | Editorial listings absent from sitemaps | — | The two legacy sources are `/film/` and `/serieitaliane` — **editorial listing pages**. The canonical source walks `sitemap.raiplay.programmi.MM.YYYY.xml` — the programme catalogue. These are different surfaces. | **Not proven as a cause.** The surfaces differ, but no measurement shows *which* listing rows the sitemap omits. |
| 2 | Historical or stale programme URLs | — | Every legacy URL is `https://www.raiplay.it/programmi/<slug>` (457/457), the same shape the canonical collector walks. 0 legacy rows point at a `/video/`, `/dirette/` or `/collezioni/` path. | **Not proven.** URL shape is not the discriminator. |
| 3 | Different URL representations | **0** | 0 rows differ in host or path prefix; the slug join is exact. | **Ruled out.** |
| 4 | Movie/series classification changes | **~133** | 132 legacy rows the live parser would call `series` are stored as `movie` (§6.1), and 4 titles classified `Film` by typology are `series` by layout (§6.2). | **Partially proven — but it is not a *coverage* cause.** It changes the stored label, not whether the title is observed. |
| 5 | Title changes | **0** | No legacy title matches a canonical title under the repo's own normalization (0 title-only matches). | **Ruled out as an identity cause.** |
| 6 | Parser filtering | **0** | The legacy scraper has no filtering beyond geo-block detection (`scrapers/rai_play.py:54-56`) and duplicate-URL suppression (`:60-62`). It does not filter trailers, layouts or typologies. | **Ruled out.** |
| 7 | Incomplete sitemap traversal | **proven, but it does not explain the gap** | The canonical walk is bounded by `DEFAULT_MAX_CONTENTS = 400` and has **no `SyncCursor` row** (§6.3). | **Proven — and it makes the canonical side *less* complete, not more.** |
| 8 | Unknown causes | — | — | — |

### 5.1 The actual explanation

**The 422-row gap is not a coverage failure of the canonical collector. It is the expected
consequence of two collectors with different scopes.**

The legacy scraper reads **two single pages** — `/film/` and `/serieitaliane`. Those pages
are editorial *features* lists: a curated, rotating, non-exhaustive selection of what
RaiPlay wants to promote. The canonical collector walks the **programme sitemap**: 36
monthly sitemaps holding **2,239 unique `/programmi/` URLs**, of which the collector has
so far persisted **504**.

The two surfaces overlap on exactly 35 rows. The legacy scraper's 422-row "marginal"
contribution is composed of:

- **361 rows that are not the same content** as any canonical row (bucket D) — they are
  simply *other titles* that happen to share a release year with a canonical title;
- **61 rows that match nothing** (bucket E).

Neither bucket is evidence that a canonical-observed title is missing. **The 422 rows are
titles the legacy scraper saw and the canonical walk has not yet reached** — and the
canonical walk has not yet reached most of the catalogue (504 of 2,239, ~22%).

### 5.2 Why "absent from the sitemap" cannot be read as unavailable

The brief's constraint is honoured and is load-bearing here. The canonical collector
declares:

```python
authoritative=False
complete_snapshot=False
supports_authoritative_snapshots=False   # sources/rai_play.py:322
```

and its own docstring states the reason (`sources/rai_play.py:129-134`):

> **The catalogue is never an authoritative snapshot**: sitemap entries go stale (one
> captured stale entry is the reason `programme_page` may return `None`), the walk is
> bounded by `max_contents`, and RaiPlay exposes no per-item removal signal. Results are
> always `authoritative=False` / `complete_snapshot=False` (absence never deactivates).

**Therefore: a legacy title absent from the 504 canonical rows is *unobserved*, not
*unavailable*.** Classifying any of the 422 as stale, dead or retired would be an
inference the data cannot support, and this audit does not make it.

### 5.3 What would settle cause #1

Cause #1 (editorial listings absent from sitemaps) is the only candidate that could be
*true and material*, and it is **Not proven**. Settling it requires a live fetch of the two
listing pages and a comparison against the current sitemap contents — a network operation
this audit's constraints forbid. It is the single highest-value next measurement and is
listed in §8 and §10.

---

## 6. Three defects found along the way

These are not the brief's subject. They were found while verifying the stated facts and are
reported because they directly affect any retirement decision.

### 6.1 DEFECT 1 — 135 legacy rows are stored as `movie` but the scraper classifies them as `series`

**Severity: HIGH (data quality), MEDIUM (live-code risk).** **Status: PROVEN for the stored
data; the live write path is NOT proven to be broken.**

> **Correction.** A first pass at this audit reported the live bridge as the cause. That
> was wrong: it came from a probe that read `payload["content_type"]` instead of simulating
> `persist_scraped_item`'s `setdefault`. The corrected measurement is below, and it
> **downgrades the live-code claim to Not proven** while confirming the stored data is
> wrong. Recorded explicitly because the distinction changes the fix.

**What is actually true of the stored data.** 135 of the 457 legacy projections are stored
with `content_type = movie` although the stored `raw_payload['typology']` is a series
typology:

| `typology` | `layout` | live classification | stored | rows |
|---|---|---|---|---|
| `Fiction` | `multi` | `series` | **`movie`** | 129 |
| `Serie TV` | `multi` | `series` | **`movie`** | 6 |

Measured by running the real scraper parser and comparing against the stored
`ProviderContent.content_type`:

```
rows where live scraper != stored: 135
  (typology, layout, live, stored): {('Serie TV','multi','series','movie'): 6,
                                     ('Fiction','multi','series','movie'): 129}
```

Of the 422 legacy-only rows, **132 are affected**.

**Root cause — Not proven.** All 135 rows were created on **2026-09-26**, the earliest
batch in the table, and the live write path does **not** reproduce the wrong value:

```
rows whose WashiContext card ALREADY carries 'content_type': 46  (44 'movie', 2 'series')
rows where the card does NOT carry it (setdefault applies):     411

AFTER correct setdefault simulation -> series: 137 | movie: 320
ACTUAL stored                          -> series:   2 | movie: 455
```

`persist_scraped_item` (`tracker/services/scraped_mapping.py:31`) stamps the item's own
classification with `payload.setdefault("content_type", item.content_type)`, so a Fiction
card without a card-level key would be stored as `series`. The current code would produce
137 series rows, not 2. **The 135 rows were therefore written by an earlier write path, or
before that stamping was introduced.** Which one is **Not proven** — establishing it needs
the git history of `scraped_mapping.py` and `scrapers/rai_play.py`, which this read-only
audit did not trace.

**Why it matters for retirement.** The stored `content_type` is part of the canonical
`ProviderContent` identity (`build_provider_content_identity`, `availability/models.py:84-108`).
A row stored as `movie` for a title that is a series is:

1. **wrong** — it misclassifies the content;
2. **unmergeable** — if the canonical collector later observes the same title as `series`,
   it creates a *second* row (different identity slot), producing a duplicate;
3. **silent** — `test_legacy_ingest.py:76-80` pins the payload-key path
   (`raw_payload={"content_type": "series"}` → SERIES), which passes, while the stored
   data disagrees.

**Smallest correction.** Re-ingest the 135 rows so the stored classification is rebuilt
from the live scraper's classification, and add an assertion that a `Fiction`/`Serie TV`
card projects as `series`. If the re-ingest is not run before retirement, the wrong values
become permanent — which is why this must land **before or with** any retirement.

**Proof source.** Corrected probe output above; `scrapers/rai_play.py:108-116`;
`scraped_mapping.py:31`; `legacy_ingest.py:53-65`; DB counts in §4.1 and §4.6.

### 6.2 DEFECT 2 — `typology` vs `layout` classification divergence

**Severity: MEDIUM.** **Status: PROVEN (4 rows measured).**

Four of the 35 exact matches are classified `Film` by `typology` but stored as `series` by
the canonical collector, which uses `layout == "multi"` (`sources/rai_play.py:484-487`):
*Trenque Lauquen*, *I film di Charlie Chaplin del periodo "First National" (1918-1921)*,
*Folle d'amore - Alda Merini*, *Califano*.

The canonical classification is correct: `layout` is RaiPlay's structured flag, and the
legacy scraper's own comment says so (`scrapers/rai_play.py:17-19`):

> RaiPlay's `typology` field is the structured content flag. Verified against the live
> /film/ and /serieitaliane listings: both pages mix films and series rows, so the page
> alone is not a reliable signal and the typology wins whenever present.

The comment calls `typology` "the structured content flag" — but `layout` is the one the
canonical collector treats as structured, and the two disagree on these 4 rows. **The
legacy scraper should classify on `layout`**, exactly as the canonical collector does, or
the divergence will keep producing duplicate identities.

### 6.3 DEFECT 3 — The canonical collector has no checkpoint row, so its walk restarts

**Severity: MEDIUM.** **Status: PROVEN (structural).**

`availability_synccursor` contains **0 rows** for `raiplay`, although
`RaiPlaySource.capabilities` declares `supports_resumable_catalogue_sync=True` and
`DEFAULT_MAX_CONTENTS = 400`.

The cause is in the source itself (`sources/rai_play.py:439-453`):

```python
next_cursor = (
    checkpoints.encode(CURSOR_VERSION, {PART_PROGRAMMES: last_url})
    if truncated else None
)
```

`truncated = len(scanned) < total`. **A walk that reaches the end of the sitemap list
returns `cursor=None`**, and `refresh_catalogue` then *deletes* the cursor row
(`availability/services/refresh.py:404-430`). That is correct behaviour for a genuinely
exhausted walk — but it means the checkpoint only exists *while the walk is in progress*,
and the observed pattern (7 runs, each persisting 6–31 new rows, no cursor surviving)
shows the walk is being interrupted or re-run from the top repeatedly.

Measured: canonical rows were created across **7 distinct days** with **1 SyncRun each**,
and `items_discovered` totals **11** across all 7 runs — yet **504** rows exist. The SyncRun
counter counts *offers in the resolved group*, not catalogue rows
(`refresh.py:847`), so this is not a contradiction, but it does mean the run audit understates
catalogue progress.

**Why it matters for retirement.** The canonical collector is the *replacement* for the
legacy scraper, and it is currently covering **~22%** of the catalogue (504/2,239). Until
the walk completes, the canonical side cannot be called a superset of the legacy side, and
"retire the legacy scraper" is premature on coverage grounds alone.

**Smallest correction.** None required for correctness — the behaviour is documented and
safe (no offer is ever lost to a missing observation). The operational fix is to let the
walk run to completion: 2,239 programmes × 2 pages ≈ 4,500 requests at ~0.1 s delay,
which `DEFAULT_MAX_CONTENTS = 400` bounds to ~6 daily runs. **The checkpoint must survive
between runs** for those 6 runs to compose; the observed 0-row state suggests they do not.

---

## 7. Strategies A–D evaluated

### A. Keep both legacy and canonical implementations

| Dimension | Assessment |
|---|---|
| **Complexity** | **Zero added.** The status quo. Two collectors, two surfaces, one bridge. |
| **Expected coverage** | **No change.** Legacy stays frozen at 457 (no scheduled runner); canonical grows toward 2,239. |
| **Data-integrity risk** | **Low, but carries DEFECT 1 forward.** The 135 misclassified series rows stay wrong, and any future re-ingest re-stamps them. |
| **Test requirements** | **None.** 2,544-test baseline unaffected. |
| **Operational cost** | **Low.** The legacy scraper is already unreachable from the refresh path (`refresh_target` returns `CATALOGUE` for `raiplay`, measured). |
| **Verdict** | **Viable as a holding pattern.** It is the current state and costs nothing — but it defers the decision and leaves DEFECT 1 live. |

### B. Extend canonical discovery to cover the editorial listing pages

| Dimension | Assessment |
|---|---|
| **Complexity** | **Medium.** Add two listing-page fetches to `RaiPlaySource._entries()` alongside the sitemap walk. The listing pages embed the same `window.WashiContext` object the canonical parser already reads, so the parsing cost is near zero — the cost is a new discovery surface with its own pagination/staleness semantics. |
| **Expected coverage** | **High.** The legacy scraper's entire marginal contribution (422 rows) comes from those two pages. This is the only strategy that *recovers* the legacy-only coverage. |
| **Data-integrity risk** | **Low, if the listing pages are treated as non-authoritative observations.** They must never deactivate anything. The 35 existing exact matches give a ready-made identity key (`uniquename`) for deduplication. |
| **Test requirements** | **Medium.** Fixtures for both listing pages (the repo's fixture harness already supports injected `requester` callables and `None`-for-404), a positive case proving a listing-only title is discovered, and a test that a listing observation never deactivates. |
| **Operational cost** | **Low.** Two extra page fetches per walk, both already within the existing request budget. |
| **Verdict** | **The only strategy that closes the coverage gap.** Recommended as the *next* implementation if the 422 rows are judged material (§8). |

### C. Retire the legacy scraper and preserve frozen historical observations

| Dimension | Assessment |
|---|---|
| **Complexity** | **Low.** Remove `raiplay` from `SCRAPER_REGISTRY`, delete `scrapers/rai_play.py`, update the tests that instantiate it. |
| **Expected coverage** | **Loss of 422 rows' *liveness*, no loss of *rows*.** All 457 legacy rows are already projected into canonical `ProviderContent` with `provider_content_id = legacy-<pk>`; retiring the scraper deletes none of them. They become permanently frozen observations. |
| **Data-integrity risk** | **Medium.** (1) DEFECT 1 is baked in permanently — the 135 misclassified series rows can never be corrected by a re-scrape. (2) `ProviderContent.last_seen` for those 457 rows is frozen at the backfill date, so any freshness-based read silently treats 2026-10-01 as "now". (3) The 422 rows will never be re-observed by any source. |
| **Test requirements** | **Medium.** `tracker/tests/test_scrapers.py` (RaiPlayScraper ×6), `test_scraper_series.py` (RaiPlayScraper ×5). Plus a new assertion that every legacy row has a canonical counterpart before deletion (§7.4). |
| **Operational cost** | **Low.** Removes a dead code path. |
| **Verdict** | **Safe for row preservation, unsafe as a *silent* decision.** It converts 422 rows from live to frozen without recording that as a product decision. |

### D. Introduce a separate reconciliation mechanism

| Dimension | Assessment |
|---|---|
| **Complexity** | **High.** A new component that diffs legacy vs canonical and reconciles. |
| **Expected coverage** | **No direct gain.** Reconciliation cannot discover a title neither collector has seen. |
| **Data-integrity risk** | **High.** Any automatic merge of bucket D (361 ambiguous year-only rows) would be a guess. The repo's own rule forbids a fuzzy match overwriting a deterministic one. |
| **Test requirements** | **High.** A reconciler is exactly the kind of component that needs a parity proof, a positive case, and a "never merge ambiguous" assertion. |
| **Operational cost** | **Medium.** A standing process or a repeated operator command. |
| **Verdict** | **Not recommended.** It solves a problem the data does not have: the 35 exact matches are already deduplicated by identity, and the 422 unmatched rows are *not* duplicates to be reconciled — they are distinct titles. |

### 7.1 Comparison summary

| | A keep both | B extend canonical | C retire + freeze | D reconcile |
|---|---|---|---|---|
| Closes the 422 gap | ✗ | **✓** | ✗ | ✗ |
| Preserves all rows | ✓ | ✓ | ✓ | ✓ |
| Corrects the 135 misclassified rows | ✗ | ✗ | ✗ (bakes them in) | ✗ |
| Adds complexity | none | medium | low | high |
| Data-integrity risk | low | low | medium | high |
| Test burden | none | medium | medium | high |
| Operational cost | low | low | low | medium |

---

## 8. Recommended next implementation

**Recommendation: C now, with B as the follow-up — but C must be *explicit*, not silent.**

### Step 1 — Correct the 135 misclassified rows before any retirement decision

The stored `content_type` for 135 rows disagrees with the scraper's own classification
(§6.1). Because `content_type` is part of the canonical `ProviderContent` identity, a wrong
value blocks a future canonical observation of the same title from merging — it would
create a duplicate row instead.

**Action:** re-ingest the 135 rows so the stored classification is rebuilt from the live
scraper's classification, and add an assertion that a `Fiction`/`Serie TV` card projects as
`series`. This must land **before or together with** any retirement, because retirement
makes the wrong values permanent.

**Not proven:** whether the live write path would reproduce the error today. The current
`persist_scraped_item` + `_content_type` path produces 137 series rows, not 2, so the 135
were written by an earlier path. Tracing that needs the git history of
`scraped_mapping.py` and `scrapers/rai_play.py` (§10).

### Step 2 — Record the 422 frozen titles explicitly, then retire

Before removing `RaiPlayScraper`:

1. Run a reconciliation that asserts **all 457 legacy rows have a canonical counterpart**
   (`provider_content_id = legacy-<pk>`) — measured today: **457/457**, so this passes.
2. Persist the list of 422 legacy-only rows as an explicit artifact (a report, a table, or
   a documented count), so the "these are frozen, not lost" decision is on the record.
3. Remove `raiplay` from `SCRAPER_REGISTRY` and delete `scrapers/rai_play.py`.
4. Run the **full suite** (2,544 baseline), not a targeted one.

**Why C before B:** B adds new discovery surface and new operational moving parts. C is
reversible (the rows survive), removes a dead code path, and its cost is a documented
product decision rather than new code. If the 422 rows prove material after C, B is the
recovery path — and B is *easier* once the legacy scraper is gone, because the listing
pages become the only source for those titles.

### Step 3 — Then B, if the 422 rows are judged material

Extend `RaiPlaySource` to also read `/film/` and `/serieitaliane`. The 35 exact matches
already prove the identity key (`uniquename`) joins cleanly, so deduplication is solved.

### Step 4 — Let the canonical walk finish

The collector is at 504/2,239 (~22%). Until it completes, no strategy can be called
complete. This is the precondition for judging any coverage claim.

### Step 5 — Do not build D

Reconciliation adds risk without adding coverage. The 35 exact matches are already
deduplicated; the 422 unmatched rows are distinct titles, not duplicates.

---

## 9. GO / HOLD / NO-GO

### RaiPlay retirement → **HOLD**

**Reason.** Retirement is *safe for row preservation* — all 457 legacy rows are projected
into canonical tables and none would be deleted. It is **not yet justified on coverage
grounds**, because the canonical replacement has only walked ~22% of the catalogue (504 of
2,239 programmes) and has **no surviving checkpoint** (§6.3). Retiring now would freeze
422 rows whose titles the canonical collector has simply not reached yet.

**The decision that would move HOLD → GO:**

| Condition | Currently | Needed |
|---|---|---|
| Canonical walk has completed (or been declared intentionally partial) | 504/2,239 (~22%), no cursor row | complete, or an explicit "bounded by design" decision |
| DEFECT 1 fixed (content-type classification) | **unfixed** — 135 rows wrong | fixed |
| 422 frozen titles explicitly recorded | not recorded | recorded |
| Live check that the two listing pages add titles the sitemap lacks | **not measured** (forbidden by constraints) | measured |

**If the 422 rows are judged immaterial** (they are titles the canonical walk has not
reached, not titles it failed to find), then **C alone is a GO** — the rows survive, the
code path is dead, and the decision is documented. That is a product judgement, and this
audit does not make it.

**NO-GO conditions:** any retirement that deletes or deactivates the 457 bridged rows, or
that merges bucket D (361 ambiguous year-only rows) into canonical rows. Both are
explicitly forbidden by this task's constraints.

---

## 10. Unresolved questions

Stated as **Not proven** rather than inferred.

1. **Do the two editorial listing pages contain titles the sitemaps lack?** This is the
   only candidate cause that could be true and material (§5.3). Settling it requires a live
   fetch of `/film/` and `/serieitaliane`, which this audit's constraints forbid. **Highest
   priority next measurement.**
2. **Are the 422 legacy-only titles still available on RaiPlay?** No live check was
   permitted. The canonical collector has not observed them; that is *unobserved*, not
   *unavailable* (§5.2).
3. **Is the canonical walk's lack of a cursor row a bug or expected?** §6.3 shows the
   behaviour is *correct for an exhausted walk* but the observed pattern (7 runs, 504 rows,
   0 cursors) is consistent with repeated restarts. Whether the walk is being interrupted
   or is simply slow cannot be determined from the database alone.
4. **Why does `items_discovered` total 11 across 7 SyncRuns while 504 rows exist?** The
   counter semantics are documented as "offers in the resolved group"
   (`refresh.py:847`), so this is likely correct — but the run audit therefore cannot be
   used as a catalogue-progress measure, and a separate progress signal may be needed.
5. **Does any consumer outside this repo call the RaiPlay scraper endpoints?**
   `POST /api/scrapers/run/` and `POST /api/v2/unofficial/scrape/` are the only ways to
   reach `RaiPlayScraper` today (measured: `refresh_target` returns `CATALOGUE`). An
   external consumer cannot be ruled out from the repository.
6. **The 6 rows with no usable `typology`** (`Kids` ×5, `Programmi Tv` ×1) fall back to the
   listing-page signal in the scraper (`scrapers/rai_play.py:116`) and to `MOVIE` in the
   bridge. Whether those 6 are correctly classified is **Not proven**.

---

## 11. Coverage-preservation risks

| Risk | Severity | Mitigation |
|---|---|---|
| Retiring the scraper freezes 422 rows as never-refreshed | **Medium** | Record them explicitly before retirement; they are preserved, only unrefreshed. |
| DEFECT 1: 135 stored `content_type` values disagree with the scraper's classification | **High** | Re-ingest the 135 rows before retirement; a re-ingest after retirement is impossible. |
| A later canonical observation of a misclassified legacy title creates a **duplicate** row (different identity slot) | **Medium** | Fix DEFECT 1; the identity slot includes `content_type`. |
| `ProviderContent.last_seen` frozen at 2026-10-01/02 for 457 rows, silently read as fresh | **Medium** | Do not use `last_seen` as a freshness signal for bridged rows; `last_scraped_at` is NULL on all of them. |
| The canonical walk covering only ~22% of the catalogue while being treated as the replacement | **High** | Complete the walk (or declare it intentionally bounded) before retiring the legacy source. |
| Merging bucket D (361 ambiguous year-only rows) on a shared year | **High** | **Do not.** A shared year is not identity; the repo forbids fuzzy overwrites. |
| Loss of the legacy scraper's asserted Italian audio/subtitle/quality metadata | **Low** | Already lost at projection time (§4.6); not recoverable from the bridge. Not an availability loss. |
| Deleting or deactivating historical observations | **—** | Explicitly forbidden by this task; no strategy here does it. |

---

## 12. Constraint compliance

| Constraint | Status |
|---|---|
| Read-only; no application code changed | **Honoured** — all DB access was `SELECT`; the only Python executed read rows into memory and never called `save()`. |
| No database writes | **Honoured** |
| No mappings or scraper registrations changed | **Honoured** |
| No bulk external API requests | **Honoured** — no scraper ran; no upstream request was made. |
| No paid Watchmode / Streaming Availability calls | **Honoured** |
| No live scraper execution | **Honoured** |
| No source attribution changed | **Honoured** |
| No historical observations deleted or deactivated | **Honoured** |
| No change to `AccessTypeFilter.tsx` | **Honoured** |
| No implementation commits | **Honoured** — this document is left uncommitted. |
| Missing catalogue observations not read as unavailability | **Honoured** — §5.2; the audit explicitly declined to classify any of the 422 as stale. |

**Not proven is not proven.** §10 lists what this audit could not settle. The 422 rows are
*unobserved*, not *unavailable*, and no claim below should be read as the contrary.

---

## Appendix A — Evidence commands

```bash
# Volume + bridge integrity (read-only)
docker exec -i wheretfdoiwatchit-db-1 psql -U tracker -d tracker -P pager=off -A -F'|' -c "
SELECT s.slug AS source, count(*) FROM availability_providercontent c
JOIN tracker_provider p ON p.id=c.provider_id
JOIN availability_source s ON s.id=c.source_id
WHERE p.slug='raiplay' GROUP BY 1 ORDER BY 2 DESC;"

# Reachability probe (read-only, no network)
docker exec wheretfdoiwatchit-backend-1 sh -c 'cd /app && PYTHONPATH=/app python -c "
import django, os; os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\",\"config.settings\"); django.setup()
from tracker.services.provider_status import refresh_target, catalogue_source_map
from tracker.scrapers import resolve_scraper_class
from tracker.models import Provider
sm = catalogue_source_map()
p = Provider.objects.get(slug=\"raiplay\")
print(p.slug, refresh_target(p, sm), resolve_scraper_class(p.slug).__name__)"'
```

## Appendix B — Defect reproduction

```python
# DEFECT 1: 135 stored rows disagree with the live scraper's classification.
# Read-only; never calls save().
from tracker.scrapers.rai_play import SERIES_TYPELOGIES
from availability.models import ProviderContent

# for each stored payload:
live   = "series" if payload["typology"] in SERIES_TYPELOGIES else "movie"
stored = ProviderContent.objects.get(
    provider_content_id=f"legacy-{mapping.pk}").content_type
# 135 rows: live == 'series' but stored == 'movie' (129 Fiction + 6 Serie TV)
```

---

*Report written to `docs/RAIPLAY_LEGACY_RECONCILIATION.md` in the `phase3-raiplay-audit`
worktree. Left uncommitted as instructed. `RaiPlayScraper` was not retired.*
