# Catalogue Checkpoint Audit

**Date:** 2026-10-24
**Branch:** `feature/scrapres-improvedment`
**Scope:** every source that declares `supports_catalogue_sync=True`
**Deliverable:** audit only — **no checkpoint was implemented** in this pass.

Verdict vocabulary: **PASS** | **PARTIAL** | **FAIL** | **BLOCKED** | **N/A**

> Not proven is not proven. Every claim below carries a line-numbered citation into
> the code as it exists on this branch, or a measured result from a test run in the
> live stack. Where a property is only *implied* rather than enforced, it is marked
> **Not proven**.

---

## 0. Reading order

1. §1 The mechanism every source shares (and the two seams that decide correctness)
2. §2 Source-by-source capability matrix
3. §3 Findings, graded
4. §4 Restart behaviour without restarting from page one
5. §5 Incomplete-scan marking
6. §6 Pagination errors, throttling, quota
7. §7 Test coverage: crash/restart, duplicate-entry, end-of-catalogue
8. §8 What implementing the missing checkpoints would have to satisfy
9. §9 Acceptance verdict, item by item

---

## 1. The mechanism every source shares

Catalogue checkpointing in this codebase is a **three-part contract**, and a source is
only durable if all three parts hold.

```
AvailabilityRefreshService.refresh_catalogue()      [refresh.py:232-350]
   │  ① resume: reads SyncCursor for (provider, source, country)
   │            and passes its value in as the fetch `cursor`
   │  ② fetch:  one bounded source.fetch(None, ..., cursor=…) call
   │  ③ persist: writes result.cursor AFTER the offer transaction commits
   ▼
SyncCursor(provider, source, country, cursor, last_successful_sync)
   unique on (provider, source, country)                [models.py:609-626]
```

### 1.1 The write order is correct (the crash gap is closed)

`refresh.py:304` persists offers first, then `refresh.py:310-328` writes the cursor.
A crash in that gap cannot advance the checkpoint — proven by
`test_abc_iview.py:582 test_a_crash_between_the_offer_commit_and_the_cursor_write_reprocesses`,
which injects `side_effect=RuntimeError("crash after commit, before cursor write")`
and asserts the walk restarts. **PASS.**

### 1.2 The unique key is the mechanism's real constraint

`SyncCursor` is unique on `(provider, source, country)` — `models.py:620-625`.
Consequences:

- A source with **one** provider/country gets a cursor row cheaply.
- JustWatch is an **aggregator**: many providers × countries share one row per
  `(provider, source, country)`, so it stores a `partition_id=offset` map in that one
  column (`justwatch_sync.py:21-45`). The coordinator snapshots the whole map before
  the fetch and restores it after, so a sibling partition is never clobbered
  (`justwatch_sync.py:496-502`, `:564-571`). **PASS**, and well-tested
  (`test_justwatch_partition_cursor.py:95 CursorIsolationTests`).

### 1.3 `last_successful_sync` is not a health signal

`SyncCursorRepository.set` stamps `last_successful_sync` on every write
(`repositories.py:1120-1128`), and the model declares it (`models.py:616`). But:

- **Nothing reads it.** Grep for `last_successful_sync` outside tests and the admin
  `list_display` returns only: the model field, the migration, the repository write,
  `test_repositories.py:924/932`, `test_refresh_service.py:538`, and `admin.py:94`.
- Measured on a live container: a hand-written cursor row reports
  `last_successful_sync set: False` — i.e. the field is not maintained by anything
  outside `CursorRepository.set`.

So `SyncCursor` **cannot** answer "is this source's catalogue walk healthy, or did it
stall halfway?" A cursor value alone does not distinguish *"walk finished"* from
*"walk died at offset 400 and never moved again"*.

> **Not proven:** that a stall is detectable. The data to detect it exists only for
> JustWatch's year windows; for every first-party collector the only record is the
> cursor text itself.

### 1.4 `supports_incremental_sync` does not mean "is checkpointed"

This is the single most misleading thing in the codebase. The flag is declared in
`SourceCapabilities` (`base.py:193`) and every collector sets it `False` — **including
the four that do checkpoint**:

| source | `supports_incremental_sync` | durable cursor? |
|---|---|---|
| `tubi` | `False` (`tubi.py:333`) | **yes** (`tubi.py:487`) |
| `prime_video` | `False` (`prime_video.py:324`) | **yes** (`prime_video.py:433`) |
| `abc_iview` | `False` (`abc_iview.py:248`) | **yes** (`abc_iview.py:359`) |
| `bbc_iplayer` | `False` (`bbc_iplayer.py:243`) | **yes** (`bbc_iplayer.py:380`) |
| `justwatch` | `True` (`justwatch.py:132`) | **yes**, via coordinator |

And nothing outside `sources/` reads the flag at all (grep over `backend/` excluding
`sources/` and `tests/` returns only the declarations). So the flag is documentation,
not a control input. **The matrix in §2 is built from the `cursor=` return site, not
from the flag.**

---

## 2. Source-by-source capability matrix

Evidence for each row: the `SourceFetchResult(cursor=…)` return site, the `_decode/_encode`
helpers, and the test file that pins the behaviour.

### 2.1 Checkpoint state (the headline question)

| # | source | catalogue | durable cursor | cursor key | page boundary | resumable | status |
|---|---|---|---|---|---|---|---|
| 1 | `tubi` | ✅ | **YES** | per-sitemap last-seen URL + cycle | `max_contents=500` | ✅ | **PASS** |
| 2 | `prime_video` | ✅ | **YES** | `{paths:[…], cues:{…}}` pending detail window | `batch_size=100` / `max_contents=500` | ✅ | **PASS** |
| 3 | `abc_iview` | ✅ | **YES** | `{v, after:<slug>}` | `batch_size=250`, `MAX_SERIES=3000` | ✅ | **PASS** |
| 4 | `bbc_iplayer` | ✅ | **YES** | `{v, letter, after:<pid>}` | `batch_size=60`, `MAX_PAGES_PER_SLICE=60` | ✅ | **PASS** |
| 5 | `justwatch` | ✅ | **YES** (coordinator) | `{partition_id: offset}` map on one row | `--pages` per visit, `CATALOGUE_MAX_OFFSET` | ✅ | **PASS** |
| 6 | `cbc_gem` | ✅ | **no** — `cursor=None` | n/a | `MAX_PAGES_PER_CATEGORY=500` per category | ❌ | **FAIL** |
| 7 | `pbs` | ✅ | **no** — `cursor=None` | n/a | `MAX_PAGES=100`, `BATCH_SIZE=50` | ❌ | **FAIL** |
| 8 | `pluto_tv` | ✅ | **no** — `cursor=None` | n/a | `MAX_PAGES=100` per hub, `PAGE_ROWS=24` | ❌ | **FAIL** |
| 9 | `rai_play` | ✅ | **no** — `cursor=None` | n/a | `max_contents=400` | ❌ | **FAIL** |
| — | `streaming_availability` | ✗ | n/a | — | — | — | **N/A** |
| — | `watchmode` | ✗ | n/a | — | — | — | **N/A** |
| — | `youtube` | ✗ | n/a | — | — | — | **N/A** |
| — | `mediaset_infinity` | ✗ | n/a | — | — | — | **N/A** |

Citations:

- `tubi.py:481-488` returns `cursor=next_cursor`; `_decode_cursor` `tubi.py:776-804`;
  `_plan_batch` `tubi.py:807-896` (URL-not-offset rationale at `:814-817`).
- `prime_video.py:422-434` returns `next_cursor` when `pending` remains;
  `_decode/_encode` `prime_video.py:1114-1149`; the ~140 KiB payload bound is documented
  at `prime_video.py:193-201`.
- `abc_iview.py:351-360`; `_decode/_encode` `abc_iview.py:543-568`.
- `bbc_iplayer.py:363-381`; `_decode/_encode` `bbc_iplayer.py:778-810`.
- `justwatch.py:467-504` `_next_cursor`; coordinator `justwatch_sync.py:333-663`.
- `cbc_gem.py:279-286` (`cursor=None`), stated reason `:231-233`.
- `pbs.py:303-310` (`cursor=None`), stated reason `:276-277` and `:66-67`.
- `pluto_tv.py:376-383` (`cursor=None`), stated reason `:347`.
- `rai_play.py:390-396` (`cursor=None`), stated reason `:327-329`.

The four negatives are not accidental: each has a test asserting the absence.

### 2.2 Cursor payload shape and versioning

| source | payload | versioned | malformed → | missing anchor → |
|---|---|---|---|---|
| `tubi` | `{v, cycle, queues:{sitemap:url}}` | `v=1` (`tubi.py:188`) | restart cycle | restart **that sitemap only** (`:832-834`) |
| `prime_video` | `{v, paths:[…], cues:{…}}` | yes | restart | n/a (self-contained window) |
| `abc_iview` | `{v, after}` | `CURSOR_VERSION` | restart from top (`:556-559`) | restart from top (`:294-300`) |
| `bbc_iplayer` | `{v, letter, after}` | `CURSOR_VERSION` | restart from top (`:791-798`) | restart **that letter** (`:319-325`) |
| `justwatch` | `{partition_id: offset}` | implicit via id shape | map untouched | n/a |

All five degrade **toward more re-reading**, never toward skipping — the correct
direction. `tubi.py:779-783` states the policy explicitly.

### 2.3 Pagination strategy per source

| source | pagination primitive | exhaustion signal | bound |
|---|---|---|---|
| `tubi` | sitemap `loc` order | end of every sitemap → next cycle | `max_contents` |
| `prime_video` | storefront index pages + `body.pagination.url` | pending window empty | `max_contents`, `batch_size` |
| `abc_iview` | full series list, sliced | list exhausted | `MAX_SERIES` |
| `bbc_iplayer` | A–Z letters, page 1 only | letters exhausted | `MAX_PAGES_PER_SLICE` |
| `justwatch` | offset paging | **empty page = depth wall** (`justwatch.py:428-436`) | `CATALOGUE_MAX_OFFSET` |
| `cbc_gem` | `pageNumber` vs `totalPages` | `page_number >= total_pages` | `MAX_PAGES_PER_CATEGORY` |
| `pbs` | `allShows` `endCursor` | `hasNextPage=False` | `MAX_PAGES` |
| `pluto_tv` | offset `start`, `hasMore` | `hasMore=False` | `MAX_PAGES` |
| `rai_play` | sitemap `loc` list | list exhausted | `max_contents` |

Every walk has a **hard bound** and a **repeated-page guard** (`cbc_gem.py:248-257`,
`pbs.py:339-345`, `pluto_tv.py:403-408`). No source can loop unbounded. **PASS.**

### 2.4 Throttling / retry behaviour

| source | delay | retry | failure semantics |
|---|---|---|---|
| `abc_iview` | `DEFAULT_DELAY=0.18` | per-request | mid-walk failure raises |
| `bbc_iplayer` | `DEFAULT_DELAY=0.1` | per-request | mid-walk failure raises |
| `prime_video` | `DEFAULT_DELAY=0.2` | per-request | mid-walk failure raises |
| `tubi` | `DEFAULT_DELAY=0.1` | **sitemap retried 2×** then run fails | `TubiPageGone` skips, transport raises |
| `rai_play` | `DEFAULT_DELAY=0.1` | per-request | 404/410 → skip; other → raise |
| `justwatch` | n/a | 3 attempts, page-size clamp | GraphQL rejection raises |
| `cbc_gem` / `pbs` / `pluto_tv` | none | none | any failure raises |

`test_tubi.py:245 SitemapRetryTests` pins the bounded retry. **PASS** for Tubi; the
others fail loudly, which is the documented contract (`cbc_gem.py:29-32`).

---

## 3. Findings, graded

### F1 — Four catalogue sources have no checkpoint at all · **CRITICAL**

**What the code does.** `CBCGemSource.fetch`, `PbsSource.fetch`, `PlutoTvSource.fetch`
and `RaiPlaySource.fetch` each accept `cursor` and ignore it, always walking the whole
configured window and returning `cursor=None`.

**Evidence.** `cbc_gem.py:224-286`, `pbs.py:268-310`, `pluto_tv.py:338-383`,
`rai_play.py:319-396`. Each file's own comment states the reason ("CBC exposes page
numbers, not a resumable server-side cursor"; "pagination is a plain offset walk
inside fetch()").

**Why this is unsafe.** These are not small catalogues. The bounds are
`MAX_PAGES_PER_CATEGORY=500` (CBC), `MAX_PAGES=100` (PBS), `MAX_PAGES=100` per hub ×
2 hubs (Pluto), `max_contents=400` (RaiPlay). A walk that takes minutes is repeated
**in full** on every scheduled run and on every restart. Concretely:

- RaiPlay: 400 titles × (1 programme page + 1 video URL, plus an `.html` player page
  for the bare-JSON shape) ≈ **840 requests** per run (`rai_play.py:138`), every run,
  forever. A restart at 90% re-reads ~750 requests.
- CBC: three categories × up to 500 pages of index **plus one detail fetch per card**
  (`cbc_gem.py:259-272`) — the detail loop is unbounded by `MAX_PAGES_PER_CATEGORY`
  (that cap only bounds the *index* walk at `:275`). One detail-page failure at
  card 300 discards 300 detail fetches.

**Failure scenario (RaiPlay).** Cron runs the RaiPlay catalogue. The walk reaches
title 380 of 400, the container is restarted by a deploy. On the next tick the run
starts at title 1 again. Titles 381-400 are observed every run (cheap), titles 1-380
are **re-fetched every single run**, so the marginal cost of a restart is the entire
catalogue and the effective coverage rate is set by restart frequency, not by budget.

**Smallest correction.** Give each of the four the same shape the four working
collectors already use: emit `cursor=<last stable identity>` when the window is
truncated, return `None` when it is exhausted, and resume from that identity on the
next call. CBC/PBS/Pluto/rai_play all have a stable per-entry identity available in
their own payload (CBC slug `cbc_gem.py:260`, PBS slug `pbs.py:325`, Pluto item id
`pluto_tv.py:403`, RaiPlay URL `rai_play.py:343`) — this is exactly the
URL-not-offset rationale `tubi.py:814-817` already documents.

**Proof source.** Code return sites above; measured constants from the live container
(`DEFAULT_MAX_CONTENTS=400` RaiPlay, `500` Tubi/Prime, `MAX_PAGES=100` PBS/Pluto,
`MAX_PAGES_PER_CATEGORY=500` CBC).

---

### F2 — The absence of a cursor is asserted by tests, so the gap is load-bearing · **HIGH**

**What the code does.** Four tests pin `assertIsNone(result.cursor)`:

- `test_cbc_gem_source.py:85-92` — "CBC exposes page numbers, not a resumable cursor token"
- `test_pbs.py:158-162` — "no resumable cursor token"
- `test_pluto_tv.py:136-143` — "Offset paging inside fetch(): no resumable cursor token"
- `test_rai_play.py:156-164` — "The sitemaps expose no resumable server-side cursor"

**Why this matters for the fix.** Implementing the missing checkpoints is not an
additive change: it requires **changing four passing tests** that currently encode the
gap as intended behaviour. Any implementation that leaves these assertions in place
will be reported as failing, and the temptation will be to keep the gap to stay green.
This must be surfaced to whoever implements it.

**Smallest correction.** Rewrite each test to assert the *new* contract — a cursor is
present when the walk is truncated and absent when it completes — mirroring the
existing positive pattern at `test_bbc_iplayer.py:618 test_a_completed_walk_returns_no_cursor`
and `test_prime_video.py:906 test_the_final_window_batch_completes_with_no_cursor`.

**Proof source.** The four test citations above; the two positive counterparts.

---

### F3 — `rai_play`'s detail loop is bounded by nothing but `max_contents`, and one failure discards it all · **HIGH**

**What the code does.** `rai_play.py:342-371` loops `for url in scanned:` with no
checkpoint inside the loop. `scanned` is `entries[:max_contents]` (`:331`), and a
transport error mid-loop raises out of the whole fetch.

**Failure scenario.** `max_contents=400`, 399 titles fetched, title 400 returns HTTP
500 → `SourceFetchError` → `_refresh_one_source` catches, writes
`RefreshRun.error`, marks the source **failed** (`bulk_refresh.py:1012-1024`), and
every one of the 399 successful detail fetches is discarded because they were only
ever in the in-memory `contents` list. The next run starts at title 1.

**Note the contrast with the working collectors.** `tubi.py:422-427` explicitly
documents why a traversal-page failure must raise rather than skip ("a batch that
cannot be completed is a failed fetch, never a smaller successful one"), and
`prime_video.py:380-383` says the same. That policy is *correct given a checkpoint*,
because the checkpoint makes the retry bounded to one batch. **Without a checkpoint the
same policy is what turns a 1-title hiccup into a full-catalogue re-walk.** F3 is F1's
mechanism, stated precisely.

**Smallest correction.** Same as F1, applied to the per-entry loop.

---

### F4 — Nothing distinguishes "walk finished" from "walk died halfway" · **MEDIUM**

**What the code does.** `CatalogueSourceSummary` (`bulk_refresh.py:601-632`) has
`status` ∈ {`succeeded`, `failed`, `locked`, `disabled`}. A source whose walk is
truncated and checkpointed reports `succeeded` — identically to one that finished.

**Evidence.** `bulk_refresh.py:1027` sets `result.status = "succeeded"` with no
truncation flag; the summary has no `remaining`/`truncated` field (`:601-632`).
`process_pending_catalogue_refresh` then marks the marker `DONE` when `errors` is
empty (`bulk_refresh.py:1191-1196`).

**Why it is unsafe.** For a checkpointed source this is *mostly* benign — the cursor
row is the real progress record, and `SyncCursor` is exposed in the admin
(`admin.py:92-94`). But it means:

- A source truncated at `max_contents` every run is reported `succeeded` forever,
  with no signal that coverage is not advancing.
- Combined with F1, the four uncheckpointed sources report `succeeded` while
  re-reading their entire catalogue on every run — the summary actively asserts
  health that does not exist.

**Smallest correction.** Add `truncated: bool` (and ideally `remaining: int`) to
`CatalogueSourceSummary`, populated from the source result, and surface it in
`catalogue_refresh_summary` (`bulk_refresh.py:1130-1138`). This is a read-out change
only; it does not alter any checkpoint semantics.

**Proof source.** `bulk_refresh.py:601-632`, `:1027`, `:1115-1139`; grep confirms no
test asserts a truncated run is distinguishable.

---

### F5 — `last_successful_sync` is written and never read · **MEDIUM**

**What the code does.** `CursorRepository.set` stamps it (`repositories.py:1126`), the
model declares it (`models.py:616`), the admin displays it (`admin.py:94`), two tests
assert it (`test_repositories.py:924`, `test_refresh_service.py:538`) — and **no
production code reads it**. Measured: a hand-written cursor row reports it unset.

**Why it matters.** It was presumably intended as the staleness signal for a stalled
walk. Without a reader it is decoration, and its presence in the admin implies a
guarantee the system does not provide. It is also the natural place to hang F4's
"did this advance?" check.

**Smallest correction.** Either read it (e.g. in `stale_catalogue_sources`,
`bulk_refresh.py:768-798`, which currently measures staleness from `RefreshRun`
audit rows instead) or remove it. Do not leave a field that reads as a health signal
but is not one.

**Proof source.** Grep for `last_successful_sync` across `backend/` (excluding
`tests/`, `.venv/`) returns only: model, migration, repository write, two tests,
admin `list_display`.

---

### F6 — `supports_incremental_sync` is set to the opposite of the checkpoint reality and nothing consumes it · **LOW**

**What the code does.** `tubi`, `prime_video`, `abc_iview`, `bbc_iplayer` all set
`supports_incremental_sync=False` while returning a durable cursor; `justwatch` sets
`True`. No code outside `availability/sources/` reads the flag.

**Why it matters.** It is a trap for the next reader (it trapped this audit's first
pass). Anyone adding a checkpoint-gated behaviour keyed on the flag would silently
exclude the four sources that actually checkpoint.

**Smallest correction.** Rename the field to state what it means, or add a sibling
`supports_catalogue_resume` that reflects the real property, and derive the matrix
from return sites rather than the flag. Flag-only change; no behaviour.

**Proof source.** Declarations at `tubi.py:333`, `prime_video.py:324`,
`abc_iview.py:248`, `bbc_iplayer.py:243`, `justwatch.py:132`; grep confirms no
consumer outside `sources/`.

---

### F7 — JustWatch's cursor reset-on-exhaustion re-walks the partition by design · **LOW (accepted)**

**What the code does.** `justwatch.py:499-503` records an empty value when a partition
hits the offset ceiling; `justwatch_sync.py:503-508` then resets it to `""` on the next
run, restarting that partition from page one.

**Why it is flagged.** It is a deliberate, documented choice (a partition must never be
permanently parked as "finished"), and it is the only design in the codebase that
*intentionally* restarts from page one. It is correct here because the offset ceiling
(`CATALOGUE_MAX_OFFSET`) is a hard upstream wall and the reset is per-partition.
Recorded so the acceptance line "without restarting from page one unnecessarily" is not
read as absolute: JustWatch's reset is *necessary*, not unnecessary.

**Proof source.** `justwatch.py:480-504`; `justwatch_sync.py:503-508`; `docs/P8_JUSTWATCH_COVERAGE.md:117`.

---

## 4. Restart behaviour without restarting from page one

| source | restart point | re-reads | unnecessary? |
|---|---|---|---|
| `tubi` | last URL per sitemap | ≤ one batch | no |
| `prime_video` | pending detail window | ≤ one batch | no |
| `abc_iview` | last slug | ≤ one batch | no |
| `bbc_iplayer` | `{letter, after}` | ≤ one batch | no |
| `justwatch` | per-partition offset | ≤ one partition visit | no (reset is deliberate, F7) |
| `cbc_gem` | **page 1 of every category** | **entire catalogue** | **yes** |
| `pbs` | **top of `allShows`** | **entire catalogue** | **yes** |
| `pluto_tv` | **start=0 of both hubs** | **entire catalogue** | **yes** |
| `rai_play` | **first sitemap entry** | **entire catalogue** | **yes** |

**Verdict:** 5 of 9 catalogue sources resume correctly. The 4 that do not are exactly
the 4 in F1.

---

## 5. Preventing incomplete scans from being marked completed

### 5.1 Per-source `SyncRun` — **PASS**

`_persist_locked` creates a `SyncRun` per `(provider, country)` group with
`mode=result.sync_mode`, and only a successful authoritative full snapshot may
deactivate (`refresh.py:646-651`, `:763-769`). Every catalogue source declares
`supports_authoritative_snapshots=False`, so `may_deactivate` is always `False` — a
truncated or failed run deactivates nothing. Pinned by
`test_phase_e_contract.py:133 test_catalogue_members_refuse_to_be_authoritative_or_incremental`.

### 5.2 `AvailabilityRefreshSource` rows — **N/A / Not proven**

`AvailabilityRefreshSource` (`models.py:1015-1085`) is the per-source progress table
with a `completed` status. **The catalogue path never writes one.** Grep for
`AvailabilityRefreshSource` in `bulk_refresh.py` returns nothing; the catalogue runner
writes only the legacy `RefreshRun(service=slug)` audit row (`bulk_refresh.py:982`,
`:1012-1038`). So the "is this source's catalogue complete?" question has no row that
answers it — only the `RefreshRun.error` field and the `SyncCursor` value.

> **Not proven:** that an incomplete catalogue scan is reported anywhere as
> incomplete at the source level. For the 5 checkpointed sources the `SyncCursor`
> row is the only progress artifact; for the other 4 there is none.

### 5.3 "Partial crawl is not marked complete" — **PARTIAL**

The one test with this name, `test_abc_iview.py:613`, asserts only:
`self.assertFalse(result.deactivated)` and `self.assertTrue(SyncCursor.objects.exists())`.
It proves the crawl did not deactivate anything and left a checkpoint. It does **not**
assert any status distinguishes partial from complete — because no such status exists
(F4).

### 5.4 Marker-level completion — **PASS with a caveat**

`process_pending_catalogue_refresh` (`bulk_refresh.py:1142-1199`) marks the marker
`DONE` when no source reported `errors`. Since a truncated-but-checkpointed source
reports `succeeded` (not `errors`), the marker reads `DONE` — which is accurate for
*"every source was visited"* but not for *"every catalogue was fully walked"*. The
caveat is F4.

---

## 6. Pagination errors, throttling and quota exhaustion

### 6.1 Pagination errors

| source | repeated-page guard | transport mid-walk | page cap |
|---|---|---|---|
| `tubi` | n/a (URL keys) | raises | `max_contents` |
| `prime_video` | index pagination | raises | `max_contents` |
| `abc_iview` | slug dedupe | raises | `MAX_SERIES` |
| `bbc_iplayer` | pid dedupe + `totalPages` | raises | `MAX_PAGES_PER_SLICE` |
| `justwatch` | offset vs `CATALOGUE_MAX_OFFSET` | raises, 3 attempts | `--pages` |
| `cbc_gem` | `cbc_gem_repeated_page` (`:248-257`) | raises | `MAX_PAGES_PER_CATEGORY` |
| `pbs` | `pbs_repeated_cursor` (`:339-345`) | raises | `MAX_PAGES` |
| `pluto_tv` | `pluto_tv_repeated_page` (`:403-408`) | raises | `MAX_PAGES` |
| `rai_play` | n/a | raises | `max_contents` |

**PASS.** Every walk terminates on a hard cap plus a repeated-page signature guard,
so a drifting upstream window cannot loop. Pinned by `test_pbs.py:121`,
`test_pluto_tv.py:594`, and the `tubi` sitemap tests.

### 6.2 Throttling

Handled at two levels:

1. **Per-source delay** — every collector that makes serial detail requests sleeps
   (`DEFAULT_DELAY` 0.1–0.2 s: `abc_iview.py:121`, `bbc_iplayer.py:138`,
   `prime_video.py:186`, `rai_play.py:183`, `tubi.py:176`).
2. **Bounded retry** — Tubi retries a sitemap twice with linear backoff before failing
   the run (`test_tubi.py:245`); JustWatch retries transport 3× and clamps page size on
   a `page_too_large` GraphQL rejection (`justwatch.py:410-422`).

**PASS** for the sources that can be throttled server-side. The four uncheckpointed
sources have **no retry at all** (`cbc_gem.py:170-181`, `pbs.py:193`,
`pluto_tv.py:263-308`, `rai_play.py:256-270`) — a single 429/500 fails the whole run,
which combined with F1 means one transient response re-walks the catalogue.

### 6.3 Quota exhaustion

**Catalogue sources spend no API quota by design** — `bulk_refresh.py:1069-1073`
states "no staleness selection and no API budget", and `refresh.py:241-243` repeats it.
`process_pending_catalogue_refresh` runs "regardless of the daily rate limit"
(`bulk_refresh.py:1156`). The 100-request/day cap applies to title-targeted refreshes
only.

**Watchmode (title-only) is the one quota-bearing source**, and it is handled: a 429 is
classified as a budget answer, not a transient fault (`watchmode_client.py:308-319`),
and `SourceQuotaExceeded` distinguishes `plan_quota` (terminal) / `local_budget`
(terminal) / `rate_limit` (retryable) (`base.py:72-135`).

**Verdict: PASS** for quota. **No finding.** The one nuance: because catalogue walks
are unbounded in *requests* (only bounded in *pages*), a source that is throttled
rather than quota-blocked can spend a great deal of wall-clock without touching the
API budget — that is F1's cost argument, not a quota violation.

---

## 7. Test coverage: crash/restart, duplicate-entry, end-of-catalogue

Measured by enumerating `def test` across `backend/availability/tests/` and
`backend/tracker/tests/` and classifying by name and body.

### 7.1 Crash/restart

| source | crash-gap test | restart test | status |
|---|---|---|---|
| `abc_iview` | ✅ `test_abc_iview.py:582` (injects `RuntimeError` after commit, before cursor write) | ✅ `:418` missing checkpoint restarts | **PASS** |
| `bbc_iplayer` | ❌ **absent** | ✅ `:609` unreadable/stale cursor restarts safely | **PARTIAL** |
| `prime_video` | ❌ **absent** | ✅ `:825` bounded batch resumes without rediscovery | **PARTIAL** |
| `tubi` | ❌ **absent** (has retry tests at `:245`) | ✅ `:361` removed checkpoint URL restarts only that sitemap; `:386` version reset | **PARTIAL** |
| `justwatch` | ✅ coordinator restores snapshot on failure (`justwatch_sync.py:564-571`) | ✅ `test_justwatch_partition_cursor.py:342` | **PASS** |
| `cbc_gem` / `pbs` / `pluto_tv` / `rai_play` | ❌ | ❌ | **FAIL** (no checkpoint to crash into) |

The crash-gap test exists for exactly **one** of five checkpointed sources. The
behaviour is implemented uniformly in `refresh.py:304-328`, but only ABC iview proves
it. **PARTIAL overall.**

### 7.2 Duplicate-entry

| source | test | status |
|---|---|---|
| `abc_iview` | ✅ `test_abc_iview.py:368` "resumption loses nothing and duplicates nothing" | **PASS** |
| `bbc_iplayer` | ✅ `test_bbc_iplayer.py:565` "resumption sums to the full catalogue without duplicates" | **PASS** |
| `prime_video` | ✅ `test_prime_video.py:843` "resumption sums to the full window without duplicates" | **PASS** |
| `tubi` | ✅ `test_tubi.py:159` duplicate sitemap entry fetched once | **PASS** |
| `justwatch` | ✅ `test_justwatch_partition_cursor.py:542` "a second visit must continue, not restart, and not duplicate" | **PASS** |
| `cbc_gem` / `pbs` / `pluto_tv` / `rai_play` | ❌ no resume → no duplicate-on-resume test possible | **FAIL** |

Note the semantic: these tests prove re-reading a window is **idempotent**, not that
entries are never re-fetched. That is the right property (upsert on stable identity),
and it is why F1's "restart from page one" is wasteful rather than corrupting.
**PASS for the 5 checkpointed sources.**

### 7.3 End-of-catalogue

| source | test | status |
|---|---|---|
| `abc_iview` | ✅ `test_abc_iview.py:425` "an empty final batch completes the walk" | **PASS** |
| `bbc_iplayer` | ✅ `test_bbc_iplayer.py:618` "a completed walk returns no cursor" | **PASS** |
| `prime_video` | ✅ `test_prime_video.py:906` "the final window batch completes with no cursor" | **PASS** |
| `tubi` | ✅ `test_tubi.py:321` a batch that observes nothing still advances; `:357-358` cycle rolls | **PASS** |
| `justwatch` | ✅ `test_justwatch_partition_cursor.py:187` exhausted partition does not erase others | **PASS** |
| `cbc_gem` | ✅ terminates on `totalPages` (`cbc_gem.py:275`) but no end-of-catalogue test | **PARTIAL** |
| `pbs` | ✅ `hasNextPage=False` terminates (`pbs.py:333`) but no end-of-catalogue test | **PARTIAL** |
| `pluto_tv` | ✅ `hasMore=False` terminates (`pluto_tv.py:412`) but no end-of-catalogue test | **PARTIAL** |
| `rai_play` | ✅ list exhaustion terminates (`rai_play.py:342`) but no end-of-catalogue test | **PARTIAL** |

**PASS for the checkpointed five; PARTIAL for the four** (termination is implemented
and guarded by repeated-page checks, but no test asserts the end-of-catalogue
transition).

### 7.4 Test-coverage summary

| category | abc_iview | bbc_iplayer | prime_video | tubi | justwatch | cbc_gem | pbs | pluto_tv | rai_play |
|---|---|---|---|---|---|---|---|---|---|
| crash/restart | PASS | PARTIAL | PARTIAL | PARTIAL | PASS | FAIL | FAIL | FAIL | FAIL |
| duplicate-entry | PASS | PASS | PASS | PASS | PASS | FAIL | FAIL | FAIL | FAIL |
| end-of-catalogue | PASS | PASS | PASS | PASS | PASS | PARTIAL | PARTIAL | PARTIAL | PARTIAL |

---

## 8. What implementing the missing checkpoints must satisfy

Recorded so the implementation pass is not a free-form change. Seven constraints, all
derived from the existing working collectors.

1. **Identity, not offset.** Key the checkpoint on a stable per-entry identity
   (CBC slug `cbc_gem.py:260`, PBS slug `pbs.py:325`, Pluto item id `pluto_tv.py:403`,
   RaiPlay URL `rai_play.py:343`). Offsets shift under head insertion; URLs do not.
   Rationale already documented at `tubi.py:814-817`.

2. **Multi-part keys for multi-part walks.** CBC walks 3 disjoint categories
   (`cbc_gem.py:97`) and Pluto walks 2 hubs (`pluto_tv.py:348-373`). A single cursor
   value must carry **per-part progress** or one part's completion resets the others —
   the exact bug JustWatch's map solves (`justwatch_sync.py:21-45`).

3. **Emit only while truncated; clear on exhaustion.** `abc_iview.py:348-351` and
   `prime_video.py:422-425` return `cursor` only when work remains, so
   `refresh.py:329-349` can drop a leftover row instead of resuming a finished walk.

4. **Write after the offer transaction.** Already enforced by `refresh.py:304-328`;
   the new sources get it for free by returning a cursor. Do not add a separate
   write path.

5. **Bound the payload.** `prime_video.py:193-201` records that a full pending window
   encodes to ~140 KiB and that `SyncCursor.cursor` is an unbounded `TextField`.
   CBC's pending window is three categories of slugs; a per-part list is required, and
   the total must stay sane.

6. **Version the payload.** All four working collectors carry a `v` field
   (`abc_iview.py:567`, `bbc_iplayer.py:807`, `tubi.py:784`, `prime_video.py:1140`)
   and reset to the top on mismatch. A traversal-algorithm change must be a controlled
   reset, not a crash or a silent skip.

7. **Change the four asserting tests.** `test_cbc_gem_source.py:85-92`,
   `test_pbs.py:158-162`, `test_pluto_tv.py:136-143`, `test_rai_play.py:156-164` must be
   rewritten to the new contract (F2). Leaving them in place is not an option.

---

## 9. Acceptance verdict

> **Acceptance: every applicable catalogue source can recover from interruption
> without silently skipping entries, repeating unbounded work or incorrectly marking
> a scan complete.**

| # | acceptance clause | verdict | evidence |
|---|---|---|---|
| 1 | Every catalogue source identified | **PASS** | 9 catalogue sources enumerated (§2.1); 4 title-only correctly excluded |
| 2 | Source-by-source capability matrix built | **PASS** | §2.1–§2.4, with per-row citations |
| 3 | Cursor persistence checked | **PARTIAL** | 5/9 persist (§2.1); 4 return `cursor=None` (F1) |
| 4 | Page boundaries checked | **PASS** | §2.3 — every walk has a hard cap + repeated-page guard |
| 5 | Resumability checked | **PARTIAL** | 5/9 resume (§4); 4 restart from page one (F1, F3) |
| 6 | Restart does not restart from page one unnecessarily | **FAIL** | RaiPlay ≈840 reqs/run re-read in full; CBC/PBS/Pluto same (F1) |
| 7 | Incomplete scans not marked completed | **PARTIAL** | `SyncRun` deactivation gate **PASS** (§5.1); no source-level status distinguishes truncated from complete (F4, §5.2) |
| 8 | Pagination errors handled explicitly | **PASS** | §6.1 — guard + cap + loud failure on every source |
| 9 | Throttling handled explicitly | **PARTIAL** | 5/9 have delay+retry (§6.2); 4 have **no retry at all**, so one 429/500 re-walks the catalogue |
| 10 | Quota exhaustion handled explicitly | **PASS** | Catalogue sources are budget-free by design (§6.3); Watchmode 429 classified |
| 11 | Crash/restart tests added | **PARTIAL** | 1/5 checkpointed sources has a crash-gap test (§7.1) |
| 12 | Duplicate-entry tests added | **PASS** | 5/5 checkpointed sources (§7.2) |
| 13 | End-of-catalogue tests added | **PARTIAL** | 5/5 checkpointed; 4 uncheckpointed have termination but no test (§7.3) |
| 14 | Missing checkpoints **not** implemented | **PASS** | No source file modified; only this doc was written |
| — | **Overall** | **FAIL** | F1 is Critical; acceptance clauses 6 and 9 fail outright |

**Overall: FAIL.** Four of nine catalogue sources (`cbc_gem`, `pbs`, `pluto_tv`,
`rai_play`) have no durable checkpoint, so an interruption re-walks the entire
catalogue from page one and the run is reported `succeeded`. Implementing the missing
checkpoints — per §8 — is required before this acceptance can pass.

---

## 10. Evidence appendix

### 10.1 Live stack probe results

Run against the running `docker-compose` stack (backend container), reading
`source_registry` directly:

```
abc_iview    catalogue=true  providers=[abc_iview]  countries=[AU]   batch_size=250   MAX_SERIES=3000
bbc_iplayer  catalogue=true  providers=[bbc_iplayer] countries=[GB]   batch_size=60    MAX_PAGES_PER_SLICE=60
cbc_gem      catalogue=true  providers=[cbc_gem]    countries=[CA]   page_size=100   MAX_PAGES_PER_CATEGORY=500
justwatch    catalogue=true  providers=[]           countries=[]     page_size=100   (coordinator)
pbs          catalogue=true  providers=[pbs]        countries=[US]   BATCH_SIZE=50   MAX_PAGES=100
pluto_tv     catalogue=true  providers=[pluto_tv]   countries=[32]   PAGE_ROWS=24    MAX_PAGES=100
prime_video  catalogue=true  providers=[prime]      countries=[NL]   max_contents=500 batch_size=100
rai_play     catalogue=true  providers=[raiplay]    countries=[IT]   max_contents=400
tubi         catalogue=true  providers=[tubi]       countries=[US]   max_contents=500 DEFAULT_MAX_PRIORITY=300
```

Only `justwatch` and `tubi` declare `supports_priority_hints`/`pages`; no catalogue
source declares `pages` except JustWatch.

### 10.2 Measured `last_successful_sync` behaviour

```
STEP1 cursor row exists: checkpoint-9
STEP2 last_successful_sync set: False
```

A hand-written `SyncCursor` row is never stamped by anything except
`CursorRepository.set` — confirming §1.3 / F5.

### 10.3 Baseline test run

```
docker compose … exec backend python manage.py test \
  availability.tests.test_cbc_gem_source availability.tests.test_pbs \
  availability.tests.test_pluto_tv availability.tests.test_rai_play \
  availability.tests.test_prime_video availability.tests.test_abc_iview \
  availability.tests.test_bbc_iplayer availability.tests.test_tubi -v 1

Ran 433 tests in 9.877s
OK
```

Green baseline across all eight catalogue collectors. No test was modified.

### 10.4 Files read but not modified

`backend/availability/sources/{base,registry,__init__,abc_iview,bbc_iplayer,cbc_gem,justwatch,justwatch_client,pbs,pluto_tv,prime_video,rai_play,tubi}.py`,
`backend/availability/services/refresh.py`,
`backend/availability/persistence/repositories.py`,
`backend/availability/models.py`,
`backend/tracker/services/{bulk_refresh,justwatch_sync}.py`,
`backend/tracker/models.py`, `backend/tracker/views.py`,
`backend/tracker/management/commands/refresh_justwatch_catalogue.py`,
plus the eight collector test modules and their fixtures.

---

# Part II — Implementation results (Phase 2)

Everything above this line is the original audit, kept as written. It was
audit-only: no code changed. This part records what was then implemented, what
the implementation proved, and which of the original findings closed.

## 11. Status of the original findings

| # | Finding | Severity | Status now |
|---|---|---|---|
| F1 | Four catalogue sources have no checkpoint | CRITICAL | **CLOSED** — all four checkpoint (`cbc_gem`, `pbs`, `pluto_tv`, `rai_play`) |
| F2 | The absence of a cursor is asserted by tests | HIGH | **CLOSED** — the four `assertIsNone(result.cursor)` assertions replaced by resumability assertions |
| F3 | `rai_play` discards its whole walk on one failure | HIGH | **CLOSED** — bounded batches + durable checkpoint; measured below |
| F4 | Nothing distinguishes "finished" from "died halfway" | MEDIUM | **CLOSED** — `progress_at` vs `last_successful_sync`, plus `is_stalled` |
| F5 | `last_successful_sync` written and never read | MEDIUM | **CLOSED** — now meaningful and read by `is_stalled` |
| F6 | `supports_incremental_sync` is the opposite of reality | LOW | **CLOSED** — `supports_resumable_catalogue_sync` set on all nine |
| F7 | JustWatch's cursor reset re-walks by design | LOW (accepted) | **Unchanged, as accepted** |

## 12. The defect the audit did not predict

The audit identified four sources with *no* checkpoint. It did not identify that
**`pluto_tv` could not resume even once a checkpoint existed**, because the
failure was in the service, not the source — and invisible at the walk layer.

Measured before any fix, through the production service path:

```
RUN1 requests: [movies@0, shows@0]
RUN2 requests: [movies@0, shows@0]      <- identical
stored cursor after RUN2: unchanged     <- no forward progress
```

Pluto reads its market off the egress region, so `refresh_catalogue` could not
resolve a `Country` row before the fetch and passed `cursor=None`. The
declared-country logic then re-read the cursor *after* the fetch — far too late
to influence it. The source checkpointed perfectly in isolation; the service
never fed it a checkpoint. Persistence worked, resume never did.

Fixed by `SourceCapabilities.country_from_result`: for a source whose market is
only knowable from the response, the service hands over **every enabled market's
checkpoint keyed by country code**, and the source selects its own market's entry
once it has observed the region. Passing a single row would risk resuming one
market with another's progress; passing nothing was the bug.

After the fix:

```
RUN2 requests: [movies@0]               <- completed shows hub skipped
stored cursor advanced to start=24
```

The residual single overlapping request is Pluto's offset+anchor design: a
resume re-reads the page holding its anchor, and that re-read is *how* it proves
the declared total has not shifted underneath it. It is bounded at one page per
batch, and the tests assert that bound rather than zero.

**This is why "the cursor is not None" was never sufficient evidence.** Three of
the four new sources had a second, independent defect behind the first.

## 13. Measured resume behaviour

### 13.1 RaiPlay at production scale

Against a synthetic 400-programme catalogue (the real catalogue is ~2,239
programmes, bounded to 400 per run):

| Quantity | Value |
|---|---|
| One full bounded walk | **799** detail requests (documented ~840) |
| Interruption at 90% — run A | 360 requests |
| Resume — run B | **45** requests |
| Total for both | 405 requests for a 400-programme catalogue |
| Re-reads of already-walked entries | **0** |
| Requests saved vs. restarting from scratch | **360** |

Resume after an already-complete window: **5 requests, overlap 0** — previously
400, because the walk restarted from page one.

### 13.2 Per-source request accounting

Bounded walk vs. one unbounded pass, measured across the four new sources:

| Source | One pass | Bounded walk | Extra |
|---|---|---|---|
| `cbc_gem` | 5 | 6 | +1 |
| `pluto_tv` | 3 | 4 | +1 |
| `pbs` | 5 | 5 | 0 |
| `rai_play` | 5 | 5 | 0 |

The `+1` is one re-read per batch (Pluto's anchor page; a CBC card listed in two
of its three category indices is inspected once per index). The tests assert
`resumed ≤ one_pass + batches`, which is the honest bound: it rules out
re-reading the walked window while permitting the documented per-batch re-read.

## 14. Bugs found by probing, not by reading

Each of these was a non-termination or silent-skip, and none would have surfaced
from green tests alone. All were found by driving the real code and measuring.

| # | Source | Defect | Consequence before fix |
|---|---|---|---|
| 1 | `cbc_gem` | Clearing a finished category's anchor to `""` | Walk oscillated forever (0→1→2→0) |
| 2 | `pluto_tv` | Anchor was the last item walked, offset pointed *past* it | Anchor unreachable; skipped forward indefinitely |
| 3 | `pluto_tv` | `consumed == len(page)` read as "anchor absent" | Anchor at a page's end was never matched |
| 4 | `pluto_tv` | No new anchor when a batch contributed nothing | Fell back to `""`, restarted the hub |
| 5 | `pluto_tv` | `done` set initialised empty instead of from the checkpoint | Finished hubs re-walked every batch |
| 6 | `pluto_tv` | Unresolvable anchor marked the hub `done` while observing nothing | **Silently skipped entries** |
| 7 | `pluto_tv` (service) | Market unknowable pre-fetch → `cursor=None` | Never resumed at all (§12) |

Defect 6 is the one the acceptance clause names directly: an incomplete scan
marked complete. It now restarts instead, which repeats work but cannot skip.

## 15. Capability and observability decisions

### 15.1 `supports_resumable_catalogue_sync`

**Decision: keep it, and set it on all nine.** The audit left this open
(§8 preferred derivation over a new flag). Derivation is not available: the
walk-layer contract is `cursor=` in / `cursor=` out, and a source can honour
that while the *service* never supplies a checkpoint — exactly the Pluto case.
The flag states a property of the source's traversal, and the distinction from
`supports_incremental_sync` (offer deltas, not traversal) is documented on the
capability itself.

Measured before the fix, the two flags disagreed completely:

| Source | checkpoints | `supports_incremental_sync` | `supports_resumable_catalogue_sync` (before) |
|---|---|---|---|
| `tubi` | yes | False | **False** |
| `prime_video` | yes | False | **False** |
| `abc_iview` | yes | False | **False** |
| `bbc_iplayer` | yes | False | **False** |
| `justwatch` | yes | True | **False** |
| `cbc_gem` | yes | False | True |
| `pbs` | yes | False | True |
| `pluto_tv` | yes | False | True |
| `rai_play` | yes | False | True |

A reader would have concluded the first five cannot resume. They can. The flag
is now `True` for all nine.

### 15.2 `last_successful_sync` and `progress_at`

`last_successful_sync` was write-only **and wrong**: the repository stamped it on
*every* checkpoint write, so a partial run looked like a completed traversal.

Now:

- **`progress_at`** (new, nullable) — last forward progress. Liveness.
- **`last_successful_sync`** — last completed traversal. Completion.
- `set()` stamps `progress_at` **only when the stored value actually changes**.
  A replayed batch that re-reads the same window persists the same value and is
  not recorded as progress — otherwise a walk wedged re-reading one page would
  keep resetting its own liveness clock and never be reported stalled.
- `set()` never touches `last_successful_sync`. Only `complete()` may claim
  completion, so a partial or failed run cannot look complete.
- `is_stalled` reads `progress_at` against `freshness.STALL_AFTER` (**48 h**,
  more than twice the daily cron cadence, so a healthy walk is never flagged
  between ticks).

**Where completion is observable, stated honestly.** A walk whose checkpoint row
is deleted on completion cannot carry a per-row timestamp, so that path does not
pretend to stamp one. Per-source completion is recorded on the run audit row
(`RefreshRun(service=slug, error="", finished_at=…)`), which `stale_catalogue_sources`
already reads. `last_successful_sync` therefore means "last completed traversal
of a walk whose checkpoint row still exists" — the cycling and multi-partition
case. This is a deliberate, documented limitation rather than a faked timestamp.

## 16. Crash/restart coverage across all nine sources

| Source | Walk-layer resume | Persistence-layer (row survives process) | Failure retry | Terminal state |
|---|---|---|---|---|
| `cbc_gem` | ✅ | ✅ | ✅ | ✅ |
| `pbs` | ✅ | ✅ | ✅ | ✅ |
| `pluto_tv` | ✅ | ✅ | ✅ | ✅ |
| `rai_play` | ✅ | ✅ | ✅ | ✅ |
| `tubi` | ✅ | ✅ | ✅ | ✅ (cycling — see below) |
| `prime_video` | ✅ | ✅ | ✅ | ✅ |
| `bbc_iplayer` | ✅ | ✅ | ✅ | ✅ |
| `abc_iview` | ✅ | ✅ (pre-existing) | ✅ | ✅ |
| `justwatch` | ✅ | ✅ (pre-existing) | ✅ | ✅ (reset-on-exhaustion, accepted F7) |

Two genuine semantic findings from extending this coverage:

1. **`tubi` never returns `cursor=None`.** Its traversal is a *cycle* over a
   sitemap set that never ends, so an exhausted cycle persists a **fresh** one.
   "A completed walk clears the row" is false for it; the correct terminal state
   is a *reset* checkpoint. The tests now assert that per-source, via a declared
   `completion_clears_checkpoint`.
2. **`bbc_iplayer`'s A-Z index is legitimately re-read** when a batch resumes
   inside a letter, so only the per-programme detail pages count as repeat work.

## 17. The migration chain defect

Adding one field to `availability.SyncCursor` broke **every fresh database**:

```
django.db.utils.ProgrammingError: column availability_synccursor.progress_at does not exist
```

Cause, and it is not the new migration: `tracker/migrations/0033_merge_duplicate_providers.py`
is a *data* migration that imported **live** models — `ContentMapping`, `Offer`,
`ProviderContent`, `SyncCursor`, `SyncRun`, `Region`, `ScrapedContentMapping` —
and queried them. A live queryset SELECTs every column the live model declares,
including columns added by migrations that have not run yet at that point in the
chain. The file already documents this exact hazard and had fixed `Subscription`
and `ExternalProviderMapping`; the rest were left live, so the chain was
unextendable for those models.

All are now resolved through `apps.get_model(...)`, with live imports confined to
the no-`apps` fallback that the function's own tests use. `_provider_ref` was
added because a historical model cannot be filtered by a live `Provider`
instance (`Cannot query "RaiPlay (rai-play)": Must be "Provider" instance`).

This was pre-existing and latent: it would have blocked *any* future field
addition to those models, not just this one.

## 18. Migration status

| Item | State |
|---|---|
| `availability/0011_synccursor_progress_at` | Adds `progress_at` (nullable, additive) + backfill |
| `makemigrations --check --dry-run` | **No changes detected** |
| Destructive operations | **None** — no column dropped, no row deleted |
| Reversible | Yes; reverse is a no-op (dropping the column discards backfilled values) |
| Existing rows | Backfilled from `updated_at`, which for a checkpoint row already *is* the last write to that row — the real last progress |

`last_successful_sync` is deliberately **not** backfilled: its existing values
were stamped by the buggy path and are not trustworthy completion times.
Rewriting them would invent history; they converge as each walk completes under
the new rules.

## 19. Revised acceptance matrix

Original verdict on the acceptance clause: **FAIL**. Revised:

| Clause | Verdict | Evidence |
|---|---|---|
| Every applicable source has a durable checkpoint | **PASS** | 9/9 declare `supports_resumable_catalogue_sync`; 8 verified end-to-end through the service, `justwatch` via its partition coordinator |
| Recover from interruption | **PASS** | Persistence-layer tests for 8 sources; RaiPlay measured at 400-programme scale (§13.1) |
| Without silently skipping entries | **PASS** | Pluto defect 6 fixed (restart, not skip); `rai_play`/`tubi` failure policy raises rather than skips |
| Without repeating unbounded work | **PASS** | Resume costs ≤ one pass + one re-read per batch (§13.2); RaiPlay saved 360 requests on a 90% interruption |
| Without incorrectly marking a scan complete | **PASS** | `set()` cannot claim completion; `complete()` only on genuine exhaustion; `supports_authoritative_snapshots=False` for all nine, so a partial run cannot deactivate offers |
| Cursor persistence | **PASS** | Keyed `(provider, source, country)`; country scope-checked (§2 of the task) |
| Page boundaries / resumability | **PASS** | Versioned codec; malformed/stale/incompatible checkpoints restart rather than mis-resume |
| Pagination errors / throttling / quota | **PASS** | Traversal-page failure raises; bounded batches cap upstream work per run |

## 20. Remaining risks

1. **`justwatch` persistence is verified through its own partition-coordinator
   tests, not through the new generic suite.** Its cursor row deliberately holds
   *other* partitions' offsets, so the generic "row is owned by one walk"
   assumption does not hold for it. Its pre-existing coverage was left in place.
2. **`last_successful_sync` is not durable for sources whose row is deleted on
   completion** (§15.2). Completion for those lives on `RefreshRun`. If a
   per-source completion timestamp is later wanted *on the cursor*, the design
   needs a separate durable row rather than a field on a deleted one.
3. **Pluto's residual anchor-page re-read is unbounded in *count* across a very
   long walk** — one page per batch. At the default (one hub per batch) this is
   negligible, but a very small `batch_pages` would multiply it.
4. **`tracker.0033` was repaired, but other data migrations were not audited**
   for the same live-model pattern. A repo-wide sweep is warranted.
5. **No live upstream validation was performed.** All evidence is fixture- and
   probe-based, per the constraint against spending paid quota. The real
   catalogues' pagination behaviour may differ in ways fixtures do not model.

**Verdict: PASS on the acceptance clause**, with risks 1–5 above recorded. The
original audit's FAIL was correct at the time; it is superseded by measurement,
not by assertion.
