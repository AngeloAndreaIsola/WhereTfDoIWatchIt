# Legacy Scraper Retirement Audit

**Date:** 2026-10-25
**Branch:** `feature/scrapres-improvedment`
**Scope:** every scraper class, catalogue source, aggregator, registry entry, worker task and scheduled entry point in `backend/`
**Deliverable:** audit only — **nothing was consolidated, deleted or migrated** in this pass.

Verdict vocabulary: **GO** | **NO-GO** | **HOLD** (needs evidence this audit could not produce)

> **Not proven is not proven.** Every claim below carries a line-numbered citation into
> the code on this branch, a row from the live `tracker` database (read-only, 2026-10-25),
> or a measured test result. Where a property is only *implied* rather than enforced, it
> is marked **Not proven**. A claim I could not establish is written as such rather than
> inferred from naming.

**Baseline test state:** `python manage.py test tracker availability -v 1 --noinput`
→ **Ran 2544 tests in 151.998s — OK** (dev overlay, live stack, 2026-10-25). Any
retirement that drops this number is a regression, not a cleanup.

---

## 0. Reading order

1. §1 What "legacy scraper" means here, and the two architectures
2. §2 Complete inventory
3. §3 Caller / dependency matrix
4. §4 Classification
5. §5 Duplicate comparison across the eleven mandated axes
6. §6 Migrations and database relationships (deletion safety)
7. §7 Required pre-retirement tests
8. §8 Retirement candidates ranked
9. §9 GO / NO-GO verdict per candidate
10. §10 Proposed implementation sequence
11. §11 Changes requiring approval
12. §12 Findings I could not settle

---

## 1. Two architectures, one word

The word "scraper" is used for two unrelated subsystems in this repo, and conflating
them is the fastest way to a wrong retirement decision.

| | **Legacy scraper stack** | **Availability collector stack** |
|---|---|---|
| Module | `tracker/scrapers/` | `availability/sources/` |
| Interface | `BaseScraper` / `ScrapedItem` (`scrapers/base.py:88`, `:64`) | `AvailabilitySource` / `ContentRequest` (`sources/base.py:268`, `:139`) |
| Write target | `tracker.ScrapedContentMapping` → bridge → canonical | `availability.ProviderContent` / `ContentMapping` / `Offer` directly |
| Registry | `SCRAPER_REGISTRY` (slug → dotted path, `scrapers/__init__.py:20`) | `source_registry` (slug → zero-arg factory, `sources/registry.py:33`) |
| Scheduling | none — synchronous, in-request | `refresh_provider_catalogue` / `refresh_worker` lanes |
| Checkpointing | none | versioned `SyncCursor` codec (`sources/checkpoints.py`) |
| Provenance | `Source(slug="legacy_scraper")` | one `Source` row per collector |
| Write barrier | shared (`writer_barrier`, `availability/services/write_barrier.py`) | same barrier |

The two are **not** interchangeable. `ScrapedContentMapping` is an *ingestion-era*
table whose every read path has already been migrated (see §6.4); the canonical
catalogue is the only table any product surface reads today.

---

## 2. Complete inventory

### 2.1 Legacy scraper classes (`tracker/scrapers/`)

| Class | File:line | `provider_slug` | Countries | Movies | Series |
|---|---|---|---|---|---|
| `BBCScraper` | `bbc.py:33` | `bbc_iplayer` | GB (hard-coded, `bbc.py:36`) | yes (only) | no |
| `NHKScraper` | `nhk.py:14` | `nhk_world` | JP (hard-coded, `nhk.py:21`) | yes (default) | no |
| `RaiPlayScraper` | `rai_play.py:32` | `raiplay` | IT (hard-coded, `rai_play.py:35`) | yes | yes |
| `KissKHScraper` | `adapters.py:70` | `kisskh` | none declared | yes | yes |
| `HydraHDScraper` | `adapters.py:240` | `hydrahd` | none declared | yes | yes |
| `AnimeWorldScraper` | `adapters.py:420` | `animeworld` | none declared | yes | yes |
| `GenericURLAdapter` | `adapters.py:183` | *any* | none | yes (default) | no |

Supporting classes (not scrapers): `ScrapedItem` (`base.py:64`),
`BaseScraper` (`base.py:88`), `ContentMatcher` (`base.py:134`).

**Count: 7 scraper implementations, 3 of them site-specific priority sources
(BBC/NHK/RaiPlay), 3 independent-source adapters, 1 generic fallback.**

### 2.2 Catalogue / title-level sources (`availability/sources/`)

Registered in `availability/sources/__init__.py:31-46`. Thirteen registrations:

| Source | File | Role (`source_policy.SOURCE_ROLES`) | Title fetch | Catalogue sync | Resumable | Countries |
|---|---|---|---|---|---|---|
| `cbc_gem` | `cbc_gem.py:221` | COLLECTOR | no | yes | yes | CA |
| `bbc_iplayer` | `bbc_iplayer.py:231` | COLLECTOR | no | yes | yes | GB |
| `tubi` | `tubi.py:321` | COLLECTOR | no | yes | yes | US |
| `prime_video` | `prime_video.py:312` | COLLECTOR | no | yes | yes | (per-country const) |
| `rai_play` | `rai_play.py:306` | COLLECTOR | no | yes | yes | IT |
| `youtube` | `youtube.py:247` | COLLECTOR | yes | no | — | `FREE_PROGRAM_COUNTRIES` |
| `pluto_tv` | `pluto_tv.py:351` | COLLECTOR | no | yes | yes | per-path |
| `mediaset_infinity` | `mediaset_infinity.py:284` | COLLECTOR | yes | no | — | IT |
| `pbs` | `pbs.py:254` | COLLECTOR | no | yes | yes | US |
| `abc_iview` | `abc_iview.py:233` | COLLECTOR | no | yes | yes | AU |
| `justwatch` | `justwatch.py:117` | BROAD_AGGREGATOR | **yes** | **yes** | yes | unrestricted |
| `watchmode` | `watchmode.py:211` | BROAD_AGGREGATOR | yes | no | — | NL/CA/JP |
| `streaming_availability` | `streaming_availability.py:26` | METERED_AGGREGATOR | yes | no | — | all |

Nine of thirteen declare `supports_resumable_catalogue_sync=True` (verified by grep:
`abc_iview`, `bbc_iplayer`, `cbc_gem`, `justwatch`, `pbs`, `pluto_tv`, `prime_video`,
`rai_play`, `tubi`).

`justwatch_providers.py` is not a source: it is the static identity table
(`PACKAGE_TO_PROVIDER_SLUG`, 342 lines) plus the per-market short-name table that the
aggregator uses to resolve a JustWatch package onto a canonical provider slug.

### 2.3 Registry entries

**`SCRAPER_REGISTRY`** (`scrapers/__init__.py:20-27`) — six entries:

```
bbc_iplayer  -> tracker.scrapers.bbc.BBCScraper
nhk_world    -> tracker.scrapers.nhk.NHKScraper
raiplay      -> tracker.scrapers.rai_play.RaiPlayScraper
kisskh       -> tracker.scrapers.adapters.KissKHScraper
hydrahd      -> tracker.scrapers.adapters.HydraHDScraper
animeworld   -> tracker.scrapers.adapters.AnimeWorldScraper
```

**`source_registry`** — thirteen entries (`sources/__init__.py:31-46`).

`availability.Source` **database** rows: 14 (`SELECT ... FROM availability_source`), i.e.
the 13 registered sources plus `legacy_scraper` (id 3), which is created by
`legacy_ingest.legacy_scraper_source()` (`availability/services/legacy_ingest.py:33`) and
seeded nowhere in a migration. `tracker.services.source_policy.SOURCE_ROLES` covers the
13 registered slugs and is asserted exhaustive by
`tracker/tests/test_availability_report_evidence.py:93`.

### 2.4 Worker tasks and scheduled entry points

| Entry point | Kind | Sources driven |
|---|---|---|
| `python manage.py refresh_worker --lane title` | always-on container (`docker-compose.yml:100`) | title-level requests (all title-fetch sources) |
| `python manage.py refresh_worker --lane catalogue` | always-on container (`docker-compose.yml:113`) | `CatalogueRefreshRequest` markers |
| `python manage.py refresh_provider_catalogue [--source X]` | **cron** `17 */6 * * *` + per-source slots (`docs/CRON.md:59-66`) | 9 catalogue sources |
| `python manage.py refresh_availability --budget 90` | **cron** `17 3 * * *` (`docs/CRON.md:11`) | stale-title sweep, metered |
| `python manage.py refresh_justwatch_catalogue` | operator command (`docs/CRON.md:193`) | JustWatch partitions |
| `python manage.py ingest_legacy_scraper_catalogue` | **one-off backfill** (`docs/DEPLOYMENT.md:90`) | legacy `ScrapedContentMapping` → canonical |
| `python manage.py seed_unofficial_providers` | setup | creates `kisskh`/`hydrahd`/`animeworld` rows |
| `python manage.py reseed_providers` | setup | replays canonical provider inserts |
| `python manage.py remediate_mapping_audit` | operator | destructive mapping repair |
| `python manage.py purge_orphan_offer_{prices,evidence}`, `cleanup_availability_history`, `sync_providers`, `backfill_movie_metadata`, `seed_catalog`, `seed_prime_fixture` | operator/setup | unrelated |

**No scheduled entry point runs a legacy scraper.** The only scraper drivers are
user-initiated HTTP endpoints (§3).

---

## 3. Caller / dependency matrix

Resolved statically from every call site in `backend/` (excluding tests), then confirmed
against the live database.

### 3.1 Legacy scraper call graph

```
POST /api/scrapers/run/            views.scraper_run          views.py:965
POST /api/scrapers/import-url/      views.scraper_import_url   views.py:900
POST /api/v2/unofficial/scrape/      api_v2.unofficial_scrape   api_v2.py:254
POST /api/providers/<slug>/refresh/  views.provider_refresh    views.py:766  (SCRAPER branch)
                                        │
                                        ▼
                            run_provider_scraper()   services/scraper_runner.py:29
                                        │
                        ┌───────────────┴────────────────┐
                        ▼                                ▼
          resolve_scraper_class(slug)          GenericURLAdapter(provider.domain)
          scrapers/__init__.py:45               adapters.py:183
                        │                                │
                        ▼                                ▼
              scraper.run(query)  ─────────────►  list[ScrapedItem]
                        │
                        ▼
        persist_scraped_item()      services/scraped_mapping.py:16
                        │  writes tracker.ScrapedContentMapping
                        ▼
        ingest_scraped_mapping()    availability/services/legacy_ingest.py
                        │  writes availability.ProviderContent (+ ContentMapping)
                        ▼
              canonical catalogue  ◄── read by every product surface
```

Read-side consumers of the canonical projection (not of the legacy table):
`tracker/services/scraped_offers.py` (`get_scraped_offers`), reached from
`views._build_prime_offers` and `api_v2.unofficial_movie_offers`; and
`api_v2.unofficial_catalog` via `ProviderCatalogueQueryService.list_unofficial`.

### 3.2 Live reachability matrix

Produced by instantiating the real registry against the real database
(`refresh_target()` + `resolve_scraper_class()`), not by reading comments:

| Provider | `category` | `refresh_kind` | `source_slug` | Scraper class | Scraper reachable? |
|---|---|---|---|---|---|
| `bbc_iplayer` | bvod | **catalogue** | `bbc_iplayer` | `BBCScraper` | **NO — shadowed** |
| `raiplay` | bvod | **catalogue** | `rai_play` | `RaiPlayScraper` | **NO — shadowed** |
| `nhk_world` | bvod | scraper | None | `NHKScraper` | YES |
| `kisskh` | scraper | scraper | None | `KissKHScraper` | YES |
| `hydrahd` | scraper | scraper | None | `HydraHDScraper` | YES |
| `animeworld` | scraper | scraper | None | `AnimeWorldScraper` | YES |

`refresh_target()` (`services/provider_status.py:93`) returns `CATALOGUE` whenever a
catalogue source names the provider, and `CATALOGUE` is checked **before** the scraper
branch (`:104-108`). The docstring says so explicitly: *"Catalogue sources win over
scrapers when a provider has both (RaiPlay and BBC iPlayer do)"*.

**This is the audit's central structural finding:** for the two providers whose legacy
scrapers are the most developed, the scraper class is **dead code on the refresh path**.
It is still importable and still listed in the registry, so a reader of
`SCRAPER_REGISTRY` alone would conclude it is live. It is not.

### 3.3 Other references

| Symbol | Non-test references |
|---|---|
| `SCRAPER_REGISTRY` | imported but **never read** at runtime: `api_v2.py:34` (dead import), `views.py:59` (dead import) |
| `resolve_scraper_class` | `scraper_runner.py:35`, `views.py:914`, `api_v2.py:79`, `provider_status.py:107`, `serializers.py:196` |
| `ContentMatcher` | `views.py:932`, `views.py:984`, `views.py:1090`, `api_v2.py:299` |
| `run_provider_scraper` | `views.py:768`, `views.py:982` |

`GenericURLAdapter` is the fallback for **any** provider without a registered scraper
class and `category == "scraper"` (`provider_status.py:107`). It is therefore the
execution path for every URL-imported provider, and it is the only scraper that has no
site-specific parser at all — it assumes `GET /api/search?q=` or a JSON list
(`adapters.py:191-203`).

---

## 4. Classification

| Implementation | Classification | Basis |
|---|---|---|
| `BBCScraper` | **Legacy, unreachable via refresh** | `refresh_kind=catalogue`; reachable only by POSTing `/api/scrapers/run/` or `/api/v2/unofficial/scrape/` |
| `RaiPlayScraper` | **Legacy, unreachable via refresh** | same shadowing; 457 legacy rows vs 504 canonical |
| `NHKScraper` | **Legacy but active** | `refresh_kind=scraper`; zero rows in DB (§6.2) |
| `KissKHScraper` | **Legacy but active** | `refresh_kind=scraper`; 61 legacy rows, no canonical source |
| `HydraHDScraper` | **Legacy but active** | `refresh_kind=scraper`; 109 legacy rows, no canonical source |
| `AnimeWorldScraper` | **Legacy but active** | `refresh_kind=scraper`; 181 legacy rows, no canonical source |
| `GenericURLAdapter` | **Canonical fallback, active** | only path for URL-imported providers |
| `BaseScraper` | Canonical infrastructure | shared by all seven |
| `ScrapedItem` | Canonical data shape | shared |
| `ContentMatcher` | **Canonical, active** | TMDb matching + `_upsert_mapping`; also the review/confirm path |
| `legacy_ingest` bridge | **Canonical, active** | write side of every legacy observation |
| `persist_scraped_item` | Canonical, active | single write seam |
| `scrapers/bbc.py`, `nhk.py`, `rai_play.py` | Duplicate files with unique coverage | §5 |
| `scrapers/adapters.py` (KissKH/HydraHD/AnimeWorld) | Unique — no canonical counterpart | §5.10 |
| `test_hydrahd.py` | **Unused** | root-level ad-hoc script, imports `config.settings.local` which does not exist |
| `seed_unofficial_providers` | Active setup command | re-asserts `category="scraper"` for the three adapters |
| `reseed_providers` | Active setup command | replays canonical inserts; deliberately does not replay the deleted aliases |
| `ingest_legacy_scraper_catalogue` | Active one-off | `docs/DEPLOYMENT.md:90` |

**No implementation in this inventory is a proven duplicate with parity.** The closest
case is RaiPlay, and it is *partial*, not proven (§5).

---

## 5. Duplicate comparison across the eleven mandated axes

Only one legacy scraper has a canonical counterpart of the same provider. BBC and NHK
have a canonical collector and a legacy scraper but **different catalogue scopes**, so
they are partial overlaps rather than duplicates. The three adapter scrapers have no
canonical counterpart at all.

### 5.1 Supported countries

| | Legacy | Canonical |
|---|---|---|
| RaiPlay | IT hard-coded (`rai_play.py:35`) | IT (`sources/rai_play.py` `COUNTRY`) |
| BBC | GB (`bbc.py:36`) | GB (`bbc_iplayer.py` `COUNTRY`) |
| NHK | JP (`nhk.py:21`) | — no canonical NHK source exists |

All three legacy scrapers hard-code a country that the canonical model carries as a
declared capability. No divergence found.

### 5.2 Movies and television series

| | Legacy movies | Legacy series | Canonical |
|---|---|---|---|
| RaiPlay | `typology ∈ {Film, Documentari}` (`scrapers/rai_play.py:21`) | `{Fiction, Serie, Serie TV}` (`:20`) | `layout=="single"` → MOVIE, `"multi"` → SERIES (`sources/rai_play.py:484-486`) |
| BBC | **explicitly movies only** (`scrapers/bbc.py:135`) | none | single programmes → MOVIE; brands → SERIES + SEASON + EPISODE |
| NHK | movies (default, no type signal) | none | — |

**Divergence: real and load-bearing.** The legacy BBC scraper reads the
`/iplayer/categories/films/featured` page and therefore *cannot* see a series, while the
canonical collector walks the A-Z index and emits series/season/episode offers
(`sources/bbc_iplayer.py:8-13`). Retiring `BBCScraper` removes nothing the canonical
source does not already cover.

### 5.3 Catalogue versus title-level responsibility

| | Responsibility |
|---|---|
| `BBCScraper` | **catalogue listing only** — one featured page, no search by title (`bbc.py:41`) |
| `NHKScraper` | catalogue listing **plus** a search route (`nhk.py:27-30`) |
| `RaiPlayScraper` | catalogue listing (`/film/`, `/serieitaliane`) plus search (`/ricerca`) (`scrapers/rai_play.py:41-43`) |
| canonical `bbc_iplayer` | catalogue walk (27 A-Z letters + per-brand pages), **no title fetch** |
| canonical `rai_play` | sitemap-driven catalogue walk, **no title fetch** |

Neither legacy scraper is title-addressable in the `AvailabilitySource` sense; neither
can answer "is this one film on RaiPlay?" — they can only list what the site currently
features. That is a strictly smaller capability than the canonical walk.

### 5.4 Metadata and external identifiers

| Field | Legacy | Canonical |
|---|---|---|
| external IDs | **none** — no TMDb id, resolved later by `ContentMatcher` | BBC/RaiPlay: none (`supports_tmdb_ids=False`); mapping via local title/year |
| audio languages | asserted (`["en"]` BBC, `["it"]` RaiPlay, `["ja"]` NHK) | BBC: none (`supports_languages=False`); RaiPlay: `subtitlesArray` only, audio always `None` |
| subtitle languages | asserted (`["en"]` / `["it"]` / `["en","ja"]`) | BBC: none; RaiPlay: measured |
| quality | asserted `"1080p"` for BBC/NHK/RaiPlay | BBC: none; RaiPlay: `uhd` (observed `null` on 48/48 captures) |

**Divergence: the legacy scrapers *assert* metadata the canonical sources deliberately
refuse to guess.** `BBCScraper` sets `audio_languages=["en"]` unconditionally
(`scrapers/bbc.py:137`); the canonical collector declares `supports_languages=False`
because iPlayer publishes no language metadata (`sources/bbc_iplayer.py:68-72`). This is
exactly the "missing fields must never become an English/1080p claim" rule the adapter
scrapers were fixed to honour (`adapters.py:158-161`).

### 5.5 Pagination and checkpointing

| | Legacy | Canonical |
|---|---|---|
| Pagination | KissKH 2 pages/type; HydraHD 1 page/category; AnimeWorld 2 pages/category; BBC/RaiPlay/NHK single page | versioned `SyncCursor` codec (`sources/checkpoints.py`), 9 sources resumable |
| Checkpointing | **none** | durable, per-(provider, source, country) |

**The legacy scrapers have no checkpoint at all.** A truncated run restarts from the
top. Phase 2 added durable checkpoints to nine sources; none of them is a legacy
scraper, and none can be, because the legacy classes have no cursor plumbing.

### 5.6 Authentication / API requirements

| | Auth |
|---|---|
| `BBCScraper` | none (public pages; geo-fenced, `bbc.py:50-52` detects and returns `[]`) |
| `NHKScraper` | none |
| `RaiPlayScraper` | none (geo-block detection `scrapers/rai_play.py:54`) |
| `KissKHScraper` | none (public JSON API) |
| `HydraHDScraper` | none (spoofed `Referer`/`User-Agent`, `adapters.py:243-248`) |
| `AnimeWorldScraper` | none |
| `GenericURLAdapter` | none |

No legacy scraper holds a credential. The only authenticated sources in the system are
`watchmode` (`WATCHMODE_API_KEY`) and `streaming_availability` (`RAPIDAPI_KEY`).

### 5.7 Rate limits and quotas

**None.** No legacy scraper is metered, throttled, or quota-guarded. It issues unbounded
synchronous requests inside an HTTP request handler with no pacing. By contrast the
canonical path has `SourceQuotaExceeded` with `plan_quota` / `local_budget` /
`rate_limit` kinds (`sources/base.py:72`), a `rate_limit` service, a 100/day metered
aggregator, and per-source advisory locks (`services/bulk_refresh.py:665`).

This is a **reliability regression carried by the legacy path**, not a feature: a
`GET /api/providers/` card press on a scraper-category provider blocks the request
thread for the duration of a multi-page crawl.

### 5.8 Provider identity and country attribution

Legacy scrapers stamp a single hard-coded provider slug and country onto every item and
persist it via `persist_scraped_item` → `ScrapedContentMapping.country_code`. The
canonical path resolves provider identity through `ExternalProviderMapping`
(country-aware, `tracker/models.py`) and `justwatch_providers.PACKAGE_TO_PROVIDER_SLUG`
for the aggregator. **No overlap in mechanism**; the legacy path has no way to express
a provider that is named differently per market.

### 5.9 Availability evidence and absence semantics

| | Legacy | Canonical |
|---|---|---|
| Absence | `ScrapedItem.availability` bool; `BaseScraper.run` swallows all exceptions → `[]` (`base.py:122-128`) | `SourceFetchResult` empty = "source reported nothing"; raising `SourceFetchError` = retrieval failure |
| Geo-block | returns `[]` — **indistinguishable from an empty catalogue** | sources skip unobservable regions and log |
| Failure | `fetch_url` retries `max_retries=2`, then `None`; `run()` returns `[]` | typed exceptions, `SyncRun` audit row, `RefreshRun` error |

**This is the sharpest divergence in the audit.** `BBCScraper._execute_run` returns `[]`
both when iPlayer is geo-blocked and when the page is empty
(`scrapers/bbc.py:50-52`); `BaseScraper.run` then converts any exception into `[]`
(`base.py:126`). A reader of the legacy table cannot tell "BBC has no films" from "we
were outside the UK" from "the request failed". The canonical contract makes these
three states distinct and tests it.

### 5.10 Provider identity — the three adapters

`kisskh`, `hydrahd`, `animeworld` have **no canonical counterpart whatsoever**. The
`availability_source_map()` probe returned only eight providers
(`abc_iview`, `bbc_iplayer`, `cbc_gem`, `pbs`, `pluto_tv`, `prime`, `raiplay`, `tubi`).
No `AvailabilitySource` declares `supported_providers` containing any of the three.

Their rows are real: 61 / 109 / 181 `ProviderContent` rows, all attributed to
`source=legacy_scraper`, 90 `ContentMapping` rows total (26 + 60 + 4), all with
`confidence ≥ 0.6`.

**These are not duplicates. They are the entire availability record for three
providers.** Retiring them without a canonical replacement would delete the only
coverage those providers have — a direct violation of the constraint *"Preserve all
existing provider coverage."*

### 5.11 Error handling and retry behaviour

| | Legacy | Canonical |
|---|---|---|
| Retry | `BaseScraper.fetch_url`, `max_retries=2`, no backoff (`base.py:98-116`) | per-source bounded retries with backoff (e.g. Tubi sitemap ×2) |
| Failure propagation | `run()` catches `Exception` → `[]` | `SourceFetchError` subclasses; `SyncRun.status=failed` |
| Audit | `Provider.last_scraped_at` only | `RefreshRun` + `SyncRun` rows, `abandoned_at` reconciliation |
| Partial progress | lost | checkpoint persists after each committed batch |

---

## 6. Migrations and database relationships

### 6.1 Migrations that created the scraper-era rows

| Migration | Effect |
|---|---|
| `0007_provider_system.py:80` | seeds `kisskh`, `hydrahd`, `animeworld` as `category="scraper"` |
| `0008_provider_domain_and_last_scraped.py` | sets `domain` on the three |
| `0011_seed_free_streaming_providers.py` | seeds `raiplay` (free), `bbc_iplayer` (free), `nhk_world` (free) |
| `0012_v2_unofficial_sources.py:12` | seeds the **alias** slugs `rai-play`, `bbc`, `nhk` |
| `0014_move_bbc_to_free.py` | `bbc_iplayer` → free |
| `0017_bbc_iplayer_country_code.py` | sets `bbc_iplayer.country_code="GB"` |
| `0018_nhk_to_free_streaming_provider.py:20` | `nhk_world` → free; **line 25-26 records that `NHKScraper` is repointed to `nhk_world` so the `nhk` slug carries no scraped content** |
| `0022_fix_legacy_scraper_providers_to_bvod.py` | three broadcasters → `bvod` |
| `0033_merge_duplicate_providers.py` | folds `rai-play`/`bbc`/`nhk` aliases into canonical rows; adds `uniq_provider_domain_root` |

`0018` is the migration that already **retired the `nhk` scraper slug**: its own comment
says the scraper was repointed to `nhk_world` and "this slug carries no scraped content
going forward". The same pattern — repoint, then merge — is the precedent for retiring a
scraper without losing provider coverage.

### 6.2 Live data at risk

Measured read-only against the live `tracker` database (2026-10-25):

```
tracker_provider (scraper/broadcaster slugs)
  animeworld   scraper  domain=animeworld.ac   last_scraped_at=2026-10-02 13:42
  bbc_iplayer  bvod     domain=bbc.co.uk/iplayer  last_scraped_at=2026-10-02 16:38
  hydrahd      scraper  domain=hydrahd.ru      last_scraped_at=2026-10-02 13:42
  kisskh       scraper  domain=kisskh.co       last_scraped_at=2026-10-02 13:42
  nhk_world    bvod     domain=nhk.or.jp/nhkworld last_scraped_at=2026-10-03 17:15
  raiplay      bvod     domain=raiplay.it      last_scraped_at=2026-10-02 16:38
  (alias slugs rai-play / bbc / nhk: absent — merged by 0033)

tracker_scrapedcontentmapping
  raiplay     457 rows   25 linked to a Movie
  animeworld  181 rows    4 linked
  hydrahd     109 rows   60 linked
  kisskh       61 rows   26 linked
  bbc_iplayer   0 rows
  nhk_world     0 rows

availability_providercontent (source='legacy_scraper')
  raiplay 457 | animeworld 181 | hydrahd 109 | kisskh 71      = 818 rows

availability_contentmapping
  raiplay 25 | hydrahd 60 | kisskh 26 | animeworld 4        = 115 rows
  all with confidence >= 0.6

availability_offer
  kisskh 0 | hydrahd 0 | animeworld 0 | nhk_world 0
  (legacy scrapes write NO offers — confirmed by design, legacy_ingest.py:24)
```

**The legacy scrapers have written zero `Offer` rows.** `legacy_ingest` is explicitly
documented as writing no offers because "a legacy scrape is a catalogue observation, not
a commercial offer" (`availability/services/legacy_ingest.py:18`). Every watchability
verdict for these providers comes from the canonical catalogue, not from the legacy
table.

### 6.3 Foreign keys — deletion safety

Nine FKs reference `tracker_provider`. **All are `NO ACTION`**, i.e. a provider delete
fails unless the child rows are removed first:

```
availability_contentmapping      -> tracker_provider   NO ACTION
availability_offer               -> tracker_provider   NO ACTION
availability_providercontent     -> tracker_provider   NO ACTION
availability_synccursor         -> tracker_provider   NO ACTION
availability_syncrun             -> tracker_provider   NO ACTION
tracker_externalprovidermapping  -> tracker_provider   NO ACTION
tracker_scrapedcontentmapping    -> tracker_provider   NO ACTION
tracker_subscription             -> tracker_provider   NO ACTION
```

`views.provider_delete` (`views.py:1037-1045`) handles this by taking `writer_barrier`,
then deleting `ContentMapping`, `ProviderContent`, `AvailabilityOffer`, `SyncRun`,
`SyncCursor` in one transaction, then the provider. `ScrapedContentMapping` rows cascade
via the FK, firing the `post_delete` mirror (`tracker/signals.py:22`).

**Deleting a provider row without that transaction is a guaranteed `IntegrityError`.**
Deleting a *scraper class* has no FK consequence at all — the class is code, and the
rows it produced survive in both tables.

### 6.4 Read paths already migrated off the legacy table

Every product read path now reads canonical tables:

| Read path | Reads | Via |
|---|---|---|
| `GET /api/catalog/unified/` | canonical only | `unified_catalogue.py:15` — *"This module never reads `tracker.ScrapedContentMapping`"* |
| `GET /api/v2/unofficial/` | canonical only | `ProviderCatalogueQueryService.list_unofficial` (`api_v2.py:113`) |
| `GET /api/v2/unofficial/movie/<id>/offers/` | canonical only | `get_scraped_offers` (`api_v2.py:459`) |
| `GET /api/prime/movie/<id>/` (BVOD block) | canonical only | `scraped_offers.py:47` |
| `GET /api/providers/` card counts | **legacy table** | `ScrapedContentMapping.objects.filter(provider_id__in=...)` (`provider_status.py:248`) |
| `GET /api/scrapers/unresolved/` | **legacy table** | `views.py:1086` — the match-review UI |

`provider_status.py:248` and `views.py:1086` are the **only two remaining legacy-table
readers**, and both are display/review surfaces. Removing the legacy table requires
replacing `available_items` for scraper-category cards (§8, candidate C3).

---

## 7. Required pre-retirement tests

Baseline: **2,544 tests, OK**. Any retirement must hold this number or explain the
delta.

### 7.1 Tests that will fail if the legacy scraper classes are deleted

Measured by symbol occurrence across `tracker/tests/` and `availability/tests/`:

| Test file | Legacy symbols referenced | Approx. tests at risk |
|---|---|---|
| `tracker/tests/test_scrapers.py` | `BBCScraper` ×8, `NHKScraper` ×5, `RaiPlayScraper` ×6, `HydraHDScraper` ×6, `SCRAPER_REGISTRY` ×2 | 17 |
| `tracker/tests/test_scraper_series.py` | 6 classes, 5 `ScrapedContentMapping` refs | 38 |
| `tracker/tests/test_scraper_reliability.py` | 4 classes incl. `AnimeWorldScraper` ×12 | 22 |
| `tracker/tests/test_p2_metadata_integrity.py` | all 4 adapter classes + `GenericURLAdapter` | — |
| `tracker/tests/test_provider_system.py` | `KissKHScraper` ×6, `HydraHD`/`AnimeWorld` | — |
| `tracker/tests/test_provider_uniqueness.py` | `SCRAPER_REGISTRY` ×3, `ScrapedContentMapping` ×5 | 3 registry tests |

**Total: roughly 77 test methods across six files directly instantiate or assert on a
legacy scraper class.** These are not all scraper-behaviour tests — several are
content-type classification tests that merely use a scraper as a fixture factory.

### 7.2 Tests that will fail if the bridge or table is deleted

`ScrapedContentMapping` is referenced by **15 test files** and `ingest_scraped_mapping`
by **10**. The highest-value ones:

| Test file | What it pins |
|---|---|
| `availability/tests/test_legacy_ingest.py` | bridge projection, `legacy-<pk>` identity, backfill command |
| `availability/tests/test_legacy_ingest_barrier.py` | bridge under the write barrier |
| `tracker/tests/test_unified_catalog_canonical.py` | read path serves legacy rows from canonical tables |
| `tracker/tests/test_provider_delete_barrier.py` | provider delete cascades the mirror |
| `tracker/tests/test_unofficial_api.py` | v2 contract field-for-field |
| `tracker/tests/test_content_matching_confidence.py` | `ContentMatcher` acceptance policy |
| `tracker/tests/test_provider_refresh.py` | `refresh_kind="scraper"` card contract |
| `tracker/tests/test_prime_endpoints.py` | ingested BVOD row surfaces once in prime offers |

### 7.3 Tests that must be *added* before each retirement

1. **A positive case for the canonical replacement.** Per this repo's own rule
   (`references/collector-drift-and-attribution-cardinality.md`): instrument the seam the
   code path actually uses before calling it drift, and verify a parser fix has a
   positive case. A retirement justified only by "the canonical source exists" is not
   proven.
2. **A parity assertion on the migrated rows.** Before deleting a legacy scraper,
   assert that every `ScrapedContentMapping` row it produced has a canonical
   `ProviderContent` counterpart (`provider_content_id = legacy-<pk>`).
3. **A full-suite run**, not a targeted one. The repo's own
   `references/verification-honesty.md` rule: parity → full suite → report.
4. **A "neuter-the-fix" proof**: re-run the suite with the retirement reverted and
   confirm the same number.

---

## 8. Retirement candidates ranked

Benefit = code/operational simplification. Complexity = migration + test surface.
Risk = data loss or coverage loss.

| # | Candidate | Benefit | Complexity | Data-loss risk | Verdict |
|---|---|---|---|---|---|
| C1 | `BBCScraper` (`scrapers/bbc.py`) | Medium — dead on refresh path, superseded | Low | **None** — 0 legacy rows, canonical covers more | **GO** |
| C2 | `RaiPlayScraper` (`scrapers/rai_play.py`) | Medium — dead on refresh path | Low | **Low** — 457 rows already bridged; 422 titles have no canonical equivalent | **GO** (with §9.2 caveat) |
| C3 | `available_items` legacy read (`provider_status.py:248`) | Low | Low | None | **GO** (with C1/C2) |
| C4 | `NHKScraper` (`scrapers/nhk.py`) | Low | Low | **None** — 0 rows, no canonical counterpart, no coverage | **GO** |
| C5 | `kisskh` / `hydrahd` / `animeworld` + their adapters | High if a canonical collector existed | **High** | **Critical** — 361 rows, 90 mappings, the only coverage for 3 providers | **NO-GO** |
| C6 | `GenericURLAdapter` | None | — | **Critical** — only path for URL-imported providers | **NO-GO** |
| C7 | `ingest_legacy_scraper_catalogue` command | Low (one-off already run) | Low | None | **HOLD** — keep until C5 is resolved |
| C8 | `ScrapedContentMapping` table + `legacy_ingest` bridge | High | **High** | **Critical** while C5 stands | **NO-GO** |
| C9 | `test_hydrahd.py` | Trivial | Trivial | None | **GO** |

---

## 9. GO / NO-GO verdict per candidate

### C1 — `BBCScraper` → **GO**

**What the code does.** Reads one featured-films page
(`scrapers/bbc.py:12`, `FEATURED_URL`), asserts `audio_languages=["en"]` and
`subtitle_languages=["en"]` for every item (`:137-138`), declares `content_type="movie"`
(`:135`), and returns `[]` on geo-block (`:50-52`).

**Why it is redundant.** The canonical `BBCIPlayerSource` walks 27 A-Z letters and
per-brand pages, emits series/season/episode offers as well as movies
(`sources/bbc_iplayer.py:8-13`), declares `supports_languages=False` because iPlayer
publishes no language metadata (`:68-72`), and has a durable checkpoint
(`supports_resumable_catalogue_sync=True`). It covers a strict superset of the legacy
scraper's scope.

**Failure scenario if retired.** None identified: `bbc_iplayer` has **0**
`ScrapedContentMapping` rows and **0** rows attributed to `source=legacy_scraper`, so
nothing is lost.

**Smallest correction.** Remove `BBCScraper` from `SCRAPER_REGISTRY`, delete
`scrapers/bbc.py`, drop the two `test_scrapers.py` / `test_scraper_series.py` tests that
instantiate it.

**Proof source.** Live DB probe: `bbc_iplayer → refresh_kind=catalogue`,
`source_slug=bbc_iplayer`; `tracker_scrapedcontentmapping` count for `bbc_iplayer` = 0.

**Caveat.** `resolve_scraper_class("bbc_iplayer")` currently returns `BBCScraper`, which
makes `ProviderSerializer.get_has_scraper` report `True` (`serializers.py:194-196`). After
removal it reports `False`. No frontend code was found that branches on `has_scraper`
(only type declarations in `ProviderGrid.tsx:22` and test fixtures), so the contract
change is cosmetic — but it must be called out because it is a serializer field.

---

### C2 — `RaiPlayScraper` → **GO**, conditional

**What the code does.** Parses `window.WashiContext` from `/film/` and `/serieitaliane`
(`scrapers/rai_play.py:13-14`), classifies by `typology` (`:20-21`), asserts Italian
audio and subtitles (`:133-134`), hard-codes `quality="1080p"` (`:135`).

**Why it is redundant.** The canonical `RaiPlaySource` discovers via sitemap
(`sources/rai_play.py:7-22`), reads the same `window.WashiContext` object plus the video
access spec, emits `availabilityStarts`/`availabilityEnds`, and has a durable checkpoint.
It covers 504 titles against the legacy scraper's 457.

**Failure scenario.** The legacy scraper has **422 titles with no canonical
equivalent** (measured: 457 legacy, 504 canonical, 35 exact-title overlap, 422
legacy-only). These are bridged into canonical tables already (`provider_content_id =
legacy-<pk>`), so they are not lost — but they will never be refreshed by any source,
and the canonical collector will not re-observe them. **They are frozen observations.**

**Smallest correction.** Remove `RaiPlayScraper` from the registry, delete
`scrapers/rai_play.py`, and add an explicit assertion that the 422 frozen titles are
acceptable as frozen (or re-walk them once before retirement).

**Proof source.** Live DB probe: `raiplay → refresh_kind=catalogue`, `source_slug=rai_play`;
overlap query returns `legacy_total=457, canonical_total=504, legacy_only=422`.

**Caveat.** This is the one candidate where "partial overlap with unique functionality"
is the correct classification rather than "duplicate". The legacy scraper observes titles
the canonical sitemap walk has not reached. Retirement is safe for *coverage* (rows are
bridged) but silently converts 422 rows from live to frozen. That is a decision, not a
refactor.

---

### C3 — `available_items` legacy read → **GO** (with C1/C2)

`provider_status.py:248` counts `ScrapedContentMapping` rows to populate the
`available_items` card field for scraper-category providers. Once the table stops being
written this count goes stale. Replace with the canonical `ProviderContent` count, which
the same function already computes for catalogue providers (`:239-243`).

**Risk: none.** The canonical count is already available; this is a one-line branch.

---

### C4 — `NHKScraper` → **GO**

**What the code does.** Fetches `https://www3.nhk.or.jp/nhkworld/` or its search route
(`scrapers/nhk.py:28-30`), parses `data-testid="program-card"` elements (`:41`), and
admits in its own comment that the homepage is a JS shell and the search route 404s
(`:53-55`).

**Why it is redundant.** There is **no canonical NHK source** — `nhk_world` appears in
no `SOURCE_ROLES` entry and in no `AvailabilitySource.supported_providers`. The legacy
scraper is the *only* implementation, but it has produced **0 rows** in the live database
and **0** `ScrapedContentMapping` rows.

**Failure scenario.** None. `nhk_world` is a seeded `bvod` provider with a `Subscription`
row (`access_mode=included`) and no catalogue rows. Retiring the scraper leaves the
provider card present with no refresh control — which is already its state.

**Smallest correction.** Remove `nhk_world` from `SCRAPER_REGISTRY`, delete
`scrapers/nhk.py`, update `test_scrapers.py` / `test_scraper_series.py` /
`test_scraper_reliability.py`.

**Proof source.** Live DB probe: `nhk_world → refresh_kind=scraper`, but
`ScrapedContentMapping` count = 0 and `ProviderContent` count = 0.

**Caveat.** Unlike C1/C2 this is *not* a consolidation — nothing replaces it. It is
simply deleting a scraper that has never successfully produced data. If NHK coverage is
a product goal, the correct action is to write a canonical `nhk_world` collector, not to
keep this class.

---

### C5 — `kisskh` / `hydrahd` / `animeworld` → **NO-GO**

**What the code does.** Three independent scrapers plus the `GenericURLAdapter` fallback
for arbitrary imported domains.

**Why this is not redundant.** No canonical source covers any of the three providers.
Measured: `animeworld`, `hydrahd`, `kisskh` each have `distinct_sources = 1` and that
source is `legacy_scraper` only. They hold 361 bridged `ProviderContent` rows and 90
`ContentMapping` rows at confidence ≥ 0.6.

**Failure scenario if retired.** Deleting the three adapters removes the only collector
for three providers the user is subscribed to. Their `ProviderContent` rows survive
(they are canonical), but no source will ever refresh them, and the
`POST /api/scrapers/import-url/` flow — which the subscriptions page exposes
(`subscriptions/page.tsx:262`) — loses its generic path entirely.

**Smallest correction.** None available. This requires writing canonical collectors,
which is new capability, not retirement.

**Proof source.** Live DB probe: `availability_source_map()` contains none of the three;
`SELECT DISTINCT source_id` per provider returns only `legacy_scraper`.

---

### C6 — `GenericURLAdapter` → **NO-GO**

The only execution path for any provider without a registered scraper class
(`scraper_runner.py:38-41`, `views.py:917-918`, `api_v2.py:284-288`). Deleting it
breaks URL-imported providers categorically.

---

### C7 — `ingest_legacy_scraper_catalogue` → **HOLD**

A one-off backfill already documented as run (`docs/DEPLOYMENT.md:90`,
`docs/CRON.md:253`). It is idempotent and harmless, but it is the safety net for any
legacy row not yet bridged. Keep until C5 is resolved.

---

### C8 — `ScrapedContentMapping` + `legacy_ingest` bridge → **NO-GO**

**What the code does.** `persist_scraped_item` writes the legacy row and projects it to
canonical (`services/scraped_mapping.py:16`); `signals.py:22` mirrors deletions;
`legacy_ingest` is the projection.

**Why this is not redundant — yet.** It is the write path for C5's three providers and
the review table for `GET /api/scrapers/unresolved/`. Fifteen test files depend on it.

**Failure scenario.** Dropping the bridge while `kisskh`/`hydrahd`/`animeworld` still
scrape would orphan every new observation: rows written to `ScrapedContentMapping` with
no canonical projection, invisible to every product read path.

**Smallest correction.** Only after C5 is resolved by a canonical replacement.

**Proof source.** `availability/tests/test_legacy_ingest.py` (215 lines) and
`test_legacy_ingest_barrier.py` (237 lines) exist specifically to pin this behaviour.

---

### C9 — `test_hydrahd.py` → **GO**

Root-level script importing `config.settings.local`, which does not exist in
`backend/config/` (only `settings.py` and `test_settings.py`). It is tracked in git but
cannot run. Delete.

---

## 10. Proposed implementation sequence

Each step is independently revertible and must hold the 2,544-test baseline.

**Step 1 — C9 (trivial, zero risk).** Delete `backend/test_hydrahd.py`. No test depends
on it.

**Step 2 — C4 (NHK).** Remove `nhk_world` from `SCRAPER_REGISTRY`, delete
`scrapers/nhk.py`, update the three test files. Zero data at risk (0 rows).
Run the full suite.

**Step 3 — C1 (BBC).** Remove `bbc_iplayer` from `SCRAPER_REGISTRY`, delete
`scrapers/bbc.py`, update tests. Zero data at risk (0 rows).
Run the full suite.

**Step 4 — C2 (RaiPlay) + C3.** Remove `raiplay` from `SCRAPER_REGISTRY`, delete
`scrapers/rai_play.py`, replace the `available_items` legacy read with the canonical
count. **Before merging:** run a one-off reconciliation that asserts all 457 legacy rows
have canonical counterparts, and record the 422 frozen titles explicitly.
Run the full suite.

**Step 5 — registry hygiene.** Remove the two dead `SCRAPER_REGISTRY` imports
(`api_v2.py:34`, `views.py:59`) and the dead `_resolve_scraper` wrapper
(`api_v2.py:77-79`).

**Step 6 — documentation.** Update `docs/PLAN.md:35`, `docs/DEPLOYMENT.md:90`,
`docs/CRON.md:253` and `docs/SCRAPER_FIX_REPORT.md` to state which scrapers remain and
why.

**Not in this sequence:** C5, C6, C7, C8. They require a decision, not a refactor.

---

## 11. Changes requiring approval

| Change | Why approval is required |
|---|---|
| Retiring `RaiPlayScraper` | Freezes 422 titles as never-refreshed. A product decision, not a code decision. |
| Retiring `NHKScraper` | Removes the only NHK implementation without a replacement. If NHK coverage is wanted, a canonical collector is the right work. |
| Any change to `kisskh`/`hydrahd`/`animeworld` | Would delete the only coverage for three subscribed providers. Explicitly forbidden by this task's constraints. |
| Removing `ScrapedContentMapping` or the bridge | Requires C5 to be resolved first. |
| Changing `has_scraper` semantics | A serializer field contract; the frontend type declares it. |
| Any migration touching `availability_source` | `ProviderContent.source` and `ContentMapping.source` are `PROTECT` FKs to it. |
| Any change to `SOURCE_ROLES` | `test_availability_report_evidence.py:93` asserts exhaustiveness; a removed source must be removed from the mapping in the same commit. |

---

## 12. Findings I could not settle

Stated as "Not proven" rather than inferred.

1. **Whether the 422 RaiPlay legacy-only titles are still available on RaiPlay.** The
   canonical sitemap walk has not observed them; the legacy scraper saw them on
   2026-10-02. No live check was permitted (constraint: no bulk external API requests),
   and a single-title probe would not settle a 422-title question.
2. **Whether `kisskh.do`, `hydrahd.ws`, `animeworld.ac` are still live.** The scrapers
   have not run since 2026-10-02. Their continued viability is untested and outside this
   audit's read-only constraint.
3. **Whether any consumer outside this repo reads `/api/scrapers/run/` or
   `/api/v2/unofficial/scrape/`.** `triggerScraperRun` is defined in `frontend/lib/api.ts:1063`
   with **zero call sites** in the frontend; `triggerUnofficialScrape` has one
   (`catalog/page.tsx:243`). An external consumer cannot be ruled out from the repo.
4. **Whether the legacy scrapers are reachable from the Django admin.** `ScrapedContentMapping`
   is registered (`admin.py:225`), but no admin action was found that invokes a scraper.
5. **The real test count for C5/C8.** The 2,544 baseline is for the current tree; the
   per-candidate counts in §7.1 are symbol-occurrence estimates, not measured test
   counts. A precise count requires running each retirement, which this audit forbids.

---

## Appendix A — Evidence commands

```bash
# Baseline suite (dev overlay required — plain compose does not mount ./backend)
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py test tracker availability -v 1 --noinput

# Reachability probe (read-only)
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "
import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from tracker.services.provider_status import refresh_target, catalogue_source_map
from tracker.scrapers import resolve_scraper_class
from tracker.models import Provider
sm = catalogue_source_map()
for p in Provider.objects.filter(slug__in=['bbc_iplayer','nhk_world','raiplay','kisskh','hydrahd','animeworld']):
    kind, src = refresh_target(p, sm)
    print(p.slug, p.category, kind, src, resolve_scraper_class(p.slug).__name__)
"

# Live data probe (read-only)
docker compose -f docker-compose.yml exec -T db psql -U tracker -d tracker -c "
SELECT p.slug, p.category, count(*) AS rows,
       count(*) FILTER (WHERE scm.movie_id IS NOT NULL) AS linked
FROM tracker_provider p LEFT JOIN tracker_scrapedcontentmapping scm ON scm.provider_id=p.id
WHERE p.slug IN ('bbc_iplayer','nhk_world','raiplay','kisskh','hydrahd','animeworld')
GROUP BY 1,2 ORDER BY 1;"
```

## Appendix B — Constraint compliance

| Constraint | Status |
|---|---|
| Read-only audit | Honoured — all DB access was `SELECT`; no migration, no write |
| No database modifications | Honoured |
| No bulk external API requests | Honoured — no scraper was executed, no upstream call made |
| No production deployment | Honoured |
| Preserve all existing provider coverage | C5/C6/C8 marked NO-GO; C1–C4 lose no coverage |
| Preserve source-specific availability evidence | Legacy rows remain bridged; no canonical row is deleted by any GO verdict |
| Do not confuse overlap with redundancy | §5 classifies RaiPlay/BBC as partial overlap, not duplicates |
| Do not touch `AccessTypeFilter.tsx` | Untouched — `git status` still shows only that file modified |
