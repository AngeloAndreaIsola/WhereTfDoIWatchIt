# Issue #151 — API Anything offline parity spike

**Status:** complete (offline). **Recommendation:** NO-GO on refactoring the six
collectors; KEEP all six; CONDITIONAL HYBRID only for a new eligible source after
#124. Details below.

- **Issue:** #151 (Wave 0, API Anything offline parity spike)
- **Base commit probed:** `f659f97905d20bd836f82a67f38e1fe031c42cba` (`origin/master`)
- **Runtime:** Django `5.2.19`, Python `3.12`, `--network none`
- **Upstream `goodnight000/api-anything`:** **not fetched, not pinned** — every
  API Anything capability claim below is **CLAIMED/UNVERIFIED** (derived from the
  #149/#151 text only).
- **Changes:** docs + fixtures only. No production code, no DB writes, no live
  requests, no PR.

## 1. What was asked vs what was measured

API Anything is a Node-based collector framework. The spike question is whether
its documented capabilities are a *strict superset* of what the six current
Python collectors already guarantee, such that adopting it would be a pure win.

Because the upstream is unpinned and unexecuted, this spike cannot validate API
Anything itself. It instead measures the **current-master collectors** against the
same 17 contract dimensions, so any future adoption has a concrete baseline to
match. "Parity" here means: *the current code already satisfies the dimension, so
API Anything must at minimum not regress it.*

## 2. Method (reproducible, offline)

Every `fetch_url` was replaced with a retained/synthetic fixture; the database was
an in-memory SQLite that was never queried. Verified counters from the run:
`network_requests=0`, `database_rows_written=0`, `offers_created=0`.

```
docker run --rm --network none \
  -v "$PWD/backend:/app" -w /app \
  -v "$PWD/docs/spikes/151-api-anything-parity:/probe" \
  -e PYTHONPATH=/probe:/app -e USE_SQLITE=1 -e DJANGO_DEBUG=1 \
  -e DJANGO_SECRET_KEY=dev-insecure-secret \
  -e DJANGO_SETTINGS_MODULE=probe_settings \
  --entrypoint python wheretfdoiwatchit-backend:latest /probe/probe.py
```

Raw output: `probe.log`; structured output: `probe_results.json`.

## 3. Gate check — the decisive finding

All six unofficial providers are `UNVERIFIED` in the discovery policy, so the
legacy execution path fail-closes before any fetch. Only the two reviewed
providers pass.

| Provider | `require_legacy_execution` | Permission | Provenance blocked |
|---|---|---|---|
| kisskh | `source_permission_unverified` | UNVERIFIED | no |
| hydrahd | `source_permission_unverified` | UNVERIFIED | no |
| animeworld | `source_permission_unverified` | UNVERIFIED | no |
| streamingcommunity | `source_permission_unverified` | UNVERIFIED | **yes** |
| cineblog01 | `source_permission_unverified` | UNVERIFIED | **yes** |
| altadefinizione | `source_permission_unverified` | UNVERIFIED | **yes** |
| raiplay (control) | `ALLOWED` | — | — |
| nhk_world (control) | `ALLOWED` | — | — |

Implication: **no collector — current or API Anything — may fetch any of the six
today.** #124 is a hard prerequisite for *any* live use, independent of which
framework parses the pages.

## 4. Parity matrix A — collector parse contract (measured)

`PASS` = current code demonstrates it on the fixture; `PARTIAL` = works but with a
noted limitation; `GAP` = missing/incorrect.

| Dimension | KissKH | HydraHD | AnimeWorld | StreamingCommunity | CineBlog01 | AltaDefinizione |
|---|---|---|---|---|---|---|
| Native ID | PASS (`id`) | **GAP** (URL only) | PASS RSS (`guid`) / GAP browse | PASS (`title_id`) | PASS (`title_id`) | PASS (`title_id`) |
| Provider alias/domain | PASS | PASS | PASS | PASS | PASS | PASS |
| Metadata-only (no offer) | PASS | PASS | PASS | PASS | PASS | PASS |
| Pagination bound | PASS (`pageSize`/`totalCount`) | PARTIAL (fixed 10) | PARTIAL (`range(1,3)`) | PARTIAL (4 fixed paths) | PASS (5/3 sections) | PARTIAL (4 fixed paths) |
| Dedupe | PASS `(id,type)` | PASS URL | **GAP** (none) | PASS `title_id` | PASS URL | PASS URL |
| Typed identity | PASS (explicit `type`) | PASS (URL path) | PARTIAL (browse cross-types) | PASS (`data-kind`) | PASS (section) | PARTIAL (href overrides kind) |
| Identity uncertainty | PASS (`pending`) | **GAP** | **GAP** | **GAP** | **GAP** | **GAP** |

Measured dedupe effect (returned / unique URLs / unique native IDs):

| Provider | returned | unique URLs | unique native IDs | fetch calls | raw parsed | note |
|---|---:|---:|---:|---:|---:|---|
| kisskh | 2 | 2 | 2 | 3 | 6 | 2 classified + 2 pending |
| hydrahd | 2 | 2 | 0 | 20 | 40 | URL identity only |
| animeworld | **8** | **2** | 0 | 4 | 8 | **no dedupe; same URL as movie+series** |
| streamingcommunity | 3 | 3 | 3 | 4 | 12 | dedupe by `title_id` |
| cineblog01 | 3 | 3 | 3 | 8 | 24 | dedupe by URL |
| altadefinizione | 2 | 2 | 2 | 4 | 8 | `tv-show` href → series |

Key parse findings:

1. **AnimeWorld browse path has no dedupe and cross-types.** The same URL is
   emitted once as `movie` and once as `series` (8 returned, 2 unique). RSS path
   is typed correctly and uses `guid`. This is the only outright correctness bug
   found; it is *not* fixed here (no production changes in this spike).
2. **HydraHD carries no native ID**, so identity is URL-only and can fragment on
   tracking-param variants.
3. **KissKH correctly quarantines ambiguous rows** (`Pending Anime`, `Missing
   Type` → pending) and promotes a pending row's type via detail (`TVSeries`).
4. **AltaDefinizione derives type from the href token**, which can override
   `data-kind` (`/tv-show/...` → `series`); acceptable but worth documenting.

## 5. Parity matrix B — shared discovery-engine contract

These are owned by `catalogue_discovery.py` / `discovery_policy.py` /
`discovery_http.py`, not by any single parser.

| Dimension | Status | Evidence |
|---|---|---|
| Checkpoint / resume | PASS | `catalogue_discovery.py` + `test_catalogue_discovery.py` |
| Interruption → PARTIAL | PASS | discovery tests (PARTIAL state) |
| Replay / idempotency | PASS | replay tests; no rewrite on unchanged |
| Request budget / rate | PASS | `discovery_http.py`: `limit=min(budget,40)`, `interval>=2.0` |
| Error classification / fail-closed | PASS | distinct 403/429/404/robots/DNS states; tests |
| Permission + robots grant | PASS | probe: all six `source_permission_unverified` |
| Self-healing drift scope | N/A today | no recipe/self-healing layer exists to widen scope |
| Test determinism | PASS | this probe: fixture-only, fake transport, in-memory DB |
| Credential handling | PASS | no credential/cookie/auth forwarding |

## 6. Go / no-go

**NO-GO — do not refactor the six collectors onto API Anything now.**

Reasons:
1. Upstream is **unpinned and unexecuted**; zero evidence it reproduces these
   contracts.
2. **#124 is missing**, so neither framework may legally fetch these sources.
3. API Anything's self-healing recipe risk (silent scope widening) is exactly the
   failure mode the current fail-closed policy prevents.
4. It introduces a **Node runtime** next to a Django stack for no measured gain.
5. Current collectors already pass most parse dimensions; the gaps (AnimeWorld
   dedupe/typing, HydraHD native ID) are small, local Python fixes.

**KEEP all six collectors** and the existing reviewed-discovery path.

**CONDITIONAL HYBRID** — only for a *new* eligible source after #124, and only
with: (a) a pinned, integrity-reviewed upstream commit; (b) an offline fixture POC
matching every dimension in `fixtures/api_anything_contract.json`; (c) a recipe
allowlist that cannot widen host/path/rate/auth/write scope; (d) a documented
runtime-cost justification.

**DO NOT PURSUE** StreamingCommunity, CineBlog01, or AltaDefinizione through API
Anything — all three are provenance-blocked.

## 7. What would change the decision

- A pinned API Anything commit plus an offline POC that reproduces matrix A and B
  without widening scope.
- A reviewed permission grant (#124) for at least one source API Anything would
  actually target.
- Evidence that the Node runtime is already deployed/operated elsewhere.

## 8. Artifacts

```
docs/spikes/151-api-anything-parity/
  REPORT.md                     this file
  README.md                     how to reproduce / artifact index
  probe.py                      offline probe (patches fetch_url)
  probe_settings.py             in-memory SQLite settings
  probe.log                     exact console output
  probe_results.json            structured metrics
  fixtures/
    api_anything_contract.json  17 dimensions + go/no-go criteria
    kisskh_listing.json         typed + pending rows
    kisskh_detail.json          pending → TVSeries promotion
    hydrahd_listing.html        browse page, URL-derived type
    animeworld_rss.xml          guid-typed RSS snapshot
    animeworld_search.html      browse page (no native ID)
    streamingcommunity_listing.html
    cineblog01_listing.html
    altadefinizione_listing.html
```

## 9. Limitations

- Fixtures are retained/synthetic and constant-return; they exercise parser and
  dedupe logic, not real pagination drift or anti-bot behavior.
- API Anything was not executed, so this is a **baseline**, not a head-to-head.
- No live validation of any provider (by design: #124 absent, offline-only).
