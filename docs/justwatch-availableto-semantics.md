# JustWatch `availableTo` semantics — verification memo (issue #174 / F5)

- **Date:** 2026-11-21 (all web sources accessed this date; UTC timestamps in §14)
- **Issue:** #174 "[Leaving Soon F5] Verify JustWatch availableTo semantics before any departure claims"
- **Parent design:** #148, merged PR #172 → `docs/leaving-soon-discovery.md` (§2.2 marks JustWatch `availableTo` "Not proven"; §9.4 decision 1 recommends **no** presentation until F5)
- **Branch:** `research/174-justwatch-availableto-semantics`, based on `origin/master` @ `1b9c4f2`
- **Scope:** research only. No production code changed. One deterministic fixture-only test file added (permitted by #174). No live provider collection of any kind (§1.2).
- **Owner default under test:** "exclude JustWatch `availableTo` until independently verified" (#174 body, owner-approved MVP defaults 2026-11-21).

## Verdict (up front)

**EXCLUDE — the owner default stands.** No JustWatch `availableTo` value may be
presented as a departure ("leaves provider X in country Y on date D") today,
for any provider, country, or access type.

What changed relative to the design doc's "Not proven": the *intended meaning*
of the field is now documented by JustWatch's own published contracts —

- input (provider feed) contract: "Available To: Date/Time of when content
  leaves your service" (§5.1);
- output (Content Partner API) contract: `available_to` = "The date this offer
  stops being available, **if known**" (§5.3);
- JustWatch itself ships a user-facing "Leaving Soon" feature (Pro, iOS/Android)
  built on this data (§5.4).

So `availableTo` is **not** a cache TTL, not a metadata date, and not merely an
invented field. It is a **provider-declared, optional, offer-level expected end
of availability** — polysemous by monetization type (for `rent`/`buy` it is a
storefront window; for `fast` it is a broadcast-slot end, minutes wide).

What remains **Not proven** is exactly what #174 asks about — *reliability*:
that a given non-null value means the title **will** leave that provider in
that country. No primary source guarantees accuracy, renewal behavior,
per-provider coverage, or the output string's format/timezone; the unofficial
web GraphQL field this repo actually reads is not officially published; the
retained fixture corpus contains **zero** non-null samples (§4); and the
permission basis for the current unofficial integration is unresolved (§10).
"Will leave" is therefore still an unverified assumption, and the field stays
EXCLUDE for presentation.

---

## 1. Method and safety boundary

### 1.1 What was examined

1. **This worktree only** (base `1b9c4f2`): source adapter, transport client,
   provider mapping, normalization, refresh/persistence, reconciliation, read
   path, freshness policy, seed migration, retained fixtures and tests,
   retained audit documents (`docs/P8_JUSTWATCH_COVERAGE.md`,
   `docs/audits/stage2c/external_evidence.csv`,
   `docs/operations/stage2g_bounded_availability_experiment.md`).
2. **Retained fixtures offline**: `backend/availability/tests/aggregator_fixtures.py`
   (recorded from live APIs in September 2026 per its docstring; served through
   injected seams; no test touches the network).
3. **Published static documentation** (plain HTTP GET of public docs/terms/help
   pages and one static OpenAPI file; URLs + access dates in §14): JustWatch
   streaming-service integration docs, the official feed JSON Schema, the
   Content Partner API docs and OpenAPI spec, the data-export docs, the
   consumer Terms of Use, the JustWatch Pro help article, and clearly-labelled
   third-party/unofficial corroboration.
4. **One new fixture-only test file**
   (`backend/availability/tests/test_justwatch_available_to.py`, 17 tests),
   run through the sanctioned isolated harness (§13).

### 1.2 What was NOT done (hard restrictions of #174, all honored)

- **No live JustWatch data endpoint was contacted.** `apis.justwatch.com/graphql`
  was never queried; no `popularTitles`/`node`/`offers`/`packages` operation was
  sent; the Content Partner API (`/contentpartner/v2/...`) was never called (no
  token exists or was sought); no title/offer/search page on `www.justwatch.com`
  was fetched or scraped.
- The only `apis.justwatch.com` requests were GETs of **published documentation
  artifacts** under `/docs/` (HTML pages and the static `feed.schema.json` /
  `jw-partner-api.yaml` files). These are the "official published documentation
  accessible without live provider calls" that #174 question 2 asks for; they
  serve no title/offer data. This distinction is recorded so the boundary is
  auditable.
- No browser-cookie import, no login, no scraping, no CAPTCHA bypass, no
  credential use, no source activation, no migrations, no production writes.
  The test run used an **ephemeral** PostgreSQL (`docker-compose.test.yml`,
  per-invocation project name, destroyed on exit).
- No collector or production code modified. `git status` before commit shows
  exactly two additions: this memo and the fixture-only test file.
- Where proof would require live access (renewal behavior, per-provider
  coverage, web-field identity — §6, §11), this memo says **Not proven** and
  routes the proof through the existing bounded-experiment authorization
  mechanism (§10.5) under **separate permission**, not through ad-hoc calls.

### 1.3 Verdict vocabulary used below

- **CONFIRMED** — directly evidenced by repo code/fixture/test output or a
  primary published source quoted verbatim.
- **SUPPORTED** — consistent with primary evidence but the exact claim (e.g.
  identity of the unofficial web field with the official partner field) is not
  itself documented; weaker than proof.
- **NOT PROVEN** — no retained or published evidence establishes the claim;
  would require live access or a JustWatch statement that does not exist publicly.
- **EXCLUDED** — evidence contradicts the hypothesis as the field's primary meaning.

---

## 2. Evidence matrix

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| E1 | `availableTo` is selected on every offer node in all five GraphQL operations | `justwatch_client.py:192` (`TitleOffer` fragment), reused by `:246,276,293,313,333,384` | CONFIRMED |
| E2 | Transport copies the raw value with no interpretation | `justwatch_client.py:919` → `JustWatchOffer.available_to` (`:457,463,477`) | CONFIRMED |
| E3 | Single write site: `Offer.available_until` ← `_parse_datetime(offer.available_to)` | `justwatch.py:564` | CONFIRMED |
| E4 | JustWatch never writes `available_from` | repo-wide grep `availableTo\|available_to` = 14 hits, none an `available_from` write; `justwatch.py` has no `available_from` reference | CONFIRMED (absence) |
| E5 | Parse contract: ISO-8601, `Z`→`+00:00`, naive→**assumed UTC**, invalid/empty→**silent `None`** | `justwatch.py:669-680`; pinned by new tests (§4.3) | CONFIRMED behavior; the UTC *assumption* is NOT PROVEN as provider intent |
| E6 | Date-only strings become midnight UTC | `datetime.fromisoformat("2026-12-01")` + `justwatch.py:678-679`; new test | CONFIRMED behavior; provider intent NOT PROVEN |
| E7 | Date passthrough is offer-type-independent (FLATRATE/RENT/BUY/ADS/FAST alike) | `justwatch.py:559,564`; `normalize.py:39-54`; new tests | CONFIRMED |
| E8 | `fast`/`cinema` monetizations map to `UNKNOWN` offer type but the row **persists with its date** | `normalize.py:39-54` (no `fast`/`cinema` keys), `:83-93` docstring ("UNKNOWN still persists"); new FAST test | CONFIRMED |
| E9 | Country is explicit per call, never inferred; offers are fetched per country | `justwatch.py:53-57,682-695`; `justwatch_client.py:151,352-384,849-853` | CONFIRMED |
| E10 | Provider identity = static `technicalName`→slug table; per-market 3-letter short names for the `packages` filter; unmapped packages skipped + WARNING | `justwatch_providers.py:36-132,177-258,261-270`; `justwatch.py:520-535` | CONFIRMED |
| E11 | Country-aware `ExternalProviderMapping` rows are seeded for JustWatch but are used for service discovery, not offer parsing | `availability/migrations/0006_seed_aggregator_sources.py:39-63,96-108`; `tracker/models.py:273-292`; `tracker/views.py:709-719` | CONFIRMED |
| E12 | Granularity: movie/show/season/episode; season/episode numbers only on those node types; series offers walked per season | `justwatch.py:100-108,227-234,557-558` | CONFIRMED |
| E13 | `bestOnly=True` by default on every operation | `justwatch.py:156`; `justwatch_client.py:684,709` | CONFIRMED setting; its selection semantics NOT PROVEN (undocumented) |
| E14 | Re-observation **overwrites** `available_until` (and `available_from`) on the raw row; no window history is kept anywhere | `persistence/repositories.py:309-310,316-331`; `OfferEvidence` has no window fields (`models.py:579-605`) | CONFIRMED |
| E15 | Offer identity includes `provider_content_id` (= JW node id) but **not** `offer.id` and **not** source | `models.py:58-85`; `justwatch.py:547,565,568` | CONFIRMED |
| E16 | Cross-source window disagreement → `None` + `WINDOW` conflict naming both ends; a `None` observation never conflicts with a dated one; no winner is picked | `services/reconciliation.py:552-583` (esp. `:560`), docstring `:15-17`; `tests/test_reconciliation_conflicts.py:144-171` | CONFIRMED |
| E17 | A past `available_until` **already** suppresses current availability today (pre-F2): `window_open` and `_offer_is_current` | `reconciliation.py:266-269,298`; `tracker/services/availability_report.py:399-409` | CONFIRMED |
| E18 | The API read boundary drops both window fields; frontend has no expiry field | `tracker/services/availability_reads.py:105-122`; grep `available_until\|availableUntil` in `frontend/` = 0 hits | CONFIRMED |
| E19 | JustWatch freshness window = 12h (policy + seeded config) | `freshness.py:85-90`; `0006_seed_aggregator_sources.py:66,86` | CONFIRMED |
| E20 | Permanently non-authoritative; a JustWatch run can never deactivate an unseen offer | `justwatch.py:22-31,147`; `tracker/services/justwatch_sync.py:79-85` | CONFIRMED |
| E21 | Retained corpus: **24 JustWatch offers, every one `availableTo: null`**; before this PR no test referenced `available_to`/`available_until` for JustWatch | `aggregator_fixtures.py:57,68` + all fixture bodies `:118-315`; repo-wide grep; new corpus test pins count=24, all null | CONFIRMED |
| E22 | Official **input** contract: "Available To: Date/Time of when content leaves your service"; optional, "strongly encouraged"; provider-declared expectation ("when a title is *expected* to appear or leave") | https://apis.justwatch.com/docs/streaming_service/ (§5.1 quotes) | CONFIRMED (input side) |
| E23 | Official feed schema: `available_to` = string, `format: date-time`, example `2018-11-13T20:20:39+00:00`; **not required**; the published example puts it on `rent` and `buy` offers | https://apis.justwatch.com/docs/static/feed.schema.json (`Offer` definition; §5.2) | CONFIRMED (input side) |
| E24 | For `fast` offers the feed contract makes `available_from`/`available_to` **mandatory broadcast-slot bounds** (example: 10 minutes apart) | integration docs, FAST section (§5.1) | CONFIRMED (input side) |
| E25 | Official **output** contract (Content Partner API): `available_to` = "The date this offer stops being available, if known."; plain `string`, **not in `required`**; no `available_from` on partner offers | https://apis.justwatch.com/docs/static/jw-partner-api.yaml `offers.Offer` (lines 129-167; §5.3) | CONFIRMED (official output side, partner channel) |
| E26 | The unofficial web GraphQL `Offer.availableTo` this repo reads has the same semantics as the partner `available_to` | same vendor/domain/field name; every unofficial wrapper parses it identically (§5.7); **no official web GraphQL schema is published** | SUPPORTED — NOT PROVEN strictly |
| E27 | JustWatch ships user-facing "Leaving Soon" / "Coming Soon" (Pro; iOS/Android apps only); staff confirm red arrow = "Leaving Soon (in X days)" | https://support.justwatch.com/article/what-do-i-get-with-justwatch-pro (updated 2026-10-21); staff reply on r/justwatchapp (§5.4, §5.7) | CONFIRMED feature exists; its accuracy NOT PROVEN |
| E28 | JustWatch ingests provider feeds "multiple times per day (Usually every 6h)"; partner data/export "daily updates" | integration docs; content_partner + data_export docs (§5.1, §5.3) | CONFIRMED (declared cadence) |
| E29 | Renewal behavior: a renewed license extends/retracts the date and JustWatch propagates it correctly | no published statement; repo overwrites silently with no history (E14) | NOT PROVEN |
| E30 | Per-provider coverage: which providers actually supply `available_to` | optional field (E23/E25); no public list; retained corpus all null (E21); P8 walked only 4/72 partitions, 2/34 countries | NOT PROVEN |
| E31 | Output format/timezone guarantee (datetime vs date-only; which zone) | partner spec: plain `string`, no format constraint; `date_created` example is date-only (`2019-07-01`); third-party title-level type uses `availableToDate` `yyyy-mm-dd` (§5.7) | NOT PROVEN |
| E32 | JustWatch derives departure dates for providers that don't supply them | only an anecdotal Reddit thread (Tubi expiry mismatch, §5.7) | NOT PROVEN (anecdote, not evidence) |
| E33 | Any accuracy/reliability warranty | ToU §1.4 "as is"/"as available", expressly no warranty of accuracy/reliability; unofficial-API disclaimer repeats it (§5.5, §5.6) | CONFIRMED **absence** of warranty |
| E34 | Permission basis for this repo's ongoing unofficial collection | ToU §1.2 (personal/non-commercial only), §7.1 (no data mining/robots/scraping/similar extraction; no circumventing blocks), §7.2 (cost/loss claims); wrapper disclaimer tolerates "private projects" but reserves damages for prohibited use; **no retained document grants this repo a license** | NOT PROVEN / unresolved → owner/legal (§10) |
| E35 | An official licensed route exists: contract + partner token (API), S3 credentials (daily export), widget; **mandatory branded attribution** | docs/api, docs/data_export, docs/content_partner; yaml `full_path` description (§5.3, §10.2) | CONFIRMED |
| E36 | `availableTo` is a cache TTL or a metadata date (release/created) | contradicted by E22/E25; metadata dates are separate fields (`originalReleaseDate`, partner `date_created` = "first seen by JustWatch") | EXCLUDED as primary meaning (stale-evidence risk remains, §9 F-6) |

---

## 3. Code path: how `availableTo` is parsed and written today

All references are against this worktree (base `1b9c4f2`).

### 3.1 Transport (no interpretation)

- `justwatch_client.py:181-201` — `TitleOffer` fragment selects `availableTo`
  (`:192`) alongside `monetizationType`, `presentationType`, `type`,
  `standardWebURL`, `elementCount`, languages, technologies, `package`.
  The fragment is shared by catalogue (`:246`), search (`:276`), details
  (`:293`), seasons (`:313`), episodes (`:333`) and the multi-country offers
  operation (`:384`).
- Offers are always **per country**: `offers(country: $country, platform: WEB,
  filter: $filter)` (`:151`); multi-country fetches alias one field per country
  (`:352-384`) and return a dict keyed by country code, empty lists preserved
  (`:668-692`) so "asked and found nothing" ≠ "never asked".
- `filter: {bestOnly: true}` is sent on every operation (`:684,709`; adapter
  default `justwatch.py:156`). What `bestOnly` selects is **not documented**
  anywhere retained; it plausibly dedupes to one "best" offer per
  package/monetization, which would mean non-best offers' dates are never
  observed (E13, §9 F-7).
- `_parse_offer` (`:903-921`) copies `availableTo` verbatim into
  `JustWatchOffer.available_to` (`:919`). No type check, no timezone handling,
  no validation at this layer.

### 3.2 Adapter (the only transform)

- `justwatch.py:545-572` `_offer()` builds `ProviderOffer`:
  - `available_until=self._parse_datetime(offer.available_to)` (`:564`) — the
    **only** place the field becomes a canonical window value;
  - `available_from` is never set (E4);
  - `offer_type=normalize_justwatch_offer_type(offer.monetization_type)` (`:559`);
  - `provider_content_id=str(node.id)` (`:547`) — the JustWatch **title node**
    id (`tm*`/`ts*`/`tss*`/`tse*`), not the offer id;
  - `source_record_id=str(offer.id)` (`:565`), `source_version=object_type` (`:568`);
  - `season_number`/`episode_number` only when the node is SEASON/EPISODE
    (`:557-558`); series-level offers carry neither, and a show's seasons are
    walked explicitly so season offers are distinct rows (`:227-234`).
- `_parse_datetime` (`:669-680`), verbatim contract:
  1. `str(value or "").strip()`; empty → `None`.
  2. `datetime.fromisoformat(raw.replace("Z", "+00:00"))`; `ValueError` → `None`
     (**silent** — no log, no metric; an upstream format change would erase
     every date invisibly, §9 F-4).
  3. Naive result → `replace(tzinfo=UTC)` (`:678-679`). A date-only string
     therefore becomes **00:00 UTC** of that day; a naive datetime is assumed
     UTC. Explicit offsets are preserved as stated.
- Country: `_require_country` (`:682-695`) raises without an explicit country;
  never inferred from IP/VPN/locale (`:53-57`). Client validates 2-letter
  codes (`justwatch_client.py:849-853`).
- Provider identity: `provider_slug_for_package` (`justwatch_providers.py:261-270`)
  looks up the static `PACKAGE_TO_PROVIDER_SLUG` table (`:36-132`). Unmapped →
  `""` → offer skipped with a WARNING (`justwatch.py:520-535`); never fuzzy
  display-name matching (`justwatch_providers.py:14-23`). Catalogue walks filter
  offers client-side to the partition's provider because `packages` selects
  titles, not offers (`justwatch.py:457-467`; `justwatch_client.py:35-38`).
- Access types (`normalize.py:39-54`): `flatrate`/`subscription`/`svod` →
  `subscription`; `ads`/`avod` → `free_with_ads`; `free`/`freemium` → `free`;
  `rent`/`rental` → `rent`; `tvod`/`buy`/`purchase` → `buy`; `addon` → `addon`;
  **anything else (incl. `fast`, `cinema`) → `unknown`, and the row still
  persists** (`:83-93`). The date transform does not branch on any of these (E7).

### 3.3 Persistence

- `ProviderOffer.available_until` (`domain/models.py:86`) flows unchanged
  through `refresh.py:788` (`for normalized in result.offers`) into
  `OfferRepository.upsert` (`refresh.py:862-863,1039-1040`).
- `repositories.py:235-351`: on create, the window is stored (`:287-288`); on
  re-observation of the same identity it is **overwritten** (`:309-310`) and
  `currently_available` reset True (`:315`); `first_seen` never moves (`:302`).
  `OfferEvidence.record` (`:335-350`) stores presence/authoritativeness only —
  **no window values** (`models.py:579-605`). Consequence: a changed or
  retracted departure date leaves **no audit trail** beyond `updated_at`
  (§7, §9 F-5).
- Identity (`models.py:58-85`): `provider|country|content_type|tmdb_id|season|
  episode|offer_type|provider_content_id`. Source and `offer.id` are **not**
  slots. For JustWatch, `provider_content_id` is the title node id, so several
  offer nodes of the same title/provider/offer_type (e.g. HD vs 4K when
  `bestOnly` is off) collapse onto one row, last write winning the date (§9 F-8).
  The `OfferEvidence` docstring (`models.py:532-538`) states the general
  last-writer-wins hazard for shared identities; distinct sources normally get
  distinct rows because their `provider_content_id`s differ.
- `Offer.available_from`/`available_until` are commented "Provider-stated
  lifecycle, distinct from our observation fields" (`models.py:371-373`);
  `first_seen`/`last_seen` are observation timestamps (`:375-378`).

### 3.4 Reconciliation and read path

- `_observe` computes `window_open = (from is None or from <= now) and
  (until is None or until > now)` (`reconciliation.py:266-269`) and
  `available_now = currently_available and window_open and not mapping_doubt`
  (`:298`); both window fields are copied onto the observation (`:293-294`).
- `_window_values` (`:552-583`): per field, collect **non-null** observations
  (`:560`); one distinct value → keep it; >1 → `None` + `WINDOW` conflict with
  every involved source/value (`:562-582`); none → `None`. No source ranking,
  no repair, no inference (docstring `:3-7,15-17`).
- `ReconciledOffer` carries the merged windows (`:444-445`; DTO
  `domain/reconciliation.py:171-172`).
- Read boundary: `_split` sends only `VERIFIED` reconciled offers to `current`
  (`availability_reads.py:90-102`); `_tracker_offer` (`:105-122`) **does not
  copy** either window field; the tracker `Offer` DTO and frontend `PrimeOffer`
  have no expiry field (E18). So **no user-facing departure claim exists today**.
- Report path: `_offer_is_current` (`availability_report.py:399-409`) treats a
  past `available_until` (or future `available_from`) as not-current. Together
  with `window_open`, an incorrect/stale JustWatch date can already suppress
  current availability evidence **before** any Leaving Soon feature (E17,
  §9 F-9, §12).
- Freshness: `justwatch` window 12h (`freshness.py:90`; seeded config
  `0006:66,86`); evidence strength is per-source (`reconciliation.py:663-686`).

### 3.5 Documentation drift worth noting (no code change made)

- `justwatch.py:16-20` says provider resolution goes "through the existing
  `ExternalProviderMapping` seam (country-aware — see `.justwatch_providers`)".
  In fact offer parsing uses the **static** `PACKAGE_TO_PROVIDER_SLUG` table;
  the DB `ExternalProviderMapping` rows (seeded per country, `0006:96-108`)
  drive service discovery/eligibility (`views.py:709-719`), not offer parsing.
  Country-awareness in the adapter lives in `PACKAGE_SHORT_NAMES` (the filter
  ids), while `technicalName`→slug is country-independent by design
  (`justwatch_providers.py:25-29`).
- `freshness.py:86-87` says JustWatch "is walked by `refresh_provider_catalogue`
  (12h)"; the actual entry point is the dedicated
  `refresh_justwatch_catalogue` command (its docstring explains why the generic
  scheduler cannot drive it), and `docs/CRON.md` currently contains **no**
  JustWatch slot (grep = 0 hits; `LEGACY_SCRAPER_RETIREMENT_AUDIT.md:139` still
  points at `docs/CRON.md:193`, which is now RaiPlay text). Cadence claims in
  this memo therefore rest on the declared 12h window, not on a verified cron.

---

## 4. Retained fixtures: exact inputs, outputs, unknown cases

### 4.1 The corpus is entirely null

`aggregator_fixtures.py:55-74` — the `offer()` helper defaults
`available_to=None` (`:57`) and emits `"availableTo": available_to` (`:68`).
**Every one of the 24 JustWatch offer nodes across all 11 retained fixture
structures passes no `available_to`**, i.e. the recorded September-2026
payloads all state `availableTo: null`:

- `JW_CATALOGUE_NL_NETFLIX` (4 offers: Netflix/Prime NL FLATRATE, iTunes RENT),
- `JW_CATALOGUE_UNMAPPED_ONLY` (1), `JW_SEARCH_RAKUTEN_NL` (2: ADS + BUY),
- `JW_SEARCH_MOVIE` (2), `JW_SEARCH_SHOW` (1), `JW_SEASONS` (2),
- `JW_EPISODES` (1), `JW_OFFERS_MULTI_COUNTRY` (2: NL/US; DE empty list),
- `JW_SEARCH_MULTI_COUNTRY_ACCESS` (7: FLATRATE/RENT/ADS/BUY across NL/IT/JP),
- `JW_SEARCH_SERIES_JP` (1), `JW_SEARCH_SERIES_UNRESOLVED` (1).

Before this PR, **no test in the repository referenced `available_to` or
asserted anything about `available_until` for JustWatch** (repo-wide grep:
the only hits are the client, the adapter, the fixture helper, and the design
doc). The design doc's "the field exists in … fixtures"
(`leaving-soon-discovery.md:158-159`) is true only as *field presence with null
values*. There is **no retained non-null sample** of what JustWatch actually
emits — its format, granularity, or frequency cannot be measured offline.
The new corpus test (§4.3) pins this so a future non-null fixture forces a
revisit of this memo.

Retained audit material is consistent: `docs/audits/stage2c/external_evidence.csv`
(14 JustWatch rows, 2026-11-13) records title-page availability observations
with confidence "Medium (secondary aggregator, title page)" and **no expiry
column at all**.

### 4.2 Exact input → transformed output (executable, new tests)

Chain: `fx.offer(...)` → `client._parse_offer` → `JustWatchSource._offers_for`
→ `ProviderOffer` (the object `refresh.py` upserts). Country NL, provider
`netflix` (mapped), node `tm174001`, movie unless noted.

| Input `availableTo` (raw JSON) | `monetizationType` | `ProviderOffer.available_until` | `offer_type` |
|---|---|---|---|
| `null` | FLATRATE | `None` (unknown) | subscription |
| `"2026-12-01T23:59:59Z"` | FLATRATE | `2026-12-01 23:59:59+00:00` | subscription |
| `"2026-12-01"` (date-only) | FLATRATE | `2026-12-01 00:00:00+00:00` (midnight-UTC coercion) | subscription |
| `"whenever"` (invalid) | FLATRATE | `None` (silent) | subscription |
| `"2026-12-01T23:59:59Z"` | RENT | `2026-12-01 23:59:59+00:00` (storefront window passed through) | rent |
| `"2026-11-21T20:30:39+00:00"` | FAST | `2026-11-21 20:30:39+00:00` (**broadcast-slot end**) | **unknown** |
| `"2026-12-01T23:59:59Z"` on a SEASON node | FLATRATE | same datetime; `season_number=2`, `episode_number=None` | subscription |

Unambiguous unknown cases: `null`, empty/whitespace, and any non-ISO value all
produce `None` — never a guessed date, never a crash. `available_from` is
`None` in every case (E4).

### 4.3 New fixture-only tests (the only code added by this PR)

`backend/availability/tests/test_justwatch_available_to.py` — 17 deterministic
tests, no HTTP, no DB writes:

- `ParseDatetimeSemantics` (7): null/empty/whitespace → None; `Z` suffix;
  explicit `+01:00` offset preserved (not rebased); date-only → midnight UTC;
  naive → assumed UTC; invalid (`"not-a-date"`, `"2026-13-45"`, `"0"`, `0`,
  `False`, epoch int) → silent None; surrounding whitespace stripped.
- `TransportPassthrough` (2): raw string copied verbatim; null stays null.
- `AdapterTransform` (7): the §4.2 table, end-to-end through the real parsers.
- `RetainedCorpusEvidence` (1): exactly 24 retained offers, every one
  `availableTo: null` (fails on purpose if a non-null fixture is ever added).

Run (sanctioned isolated harness, ephemeral PostgreSQL, destroyed on exit):

```
./scripts/test-backend-isolated.sh availability.tests.test_justwatch_available_to -v 2
...
Ran 17 tests in 0.002s
OK            (exit code 0, 2026-11-21)
```

These tests pin **current behavior**, including the conservative-but-invisible
choices (silent None, UTC assumption). They deliberately do not "fix" anything;
changing the transform is a production decision gated on §11.

---

## 5. Primary published sources (quoted, with access dates)

All accessed **2026-11-21** via plain GET of public static pages/files. No
data endpoint was contacted (§1.2).

### 5.1 JustWatch — Streaming Service integration docs (official, input side)

URL: https://apis.justwatch.com/docs/streaming_service/ (page title
"Streaming Service: Getting Started"; addressed to streaming services
integrating *into* JustWatch).

- "Giving us detailed information like when a title is **expected to appear or
  leave your service** will improve the user experience of people clicking out
  to your service."
- Minimum required availability fields: Quality, Type
  (`Rent, Buy, Flatrate, Free, Ads, FAST, Cinema`), Price/Currency (rent/buy
  only), Country, URLs. **`Available From`/`Available To` are NOT minimum
  required** — they are listed under "additional fields … strongly encouraged":
  - "**Available From: Date/Time of when content becomes available on your service**"
  - "**Available To: Date/Time of when content leaves your service**"
- "We access this feed multiple times per day (**Usually every 6h**) and ingest
  it into our system."
- The published example feed puts `available_from`/`available_to`
  (`"2018-11-13T20:20:39+00:00"` / `"2019-11-13T20:20:39+00:00"`) on **`rent`
  and `buy`** iTunes offers — i.e. the same field names carry storefront
  windows, not only subscription exits.
- FAST section: "A FAST offer **must have** 'available_from' and 'available_to'
  fields to indicate **start time and end time**. You may need to add as many
  offers as required for indicating different playback times." Example slots:
  `20:20:39 → 20:30:39` (10 minutes). This is decisive evidence that
  `available_to` is **polysemous by monetization type**.
- Metadata (posters/descriptions/cast) "is sourced from TMDb and IMDb **with
  permission**" — JustWatch's own upstream metadata licensing statement.
- Schema "Last modified: 15.10.2026"; changelog shows the format evolving
  (2021→2026), i.e. the contract is not frozen.

### 5.2 JustWatch Standard VOD Feed JSON Schema (official, input side)

URL: https://apis.justwatch.com/docs/static/feed.schema.json

- `Offer.required = [quality, monetization_type]` plus a `oneOf` on
  `countries_configuration` / `country_iso`. **`available_to` is optional.**
- `available_to`: `{"type": "string", "format": "date-time", "examples":
  ["2018-11-13T20:20:39+00:00"]}` — no description text in the schema itself;
  the prose definition lives in §5.1. Same for `available_from`.
- `monetization_type` pattern: `(buy|rent|free|ads|flatrate|fast|cinema)`.
- Geo model: `country_iso` **or** `countries_configuration`
  (`is_global` + `exclude_isos`, or `include_isos`) — offers can be global with
  exclusions; JustWatch resolves them per country. A departure is therefore a
  per-offer, per-country fact, never a global assumption.
- Availability is required on Movie and Episode objects; Show/Season objects
  may carry offers but are not required to — season-level expiry evidence is
  structurally optional upstream too.

### 5.3 JustWatch Content Partner API + OpenAPI spec (official, output side)

URLs: https://apis.justwatch.com/docs/api/ ,
https://apis.justwatch.com/docs/content_partner/ ,
https://apis.justwatch.com/docs/data_export/ ,
https://apis.justwatch.com/docs/static/jw-partner-api.yaml
(raw spec downloaded 2026-11-21; 65,059 bytes;
sha256 `3fb1bd2b5789cdc80b2633cb49495ccdf71d6a5189c89c677ad4f8c9b60cb468`).

- **`offers.Offer.available_to` (spec lines 131-134), verbatim:**
  > "The date this offer stops being available, **if known**."
  `type: string`, `format: string` — **no `date-time` constraint** on the
  output side (contrast the input schema). `available_to` is **not** in
  `required` (`required: [monetization_type, provider_id, presentation_type,
  urls]`, lines 163-167). There is **no `available_from`** on partner offers.
- `date_created`: "The date this offer was first seen by JustWatch." — a
  *metadata* date, distinct from `available_to`; spec examples render it
  date-only (`"2019-07-01"`), evidence that JustWatch output date strings can
  be date-only.
- `monetization_type` (partner output): "How the offer is priced: free,
  flatrate, buy, or rent." (the partner prose omits ads/fast/cinema even though
  the input feed supports them — another sign the output surface is not a
  1:1 mirror of the feed).
- Access is **contract-gated**: "Once the contract is concluded, each partner
  is handed a unique partner token. This token is required for every request"
  (root `https://apis.justwatch.com/contentpartner/v2/content`). Data export:
  daily NDJSON via S3 `jw-data-partner-out`, credentials handed out after
  contract; "Use the latest export to **overwrite** previously downloaded data
  to maintain a fresh catalog" (i.e. JustWatch's own licensed freshness model
  is full-overwrite daily, not per-field deltas).
- **Attribution is mandatory for partners**: branded JustWatch link next to
  every widget/"where to watch" integration, country-specific title URL,
  anchor text "JustWatch"; the spec's `full_path` description repeats:
  "partners are **required** to attribute data with a branded link built from
  this path."
- The content_partner offer description lists Price/Currency, Monetization,
  Presentation, Provider ID, URLs — it does **not** advertise expiry dates as
  a partner offer field at all, even though the OpenAPI schema includes
  `available_to`. Coverage/quality of the field on the partner channel is
  therefore also not promised in prose.
- Data inaccuracies route: `partner-support@justwatch.com` + user feedback form.

### 5.4 JustWatch Pro help article (official, user-facing feature)

URL: https://support.justwatch.com/article/what-do-i-get-with-justwatch-pro
(updated 21 Oct 2026). Pro features include, verbatim:

- "**View titles Leaving Soon** from your streaming services (on iOS and
  Android apps only)"
- "**View titles Coming Soon** to your streaming services (on iOS and Android
  apps only)"

So JustWatch itself presents departure dates to end users — the *concept* is
officially endorsed. This does not warrant the accuracy of any individual
date, and the apps-only scope means the web GraphQL surface this repo reads
is not the documented delivery channel for that feature.

### 5.5 JustWatch consumer Terms of Use (official, legal)

URL: https://support.justwatch.com/article/just-watchs-terms-of-use
(updated 5 May 2026; JustWatch GmbH, Berlin; German law §9.1).

- **§1.2**: "The use of the service on JustWatch is intended for **personal
  and non-commercial use only** …"
- **§1.4**: provided "as it is" and "as available" **without any warranty**;
  "We do not warrant that JustWatch is free of inaccuracies … or that the
  results of its use will be accurate or reliable"; use at the user's own risk.
- **§7.1**: users agree not to "frame, **scrape**, rent, lease, loan, sell,
  assign, sublicense, distribute or create derivative works based on, or
  reproduce, display, publicly perform, or otherwise use the Service Content
  in any way for any public or commercial purpose …"; "**In connection with
  the use of the Website users will not engage in or use any data mining,
  robots, scraping or similar data gathering or extraction methods.**";
  "If users are blocked by JustWatch from accessing the Website (including by
  blocking of IP address), users agree **not to implement any measures to
  circumvent such blocking** (e.g., by masking your IP address or using a
  proxy IP address)."; violation **automatically terminates** the right to
  access and requires destroying copies.
- **§7.2**: JustWatch reserves "the right to take legal proceedings against
  users for reimbursement of all costs or losses resulting from the breach of
  this section".
- §1.1 scope: "the website https://www.justwatch.com … and … the JustWatch
  apps". Whether `apis.justwatch.com/graphql` (the website's backend, per
  `justwatch_client.py:88-92`) falls under "the Website" is **not resolved by
  the text** — an owner/legal question, not something this memo adjudicates.

### 5.6 Unofficial-API disclaimer (JustWatch's stated position, third-party hosted)

URLs: https://github.com/dewey/JustWatch-API (README "Disclaimer"; identical
text in https://github.com/dawoudt/JustWatchAPI). Quoted verbatim in key part:

- "**This is not the official JustWatch API.** … Therefore it is **prohibited
  to use the API for commercial purposes** … The API **may be used for
  non-commercial purposes such as private projects**, but please be respectful
  with your API calls to prevent an overload on the API."
- "JustWatch does not warrant that the API is free of inaccuracies … **JustWatch
  can disable and change the API at any time without notice** and without
  giving any reason. … **Incorrect or prohibited use of the API, for example
  for commercial use, may result in a claim for damages by JustWatch.**"
- "If you would like to work with JustWatch and use the official Data API …
  contact us at data-partner@justwatch.com. **Currently, JustWatch can only
  work with bigger partners and clients.**"

Provenance caveat: this is a wrapper README quoting JustWatch's position, not
a JustWatch-published page; it is the clearest public statement of the
unofficial API's tolerated use, and it is consistent with ToU §1.2/§7. It is
**not** a license grant to this repository.

### 5.7 Third-party / unofficial corroboration (labelled; not primary)

- **filmigo (Go)**, https://raw.githubusercontent.com/Jisin0/filmigo/v0.2.3/justwatch/types.go
  (also rendered at https://pkg.go.dev/github.com/Jisin0/filmigo/justwatch):
  reverse-engineered types show a **title-level** `availableTo` **array** of
  `{availableCountDown: int, availableToDate: string "yyyy-mm-dd", package:
  {shortName}}` on Title/SeasonPreview/EpisodePreview — i.e. the app-side
  "leaving soon" shape is **date-only + countdown + package**, distinct from
  the offer-level scalar this repo reads. Its `GetTitleOffersResult` buckets
  offers as `flatrate/buy/rent/free/fast`, corroborating FAST as a separate
  monetization bucket on the web surface. Unofficial; no semantic guarantee.
- **simple-justwatch-python-api** (the library this repo's client mirrors,
  `justwatch_client.py:9-13`),
  https://raw.githubusercontent.com/electronic-mango/simple-justwatch-python-api/master/src/simplejustwatchapi/query.py:
  `_parse_offer` copies `available_to = json.get("availableTo")` raw — same
  passthrough, no semantics documented.
- **simple-justwatch-js**,
  https://raw.githubusercontent.com/anthonyfranc/simple-justwatch-js/main/index.js:
  same `TitleOffer` fragment incl. `availableTo`; header states the JustWatch
  GraphQL API "is **undocumented and may change at any time** … Use at your
  own risk."
- **reefapi** (third-party reseller of a "Streaming Availability API
  (JustWatch where-to-watch)"), https://reefapi.com/docs/streaming.md:
  `page_type=leaving` "returns titles about to expire (with availableTo
  dates)" — ecosystem treats the field as expiry; reseller docs, not JustWatch.
- **Reddit, r/justwatchapp** (https://reddit.com/r/justwatchapp/comments/1umfgb0/):
  a self-identified JustWatch team member: "Green box with arrow pointing down
  = Coming Soon (in X days); Red box with arrow pointing up = **Leaving Soon
  (in X days)**" — staff confirmation of UI semantics; anecdotal platform,
  weight limited to corroborating E27.
- **Reddit, r/TubiTV** (https://reddit.com/r/TubiTV/comments/1rsdcjy/): user
  reports JustWatch marks Tubi films expiring that Tubi's own "leaving soon"
  page does not list. **Anecdote only** — it neither proves JustWatch derives
  dates nor proves inaccuracy; recorded because it is the only public signal
  about cross-provider date provenance (E32 stays NOT PROVEN).

### 5.8 What no primary source says (the proof gaps)

1. No official schema/documentation for the **web GraphQL** `Offer.availableTo`
   (the field this repo actually reads). Identity with the partner
   `available_to` is SUPPORTED, not proven (E26).
2. No statement of **which providers** supply `available_to`, or coverage
   rates per provider/country (E30).
3. No **renewal/retraction** semantics: what happens to a published date when
   a license is renewed, or when a provider stops sending the field (E29).
4. No **output format/timezone** contract: partner spec says plain `string`;
   date-only outputs exist for sibling fields; the web field's shape is
   unmeasured offline because the retained corpus is all null (E31, E21).
5. No **accuracy warranty** anywhere — expressly disclaimed (E33).
6. No public **license** for automated collection via the unofficial endpoint
   (E34); the official route is contract-gated and attribution-bearing (E35).

---

## 6. Semantics discrimination (the core research question)

Does `availableTo` reliably mean "this title/offer **WILL LEAVE** this provider
in this country"? Distinguishing every candidate meaning:

| Candidate meaning | Assessment | Evidence |
|---|---|---|
| **Verified future removal** from named provider+country | **Intended meaning: SUPPORTED. Reliability: NOT PROVEN.** Officially "when content leaves your service" / "stops being available, if known" — but optional, provider-declared, unwarranted, renewal behavior unknown, coverage unknown, web-field identity unproven. A non-null value is an *expected* end, not a verified commitment. | E22, E25, E27 vs E26, E29-E33 |
| **Contractual / listing window end** | This is what the field *is* for flatrate/free/ads: the provider's declared license/listing window end. It may be renewed; the date can move or vanish. "Window end" and "removal" coincide only if the window is not renewed — which no source guarantees. | E22 ("expected"), E23, E29 |
| **Rental / purchase availability period** | **YES for rent/buy offers.** The official example feed carries `available_to` on rent and buy offers; it is a storefront window, not a subscription departure. Out of SPEC scope for ranking and for Leaving Soon. | E23, E7; SPEC (rent/buy excluded) |
| **FAST broadcast-slot end** | **YES for fast offers** — mandatory slot bounds minutes apart. Never a catalogue departure. Persists today as an `unknown`-type offer **with a date** (E8) — a live trap for any naive expiry query. | E24, E8 |
| **Metadata date** (release, first-seen, created) | **EXCLUDED.** Distinct fields exist (`originalReleaseDate`; partner `date_created` = "first seen by JustWatch"). `available_to` is defined as availability end, not metadata. | E36, §5.3 |
| **Cache TTL** | **EXCLUDED as meaning.** It is a provider-declared date, not a TTL. But *our* observation of it can be stale (12h window vs ~6h upstream ingestion vs daily export), so it can function as stale evidence. | E36, E19, E28 |
| **Renewal marker** | **NOT PROVEN.** No document describes renewal propagation. Repo behavior: a changed date silently overwrites the old one with no history (E14) — renewed, corrected, and retracted dates are indistinguishable after the fact. | E29, E14 |
| **Stale evidence** | **Possible and unbounded per-field.** Freshness is measured on the observation (`last_seen`), never on the date itself; a "fresh" row can carry a date the provider changed upstream hours ago. | E19, E28, §9 F-6 |

**Synthesis.** `availableTo` is best read as: *"the provider-declared expected
end of this offer's availability in this country, if JustWatch knows it"* —
offer-level, country-scoped, optional, polysemous by monetization type, and
explicitly unwarranted. That is materially stronger than the design doc's
"end of a rental window? catalogue exit? promo end?" guesswork: for
subscription/free/ad-supported offers the official contracts **do** describe a
departure-style meaning. But #174's bar is *reliable evidence of a specific
future removal*, and none of the reliability preconditions (coverage, renewal,
format, accuracy, web-field identity, permission) is proven. Hence EXCLUDE for
presentation, with a concrete upgrade path (§11).

---

## 7. Reconciler behavior: disagreement and changed dates (no repair, no inference)

1. **Two sources disagree now** (same content/provider/country/offer_type
   group, distinct raw rows): `_window_values` sees >1 distinct non-null value
   → merged `available_until = None` + a `WINDOW` conflict recording every
   source and value (`reconciliation.py:552-583`). No winner, no averaging, no
   "most recent wins" — the module "cannot pick a winner (there is no trusted
   source)" (`:15-17`). Pinned by
   `test_reconciliation_conflicts.py:144-171` (two sources, +10d vs +30d →
   both windows None, one WINDOW conflict, 4 facts).
2. **One source dates, another is silent** (`None`): null observations are
   filtered out before comparison (`:560`) → the dated value survives with no
   conflict. A source that never publishes dates **cannot veto** a JustWatch
   date. This is correct conservative merging, but it means cross-source
   corroboration of a JW date is absent by construction for most providers
   (design doc §2.1: only ~6 sources write windows at all).
3. **The same source changes an earlier date** (renewal, correction,
   retraction): `OfferRepository.upsert` overwrites `available_until` on the
   raw row (`repositories.py:309-310`); the previous value is **lost** —
   `OfferEvidence` stores presence only (`models.py:579-605`), and no other
   table records window history. There is **no auto-repair and no inference**
   (consistent with #174), but also **no audit trail** of date changes; a
   retraction to `null` silently clears the window. `first_seen` never moves
   (`:302`), `last_seen`/`updated_at` move — that is the only trace.
4. **A date passes into the past**: `window_open` false
   (`reconciliation.py:266-269`) → `available_now` false (`:298`); the report
   path's `_offer_is_current` also rejects it (`availability_report.py:404-406`).
   The reconciled offer can still be `currently_available=True` if another
   observation is available (`:439`); expired-but-listed rows belong to
   `last_known`, not "leaving soon" (design §7.6; `availability_reads.py:90-102`).
5. **Identity collapse caveat**: because `offer.id` is not an identity slot
   (E15), two JustWatch offer nodes for the same title/provider/offer_type
   share one raw row; the later observation's date wins **without any conflict
   being recorded** (conflicts need distinct rows). `bestOnly=True` normally
   prevents this; turning it off would activate the hazard (§9 F-8).
6. **Freshness never resurrects a date**: evidence strength is per-source
   (`_evidence_values`, `:663-686`); a stale JW row stays stale even if another
   source is fresh, and only `VERIFIED` reaches `current`
   (`availability_reads.py:98-99`).

Net: the reconciliation layer already implements exactly the
"disputed → unknown, never repaired" semantics a departure feature needs.
Its blind spots are **within-source date history** (3) and **same-identity
collapse** (5), both of which matter if JW dates are ever promoted to claims.

---

## 8. Country-specific behavior and provider identity

- **Country is a hard, explicit scope.** Adapter raises without one
  (`justwatch.py:682-695`); client validates and upper-cases
  (`justwatch_client.py:849-853`); every GraphQL offers field carries
  `country:` (`:151`); multi-country responses stay keyed per country with
  empty lists preserved (`:668-692`; fixture `JW_OFFERS_MULTI_COUNTRY` shows
  NL/US offers and `DE: []`). A departure observed for NL is never evidence
  about US. Upstream, offers can be `is_global` with `exclude_isos` (§5.2), so
  JustWatch itself resolves geo per offer — the per-country read is the
  correct granularity.
- **Provider identity is per-market at the filter layer, canonical at the
  identity layer.** `PACKAGE_SHORT_NAMES` (`justwatch_providers.py:177-258`):
  Prime Video is `prv` in NL/IT/CA/AU/FR but `amp` in US/GB/DE; both
  `amazonprimevideo` and `amazonprime` map to canonical `prime`
  (`:39-40`). Netflix `nfx` across US/GB/NL/IT/CA/AU/DE/FR; Disney `dnp`;
  Apple TV+ `atp`; Max `mxx`; Paramount `pmp`; plus US-only peacock/hulu,
  AU iview/sbs, IT raiplay/mediaset, GB bbc, and a measured NL niche set
  (videoland, viaplay, skyshowtime, canal+, nlziet, film1, hayu, ziggo,
  npo_plus, picl, sooner, iffr, pathe, rakuten via legacy `wuaki`/`wki`,
  justwatch_tv, filmzie, south_park, and 21 Amazon add-on channels with
  declared parent `prime`, `:139-164`). **An unmeasured market means "not
  walkable", never a guessed id** (`:205-208,301-309`).
- **Unmapped packages are skipped, logged, never fuzzy-matched**
  (`justwatch.py:520-535`; `justwatch_providers.py:14-23,261-270`). Near-name
  collisions (`amazonhbomax` = Max add-on vs `max`; `amazonprimevideowithads`;
  `itunes` store vs `appletvplus`) are explicitly kept distinct.
- **DB mapping vs static table**: country-aware `ExternalProviderMapping`
  rows are seeded (`0006:39-63,96-108`; `tracker/models.py:273-292`) and used
  for catalogue-driven service discovery (`views.py:709-719`); offer parsing
  uses the static table (§3.5 drift note). Both agree on the per-market ids.
- **Measured coverage is tiny** (retained, `docs/P8_JUSTWATCH_COVERAGE.md:13-51`):
  13 succeeded runs, 906 titles, 602 offers, **0 deactivations**; only **4 of
  72 partitions** ever walked (NL/netflix, NL/apple, AU/netflix, AU/abc_iview);
  **2 of 34 enabled countries** (NL, AU). Even if semantics were approved
  tomorrow, expiry coverage would be near-zero and unmeasured for every
  provider/country cell that matters (Netflix/Disney/Max NL/IT/US…).
- **JustWatch is trusted for TMDb identity assertions**
  (`identity_policy.py:5-9`) — relevant because a wrong title mapping would
  attach a real departure date to the wrong title; the adapter's conservative
  search (`justwatch.py:240-340`) mitigates this (namespace checks, conflicting
  ids rejected, ambiguity → empty).

---

## 9. Failure modes (ranked for the Leaving Soon feature)

| ID | Failure | Mechanism | Severity if F2 unguarded |
|---|---|---|---|
| F-1 | **FAST slot end read as departure** | `fast` → `unknown` offer type but date persists (E8); official FAST dates are minutes-wide slot bounds (E24) | HIGH — a 10-minute broadcast window would render as "leaving soon" / immediately expired |
| F-2 | **Rent/buy storefront window read as subscription departure** | date passthrough is offer-type-independent (E7); official examples put dates on rent/buy (E23) | HIGH — out-of-SPEC access types leaking into claims |
| F-3 | **Date-only → midnight UTC** | `_parse_datetime` coerces (E6); if providers mean local end-of-day, offers flip `window_open=false` up to ~24h+ early | MEDIUM — intent unproven (E31); suppresses availability early |
| F-4 | **Silent invalid → None** | no log/metric on parse failure (`justwatch.py:676-677`) | MEDIUM — upstream format change erases all dates invisibly; conservative direction but unobservable |
| F-5 | **Renewal/retraction indistinguishable, no history** | upsert overwrites; OfferEvidence has no windows (E14, §7.3) | MEDIUM — cannot audit "the date moved"; a retracted date silently becomes unknown |
| F-6 | **Stale upstream date inside a fresh observation** | 12h local window (E19) vs ~6h feed ingestion / daily export (E28); no per-field timestamp | MEDIUM — "fresh" evidence can carry an already-renewed date |
| F-7 | **bestOnly selection bias** | only "best" offers returned; semantics undocumented (E13) | MEDIUM/unknown — dates on non-best offers never observed; coverage silently partial |
| F-8 | **Same-identity date flip without conflict** | `offer.id` not in identity (E15); multiple presentation types collapse when bestOnly off | LOW today (bestOnly on) |
| F-9 | **Pre-F2 suppression of current availability** | a wrong/past JW date already flips `window_open`/`_offer_is_current` (E17) | EXISTS TODAY — conservative (hides rather than invents) but can hide a watchable title; owner decision whether to quarantine (§12) |
| F-10 | **Absence misread as "nothing leaving"** | aggregator coverage incomplete; non-authoritative (E20); P8 coverage tiny (§8) | MEDIUM — UI must label unknown-coverage, never empty (design §4c/§8.3 already requires this) |
| F-11 | **ToU / permission exposure of ongoing unofficial collection** | §10 | OWNER/LEGAL — not a data bug; gates any continued integration |

---

## 10. Data rights, attribution, freshness, and source permission

### 10.1 Current integration basis (unofficial, unresolved)

- The client speaks the **unofficial, unauthenticated** website GraphQL
  endpoint (`justwatch_client.py:1-17,70`), mirroring an unofficial library,
  with a browser User-Agent because "a bare python-requests User-Agent is
  rejected by its edge" (`:88-100`). The docstring frames this as client
  identity, "not an attempt to evade anything".
- ToU §7.1 prohibits "data mining, robots, scraping or similar data gathering
  or extraction methods" in connection with use of the Website, and prohibits
  circumventing blocks (§5.5). An automated GraphQL collector is, on its face,
  "similar data gathering or extraction"; whether the API subdomain is "the
  Website" (§1.1) is unresolved. The UA choice is legally sensitive precisely
  because §7.1's anti-circumvention clause exists: if the edge rejects
  non-browser clients, presenting a browser UA to get through is the pattern
  that clause targets. **This memo does not adjudicate legality**; it records
  that no retained document grants this repo permission, and that the
  wrapper disclaimer (JustWatch's stated position, §5.6) tolerates
  non-commercial private projects while reserving damages claims for
  prohibited use and the right to disable the API without notice.
- The project is personal/single-user/non-commercial (SPEC; CLAUDE.md), which
  fits §1.2 and the disclaimer's "private projects" tolerance — but that
  tolerance is not a license, and §7.1's extraction ban is not limited to
  commercial use. **Owner/legal decision required before any ongoing
  integration is treated as permitted** (§10.6).

### 10.2 The official licensed route (what "permission" would look like)

- Content Partner API: contract → partner token per request (§5.3).
- Data export: contract → S3 credentials, daily full-overwrite NDJSON (§5.3).
- Widget: contract-gated (§5.3 content_partner).
- **Mandatory attribution**: branded JustWatch link beside every
  where-to-watch display, country-specific title URL, "JustWatch" anchor
  (§5.3; yaml `full_path` description). Any future licensed integration must
  implement this in the UI; today the app stores the JW detail URL
  (`justwatch_client.py:944`) and the provider's offer URL (`justwatch.py:560`)
  but implements **no JustWatch branding/attribution** (no frontend requirement
  found; grep §3.4).
- Practical gate: "Currently, JustWatch can only work with bigger partners and
  clients" (§5.6) — a personal project may simply not qualify, in which case
  the licensed route is unavailable and EXCLUDE is the only honest posture.
- Accuracy feedback channel exists (`partner-support@justwatch.com`, §5.3) —
  the documented way to report/resolve date inaccuracies under a contract.

### 10.3 Rights provenance seam (exists, unused by JustWatch)

`DiscoveredContent.rights_provenance` — "Structured rights evidence for
audit/revalidation; never implies availability" (`domain/models.py:51-52`) —
is populated only by `public_domain_archive` (repo grep: 12 hits, none in
JustWatch code). If any JW-derived date is ever promoted, rights provenance
should be recorded here per observation; today there is none.

### 10.4 Freshness obligations before ongoing integration

- Declared local window: 12h (`freshness.py:90`). Upstream reality: feeds
  ingested ~6h (§5.1), partner export daily (§5.3). A departure claim needs
  the **date itself** re-verified within a bound, not just the row re-seen;
  currently only `last_seen` exists (no per-field `observed_at` for the window).
- Design §7.3 already requires excluding stale/unknown evidence from dated
  lists; that rule must apply to JW dates with the same 12h window, and any
  future "as of" caption must use the observation time, never the date's
  supposed precision.
- Cadence documentation drift (§3.5): `docs/CRON.md` has no JustWatch slot;
  the 12h claim rests on policy + seed config, not a verified cron entry.

### 10.5 Bounded live verification already has an authorization mechanism

If the owner later wants the NOT PROVEN items measured (E26, E29-E31), the
repo already contains the safe path — **not used in this research**:

- `AvailabilityExperiment` durable budget/authorization state
  (`availability/models.py:143-151`: authorization_id, manifest_digest,
  database_identity, maximum_attempts).
- `experiment_runner.py:781-785` builds an instrumented JustWatch client with
  injected transport and bounded retries.
- `docs/operations/stage2g_bounded_availability_experiment.md`: fixed compiled-in
  manifest (2 titles × NL, `GetSearchTitles` only), **global ceiling 6 HTTP
  requests**, durable per-request PostgreSQL reservations, 429 fail-fast,
  ambiguous-outcome stop, read-only preflight, advisory locks, no source
  activation, manifest is inert until authorized.

Any future semantics probe must go through this mechanism (or an equivalent
explicit authorization) with **separate owner permission**, per #174's "if
proof would require live access, stop and request separate permission".

### 10.6 Decisions needed (owner / legal)

1. **Permission basis**: accept the risk of continued unofficial collection
   under ToU §7.1/§7.2 (personal, non-commercial, bounded), or cease it, or
   pursue a partner contract. This gates the *existing* source, not just F5.
2. **Presentation gate**: confirm EXCLUDE for JW dates in Leaving Soon until
   the §11 acceptance criteria are met (this memo's recommendation).
3. **Pre-F2 suppression (F-9)**: decide whether JW-sourced windows should keep
   gating `window_open`/`_offer_is_current` while unverified, or be quarantined.
   Either change is production code → separate issue, not this PR.
4. **Attribution policy** if any JW data is displayed (partner route makes it
   mandatory; unofficial route has no grant at all).
5. **Live-probe authorization** (separate permission, §10.5) if the owner wants
   E26/E29-E31 measured rather than left NOT PROVEN.

---

## 11. Recommendation by provider / country / access type

Default per #174: **EXCLUDE unless evidence directly proves the semantics.**
The evidence proves *intended meaning* (§6) but not *reliability*; therefore:

### 11.1 Today (all cells)

| Scope | Verdict | Why |
|---|---|---|
| **Every mapped provider × every country × subscription / free / free_with_ads / addon** — presenting `availableTo` as a departure date | **EXCLUDE** | E26 (web field identity not strictly proven), E29-E31 (renewal/coverage/format unknown), E33 (no warranty), E21 (zero retained non-null samples), E34 (permission unresolved); owner default |
| **rent / buy offers** (e.g. `itunes`/`apple_tv_store`, any market) | **EXCLUDE — permanent for this feature** | storefront window ≠ subscription departure (E23, E7); SPEC excludes rent/buy |
| **fast / cinema / unknown offer types** (any provider, any market) | **EXCLUDE — permanent** | broadcast-slot ends / theatrical windows (E24, E8); `unknown` rows persist dates and must never feed an expiry query |
| **Unmapped providers / unmeasured markets** | **EXCLUDE** | not walkable, never guessed (`justwatch_providers.py:205-208`); no evidence exists or can exist without new measurement |
| **Internal storage** of JW `available_until` (no presentation) | **CONDITIONAL — already happening** | conservative unknown on invalid (E5), non-authoritative (E20), conflict→unknown (E16); but F-9 suppression is live → owner decision 10.6.3 |

No provider/country cell qualifies for USE or CONDITIONAL *presentation*
today. This is not a statement that JustWatch dates are wrong — it is a
statement that "will leave" is unproven, exactly as #174 requires.

### 11.2 Upgrade path (future, per provider+country cell, all required)

A cell may move to **CONDITIONAL** (labelled "provider-reported, as of
<observation>", never bare "leaves") only after:

1. **Authorized live probe** (§10.5, separate permission) samples non-null
   `availableTo` for that provider+country and confirms the web field matches
   the partner `available_to` semantics (closes E26 for that cell).
2. **Format/timezone distribution measured** (datetime vs date-only; offsets)
   and the parser contract pinned or corrected (closes E31; F-3/F-4).
3. **Renewal/retraction observed** over ≥2 refresh cycles on sampled titles,
   with per-field window history or an audit trail added before any claim
   (closes E29; F-5).
4. **Coverage measured** for that provider+country (what fraction of offers
   carry dates); UI labels unknown-coverage, never "nothing leaving"
   (closes E30; F-10).
5. **Owner/legal clearance** of the integration basis + attribution (§10.6).
6. **F2 gates implemented and tested** (§12): source allowlist, offer-type
   allowlist `{subscription, free, free_with_ads, addon}`, `unknown`/rent/buy/
   fast/cinema excluded, reconciled non-null window, `VERIFIED` freshness,
   explicit country, no inference.

**USE** (unqualified departure claim) would additionally require a warranty or
contractual accuracy commitment that no public source offers (E33) — treat as
out of reach for the unofficial route; only a partner contract could change it.

---

## 12. Change impact on F1 / F2 (and the pre-existing read path)

- **F1 (extend tracker `Offer` DTO + `PrimeOffer` TS with window fields):**
  additive, semantics-agnostic, no migration (fields exist,
  `availability/models.py:372-373`). Safe to build. **But** exposing JW-sourced
  `available_until` without an F5 gate would create de-facto departure claims
  through detail pages. Recommendation: F1 either (a) omits windows whose
  reconciled sources include `justwatch` until F5 approval, or (b) carries
  `source`/`evidence` alongside the window so the UI cannot render "leaves"
  for an unapproved source. Option (a) is the conservative default.
- **F2 (`GET /api/leaving-soon/` + service + index):** design §7 rules already
  encode conflict→unknown, stale exclusion, country hard scope, no inference.
  **Add three gates** on top: (1) `source != justwatch` (or an explicit F5
  allowlist) — this memo's EXCLUDE verdict; (2) offer-type allowlist
  `{subscription, free, free_with_ads, addon}` with `unknown` excluded
  (F-1/F-2); (3) never read raw `Offer.available_until` directly — only the
  reconciled, non-conflicted value (design §9.2 already requires this).
  Index proposal (`Offer(available_until)` partial) unchanged.
- **Pre-existing behavior, not introduced by F1/F2 (E17, F-9):**
  `window_open` (`reconciliation.py:266-269`) and `_offer_is_current`
  (`availability_report.py:404-406`) already consume JW dates. An incorrect
  past date can hide a watchable title today. Quarantining JW windows there is
  a **production-code change → separate issue + owner decision** (§10.6.3);
  this PR deliberately changes nothing.
- **F3 (watchlist UI), F4 (per-provider view), F6 (catalogue page):** unchanged
  by this memo except that F4 inherits the same EXCLUDE gate per cell, and the
  P8 coverage measurement (§8) means even approved cells would be sparse —
  the "providers without dates" explainer (design §8.3) stays mandatory.
- **Streaming Availability / Watchmode:** still write no windows (design §2.1);
  nothing in this memo changes their status. (The third-party reefapi docs
  suggest the ecosystem exposes "leaving" feeds; whether the SA API this repo
  uses has an unused expiry field is a separate question, out of #174 scope.)

---

## 13. Acceptance tests

### 13.1 Offline (delivered / available now)

- **Delivered in this PR:** 17 fixture-only pins (§4.3), run green via
  `./scripts/test-backend-isolated.sh availability.tests.test_justwatch_available_to -v 2`
  → `Ran 17 tests … OK`, exit 0, ephemeral PostgreSQL, 2026-11-21.
- **Already retained:** `test_reconciliation_conflicts.py:144-171`
  (WINDOW conflict → unknown); JustWatch country-propagation, integrity,
  coverage, regression suites (none previously touched `availableTo`).
- **When F2 is built (fixture-only):** source-exclusion gate (JW rows never
  listed), offer-type allowlist (rent/buy/fast/cinema/unknown excluded even
  with dates), stale-evidence exclusion, country isolation (NL date never
  leaks into US), conflict→excluded, null→excluded, `last_option` vs
  `leaving_provider` computation.

### 13.2 Live (NOT done; requires separate permission via §10.5)

- Semantics probe: for a bounded sample of mapped provider×country cells,
  capture non-null `availableTo` values; verify format/zone distribution;
  cross-check a subset against the provider's own published expiry notice
  where one exists (e.g. Tubi "leaving soon"), recording mismatches as
  evidence, never auto-repairing.
- Renewal observation: re-probe the same titles across ≥2 refresh cycles;
  record date extensions/retractions and their latency vs the ~6h ingestion.
- Coverage census: fraction of offers carrying dates per provider×country.
- All bounded by the experiment manifest/ceiling mechanism, fail-fast on 429,
  durable reservations, no source activation, no production writes beyond the
  authorized experiment scope.

---

## 14. Source index

### 14.1 Repository (worktree @ `1b9c4f2`; line numbers verified this session)

- `backend/availability/sources/justwatch_client.py` — 1-17 (unofficial
  endpoint), 70, 88-100 (UA), 151, 181-201 (fragment, `availableTo` :192),
  352-384, 451-483, 668-692, 684/709 (bestOnly), 849-853, 903-921 (:919), 944.
- `backend/availability/sources/justwatch.py` — 16-20, 22-31, 53-57, 100-108,
  147, 156, 227-234, 240-340, 457-467, 520-535, 545-572 (:547, :557-559, :564,
  :565, :568), 669-680, 682-695.
- `backend/availability/sources/justwatch_providers.py` — 14-29, 36-132,
  139-164, 171, 177-258, 261-270, 301-309.
- `backend/availability/services/normalize.py` — 39-54, 83-93.
- `backend/availability/domain/models.py` — 51-52, 70-97 (:85-86).
- `backend/availability/domain/reconciliation.py` — 100-101, 171-172.
- `backend/availability/services/refresh.py` — 788, 862-863, 1039-1040.
- `backend/availability/persistence/repositories.py` — 235-351 (:287-288,
  :302-310, :315, :335-350).
- `backend/availability/models.py` — 58-85, 130-137 (Source.enabled),
  143-151 (AvailabilityExperiment), 344-430 (Offer; :371-373), 527-623
  (OfferEvidence).
- `backend/availability/services/reconciliation.py` — 3-7, 15-17, 266-269,
  293-298, 431-458, 552-583 (:560), 663-686.
- `backend/availability/services/identity_policy.py` — 5-9.
- `backend/availability/freshness.py` — 85-92.
- `backend/availability/migrations/0006_seed_aggregator_sources.py` — 39-63,
  65-68, 76-88, 96-108.
- `backend/tracker/models.py` — 273-292 (ExternalProviderMapping).
- `backend/tracker/views.py` — 709-719.
- `backend/tracker/services/justwatch_sync.py` — 1-96 (esp. 79-85), 123.
- `backend/tracker/management/commands/refresh_justwatch_catalogue.py` —
  docstring (bounded partitions, exit codes).
- `backend/tracker/services/availability_reads.py` — 90-122.
- `backend/tracker/services/availability_report.py` — 399-409.
- `backend/availability/tests/aggregator_fixtures.py` — 1-11, 55-74 (:57, :68),
  118-315.
- `backend/availability/tests/test_reconciliation_conflicts.py` — 144-171.
- `backend/availability/tests/test_justwatch_available_to.py` — **new in this
  PR** (17 tests).
- `docs/leaving-soon-discovery.md` — 137, 158-165, 199, 228-235, 588-596.
- `docs/P8_JUSTWATCH_COVERAGE.md` — 13-24, 37-51.
- `docs/audits/stage2c/external_evidence.csv` — header + 14 JustWatch rows.
- `docs/operations/stage2g_bounded_availability_experiment.md` — fixed scope,
  ceilings, reservations, authorization.
- `docs/CRON.md` — no JustWatch slot (grep, 2026-11-21);
  `docs/LEGACY_SCRAPER_RETIREMENT_AUDIT.md:139` (stale CRON pointer).
- GitHub issue #174 (via `gh issue view`, 2026-11-21; no comments).

### 14.2 Published web sources (all accessed 2026-11-21; static docs only)

| Source | URL | Kind |
|---|---|---|
| Streaming Service integration docs | https://apis.justwatch.com/docs/streaming_service/ | **primary (official)** |
| Standard VOD Feed JSON Schema (last modified 15.10.2026 per docs) | https://apis.justwatch.com/docs/static/feed.schema.json | **primary (official)** |
| Content Partner API docs | https://apis.justwatch.com/docs/api/ | **primary (official)** |
| Partner API OpenAPI spec (65,059 bytes; sha256 `3fb1bd2b5789cdc80b2633cb49495ccdf71d6a5189c89c677ad4f8c9b60cb468`; `offers.Offer.available_to` lines 131-134, required 163-167) | https://apis.justwatch.com/docs/static/jw-partner-api.yaml | **primary (official)** |
| Content Partner overview | https://apis.justwatch.com/docs/content_partner/ | **primary (official)** |
| Data export docs | https://apis.justwatch.com/docs/data_export/ | **primary (official)** |
| Consumer Terms of Use (updated 5 May 2026) | https://support.justwatch.com/article/just-watchs-terms-of-use | **primary (official)** |
| JustWatch Pro features (updated 21 Oct 2026) | https://support.justwatch.com/article/what-do-i-get-with-justwatch-pro | **primary (official)** |
| Partner Dashboard ToU (licensing model context) | https://partners.justwatch.com/legal/termsofuse | primary (official, partner scope) |
| Unofficial wrapper disclaimer (JustWatch's stated position) | https://github.com/dewey/JustWatch-API ; https://github.com/dawoudt/JustWatchAPI | third-party hosted statement |
| filmigo reverse-engineered types (title-level `availableTo` = countdown + `availableToDate` yyyy-mm-dd + package) | https://raw.githubusercontent.com/Jisin0/filmigo/v0.2.3/justwatch/types.go ; https://pkg.go.dev/github.com/Jisin0/filmigo/justwatch | unofficial corroboration |
| simple-justwatch-python-api `_parse_offer` | https://raw.githubusercontent.com/electronic-mango/simple-justwatch-python-api/master/src/simplejustwatchapi/query.py | unofficial corroboration |
| simple-justwatch-js ("undocumented and may change at any time") | https://raw.githubusercontent.com/anthonyfranc/simple-justwatch-js/main/index.js | unofficial corroboration |
| reefapi "Streaming Availability" reseller docs (`page_type=leaving`) | https://reefapi.com/docs/streaming.md | third-party, not JustWatch |
| JustWatch staff reply (green=Coming Soon, red=Leaving Soon) | https://reddit.com/r/justwatchapp/comments/1umfgb0/ | anecdote, staff-authored |
| Tubi expiry-mismatch question | https://reddit.com/r/TubiTV/comments/1rsdcjy/ | anecdote only |

---

## 15. Verification log (this research session)

- Worktree clean at start; branch `research/174-justwatch-availableto-semantics`
  @ `1b9c4f2` (= origin/master). `git status` before commit: only the new test
  file untracked; after writing this memo, exactly two new files.
- Issue #174 read in full via `gh issue view 174 --json …` (body + comments;
  zero comments). Owner defaults quoted in the header.
- Repo-wide greps: `availableTo|available_to` → 14 hits (4 files), all cited;
  `available_until` → 134 hits across the write/read/reconcile path, traced;
  frontend `available_until|availableUntil` → 0 hits; `docs/CRON.md`
  justwatch → 0 hits.
- Fixture corpus counted programmatically by the new corpus test: **24 offers,
  all `availableTo: null`** (test asserts the exact count and nullness).
- Tests: `./scripts/test-backend-isolated.sh availability.tests.test_justwatch_available_to -v 2`
  → **Ran 17 tests in 0.002s — OK**, exit 0 (ephemeral PostgreSQL project,
  destroyed on exit; no production database touched).
- Web: only the static documentation URLs in §14.2 were fetched (GET). The
  OpenAPI YAML was downloaded once via curl to scratch and hashed (§14.2).
  **No GraphQL operation, no partner API call, no title/offer/search page,
  no scraping, no login/cookies/CAPTCHA, no credentials, no source activation,
  no migrations, no production writes.**
- Nothing merged, deployed, or changed in any other worktree/branch/service.
