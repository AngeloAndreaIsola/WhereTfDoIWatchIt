# Leaving Soon — country-aware discovery design (issue #148)

Status: **research/design only**. Nothing here is implemented; every "future
plan" section describes proposed work, not delivered code. All file:line
citations are against this worktree.

Related issues named in #148: #97 (accuracy), #101 (season granularity),
#103 (entitlements), #119 (homepage eligibility). `docs/PLAN.md:472` already
lists "Availability history / leaving-soon predictions" as deferred scope —
this document is the discovery brief for that item.

---

## 1. Existing data structures (what the repo actually stores)

### 1.1 Canonical offer model — `availability.Offer`

`backend/availability/models.py:344-430`. The load-bearing fields:

- `available_from` / `available_until` — `models.py:372-373`, commented
  "Provider-stated lifecycle, distinct from our observation fields below."
  These are `DateTimeField(null=True, blank=True)`.
- `currently_available` — `models.py:379`.
- `first_seen` / `last_seen` — `models.py:377-378` (observation timestamps,
  never provider dates).
- `observed_at`, `source_record_id`, `source_version` — `models.py:381-383`.
- `provider` FK → `tracker.Provider`, `country` FK → `availability.Country`
  (`models.py:345-349`); identity includes `content_type`, `tmdb_id`,
  `season_number`, `episode_number`, `offer_type`, `provider_content_id`
  (`models.py:356-363`, `build_offer_identity` `models.py:58-85`).

So the schema **already has** a provider-declared end-of-availability field
at movie, series, season and episode granularity. No migration is needed to
store it; it is already populated by several sources (§2).

### 1.2 Reconciliation layer

`backend/availability/services/reconciliation.py`:

- `_observe` computes `window_open` from the raw row:
  `available_from <= now < available_until`
  (`reconciliation.py:266-269`) and copies both fields onto the observation
  (`reconciliation.py:293-294`).
- `_window_values` merges observations of the same effective offer: agreeing
  values are kept; **disagreement yields `None` plus a `WINDOW` conflict**
  (`reconciliation.py:552-583`). This is exactly the semantics a "leaving
  soon" feature needs — a contested date is already suppressed to unknown.
- `ReconciledOffer` carries `available_from`, `available_until`,
  `currently_available`, `evidence`, `last_verified_at`,
  `last_seen_available_at` (`reconciliation.py:431-458`;
  DTO fields in `backend/availability/domain/reconciliation.py:100-101,
  171-172`).

### 1.3 Read path — where the window is dropped today

- `backend/tracker/services/availability_reads.py:105-122`
  (`_tracker_offer`) projects `ReconciledOffer` onto the tracker `Offer`
  DTO — and **does not copy `available_from`/`available_until`**. The
  tracker `Offer` dataclass (`backend/tracker/services/offers.py:29-56`)
  has no window fields at all.
- The frontend type mirrors that: `PrimeOffer` in
  `frontend/lib/api.ts:61-109` has `last_seen_at` and
  `availability_evidence` but no expiry field.
- `_offer_is_current` in
  `backend/tracker/services/availability_report.py:399-418` already treats a
  past `available_until` as not-current for report purposes — so the
  *concept* is honoured on one internal path even though it never reaches
  the API response.

### 1.4 Legacy / secondary structures

- `tracker.Availability` (`backend/tracker/models.py:111-134`): legacy
  per-(movie, region) row with `is_available`, `offer_type`,
  `last_checked_at`. **No expiry field.** Region = provider+country pair
  (`Region`, `models.py:98-108`).
- `tracker.ScrapedContentMapping` (`models.py:432-480`): scraper write/review
  table; `is_available` boolean + `last_scraped_at`; **no expiry**. Reads now
  go through canonical `ProviderContent`/`ContentMapping`
  (`backend/tracker/services/scraped_offers.py:37-87`;
  `backend/availability/services/catalogue_query.py:212-232`).
- `availability.ProviderContent` (`models.py:235-341`): provider catalogue
  observation; `currently_available`, `first_seen`, `last_seen`,
  `release_date`. **No expiry/removal field** — a catalogue entry leaving a
  provider's listing is only observable as `currently_available=False`
  (and only authoritative syncs may set that; see §3).
- `availability.ProviderCataloguePackage` (`models.py:370-429`): per-country
  JustWatch package catalogue; explicitly "never an availability signal"
  (`models.py:376-377`).
- `tracker.DiscoveredCatalogueRecord` (`models.py:513-554`): raw provider
  listing observations with `last_seen_at`; **no expiry**.

### 1.5 Frontend surfaces that would host the feature

- Watchlist: `frontend/app/watchlist/page.tsx` — grid of `MovieCard`-style
  rows fed by `GET /api/watchlist/report/`
  (`backend/tracker/views.py:1541-1661`), which already evaluates watchability
  per title via `apply_composable_filters`
  (`backend/tracker/services/filtering.py:64-420`) and returns
  `watchability`, `available_countries`, `best_service`, `is_watched` per row
  (`views.py:1612-1622`).
- Catalog: `frontend/app/catalog/page.tsx` — unified official+unofficial
  catalogue via `GET /api/catalog/unified/`
  (`frontend/lib/api.ts:362-374`, `UnifiedCatalogItem` `api.ts:311-350`).
- Detail pages: `GET /api/prime/movie/<id>/` and `/api/prime/series/<id>/`
  build offers in `_build_prime_offers`
  (`backend/tracker/views.py:2879-2942`) and share the access verdict via
  `tracker.services.access.resolve_title`.
- Country selection: `_active_country` (`views.py:2795-2810`) — query param
  `current_country`/`country`, else saved `Preference.current_country`,
  else `"US"`. `CountrySelect` component exists
  (`frontend/components/CountrySelect.tsx`).
- Subscriptions/entitlements: `Subscription.access_mode`
  (`models.py:657-672`), resolved by `build_user_access`
  (`backend/tracker/services/access.py:334-401`) and `UserAccess.entitles`
  (`access.py:295-309`). Qualifying access types:
  `{subscription, free, free_with_ads, addon}`
  (`backend/tracker/services/watchability.py:49`).

---

## 2. Do trustworthy provider-specific expiry dates exist?

**Verdict: partially — a real provider-declared field exists and is
populated by ~6 of ~14 sources, but coverage is thin exactly where it
matters (major SVOD catalogues), and the semantics of several fields are
"window end" rather than proven "removed from catalogue".**

### 2.1 Provider-declared dates (evidence in the write path)

| Source | Field written to `Offer.available_until` | Evidence |
|---|---|---|
| PBS | asset `expireDate` / group expiry | `backend/availability/sources/pbs.py:503,534,566`; `premiereDate`→`available_from` (`pbs.py:54`) |
| BBC iPlayer | `isoEndTime` of programme version; episode end | `backend/availability/sources/bbc_iplayer.py:431-432,522` |
| Tubi | `availability_ends` | `backend/availability/sources/tubi.py:718,750` |
| RaiPlay | `availabilityEnds` (movies only) | `backend/availability/sources/rai_play.py:884-885,921-922` |
| Mediaset Infinity | `expirationDate` | `backend/availability/sources/mediaset_infinity.py:434` |
| JustWatch (aggregator) | `availableTo` on offer node | `backend/availability/sources/justwatch.py:564`; parsed at `justwatch_client.py:919` |

Sources that **explicitly never write** `available_until` (stated in their
docstrings): Prime Video (`prime_video.py:116-117`), ABC iview
(`abc_iview.py:55`), CBC Gem (`cbc_gem.py:62`), Pluto TV (`pluto_tv.py:55`),
YouTube (`youtube.py:359-360`), public domain archive
(`public_domain_archive.py:362-363`). **Not proven** for
`streaming_availability.py` and `watchmode.py`: neither file contains any
`available_until`/`available_from` reference (grep over
`backend/availability/sources/`), so they persist `None` — *inferred from
absence of code, not from a docstring*.

### 2.2 Semantics caveats — "provider-declared" ≠ "leaving the service"

- For PBS, `expireDate` is the end of a free streaming window — genuinely a
  removal date for an AVOD asset. High confidence.
- For BBC iPlayer, `isoEndTime` is the end of the *broadcast/availability
  window* of a version — plausibly "leaves iPlayer", medium confidence.
- For RaiPlay/Tubi `availabilityEnds`, these look like licence windows —
  medium confidence.
- For Mediaset `expirationDate` — medium confidence.
- For JustWatch `availableTo` — **Not proven.** The field exists in the
  client parser and fixtures (`backend/availability/tests/aggregator_fixtures.py:68`)
  but no repository document defines what JustWatch means by it (end of a
  rental window? catalogue exit? promo end?). Treating it as a removal date
  requires a verification step before any user-facing claim.
- None of these sources covers Netflix/Disney+/Max-class SVOD expiry;
  those offers arrive via `streaming_availability`, `justwatch`, or
  `watchmode`, of which only JustWatch emits a date at all.

### 2.3 Inferred dates — do they exist?

**No.** The system deliberately refuses to infer removal from absence:

- `OfferRepository.deactivate_unseen` only accepts `SyncStatus.SUCCEEDED`
  authoritative runs (`backend/availability/models.py:38-43` comment on
  `SyncStatus.PARTIAL`/`FAILED`), and catalogue sources are mostly
  `authoritative=False` (e.g. RaiPlay `docs/PLAN.md:28`, CBC Gem
  `availability/models.py:257-258`).
- `ProviderContent.currently_available` "is never changed merely because a
  non-authoritative (or incremental) refresh did not return an entry"
  (`availability/models.py:252-258`).
- The only "inferred leaving" signal that exists is observational:
  `last_seen`/`last_verified_at` timestamps and `RefreshRun`/`SyncRun`
  history — i.e. *when we last saw it*, never *when it leaves*.

Consequence for design: any "leaving soon" list built on inferred absence
would fabricate dates. The feature must be driven **only** by
`Offer.available_until` (provider-declared) — which is precisely what the
reconciliation layer already preserves (§1.2).

---

## 3. Source coverage and missing evidence

Coverage is reported from repository declarations only; no live data was
touched. "Expiry evidence" = does the source ever write
`Offer.available_until` (§2.1).

| Source (slug) | Role | Countries | Content granularity | Expiry evidence |
|---|---|---|---|---|
| streaming_availability | metered aggregator, title-targeted, 100 req/day (`availability/freshness.py:60-62`) | all (API param) | movie/series/season/episode offers | **None — Not proven** (no field writes) |
| justwatch | broad aggregator, catalogue walk | per-country partitions (`ProviderCataloguePackage.country`, `models.py:380`; `ExternalProviderMapping.country`, `models.py:301-307`) | movie/series/season/episode (`justwatch.py:553-558`) | `availableTo` → `available_until`, semantics unverified |
| watchmode | metered aggregator | — | title-level | **None — Not proven** |
| prime_video | catalogue collector | pinned NL (`docs/PLAN.md:27`) | MOVIE/SERIES/SEASON/EPISODE | None (docstring `prime_video.py:116`) |
| rai_play | catalogue collector | IT | MOVIE/SERIES | `availabilityEnds`, movies only (`rai_play.py:884`) |
| cbc_gem | catalogue collector, 6h cadence | CA | series/episodes | None (`cbc_gem.py:62`) |
| abc_iview | catalogue collector | AU | series/episodes | None (`abc_iview.py:55`) |
| bbc_iplayer | catalogue collector | GB | programmes/episodes | `isoEndTime`, episode end |
| tubi | catalogue collector | region-derived (`docs/PLAN.md:30`) | movies/episodes | `availability_ends` |
| pluto_tv | catalogue collector | egress-IP region | movies/shows | None (`pluto_tv.py:55`) |
| mediaset_infinity | title-targeted | IT | movies | `expirationDate` |
| pbs | catalogue collector | US | shows/assets | `expireDate` |
| youtube | title-targeted web | US + 5 free-program countries (`docs/PLAN.md:29`) | videos | None (`youtube.py:359`) |
| public_domain_archive | catalogue | — | movies | None (`public_domain_archive.py:362`) |
| legacy_scraper / unofficial (StreamingCommunity, CineBlog01, AltaDefinizione…) | scraper BVOD | IT-centric (`docs/CAPABILITY_MATRIX.md`) | movies/series | None — `ProviderContent` has no expiry field at all |

Missing evidence, stated plainly:

- **No source declares catalogue-removal dates for the major SVOD
  providers** (Netflix, Disney+, Max, Paramount+, Apple TV+…). Their offers
  come from aggregators; of those, only JustWatch emits a date and its
  meaning is unverified (§2.2).
- **Series granularity**: `Offer` supports `season_number`/`episode_number`
  (`availability/models.py:358-359`), and JustWatch/Prime/RaiPlay emit
  season/episode rows — but expiry evidence exists only at whatever
  granularity the source wrote it. RaiPlay writes windows for movies only
  (`rai_play.py:884` `if is_movie`). **Per-season "leaving" is partially
  supported by schema, thinly supported by evidence.**
- **Country coverage of expiry**: expiry-capable sources are concentrated in
  GB (BBC), US (PBS, Tubi partially), IT (RaiPlay, Mediaset), plus the
  unverified JustWatch partitions. NL — the apparent primary market
  (`prime_video` pinned NL; `ExternalProviderMapping` docstring example is
  NL) — has **no first-party expiry source**; only JustWatch NL partitions
  *might* carry `availableTo`.
- **Staleness bounds**: per-source freshness windows are declared in
  `backend/availability/freshness.py:68-92` (6h–24h typical, 12h legacy
  scraper). An expiry date observed longer ago than the window must be
  treated as stale evidence (§7).

---

## 4. Three UX concepts compared

### (a) Watchlist urgency — "Leaving soon" section on `/watchlist`

Group watchlisted titles by days-to-expiry (7/14/30) within the active
country and entitled providers.

- **Pros**: smallest surface; reuses `watchlist_report`'s existing bulk
  canonical read (`filtering.py:283-296` already batches
  `get_offer_evidence_bulk`); urgency is personally relevant; empty state is
  natural ("nothing on your list is leaving soon"); aligns with the issue's
  stated primary use ("especially titles on their watchlist").
- **Cons**: only helps titles already tracked; discovery value limited;
  inherits watchlist-report cost shape (fine — already paginated/filtered).

### (b) Full-catalogue discovery page

Country/provider filters + date-grouped timeline over the whole catalogue.

- **Pros**: maximal discovery; matches "what's leaving everywhere" browsing.
- **Cons**: the catalogue is huge (thousands of `ProviderContent` rows per
  provider); almost all rows will have `available_until=None`, so the page
  is ~empty or requires scanning every offer row per request; needs a new
  index + probably a materialised/denormalised expiry list to be fast;
  highest build cost for the least certain data (§3 shows expiry coverage
  is thin). Feels like a catalogue browser, not an urgency feature.

### (c) Per-provider view — "Leaving Netflix NL"

One provider+country scope, watchlist matches first.

- **Pros**: precise mental model ("what is leaving the service I pay
  for"); naturally bounded query (`provider`+`country`+`available_until`
  range); good empty states per provider; works even with sparse data
  because scope is small.
- **Cons**: requires the user to pick a provider — a provider that has no
  expiry data shows an empty page that could be misread as "nothing is
  leaving" rather than "this provider reports no dates" (**must be
  labelled as unknown-coverage, not empty**); less serendipitous than (a);
  per-provider × per-country matrix explodes combinatorially.

**Trade-off summary**: (a) is cheapest and most honest about sparse data;
(c) is the best *secondary* view once (a) proves the data is trustworthy;
(b) is the most expensive and the most likely to over-promise given §3
coverage.

---

## 5. Recommended MVP — exactly one

**Recommend concept (a): a "Leaving soon" section on the watchlist page,
scoped to the active country and entitled providers, backed by a dedicated
endpoint.** Do not build (b) or (c) now.

Rationale:

1. The data that exists (`Offer.available_until`, reconciled with conflict
   → unknown semantics) maps 1:1 onto "watchlist titles leaving a service
   you have" — the issue's primary scenario.
2. Reuses the exact bulk-read and entitlement machinery the watchlist
   report already runs (`filtering.py:283-330`), so entitlement/country/
   language rules cannot drift from the badge.
3. Its empty/unknown states are honest by construction: a provider with no
   expiry evidence contributes nothing, and the section can say "no
   confirmed departures" rather than implying "nothing is leaving".
4. (c) can later be the same endpoint with `provider=` preset; (b) is a
   separate, larger decision gated on expiry coverage improving (§3).

Explicit non-goals for MVP: no inferred/absence-based departures, no
full-catalogue scan, no notifications, no per-episode granularity UI
beyond what the API returns.

---

## 6. Country and provider filtering rules

**Country scope — one market per query, never mixed:**

- The page's country is the same `_active_country` resolution used by
  watchlist/catalog/home (`views.py:2795-2810`): explicit
  `current_country`/`country` param → saved `Preference.current_country`
  → `"US"` fallback. The Leaving Soon section inherits this; there is no
  second country picker.
- Each leaving offer is keyed by its own `Offer.country`
  (`availability/models.py:348`). A row must display the country it
  belongs to; a title leaving Netflix **NL** and remaining on Netflix **US**
  are two different facts and must not be merged into one badge.
- Reach semantics reuse `CountryReach.locality`
  (`access.py:413-434`): `local` (== current country), `vpn`
  (`vpn_enabled` + `candidate_countries`, `access.py:437-450`),
  `anywhere` (explicit `territorial_scope=worldwide`,
  `offers.py:14-26`), `unknown`. Leaving-soon rows carry a `locality`
  field so the UI can mark VPN-reachable departures differently — never
  silently count a foreign departure as "yours".
- `territorial_scope=unknown` never upgrades to global
  (`scraped_offers.py:19-21` documents the same rule for scraped offers).

**Provider scope — subscriptions you already own:**

- Default provider set = providers whose offers the user is **entitled**
  to: `Subscription.access_mode in {included, addon, free}` via
  `build_user_access` (`access.py:334-401`), plus the auto-counted
  BVOD/AVOD/FAST categories (`access.py:375-400`). `access_mode="none"`
  grants nothing (`access.py:353-354`); `rent_buy` accounts do **not**
  count for "leaving your subscription" (`access.py:307-308`).
- Offer access types are classified by the shared resolver
  (`access.py:66-79`, `INCLUDED_ACCESS_TYPES`); the section only lists
  offers whose `access_type` is in `{subscription, addon, free,
  free_with_ads}` — rent/buy departures are out of scope by SPEC.
- Provider identity uses canonical slugs + `SAFE_PROVIDER_ALIASES`
  (`access.py:166-268`) and add-on rules (`Provider.is_addon`,
  `models.py:229-246`): an add-on channel departure requires the base
  platform too — surface `required_base` if shown.
- A "show all providers" toggle may widen scope, but the default must be
  entitled-only; the issue asks to avoid mixing incompatible regions and
  honour owned subscriptions — this is the mechanism that already exists.

---

## 7. Missing dates, stale evidence, partial removals

Rules the feature must encode (all derivable from existing machinery):

1. **Unknown expiry is never "leaving soon".** Only rows with a non-null,
   reconciled `available_until` in the future and within the horizon
   (7/14/30-day buckets) may appear. `available_until=None` → excluded,
   silently; the section may report a count of "checked, no date" only if
   product wants it — never per-title "leaving?".
2. **Window conflicts are already suppressed.** When sources disagree on
   `available_until`, reconciliation stores `None` + a `WINDOW` conflict
   (`reconciliation.py:559-573`). Those rows simply don't qualify — the
   conflict remains inspectable via the existing report surfaces.
3. **Stale evidence**: an expiry observed longer ago than the source's
   `SOURCE_FRESHNESS` window (`freshness.py:68-92`) should either refresh
   first or be labelled. MVP recommendation: **exclude offers whose
   evidence is `stale`/`unknown` from the dated list** (same split as
   `availability_reads.py:90-102`), because a stale "leaves Friday" claim is
   worse than no claim. `last_seen_at`/`last_verified_at` on each row let
   the UI say "as of <date>".
4. **Partial provider removal**: one offer leaving ≠ title unavailable.
   Each row must carry `remaining_accessible_offers` (count of other
   currently-accessible offers for that title+country after hypothetically
   removing the expiring one) so the UI can say "leaves Netflix NL — still
   on Disney+ NL" vs "last option expiring". This is computable from the
   same reconciled offer set the watchlist report already loads.
5. **"Leaves this provider" ≠ "unavailable everywhere"** — distinct labels:
   - `leaving_provider` — dated departure from one provider/country.
   - `last_option` — variant when no other entitled offer remains.
   - Never render a departure as "will be unavailable" unless the title has
     no other accessible offer *and* no `last_known` evidence
     (`watchability.py:299-322` semantics).
6. **Expired-but-still-listed**: `available_until <= now` already makes
   `_offer_is_current` false (`availability_report.py:404-406`) and
   `window_open` false (`reconciliation.py:266-269`) — such rows are
   already excluded from current offers; they belong to `last_known`
   history, not to "leaving soon".

---

## 8. Wireframes / mockups — recommended MVP

### 8.1 Desktop — new section above the watchlist grid (`/watchlist`)

```
┌──────────────────────────────────────────────────────────────────┐
│ Wishlist / Watchlist                                             │
│ All titles you want to watch…                                    │
├──────────────────────────────────────────────────────────────────┤
│ Availability                          [ Refresh all ]            │
│ …existing AvailabilityStatusPanel…                               │
├──────────────────────────────────────────────────────────────────┤
│ ⏳ Leaving soon — Netherlands (NL)          [Your services ▾]     │
│ Confirmed departure dates from your subscriptions.               │
│                                                                  │
│ ── Next 7 days ─────────────────────────────────────────────     │
│ ┌────┐  Title (2024)                    Netflix NL · leaves Fri │
│ │img │  🟢 Watchable here               Subscription · 4K       │
│ └────┘  "Last option" badge if applicable                       │
│                                                                  │
│ ── 8–14 days ──────────────────────────────────────────────     │
│ ┌────┐  Other Title (2021)              BBC iPlayer · leaves …  │
│ │img │  🟡 Watchable with VPN           Free · window end       │
│ └────┘                                                        │
│                                                                  │
│ ── 15–30 days ─────────────────────────────────────────────     │
│ (rows…)                                                          │
│                                                                  │
│ ℹ️ Only providers that report departure dates are shown.         │
│    Netflix, Disney+ and others don't publish them — absence     │
│    here does not mean nothing is leaving.          [Learn more] │
└──────────────────────────────────────────────────────────────────┘
```

Each row: poster thumb, title+year, provider display name + country flag,
"leaves {date}" (absolute, never relative-only), access-type chip
(Subscription / Free with ads / Add-on), evidence caption
"as of {last_seen}", and — when applicable — "Still on {other provider}"
or a warning "Last way to watch this for you".

### 8.2 Mobile — same section collapses to a horizontal-card list

```
┌─────────────────────────────┐
│ ⏳ Leaving soon · NL         │
│ Next 7 days                  │
│ ┌───────┐ ┌───────┐          │
│ │ poster│ │ poster│  → swipe │
│ │ Title │ │Title2 │          │
│ │Netflix│ │iPlayer│          │
│ │ Fri 21│ │ Nov 28│          │
│ └───────┘ └───────┘          │
│ ℹ️ Providers without dates   │
│    aren't listed.            │
└─────────────────────────────┘
```

### 8.3 Empty / unknown / stale states (accessibility-required)

- **Empty (confirmed none)**: "Nothing on your watchlist has a confirmed
  departure date in the next 30 days." + the "providers don't all publish
  dates" explainer. `role="status"`, not an error.
- **Unknown coverage (no expiry-capable provider entitled)**: "None of
  your services report departure dates yet." — distinct copy from empty;
  this is the common case today (§3).
- **Stale evidence**: rows never appear; if the whole section's newest
  evidence is older than the source window, show "Departure data may be
  out of date — last checked {date}" with the existing refresh affordance.
- **Loading**: skeleton rows matching card dimensions (same pattern as
  `page.tsx:444-447` pulse block).
- Screen-reader text: each card announces "{title}, leaves {provider} in
  {country} on {date}, {access label}".

---

## 9. Backend/API contract and future implementation plan

> **This entire section is a proposal. No code exists for it.**

### 9.1 Data already available — no schema change required for MVP

`Offer.available_until`, `available_from`, `last_seen`,
`currently_available`, `provider`, `country`, `offer_type`,
`season_number`/`episode_number` all exist (`availability/models.py:344-430`)
and reach `ReconciledOffer` (`reconciliation.py:431-458`). The gap is only
at the read boundary: `_tracker_offer` drops the window fields
(`availability_reads.py:105-122`) and the tracker `Offer`/TS `PrimeOffer`
types lack them (`offers.py:29-56`, `api.ts:61-109`).

### 9.2 Proposed endpoint

```
GET /api/leaving-soon/?days=30&country=NL&scope=entitled&provider=<slug optional>
```

Response (additive, mirrors watchlist-report row conventions):

```json
{
  "country": "NL",
  "horizon_days": 30,
  "generated_at": "…",
  "groups": [
    {"bucket": "7d", "items": [ … ]},
    {"bucket": "14d", "items": [ … ]},
    {"bucket": "30d", "items": [ … ]}
  ],
  "coverage": {
    "providers_with_dates": ["bbc_iplayer", "tubi"],
    "providers_without_dates": ["netflix_via_aggregator", "…"],
    "note": "providers without date evidence are never listed as empty"
  },
  "item": {
    "tmdb_id": 550, "content_type": "movie",
    "title": "…", "year": 1999, "poster_path": "…",
    "is_in_watchlist": true,
    "leaving_offers": [{
      "provider": "rai_play", "provider_display": "RaiPlay",
      "country": "IT", "locality": "vpn",
      "access_type": "free_with_ads",
      "offer_type": "free_with_ads",
      "available_until": "2026-12-01T00:00:00Z",
      "available_from": null,
      "evidence": "verified",
      "last_seen_at": "2026-11-20T…",
      "season_number": null, "episode_number": null,
      "is_entitled": true, "is_accessible": false,
      "requires_payment": false,
      "remaining_offers": 2,
      "departure_kind": "leaving_provider"
    }]
  }
}
```

Contract rules:

- `available_until` is emitted **only** when reconciliation produced a
  non-conflicted value (i.e. `ReconciledOffer.available_until` non-null).
- `evidence` ∈ `{verified, stale, unknown}` reuses
  `AvailabilityEvidence` (`domain/enums.py:81-102`); MVP lists `verified`
  only and reports counts for the others in `coverage`.
- `departure_kind`: `leaving_provider` | `last_option` (computed, §7.4).
- `country` param is a hard scope: offers in other countries are never
  merged; `locality` tells the UI reach.
- Read-only, local DB only — same guarantee as
  `AvailabilityQueryService` (`query.py:1-9`). No network, no refresh
  trigger (refresh stays on the existing queue).

### 9.3 Implementation sketch (future)

1. **Read service** `tracker/services/leaving_soon.py`: for the watchlist
   title set (or `provider=` scope for the later per-provider view), reuse
   `get_offer_evidence_bulk`-style batched reconciliation
   (`availability_reads.py:65-87`, `query.py:176-204`), then filter on
   `ReconciledOffer.available_until` — requires extending `_tracker_offer`
   / the tracker `Offer` DTO with the two window fields (additive).
2. **Query shape**: `Offer.objects.filter(available_until__range=(now,
   now+horizon), currently_available=True)` joined to `Movie` +
   `WatchlistItem`, then reconciled per title. Watchlist scope keeps this
   small; **add index** `Offer(available_until)` (partial on
   `currently_available`) — one small migration, the only schema change
   contemplated.
3. **Entitlement/country**: run each candidate through the existing
   `classify_offer`/`resolve_title` path (`access.py`) so badges match the
   watchlist exactly.
4. **Frontend**: new section component on `watchlist/page.tsx` +
   `fetchLeavingSoon` in `lib/api.ts`; i18n keys alongside existing
   `nav.*`/status strings.
5. **Performance**: bounded by watchlist size (~hundreds of titles) ×
   offers per title; the bulk reconciler already exists to avoid N+1.
   `days` capped (≤90). Cacheable per (user, country, day) if needed later.
6. **Test plan**:
   - Unit: window merge/conflict already tested in reconciliation tests —
     add cases asserting disputed `available_until` → excluded.
   - Source parser tests: fixtures exist per source
     (`backend/availability/tests/fixtures/`); assert which write
     `available_until` and which write `None`.
   - Endpoint tests: entitled vs non-entitled provider, VPN locality,
     `last_option` vs `leaving_provider`, stale-evidence exclusion,
     empty/unknown states, country isolation (NL row never leaks into US
     query).
   - Frontend: component tests for the three states + a11y labels.
7. **Follow-up issues to file after owner sign-off**:
   - F1: extend tracker `Offer` DTO + `PrimeOffer` TS type with
     `available_from`/`available_until` (also benefits detail pages).
   - F2: `GET /api/leaving-soon/` endpoint + `leaving_soon` service + index.
   - F3: watchlist UI section + states.
   - F4: per-provider view (`provider=` param + dedicated page) — only
     after F1–F3 prove data quality.
   - F5: JustWatch `availableTo` semantics verification (fixture/docs +
     owner decision whether it may be shown as a departure date).
   - F6: full-catalogue discovery page — deferred pending expiry coverage
     (§3); revisit after F1–F5.

### 9.4 Open decisions requiring owner input

1. Whether JustWatch `availableTo` may be presented as a departure date
   before its semantics are verified — recommend **no** until F5.
2. Whether "leaving soon" should include `free_with_ads`/AVOD departures
   (recommended: yes — they are owned-access types) or SVOD-only.
3. Whether stale-but-dated offers may appear with an "as of" caveat
   (recommended: no for MVP).
4. Default horizon (30 days proposed) and whether 7/14/30 grouping or a
   flat date-sorted list is preferred.
5. Whether the feature should live only on `/watchlist` or also surface a
   count badge on `/catalog`/home (out of MVP scope either way).

---

## Evidence index (files inspected)

- `backend/tracker/models.py` (Movie, Region, Availability, LanguageInfo,
  Provider, ExternalProviderMapping, ProviderCataloguePackage,
  ScrapedContentMapping, DiscoveredCatalogueRecord, Subscription,
  Preference, WatchlistItem, RefreshRequest ladder)
- `backend/availability/models.py` (Country, Source, SyncRun, ProviderContent,
  Offer, OfferPrice, SyncStatus rules)
- `backend/availability/domain/enums.py`, `domain/reconciliation.py`
- `backend/availability/services/reconciliation.py`, `query.py`,
  `catalogue_query.py`, `freshness.py`
- `backend/availability/sources/{justwatch,justwatch_client,rai_play,
  bbc_iplayer,mediaset_infinity,tubi,pbs,prime_video,abc_iview,cbc_gem,
  pluto_tv,youtube,public_domain_archive}.py` (expiry write/no-write evidence)
- `backend/tracker/services/{availability_reads,offers,watchability,access,
  filtering,scraped_offers,availability_report}.py`
- `backend/tracker/views.py` (`watchlist_report` 1541, `_active_country`
  2795, `_build_prime_offers` 2879, `_prime_detail_response` 2945)
- `backend/tracker/urls.py`, `frontend/lib/api.ts`,
  `frontend/app/watchlist/page.tsx`, `frontend/app/catalog/page.tsx`,
  `frontend/components/{AppNav,CountrySelect,OfferList}.tsx`
- `docs/{PLAN,CAPABILITY_MATRIX,API}.md`, `docs/sources/*` (coverage claims)
- GitHub issue #148 (via `gh issue view`)
