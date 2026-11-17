# RaiPlay bounded catalogue completion plan

**Status: NO-GO for execution until the approval gates below are accepted.**

This is an operational plan only.  It does not start a refresh, call RaiPlay,
change application code, change database rows, or alter legacy observations.

**Phase 3.3J review — 2026-10-25 (read-only).**  The request accounting below
was re-derived from the run log of the `2026-10-25` 25-entry resume run rather
than from the code path alone, and three figures in the previous revision were
wrong: the sitemap overhead is **38**, not 37 (1 index + 37 programme sitemap
children, not 36); the catalogue is **2,302** entries, not 2,239; and the
detail cost is a **deterministic 3 requests per entry** at the currently
observed payload shape, not a "2–3" range.  The previous 400-entry envelope of
1,237 is corrected to **1,238**.  No execution was performed in this review.

The `2026-10-24` 400-entry attempt must **not** be treated as the first
window of the new traversal.  The durable RaiPlay checkpoint landed in commit
`2de6ad2` on `2026-10-25`; the historical code accepted `cursor` but always
returned `cursor=None`.  The current database corroborates this: its last
RaiPlay `RefreshRun` started `2026-10-24 20:49:21Z`, processed 400 scanned
entries (`rows_refreshed=1`, `rows_skipped=397`), and there is no RaiPlay
`SyncCursor`.  Thus the absent row is explained by a run that predates the
implementation, not by successful exhaustion or a current coordinator
deleting a completed cursor.

## Verified baseline (read-only audit: 2026-10-25, Phase 3.3J)

| Fact | Result |
| --- | --- |
| Current source | `RaiPlaySource`, `rai_play`, scoped to `raiplay` / `IT`; `supports_resumable_catalogue_sync=True` |
| Current traversal | Sitemap index -> only monthly programme sitemaps -> de-duplicated, interleaved `/programmi/` URLs -> programme page -> video detail/player page where necessary |
| Bound | `DEFAULT_MAX_CONTENTS=400`; no public CLI flag changes it |
| Current canonical observations | 504 `ProviderContent` rows with source `rai_play` |
| Preserved legacy observations | 457 `ProviderContent` rows with source `legacy_scraper`, all currently available |
| Current offers | 2 RaiPlay offers, all current; this is not a proxy for programme coverage because unresolved content intentionally has no offer |
| Cursor | **present** — `SyncCursor` id=17, `v:1`, anchor `…/programmi/osnottaviodantonetraclassicismoeneoclassicismo`, `progress_at 2026-10-25T17:41:12Z` |
| Anchor position | 49 of 2,302 live entries (2,253 remaining) |
| Snapshot semantics | `authoritative=False`, `complete_snapshot=False`, `SyncMode.FULL` |

### Checkpoint state — verified, not modified (Phase 3.3J)

Read directly from the database; **nothing was written**.

| Field | Value |
| --- | --- |
| `SyncCursor` row | id **17** |
| `cursor` | `{"parts":{"programmes":"https://www.raiplay.it/programmi/osnottaviodantonetraclassicismoeneoclassicismo"},"v":1}` |
| `v` | 1 (matches `CURSOR_VERSION`) |
| `progress_at` | `2026-10-25T17:41:12.951304+00:00` |
| `last_successful_sync` | `None` — **expected**: RaiPlay's row is deleted on completion, so it is never stamped (`repositories.py:1149-1171`) |
| `updated_at` | `2026-10-25T17:41:12.953390+00:00` |

The cursor is **nonempty, version-1, and positioned at entry 49**.  The next run
resumes immediately after `osnottavio…` and walks entries 50 onward.  The
previous plan recorded "no `SyncCursor` for `raiplay`" — that was true before
commit `2de6ad2` landed the checkpoint; it is **stale** and the row now exists.

### Measured run evidence (the 113-request figure is from the run log, not derived)

The `2026-10-25` resume run log records **113 HTTP GET requests, all HTTP 200,
zero retries, zero non-RaiPlay hosts**:

| Category | Count |
| --- | ---: |
| Sitemap index | 1 |
| Programme sitemaps | 37 |
| Programme pages | 25 |
| `.json` video requests | 25 |
| `.html` player-page requests | 25 |
| **Total** | **113** |

Application counters from the same log: `entries=2253 contents=25 offers=25
skipped=0`, coordinator `discovered=25 mapped=0 unresolved=25 offers=0
created=0 updated=0 skipped=25 duration_s=27.0`.  Resume logged as
`rai_play_walk_resume after=…/laforzacheunisce remaining=2253`; **no**
`rai_play_cursor_checkpoint_missing` and **no** `rai_play_content_missing`
occurrences.  Every one of the 25 entries took the bare-record branch and
required the `.html` player page.

`discovered=25` produced **zero new `ProviderContent` rows** (504 → 504): all 25
entries were already persisted, so the idempotent upsert advanced `last_seen`
only.  `discovered` is **not** a new-row count — a reporting trap the previous
revision did not flag.

The current source generates a versioned checkpoint containing the last
**inspected programme URL** when, and only when, the window truncates.  On the
next call `AvailabilityRefreshService.refresh_catalogue()` loads the cursor
for `(provider=raiplay, source=rai_play, country=IT)` and passes it to the
source.  The source locates that URL in the freshly read sitemap list and
starts immediately after it.  If the anchor disappeared, it deliberately
starts at entry zero, which may repeat work but cannot skip work.  When the
remaining list is exhausted, the source returns no cursor and the service
deletes the row.  That deletion is therefore the positive current-code signal
for traversal completion; completion time is recorded by the successful
`RefreshRun`, not by a deleted cursor.

## Request and failure budget

Every run issues **38 sitemap requests regardless of window size** (1 index + 37
programme sitemap children), then **2–3 requests per selected programme**.  The
measured run proves the upper branch is the live one: **38 + 3N**.

| Window | Sitemap (fixed) | Detail per entry | Maximum planned request envelope |
| --- | ---: | ---: | ---: |
| 25-entry proof (measured) | 38 | 50–75 | **113** — observed |
| 100-entry intermediate | 38 | 200–300 | 238–338 |
| 200-entry | 38 | 400–600 | 438–638 |
| 400-entry default window | 38 | 800–1,200 | **1,238** |
| Whole 2,302-entry traversal, six default windows | 228 | 4,800–7,200 | 5,028–7,428 |

These are HTTP-call envelopes, not successful observations.  The client has a
30-second timeout and a 0.1-second delay before each live request.  It has no
configured HTTP retry loop, source quota, or catalogue-worker request budget;
a transport/non-200 failure (other than 404/410) raises.  A 404/410 is a
stale entry and is counted as inspected progress.  Budget safety is therefore
the `max_contents` cap plus the delay, not a token-bucket limiter.

### Why 38, not 37 — and why the count moves

`PROGRAMME_SITEMAP_RE` (`rai_play.py:212-214`) matches
`sitemap.raiplay.programmi.MM.YYYY.xml`.  The index grew between revisions: the
previous plan assumed 36 children, the `2026-10-25` run measured **37**.  The
index is regenerated monthly, so **the fixed overhead is not a constant** — it
must be re-measured before every window, and it is not included in the
per-entry figure.  The measured run log confirms `rai_play_catalogue_started
sitemaps=37`.

### Why the detail cost is 3, not "2–3"

`_video_state` (`rai_play.py:517-574`) requests the programme's `first_item`
`.json` URL, then inspects `data_type`:

- **`data_type` present** → the payload *is* the hydration state → 1 request for
  the whole entry (2 per programme with the programme page).
- **`data_type` absent** → the `.json` answered with the **bare JSON record**,
  which carries no access model → the record's own `weblink` is fetched as the
  `.html` player page → **2 requests** for the entry (3 per programme).

The `2026-10-25` run log shows **25 `.json` requests immediately followed by 25
`.html` requests, paired consecutively, 25/25**.  The bare-record shape is what
the live site serves today for every sampled entry, so the **realistic
projection is the 3-per-entry branch, not the 2**.  The previous plan's "50–75"
range encoded this as uncertainty; it is not uncertain — it is a payload-shape
branch that is currently taken 100% of the time.

### Redirects, hidden retries, and hidden fan-out

Verified by reading the transport and by inspecting the client object in the
running container — not inferred:

- **Redirects are followed silently and free.**  `requests.get` defaults to
  `allow_redirects=True` (`requests.Session.request` parameter default), and
  `RaiPlayClient._http` (`rai_play.py:287-303`) does **not** set
  `allow_redirects`.  A 3xx therefore costs **two** upstream round-trips that
  the accounting above does not count.  No observed request in the run log
  redirected (all 113 answered `200` directly), but nothing *prevents* one, and
  the 30 s timeout covers the whole chain.
- **No retry loop.**  No `Session`, no `max_retries`, no `Retry`, no adapter
  mount in `RaiPlayClient`.  The default `HTTPAdapter().max_retries.total` is
  `0`.  A transient failure raises `SourceFetchError` out of the fetch and the
  whole window is discarded (the checkpoint does not advance, so it is
  replayed, never skipped).
- **No fan-out.**  One programme page and at most two video requests per
  entry; the source declares `supports_priority_hints=False`
  (`rai_play.py:312-330`), so the generic runner passes no hint list and no
  extra pages are fetched.  `refresh_catalogue` passes `pages`/`priority` only
  to sources that declare them (`refresh.py:319-322`), and RaiPlay declares
  neither.
- **No external calls on the catalogue path.**  Mapping is local
  (`match_local_catalog`, `matching.py:48`) — it queries `tracker.Movie` rows
  and makes **no** TMDb call.  The `availability_mapping_unresolved` lines in
  the run log are database lookups, not HTTP.

### Runtime envelope

At the measured 0.1 s politeness delay plus observed per-request latency, the
worst case is ~2.7 minutes for a 400-entry window and ~14.7 minutes for the
whole catalogue.  The observed 25-entry run completed in 27.0 s for 113
requests, i.e. **~0.24 s per request**.

The request volume makes the five-run constraint decisive: a 25-entry proof
plus four 400-entry windows reaches only **1,625** inspected entries.  Even five
400-entry windows reaches only 2,025, short of the 2,302-entry catalogue.  Under
the current safe default there is **no honest plan that both uses at most five
additional runs and completes this traversal**.  Do not quietly raise the cap:
400 is the documented production safety bound.

## Expected vs enforceable — what can actually stop a run

This is the distinction the previous revision blurred.  Three different things
are routinely conflated:

| Property | Status | Mechanism |
| --- | --- | --- |
| **Expected** request count | Known, measured | `38 + [2N .. 3N]`; 113 observed at N=25 |
| **Enforceable** per-run bound on *entries* | **Yes** | `RaiPlaySource.max_contents` → `entries[:max_contents]` (`rai_play.py:374`) |
| **Enforceable** bound on *HTTP requests* | **No** | Nothing counts, meters, or refuses RaiPlay requests |
| **Enforceable** cross-run cumulative budget | **No** | No counter, no ledger row, no circuit breaker |

**There is no request counter and no circuit breaker for RaiPlay.**  Verified:

- `ApiCallCounter` holds only `streaming_availability` and `watchmode`.  There
  is **no** `rai_play` row, and `SOURCE_DAILY_LIMITS` / `SOURCE_MONTHLY_LIMITS`
  (`rate_limit.py:82-132`) contain only `streaming_availability` and
  `watchmode`.  `rate_limiter_for("rai_play")` therefore returns an *unmetered*
  limiter, and `RateLimiter.metered` is `False` for it.
- The catalogue path never consults a limiter.  `process_catalogue_refresh`
  documents "no staleness selection and **no API budget**"
  (`bulk_refresh.py:1110`), and `_default_catalogue_runner` calls
  `service.refresh_catalogue()` with no budget argument (`bulk_refresh.py:868`).
  `refresh_catalogue` states "There is **no budget accounting here**"
  (`refresh.py:240`).  `source_budget` is used only on the title lane
  (`refresh_requests.py:935`, `bulk_refresh.py:539`).
- `SourceQuotaCircuit` (`rate_limit.py:208-249`) can only ever trip on
  `SourceQuotaExceeded`, which a sitemap scraper never raises — it has no quota
  to exhaust.  It is structurally incapable of stopping a RaiPlay walk.
- `max_contents` bounds **entries**, not requests.  It is a default argument,
  not a CLI flag: `refresh_provider_catalogue` exposes only `--source`
  (`refresh_provider_catalogue.py:41-46`), and the registry constructs
  `RaiPlaySource()` with the default `400`.  So a run is *expected* to cost
  `38 + 3N` and *enforced* only to cost at most `38 + 3N` for `N ≤ 400`.

**Consequence:** the only thing that stops a run before it exceeds a configured
HTTP budget is the operator choosing a smaller `max_contents` *before* the run.
There is no runtime guard that could abort a run already in progress, and no
cumulative budget that survives across runs.  A configured budget is therefore
**an operator contract, not an enforced limit** — and any plan that says
"budget safety is the `max_contents` cap" is describing a pre-run choice, not a
control.

**Stopping a run mid-window is also not possible.**  `fetch()` runs
synchronously to completion or raises; there is no cancellation token, no
checkpoint written inside the entry loop, and no signal handler.  The only
interruption is killing the process, which leaves the previous cursor intact
(so the window is replayed, not skipped).

## Is a 100-entry window safer than jumping straight to 400?

**Yes — but not for the reason the previous revision implied.**

What a smaller window actually buys:

1. **It bounds the blast radius of an unforeseen request-shape change.**  The
   `38 + 3N` model rests on two measured assumptions: 37 programme sitemap
   children and a 3-request-per-entry detail cost.  If RaiPlay serves the
   rendered-player shape again (2 requests/entry), a 400-entry window costs
   838 instead of 1,238 — a **332-request swing**.  A 100-entry window caps that
   same swing at 100 requests.
2. **It bounds the cost of a silent redirect or a new sub-request.**  Any
   per-entry request that the model does not know about is multiplied by N.  At
   N=400 one unmodelled request per entry is 400 uncounted round-trips; at
   N=100 it is 100.
3. **It gives a second independent measurement of the model before committing
   to the expensive windows.**  The 25-entry run validated the model at 113
   exactly.  A 100-entry run is the last cheap chance to confirm `38 + 3N`
   still holds at a larger N before six 400-entry runs are scheduled.

What it does **not** buy:

- **No completion.**  A 100-entry window advances the anchor by 100 entries and
  leaves 2,104.  It is *slower* to finish: 23 more 100-entry runs versus 6 more
  400-entry runs.  The traversal is not complete after any number of
  intermediate windows; it is complete only when the final window exhausts the
  list and the cursor row is deleted.
- **No reduction in total requests.**  Total requests for the traversal are
  `38 × runs + 3 × entries`.  Smaller windows mean **more runs**, hence **more
  sitemap overhead**: 23 runs × 38 = 874 sitemap requests versus 6 × 38 = 228.
  Smaller windows are *less* request-efficient overall, not more.
- **No enforcement.**  As established above, no counter exists at any window
  size.  A 100-entry window is not "safer" in the sense of being more
  controllable — it is safer only in the sense of being a smaller unmodeled
  bet.

**Recommendation:** a single **100-entry** intermediate window is worth
authorizing *as a model-validation step*, explicitly not as progress toward
completion.  It should be approved with the understanding that it costs
238–338 requests, advances the anchor to position 149, and does not bring the
traversal meaningfully closer to done.  If the goal is completion, go straight
to 400-entry windows — they are more request-efficient and the model is already
validated at N=25.

## Read-only preflight and verification commands

Run these from the repository root.  They read the existing stack only; none
contacts RaiPlay or writes the database.

```sh
docker compose exec -T backend python manage.py shell -c '
from availability.models import Source, SyncCursor, SyncRun, ProviderContent, ContentMapping, Offer
from tracker.models import RefreshRun
from django.db.models import Count
print("source", list(Source.objects.filter(slug__in=["rai_play", "legacy_scraper"]).values("slug", "enabled")))
print("cursor", list(SyncCursor.objects.filter(provider__slug="raiplay", source__slug="rai_play").values("cursor", "progress_at", "last_successful_sync", "updated_at")))
print("sync", list(SyncRun.objects.filter(provider__slug="raiplay", source__slug="rai_play").order_by("-created_at").values("id", "status", "items_discovered", "items_processed", "offers_created", "offers_updated", "offers_deactivated", "started_at", "completed_at")[:10]))
print("refresh", list(RefreshRun.objects.filter(service="rai_play").order_by("-started_at").values("id", "rows_refreshed", "rows_skipped", "error", "started_at", "finished_at")[:10]))
print("content", list(ProviderContent.objects.filter(provider__slug="raiplay").values("source__slug", "currently_available").annotate(n=Count("id")).order_by("source__slug", "currently_available")))
print("mapping", list(ContentMapping.objects.filter(provider__slug="raiplay").values("source__slug", "match_method").annotate(n=Count("id")).order_by("source__slug", "match_method")))
print("offer", list(Offer.objects.filter(provider__slug="raiplay").values("source__slug", "currently_available").annotate(n=Count("id")).order_by("source__slug", "currently_available")))
'
```

Before each write run, save the command output as that run's baseline and
confirm all of the following:

1. Branch and HEAD are the approved revision; `git status --short` shows that
   `frontend/components/AccessTypeFilter.tsx` and unrelated work are untouched.
2. `Source(rai_play).enabled` and `Country(IT).enabled` are true.
3. No other RaiPlay catalogue process owns the advisory lock.  A command that
   reports `locked` is a no-op, not a successful window; wait for the owner and
   re-run the read-only query.
4. The legacy count is still 457 and its identity/provenance is
   `source=legacy_scraper`; do not run any cleanup, reset, reseed, or delete
   command.
5. Existing cursor state is recorded verbatim.  A nonempty current cursor means
   resume it; do not delete or edit it.

## Exact execution interfaces (for use only after GO)

The normal supported operator command resumes automatically and uses the 400
entry default:

```sh
docker compose exec -T backend python manage.py refresh_provider_catalogue --source rai_play
```

It is the preferred interface for ordinary 400-entry windows.  `--source` is
important: omitting it also runs every enabled catalogue collector.

There is no CLI batch-size option.  The following one-shot, coordinator-backed
operator invocation is the controlled way to perform the small resume proof
without changing the registry or application code.  It retains the source
advisory lock and creates the normal `RefreshRun`; `refresh_catalogue()` loads
the durable cursor automatically.

```sh
docker compose exec -T backend python manage.py shell -c '
from availability.sources.rai_play import RaiPlaySource
from availability.services.refresh import AvailabilityRefreshService
from tracker.services.bulk_refresh import process_catalogue_refresh
print(process_catalogue_refresh(refresh_service=AvailabilityRefreshService(source=RaiPlaySource(max_contents=25))))
'
```

This command is intentionally an operator API rather than an undocumented
registry mutation.  It makes no Watchmode or Streaming Availability call.

## Approved-run state machine and gates

`C0` means no cursor; `C(anchor)` means the stored version-1 anchor.

```text
C0 -- successful truncated proof/window --> C(anchor after last inspected URL)
C(anchor) -- successful truncated window --> C(later anchor)
C(anchor) -- successful exhausted final window --> C0 (cursor row deleted)
C(any) -- fetch/persist/process interruption --> C(same prior anchor), retry same window
C(anchor missing from live sitemap) --> source restarts at entry zero; stop for review
```

The table below deliberately schedules only five possible executions.  It is a
proof-and-progress plan, **not** a claim of completion within five runs.

| Run | Entry cap | Maximum HTTP calls | Expected cursor | Approval gate after it |
| --- | ---: | ---: | --- | --- |
| 1 | 25 | 113 | `C(anchor)` | **DONE 2026-10-25** — 113 requests measured; anchor pos 24 → 49; `progress_at` advanced |
| 2 | 25 (resume) | 113 | `C(anchor)` | **DONE 2026-10-25** — resume proven; anchor pos 49; 113 requests |
| 3 | 100 | 338 | later `C(anchor)` | Optional model-validation step only; advances anchor to ~149 |
| 4 | 400 | 1,238 | later `C(anchor)` | Compare cursor text and `progress_at`; it must change. Review `RefreshRun`, aggregate content/mapping/offer counts, and `offers_deactivated=0`. |
| 5 | 400 | 1,238 | later `C(anchor)` | Same evidence; stop on unchanged cursor, error, unexpected restart, or any legacy-count change. |
| 6 | 400 | 1,238 | later `C(anchor)` | Same evidence; estimate remaining entries from the run caps, not from `ProviderContent` count. |
| 7 | 400 | 1,238 | later `C(anchor)` | Same evidence; **this is the earliest window that can complete the traversal** (25 + 6×400 = 2,425 ≥ 2,302). |

The traversal is **not** complete after run 5.  From the anchor at position 49
with 2,253 entries remaining, **six** more 400-entry windows are required; the
fifth window leaves 277 entries unwalked.  The previous revision's "stop after
run 5" was correct as a *stop-and-re-approve* point but must not be read as
"completion is impossible" — it is possible, and it needs runs 6–7.

After each successful call, re-run the read-only query above and record:

- One successful `RefreshRun(service="rai_play")` with an empty `error` and a
  later `finished_at`; it is the durable per-attempt audit.
- Zero or more succeeded `SyncRun`s.  Their counts describe mapped offers only,
  so absence of a `SyncRun` is possible for an all-unresolved window; use
  `ProviderContent` as discovery evidence.
- A changed `SyncCursor.cursor` and `progress_at` for every non-final window.
  `last_successful_sync` need not be set: RaiPlay deletes its row on final
  completion.
- `ProviderContent` counts by `source`; `legacy_scraper` must remain 457.
- `offers_deactivated=0` for every RaiPlay `SyncRun`.

### Stop and recovery rules

Stop immediately (NO-GO for the next window) if the process is `locked`, a
`RefreshRun.error` is nonempty, a source fetch fails, the cursor is unchanged
after a claimed truncated run, the source logs
`rai_play_cursor_checkpoint_missing`, the cursor is deleted before the expected
final window, legacy counts/provenance change, or a RaiPlay `SyncRun` reports a
deactivation.

**Also stop — and this is a defect, not an operator error — if the run's
request count exceeds `38 + 3N`.**  Nothing in the application detects this, so
it must be checked by the operator against the run log.  A count materially
above the model means an unmodelled request path has appeared and the model is
invalid until re-derived.

For an interruption or failed window, do **not** delete the cursor and do not
start from a hand-copied cursor.  Re-run the identical approved command after
the source lock is free.  The last committed cursor remains the prior anchor,
so the batch is replayed rather than skipped.  If a restart occurs because the
anchor disappeared from a changed sitemap, it is safe from data loss but may
consume a full repeated prefix; pause for a new request-budget approval rather
than treating it as normal progress.

### Known defect: INFO-level evidence is invisible in production (High)

`config/settings.py` contains **no `LOGGING` block** — verified by reading the
file and by inspecting the live container.  Consequences:

- `logger.info("rai_play_walk_resume …")` (`rai_play.py:363`) and
  `rai_play_catalogue_started` (`rai_play.py:463`) never reach stdout.  Only
  WARNING and above surface, via `logging.lastResort`.
- The verification gate "the next run logs `rai_play_walk_resume`" is
  **unsatisfiable through normal operation**.  The `2026-10-25` evidence for
  resume was captured by attaching a `FileHandler` in a one-off harness; the
  application itself cannot produce it.
- This does **not** affect correctness of the walk or the checkpoint.  It
  affects the ability to *prove* the walk happened.  The resume was
  independently corroborated by the 25-URL request log and the 24 → 49 anchor
  advance, neither of which depends on INFO logging.

**This must be fixed (a one-block `LOGGING` config change) before any further
window is authorized**, or the approval gates below cannot be met by any means.
It is a source change and requires explicit approval.

## Persistence guarantee: verified boundary and limitation

The safety property needed for a bounded walk holds: the source fetches first;
the refresh service commits canonical discovery/mapping, then offer/`SyncRun`
writes; only after `_persist()` returns does `refresh_catalogue()` write the
new `SyncCursor`.  Consequently a failed, uncommitted batch cannot silently
advance the cursor beyond its observations.  A zero-offer/all-unresolved batch
still receives a checkpoint after successful discovery persistence, avoiding a
loop on such entries.

This is deliberately **ordered durability**, not one all-or-nothing database
transaction covering provider content, offers, `SyncRun`, and cursor.  The
content/mapping phase and the offer phase are separate transactions, and the
cursor write follows both.  A crash after a successful persistence commit but
before the cursor write causes replay (idempotent upserts), never a skip.  A
strict requirement that every one of those records be atomically committed
together is therefore **not proven by the current code** and is an independent
NO-GO if required.  Do not represent the current implementation as stronger
than it is.

## Interpreting the result without deleting history

- **New discovery:** a new `ProviderContent` identity for `raiplay`, normally
  source `rai_play`.  It may have no offer and no mapping.
- **Previously observed title:** the same provider-native identity; upsert
  preserves `first_seen` and advances `last_seen`, rather than creating a
  duplicate.
- **Unresolved mapping:** a RaiPlay `ProviderContent` with no corresponding
  `ContentMapping`; it is intentionally retained and is counted in the
  coordinator result's `unresolved` field.  It is not a failed traversal.
- **Duplicate:** identical `(provider, provider_content_id, content_type,
  season, episode)` converges on the unique `ProviderContent.identity`.  Use
  identity counts, not title text, because distinct programmes can share a
  title.
- **Legacy evidence:** `legacy-*` provider-content identities/provenance remain
  separate observations.  The collector does not delete `ProviderContent`,
  `ContentMapping`, or `Offer`; RaiPlay's declared non-authoritative,
  incomplete snapshot semantics prohibit deactivation on absence.  The only
  code path that could deactivate offers is guarded by authoritative complete
  snapshots, neither of which RaiPlay supplies.

## Completion decision after a later approved extension

Completion is established only when all conditions are true:

1. The final current-code invocation succeeds, its `RefreshRun.error` is
   empty, and it has a completion timestamp.
2. The source reports no truncation and the RaiPlay `SyncCursor` row is absent
   **after** that final success.  A missing cursor before the first
   checkpointed run is not completion evidence.
3. The immediately preceding successful run had a cursor and the final run
   resumed from it (logs show `rai_play_walk_resume`); no
   `checkpoint_missing` restart occurred in the completion sequence.
4. All RaiPlay `SyncRun`s remain non-authoritative with
   `offers_deactivated=0`; `legacy_scraper` remains at 457 observations and
   its mappings/evidence are still present.
5. The final report separates total RaiPlay `ProviderContent`, mapped
   `ContentMapping`, unresolved provider content, and offers.  It must never
   claim that 2,302 sitemap entries equal 2,302 persisted programmes: stale,
   unsupported, trailer, unavailable, and duplicate sitemap entries are
   deliberately distinct outcomes.  Note also that a resumed window re-inspects
   already-persisted entries, so `discovered` overstates new rows.

## Explicit approvals required

1. ~~Approve the 25-entry operator-API proof and its <=112 request envelope.~~
   **DONE** — executed 2026-10-25; 113 requests measured (envelope corrected
   from 112 to 113).
2. ~~After proof, approve each 400-entry default window only after its recorded
   verification gate passes.~~  **Superseded** — the 400-entry envelope is
   1,238, not 1,237, and the five-run schedule cannot complete the traversal.
3. Approve a **100-entry model-validation window** (238–338 requests) *only* if a
   second independent measurement of the `38 + 3N` model is wanted before
   committing to 400-entry windows.  This is optional; the model is already
   validated at N=25.
4. Approve **six 400-entry windows** (1,238 requests each) as the completion
   path, or an explicitly reviewed alternative bound.  Runs 1–2 are done;
   **runs 4–7 remain unauthorized**.  Without this, completion is not
   authorised and the correct decision remains NO-GO.
5. **Fix the INFO-logging defect first** (one-block `LOGGING` change in
   `config/settings.py`).  Until it is fixed, the gate "the run logs
   `rai_play_walk_resume`" cannot be met by the application, and every window
   would be approved on harness-captured evidence rather than production logs.
   This is a source change and requires explicit approval.
6. If strict single-transaction batch atomicity is mandatory, approve an
   application change and fresh verification first; the current ordered
   cursor-after-persistence guarantee is insufficient for that stronger rule.

## GO / NO-GO recommendation

**NO-GO for the next window as the plan stands.**  Not because the mechanism is
broken — it is not — but because two of the plan's own gates are unsatisfiable
and one of its core numbers was wrong.

| # | Gate | Status |
| --- | --- | --- |
| Resume mechanism proven at request-log granularity | **PASS** — 113 requests, anchor 24 → 49, `rai_play_walk_resume` logged, no `checkpoint_missing` |
| Checkpoint is durable, version-1, non-empty | **PASS** — `SyncCursor` id=17, `v:1`, pos 49 |
| Legacy observations preserved | **PASS** — 457 rows, byte-identical identity hash |
| No deactivation / no paid API spend | **PASS** — `offers_deactivated=0` on all SyncRuns; `ApiCallCounter` unchanged |
| Request envelope correctly stated | **FAIL (fixed in this revision)** — was 1,237; is **1,238** |
| `rai_play_walk_resume` observable in production | **FAIL** — no `LOGGING` block; INFO is invisible |
| Budget enforceable at runtime | **FAIL (by design)** — no counter, no circuit breaker; `max_contents` bounds entries only |
| Completion achievable within five runs | **FAIL** — needs **six** 400-entry windows from pos 49 |

**What would make it a GO:**

1. Approve the `LOGGING` fix (gate 5) and re-verify on a short window.
2. Approve six 400-entry windows, explicitly not five.
3. Accept that the request budget is an **operator contract, not an enforced
   limit**, and that the operator must check the run log against `38 + 3N`
   after every window — the application cannot do it.

**What would keep it a NO-GO:** treating `max_contents` as a request budget,
treating `discovered` as a new-row count, or assuming the 113-request figure
scales linearly without re-measuring the sitemap overhead (which is 38 today
and moves monthly).
