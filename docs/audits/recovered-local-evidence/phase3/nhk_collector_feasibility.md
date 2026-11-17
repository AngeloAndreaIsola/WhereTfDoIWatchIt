# Recovered local evidence — Phase 3: NHK World collector feasibility

> **This file is a recovered archive.** The source document was present only in a
> local worktree and was left uncommitted there. It has been copied unchanged
> (body below) for preservation; this header records its origin and limits.
>
> The findings are historical. They describe the repository and the NHK
> WORLD-JAPAN service as measured at the time of the original audit and are
> **not claims about current provider behavior**. Do not activate or modify the
> NHK collector based on this archive.

## Provenance

| Field | Value |
|---|---|
| Original worktree | `.worktrees/phase3-nhk-feasibility` (canonical checkout `.../WhereTfDoIWatchIt`) |
| Source path | `docs/NHK_COLLECTOR_FEASIBILITY.md` |
| Source branch | `phase3-nhk-feasibility` |
| Source HEAD SHA | `5dcd6cf0c4daff70551cd2e0748003b17ac4814e` |
| Source file tracked? | **No — untracked** (`?? docs/NHK_COLLECTOR_FEASIBILITY.md`) |
| Date of original findings | 2026-10-25 (commit date of HEAD `5dcd6cf`; report references live measurements from that session) |
| Archived destination | `docs/audits/recovered-local-evidence/phase3/nhk_collector_feasibility.md` |
| Content already present in master? | **No.** `docs/NHK_COLLECTOR_FEASIBILITY.md` is not tracked in `origin/master` (`7cec749f40ec1bfad51ac6d3c54a5b80485fc5ec`), and no `nhk_world` canonical availability source exists in master (`backend/availability/sources/` has no `nhk` source). |
| Unique findings preserved? | Yes — full body archived below. |
| Unresolved technical concerns | (a) Country/geo availability of NHK WORLD-JAPAN — **BLOCKER, unresolved**; (b) Terms of Use (Article 8) legal risk — **unresolved, accepted risk only with named owner**; (c) Explicit expiry — **requirement gap**, API has no expiry field; only HTML `VideoObject.expires` (~1 yr post-upload); (d) No `year` field, no TMDb IDs, no movies, `caption_langs` always empty; (e) Undocumented API host `api.nhkworld.jp`, stale `api3.json` spec (modified 2017-01-08). |

## Source document (unchanged)

# Phase 3.3E — NHK World Collector Feasibility

**Status:** research only. No application source, database, or scraper was modified.
**Base commit:** `5dcd6cf` (branch `phase3-nhk-feasibility`).
**Scope of evidence:** every claim below is either read from this repository at the
base commit or measured against the live public NHK WORLD-JAPAN endpoints with a
handful of bounded GETs (no bulk crawling, no auth, no paid API). Where a
capability could not be proven, it is marked **Not proven** rather than assumed.

**Decision: HOLD.** A canonical collector is technically buildable on a real,
key-free, paginated JSON catalogue, but one hard blocker (geo-availability) is
unresolved and one requirement gap (explicit expiry in the machine payload) is
unmet. Recommended path is A deferred behind a geo probe — see §9.

---

## 1. Current implementation diagnosis

### 1.1 What exists

| Artifact | Location | State |
|---|---|---|
| `NHKScraper` | `backend/tracker/scrapers/nhk.py` (64 lines) | Registered, runs, yields nothing |
| Registry entry | `backend/tracker/scrapers/__init__.py:22` | `"nhk_world": "tracker.scrapers.nhk.NHKScraper"` |
| Denylist guard | `backend/tracker/services/scraper_selection.py:27` | `nhk_world` in `LEGACY_SCRAPER_FALLBACK_DENYLIST` |
| Provider row | migration `0011` (seeded `free`), `0018` (re-asserted `free`) | `category=bvod`, `is_enabled=true` |
| Canonical source | **none** | no `nhk_world` entry in `availability/sources/` |

### 1.2 Verified non-functionality

The scraper fetches one of two URLs and parses for `data-testid="program-card"`
(`nhk.py:28-41`). Measured against the live site:

| URL fetched | Live result |
|---|---|
| `https://www3.nhk.or.jp/nhkworld/` | `200`, 7,383 bytes |
| `https://www3.nhk.or.jp/nhkworld/search/?q=…` | **`404`** — `<title>Error: 404 Not Found - NHK WORLD` |
| `https://www3.nhk.or.jp/nhkworld/en/shows/` | `200`, 7,618 bytes |
| `https://www3.nhk.or.jp/nhkworld/en/shows/2101069/` | `200`, 112,687 bytes |
| `https://www3.nhk.or.jp/nhkworld/en/shows/category/19/` | `200`, 173,378 bytes |

`data-testid="program-card"` and `data-testid="program-title"` appear **0 times**
in every one of those five pages (home, shows index, episode detail, category
listing). The homepage is a JS shell (7.4 KB) with no embedded state — the only
`window.*` assignment anywhere is `window.NWTool`, a UI helper, not a data
hydration object. The search route the scraper builds at `nhk.py:30` 404s.

**The scraper therefore cannot produce a single item.** Its own source comment
concedes this (`nhk.py:53-55`: *"the homepage is a JS shell and the search route
404s"*), and it then hard-codes `content_type="movie"`, `quality="1080p"`,
`audio_languages=["ja"]` and `subtitle_languages=["en","ja"]` for whatever it
might find — three of those four values are fabricated, not observed.

### 1.3 Why the tests pass anyway

`test_scrapers.py:122-135` and `test_scraper_series.py:284-294` both
`@patch.object(NHKScraper, "fetch_url")` and feed a **synthesised** HTML string
containing `data-testid="program-card"`. The assertion at
`test_scrapers.py:129` is that the item carries `JA` in `audio_languages` — a
property no real NHK page supplies. The tests verify the parser against HTML the
site does not emit; they are not evidence the collector works. This is the
"tests encode the shape of the fixture, not the shape of the source" failure
mode.

`test_scraper_reliability.py:236` only asserts the query is URL-encoded
(`_params(url)["q"] == [QUERY]`), which passes for any URL.

### 1.4 Database state (live `tracker` DB, read-only)

```
tracker_provider        nhk_world | NHK WORLD-JAPAN | bvod | is_enabled=t
                        last_scraped_at = 2026-10-03 17:15:52.708445+00
tracker_scrapedcontentmapping (nhk_world) = 0
availability_providercontent      (nhk_world) = 0
availability_offer                (nhk_world) = 0
availability_synccursor for any nhk source    = 0
```

**Verified:** zero legacy NHK observations of any kind. Note the one nuance that
contradicts a naive reading: `last_scraped_at` is **populated**, so the
"never ran" framing is wrong. The correct framing is *"the scraper was invoked
and produced nothing"* — consistent with a 404 on the search route. The card
status for `nhk_world` reads `refresh_kind="scraper"` (registry hit at
`scraper_selection.py:87`), `available_items=0`.

### 1.5 Finding verification summary

| Supplied finding | Verdict |
|---|---|
| `NHKScraper` is the only NHK-specific implementation | **Confirmed** — sole NHK class; only `nhk_world` slug exists (`nhk` merged away in `0033`) |
| No canonical `nhk_world` availability source exists | **Confirmed** — absent from `availability/sources/__init__.py` and the registry |
| Local DB contains zero legacy NHK observations | **Confirmed** — 0 across all three tables |
| Relies on a JS-rendered page | **Confirmed** — 7.4 KB shell, no embedded catalogue state |
| Search route returns 404 | **Confirmed** — `404`, 2,616 bytes, NHK error page |
| NHK World remains a seeded provider with subscription metadata | **Partially confirmed** — provider row is `bvod` + `is_enabled`, but it is **not** a `subscription` row and carries no subscription metadata (no `Subscription` link). Treat the "subscription metadata" half as **not proven** |

---

## 2. Candidate discovery mechanisms

Four surfaces were investigated. All HTTP results below are live measurements.

### 2.1 Ruled out

| Surface | Verdict | Evidence |
|---|---|---|
| Homepage scraping | **Dead** | 7,383-byte JS shell; no catalogue state |
| `/nhkworld/search/` | **Dead** | `404` |
| `robots.txt` sitemap discovery | **Dead** | `sitemap.xml` → `sitemap_app` (19 locs), `sitemap_alternate` (40), `sitemap_other` (264). **Zero `/shows/` URLs** in any of them |
| A-Z index (iPlayer pattern) | **N/A** | NHK has no A-Z route |
| Legacy `tv_programs_en.json` | **Misleading** | 388 items but **179 distinct `PgmID_S`**; `url` empty on 365/388; no years; only a thumbnail-derived series link |

The legacy `tv_programs_en.json` deserves a warning: it looks like a catalogue
but is a genre/navigation index. `PgmID_S` is **not** a programme identity
(`3004` maps to 38 distinct titles across seasons; `3016` to 40). Using it as a
content key would create 179 rows for ~388 programmes and collide on titles.

### 2.2 The viable surface: `api.nhkworld.jp/showsapi/v1/`

Found by reading the site's own SDK (`/nhkworld/assets/javascripts/api-sdk.js`)
and its endpoint spec (`/nhkworld/assets/api_sdk/api3.json`). Key facts:

- **No API key required.** `api3.json` marks every `showsapi` resource
  `requireKey: false`. Verified live: `200` with no auth header.
- **Host:** `https://api.nhkworld.jp/showsapi/v1/{lang}/…`
- **Language segment:** `en` (also `ar`, `zh`, `zt`, `fr`, `id`, `ko`, …).
- **`requireKey: true` resources are the trap** — `showssearch/…/list.json`
  returns `403 {"message":"Missing Authentication Token"}` and the `vod`/`rod`
  families need keys. Only the `shows` prefix is open.

#### 2.2.1 Programme catalogue — paginated, 462 programmes

```
GET https://api.nhkworld.jp/showsapi/v1/en/video_programs
```

Measured response:

```json
{
  "pagination": {
    "limit": 100, "offset": 0, "count": 100, "total": 462,
    "next": "/showsapi/v1/en/video_programs?offset=100",
    "previous": null
  },
  "items": [ … 100 VideoProgram objects … ]
}
```

- **Pagination proven live:** `?offset=100` returned 100 *different* ids
  (`overlap: 0`), `?offset=400` returned 62 with `next: null`. 5 pages, 462 total.
- Each item carries `id` (slug, e.g. `design_stories`), `series_id` (`"2101"`),
  `title`, `description`, `url`, `categories[]`, `tags[]`, `langs[]`,
  `is_new`, `is_closed`, `episode_sort`, and nested `video_episodes` /
  `video_clips` **counts** with their own `uri`.

#### 2.2.2 Episodes per programme

```
GET https://api.nhkworld.jp/showsapi/v1/en/video_programs/{slug}/video_episodes
```

```json
{
  "pagination": { "limit": 100, "offset": 0, "count": 21, "total": 21,
                  "next": null, "previous": null },
  "items": [ … ]
}
```

Episode item keys (measured, complete set):

```
broadcast_schedules, caption_langs, categories, description,
first_broadcasted_at, html_description, html_title, id, images, lang,
tags, title, type, url, video, video_program, voice_langs
```

Sample episode: `id="5131007"`, `title="Design Hunting in Yamaguchi"`,
`first_broadcasted_at="2026-07-17T14:30:00Z"`,
`url="/nhkworld/en/shows/5131007/"`, `voice_langs=["en"]`, `caption_langs=[]`.

Measured episode counts per programme (real, not estimated): `journeys` 25,
`scienceview` 24, `catseye` 23, `design_stories` 21, `centuryfilm` 4,
`100years-midosuji` 1.

#### 2.2.3 Static JSON mirror (secondary, no API host)

```
GET /nhkworld/data/{lang}/tv/program/{Slug}.json          → programme detail
GET /nhkworld/data/{lang}/tv/episode_list/{Slug}.json    → episode list
GET /nhkworld/data/en/tv/tv_programs.json                 → genre → category map
GET /nhkworld/apibase/tv_programs_en.json                 → legacy index (avoid)
```

`program/{slug}.json` carries `id`, `series_id` (`program_slag`), `title`,
`description`, `created_at`, `updated_at`, `public_at`, `categories[]`,
`thumbnails[]`, `schedules[]`. `episode_list/{slug}.json` carries the same 21
fields as the showsapi episode plus `onair_date`, `onairs[]`, `public_at`,
`public_id`, `thumbnails[]`.

**These two are not paginated.** `?limit=2`, `?offset=2` and `?page=2` all
returned byte-identical responses (88,120 bytes, 54 episodes). A full programme's
episode list arrives in one request — which is *why* the walk is cheap (see §6).

### 2.3 Episode detail page — the only expiry signal

```
GET /nhkworld/en/shows/{id}/  →  200, 112,687 bytes
```

The page carries `schema.org` JSON-LD:

```json
{
  "@type": "VideoObject",
  "name": "Designs for Touch - Design × Stories",
  "description": "…",
  "thumbnailUrl": "https://www3.nhk.or.jp/nhkworld/en/shows/2101069/images/wide_l_….jpg",
  "uploadDate": "2026-06-19T23:58:00+09:00",
  "duration": "PT28M0S",
  "embedUrl": "https://www3.nhk.or.jp/nhkworld/en/shows/2101069/",
  "expires": "2027-06-18T23:59:00+09:00"
}
```

This is the **only** expiry source found: ~1 year after upload. It is HTML
scraping (one request per episode), not an API field — see §5, gap (c).

---

## 3. Capability assessment against the required questions

| Required capability | Status | Evidence |
|---|---|---|
| Stable programme identities | **Yes** | `id` slug + `series_id` numeric; `VideoProgram`/`VideoEpisode` `@type`; `is_closed` flags finished runs |
| Titles | **Yes** | `title` + `html_title`; programme and episode level |
| Years | **Partially** | **No `year` field anywhere.** `first_broadcasted_at` (ISO-8601) yields a year. Catalogue items have zero year/release fields |
| Movies | **Not proven** | No `movie`/`film` content type. Genre vocabulary has no "Movie". Closest are `J-FLICKS` (Culture & Lifestyle/Entertainment) and Drama-coded titles (`Kurara…`, `Kiriko's Crime Diary`) — these are series/episodes, not films. NHK World is a documentary/news broadcaster |
| Series | **Yes** | `VideoProgram` with `video_episodes.total` |
| Episodes | **Yes** | `VideoEpisode` items with ids, titles, dates, urls |
| Playable programme URLs | **Yes** | `/nhkworld/en/shows/{id}/` on every programme and episode |
| Country availability | **Not proven — BLOCKER** | No country/region field in any payload. `categories[].uri` is `en/categories/19` (language, not country). See §5 |
| Pagination | **Yes** | `pagination.next/previous`, proven live, 5 pages |
| Incremental updates | **Partially** | `is_new`, `is_closed`, `updated_at`, `public_at`, `first_broadcasted_at` allow change detection; no delta feed |
| Explicit expiry information | **Not met** | API has **no** expiry field (checked every episode and programme key: `expiry-ish: []`). Only the HTML `VideoObject.expires` |

---

## 4. Proposed canonical source capabilities

The existing `AvailabilitySource` protocol fits without modification. A
`NHKWorldSource` would declare:

```python
slug = "nhk_world"              # matches Provider.slug, like rai_play/bbc_iplayer
name = "NHK WORLD-JAPAN"
source_type = SourceType.WEB
capabilities = SourceCapabilities(
    supported_providers=frozenset({"nhk_world"}),
    supported_countries=frozenset({"JP"}),        # ← see §5, unresolved
    supported_content_types=frozenset({ContentType.SERIES, ContentType.EPISODE}),
    supports_catalogue_sync=True,
    supports_title_fetch=False,                   # no per-title lookup exists
    supports_resumable_catalogue_sync=True,       # offset checkpoint, cf. RaiPlay
    supports_incremental_sync=False,              # no delta feed
    supports_authoritative_snapshots=False,       # no removal signal; bounded walk
    supports_tmdb_ids=False,                      # no external ids published
    supports_seasons=False,                       # no season numbers, only dates
    supports_episodes=True,
    supports_languages=False,                     # caption_langs=[] on every sample
    country_from_result=False,                    # cannot learn market from response
)
```

**Mapping behaviour.** With `supports_tmdb_ids=False`, offers are written only
after the refresh service resolves a local title/year match. This is the RaiPlay
and BBC iPlayer shape, not a new pattern. Two consequences must be designed for:

1. NHK titles are programme/episode titles (`"Design Hunting in Shizuoka"`),
   not film titles. Unmatched content yields **no `Offer`** rows
   (`Offer.tmdb_id` is non-null) but **does** persist `ProviderContent` — so
   discovery is retained even when mapping fails. Expect a high unresolved rate.
2. No year is published, so year cannot be part of the match key; title-only
   matching applies (the same constraint BBC iPlayer documents).

**Identity choice.** The provider-native identity should be the **episode id**
(`5131007`) for episode offers and the **programme slug** (`design_stories`) for
the series row — both stable and unique, unlike `PgmID_S`.

**Checkpoint.** `SyncCursor` is unique on `(provider, source, country)` and its
`cursor` is opaque text, so the `checkpoints.encode/decode` codec applies
directly. The natural anchor is the programme index (0–461) or the programme id,
both stable under re-ordering — the same property RaiPlay documents for its
sitemap anchor. `supports_resumable_catalogue_sync=True` with
`supports_incremental_sync=False` is the correct, already-established pairing.

**Registration.** Requires one import + one `source_registry.register(...)` line in
`availability/sources/__init__.py`, plus a `Source` DB row. Note the registry
maps DB `Source.slug` → code implementation, and registration is application
code, not data (`registry.py:9-11`). `catalogue_source_map()` in
`provider_status.py:76` is built from capabilities alone, so once the source
declares `supported_providers={"nhk_world"}`, the provider card switches from
`refresh_kind="scraper"` to `"catalogue"` with **no view changes**.

---

## 5. Risks, blockers, and unresolved questions

### (a) COUNTRY AVAILABILITY — **BLOCKER, unresolved**

This is the decisive question and it cannot be answered from the payloads.
No programme, episode, or category object carries a country, region, or
`eligibleRegion` field. The only language-scoped data is the `{lang}` path
segment and `langs[]` (e.g. `["en","zh","zt","fr","hi","id","pt","ru","es","th",
"tr","vi","uk"]`) — those are **interface languages**, not markets.

The repository's own rule applies here: a source that cannot observe a region
must be skipped as unobservable rather than assumed (`rai_play.py:69-71` —
*"a region the source does not declare is skipped, and a missing region is
skipped as unobservable rather than assumed"*). Declaring `JP` because NHK is
Japanese would be exactly the assumption that rule forbids.

NHK WORLD-JAPAN is by design an **international** service. The geo question
genuinely matters: if it is globally available, the country scope is not `JP`
but "every enabled region", which is a different (and larger) claim than any
existing collector makes. If it is geo-fenced outside Japan, the scope is `JP`
and the collector is only useful to a JP-region user.

**Required before GO:** a bounded probe of the episode player/stream endpoint
from at least one non-JP egress to establish whether playback is region-locked.
Until that is measured, `supported_countries` cannot be filled in honestly.

### (b) TERMS OF USE — **HIGH, unresolved legal risk**

`https://www3.nhk.or.jp/nhkworld/en/terms/` (NHK Online Services Terms of Use)
prohibits, in Article 8:

> *"Engaging in any action relating to the Services for the purpose of
> commercial gain or the purpose of preparing to do so"*

and:

> *"This includes collecting data on the Services and reproducing the said data
> by circumventing technical measures NHK takes to prevent the reproduction of
> its copyrighted work (including but not limited to description of robots.txt
> files and authentication using Login ID and password) **for the purpose of
> developing or training AI**"*

Also relevant: *"Applying excessive loads on the network devices or systems"* and
the Article 11 prohibition on *"Links intentionally generated in large numbers
through automated means such as bots"*.

Assessment for this project: **personal, non-commercial, single-user, low-volume
metadata collection** (titles, dates, URLs — not video, audio, or article text)
sits far from the commercial-gain and AI-training prohibitions. But:

- The terms are amended unilaterally and NHK disclaims completeness (Art. 9).
- The API host `api.nhkworld.jp` has **no published developer terms**; only the
  website terms were found. Whether they govern the API host is **not proven**.
- `robots.txt` disallows `/` for GPTBot/ClaudeBot/CCBot and other AI crawlers
  (`User-agent: GPTBot … Disallow: /`). A collector identifying as
  `WhereTfDoIWatchIt/1.0` is not in that list, but this is a direct signal of
  NHK's stance toward automated collection and should be treated as a live risk,
  not ignored.

**Not a blocker for a personal single-user tool, but it must be recorded as an
accepted risk with a named owner, and the collector must send a descriptive
User-Agent, keep volume low, and cache aggressively.**

### (c) EXPLICIT EXPIRY — **requirement gap**

The API exposes no expiry field. Only the HTML `VideoObject.expires` does, at
~1 year post-upload. Getting it costs one HTML request per episode.

Options, in order of preference:

1. **Do not record expiry.** Declare the source non-authoritative (already the
   plan) and rely on re-walking. Absence never deactivates, so a programme that
   expires stays listed until a mapping is removed manually. Acceptable for a
   personal tracker, but it means stale entries persist.
2. **Scrape `VideoObject.expires`** for episodes. Adds 1 request/episode and a
   BeautifulSoup/JSON-LD dependency to a source that is otherwise pure JSON.
3. **Infer a window** from `uploadDate` + 1 year. This is an assumption, not an
   observation — the same class of fabrication the current scraper already
   commits with `quality="1080p"`. **Recommended against.**

### (d) GEO-RESTRICTION — subsumed by (a)

### (e) REQUEST RATE / RELIABILITY

Volume is modest (see §6): ~470 catalogue requests plus one per episode. The
established collector pattern (politeness delay, `requester` injection for tests,
`SourceFetchError` on non-200, bounded `max_contents`) applies unchanged. The
main reliability risk is **silent contract change**: `api3.json` was last
modified *"Sun Jan 08 2017"*, so the spec is stale; the endpoints work today but
nothing pins them. A parser must fail loudly (`SourceFetchError`) on an
unexpected payload shape rather than returning an empty catalogue — the
`rai_play.py:30-34` rule.

### (f) MOVIES ARE ABSENT

NHK World does not appear to publish feature films. A canonical collector would
therefore populate **no `movie` rows** for this provider. That is a correct
reflection of the source, not a gap — but it means the collector cannot satisfy
any "where can I watch this film on NHK World" query, and the provider card
will show series/episodes only.

---

## 6. Estimated implementation effort

Measured inputs: 462 programmes over 5 pages; ~44/100 sampled programmes carry
episodes; observed episode counts 1–25. Extrapolating the sample, a full walk is
on the order of **5 catalogue requests + ~462 programme-detail requests +
~1,000–2,000 episode requests** ≈ 1,500–2,500 requests. At the collectors'
standard 0.1 s delay that is roughly **3–5 minutes per full walk**.

| Task | Est. | Basis |
|---|---|---|
| `NHKWorldSource` + client (catalogue, programme, episodes) | 3–4 d | `rai_play.py` (931 lines) and `bbc_iplayer.py` (814 lines) are the direct templates |
| Checkpoint integration (`checkpoints.encode/decode`) | 0.5 d | Codec already exists; `CURSOR_VERSION` + one part |
| Registration + `Source` row + migration/seed | 0.5 d | One import, one `register()`, one row |
| Tests (fixture router, no network) | 2 d | Every collector has a `requester`-injected test suite |
| Terms/geo decision + docs | 1 d | Blocked on §5(a) and §5(b) |
| **Total** | **~7–8 working days** | Excluding the geo probe and any legal review |

**Maintenance burden: LOW–MEDIUM.** One JSON API, no auth, paginated, stable
ids. The exposure is contract drift (undocumented API, stale spec) rather than
structural complexity. Compare: RaiPlay requires ~4,500 requests for a full walk
and needs two page fetches per programme; NHK needs one programme request and
one episode-list request per programme, both unpaginated.

---

## 7. Options compared

### A. Build a canonical NHK World collector — **recommended, deferred**

*Pros:* Real key-free paginated catalogue (462 programmes); pagination proven;
stable identities; fits the existing `AvailabilitySource` + checkpoint
framework with no interface changes; switches the provider card from a dead
scraper to a working queue-backed collector automatically.

*Cons:* Geo-availability unresolved (**blocker**); no movies; high expected
unresolved-mapping rate; terms-of-use risk to accept explicitly.

*Verdict:* **The right long-term answer.** Technically the cleanest of the four
options and the only one that produces real data.

### B. Repair the existing `NHKScraper` — **rejected**

There is nothing to repair. The scraper's target markup does not exist on the
site, its search route 404s, and its fallback values are fabricated. Repointing
it at the `showsapi` would turn a 64-line legacy scraper into a second,
inconsistent implementation of the same collector — two code paths, two
identity schemes, two checkpoint semantics, and a registry entry that
`scraper_selection.py` already treats as a retirement candidate. It would also
remain outside the canonical `ProviderContent`/`ContentMapping`/`Offer` pipeline.

### C. Keep NHK listed without a functional collector — **acceptable interim**

The provider row is already seeded, `is_enabled`, and rendered as a BVOD card
with `available_items=0`. Leaving it alone costs nothing and is strictly better
than the current state (a scraper that runs and yields nothing while appearing
to be a live collector). This is the honest status quo and the correct holding
pattern while §5(a) is resolved.

### D. Retire `NHKScraper` while retaining the provider record — **do not do now**

Retirement is *already prepared for, not forbidden*: `nhk_world` sits in
`LEGACY_SCRAPER_FALLBACK_DENYLIST` (`scraper_selection.py:27-31`), so removing
the registry entry would make scraper execution **fail closed** with
`code="retired_named_scraper"` rather than falling back to a speculative
`/api/search` crawl. `test_scraper_selection.py:101` asserts exactly that.

But retiring now would leave the provider card with `refresh_kind=None` and no
collector at all — a regression from "broken scraper" to "no collector", and it
destroys the working registry entry that option A would reuse. Retirement is the
correct *sequencing step after* A ships, not instead of it.

---

## 8. Recommendation

### Decision: **HOLD**

A canonical NHK World collector is **feasible and worth building**, but not yet.
One hard blocker is unresolved and must be measured before any code is written.

**Gate to GO — all three required:**

1. **Geo probe (blocking).** Determine whether NHK World episode playback is
   region-locked, by testing the player/stream endpoint from one non-JP egress.
   Result decides `supported_countries`: `{"JP"}` if locked, or a documented
   multi-region scope if not. *Until measured, this field cannot be filled in
   honestly.*
2. **Expiry decision (blocking).** Choose between "no expiry, non-authoritative,
   stale entries persist" and "scrape `VideoObject.expires`". Option 1 is
   recommended for a personal tracker; option 3 (inferring a 1-year window) is
   rejected as an assumption.
3. **Terms sign-off (blocking).** Record the Article 8 commercial-gain and
   AI-training prohibitions as an accepted risk with a named owner, confirm the
   collector sends a descriptive User-Agent, and confirm whether the website
   terms govern `api.nhkworld.jp` (**not proven**).

**Recommended sequence:**

| Step | Action | Authorised? |
|---|---|---|
| 1 | **HOLD** — no implementation | ✅ this report |
| 2 | Keep provider listed, no collector (option C) | ✅ current state |
| 3 | Run the geo probe; resolve §5(a) | ⏳ needs approval |
| 4 | Decide expiry policy; record terms risk | ⏳ needs approval |
| 5 | Build `NHKWorldSource` (option A) | ⏳ needs approval |
| 6 | **Then** retire `NHKScraper` (option D) — the denylist already makes this safe | ⏳ needs approval |

**Explicitly not authorised by this report:** modifying application source,
changing the database, retiring the scraper, building the collector, or touching
`AccessTypeFilter.tsx`.

---

## 9. Unresolved questions

1. **Is NHK World playback geo-restricted?** (§5a — the blocker)
2. **Do the website Terms of Use govern `api.nhkworld.jp`?** Not proven; no API-specific terms were found.
3. **Is `caption_langs` ever non-empty?** Every episode sampled returned `[]`; if NHK never publishes captions, `supports_languages=False` is correct and the field can be ignored rather than modelled.
4. **Does `is_closed` imply removability?** A closed programme with `video_episodes.total=0` was observed (`10yearshayaomiyazaki`), which suggests a removal signal — but one sample is not proof of a general rule.
5. **What is the real episode universe?** The 462-programme catalogue was measured; total episode count was extrapolated from a 100-programme sample, not enumerated.
6. **Does any programme publish a year?** None of 388 catalogue items did. If a year is required for matching, title-only resolution is forced — is that acceptable to the product?
7. **`PgmID_A` is empty on all 388 items.** Its purpose is unknown; do not model it.

---

## Appendix — evidence log

| # | Claim | Source |
|---|---|---|
| 1 | Search route 404 | `curl` → `404`, 2,616 B, `<title>Error: 404 Not Found - NHK WORLD` |
| 2 | Homepage is a JS shell | 7,383 B; only `window.NWTool`; no catalogue state |
| 3 | `data-testid="program-card"` absent | 0 occurrences in 5 fetched pages |
| 4 | Catalogue endpoint paginates | `?offset=0/100/400` → 100/100/62 items, `total: 462`, zero overlap |
| 5 | `showsapi` is key-free | `api3.json` `requireKey: false`; live `200` with no auth header |
| 6 | `showssearch` needs a key | live `403 {"message":"Missing Authentication Token"}` |
| 7 | Episode fields (21 keys) | `video_programs/{slug}/video_episodes` items |
| 8 | No expiry in API | every episode/programme key scanned: `expiry-ish: []` |
| 9 | `VideoObject.expires` exists | episode detail page JSON-LD |
| 10 | Episode lists unpaginated | `?limit/offset/page` → byte-identical 88,120 B |
| 11 | Sitemaps lack `/shows/` | 3 sitemaps, 323 locs, 0 `/shows/` |
| 12 | `tv_programs_en.json` is not a catalogue | 388 items, 179 distinct `PgmID_S`, 365 empty `url` |
| 13 | DB has zero NHK content | `tracker` DB, 3 tables, read-only query |
| 14 | Terms prohibit commercial gain / AI collection | `nhkworld/en/terms/`, Article 8 |
| 15 | `robots.txt` disallows AI crawlers | `User-agent: GPTBot … Disallow: /` |
