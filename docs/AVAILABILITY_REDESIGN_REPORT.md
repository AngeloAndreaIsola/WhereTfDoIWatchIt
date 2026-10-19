# Availability Redesign — Final Report

> **Status:** complete (working tree, uncommitted)
> **Scope:** the seven-part availability follow-up — detail/watchlist agreement,
> the muted-offer contract, the catalogue completion, the access-type filter, and
> the 31-service Netherlands acceptance list
> **Branch state:** all changes sit in the git working tree; nothing is committed.

This report closes the loop on the seven-part follow-up: the two reported bugs
and their root causes, the muted-offer contract, the completed catalogue, the
access-type filter, the executable 31-service Netherlands acceptance list, the
test evidence behind all of it, and the honest limits of the result.

---

## 1. Summary

Two concrete bugs started this follow-up, and both were the same shape: **two
layers answering the same question differently.** Movie 194 showed "available"
with an empty offer list because the offer pipeline silently dropped unowned
services; movie 615457 read 🟢 on its detail page and 🟡 in the watchlist because
the list surfaces evaluated a hard-coded `"US"` while the detail page used the
user's saved country. Both are fixed and guarded by regression tests.

On top of that, the catalogue was completed from the live JustWatch endpoint:
**23 further NL services** were mapped (Videoland, Viaplay, SkyShowtime, Canal+,
NLZIET, Film1, Hayu, Ziggo TV, NPO Plus, Picl, Sooner, IFFR Unleashed, Pathé
Thuis, Apple TV Store, Curiosity Stream, GuideDoc, DOCSVILLE, Magellan TV,
BroadwayHD, WOW Presents Plus, JustWatch TV, Filmzie, South Park; Netflix Kids
aliased to Netflix), and discovered-but-disabled services are now visible and
selectable in the service-access manager instead of being hidden.

The task also specified a **minimum set of 31 services that must be covered for
the Netherlands**. That list is now an **executable test**
(`backend/tracker/tests/test_nl_catalogue_acceptance.py`) rather than prose, so a
future rename or removal that silently drops a service fails the suite instead of
quietly shrinking what the app can cover.

The acceptance test surfaced **one further fix**: `rakuten_tv` (JustWatch's
pre-rebrand id `wuaki`) had a package→slug mapping but no measured NL short name,
so its titles could be discovered but never *walked*. Adding
`"rakuten_tv": {"NL": "wki"}` to `PACKAGE_SHORT_NAMES` closed that gap.

**Evidence in one line each:**

| Check | Result |
| --- | --- |
| Full backend suite | **2104 tests, 4 failures — confirmed pre-existing on an unmodified HEAD worktree** (run with dev workers idle; see §11 for the lock-test caveat) |
| Frontend | **170 vitest passed**; 2 pre-existing `tsc` errors only |
| `makemigrations --check` | **No changes detected** |
| Movie 194 (live) | `offers=7`, verdict 🟠 `available_elsewhere`, `availability=available` |
| Movie 615457 (live) | detail 🟢 `available_here` · watchlist 🟢 `watchable_here` |
| `sync_providers --country NL` | 114 packages seen · 33 mapped packages · **31 stored rows / 31 slugs** · 0 new providers · 0 new mappings · 81 unmapped · 0 errors |
| Catalogue size (live) | 70 providers · 47 enabled · 44 JustWatch-mapped (21 enabled + 23 disabled) · 23 listed-but-disabled |
| Acceptance list size | 30 mapped services + 1 deliberately absent = **31 entries (29 distinct canonical slugs)** |

---

## 2. Objective & acceptance criteria

The task fixed a minimum *31-service* NL list spanning four groups:

- **Subscription SVOD** — Netflix (+ Netflix Kids), Amazon Prime Video, Disney+,
  Apple TV+, Videoland, Viaplay, SkyShowtime, Canal+, NLZIET, Film1, Hayu,
  Ziggo TV;
- **Broadcasters / niche** — NPO Start, Picl, Sooner, IFFR Unleashed;
- **Rent/buy storefronts** — Pathé Thuis, Apple TV Store (iTunes), Google Play
  Movies, Rakuten TV;
- **Specialist / documentary / free** — Crunchyroll, Curiosity Stream, GuideDoc,
  DOCSVILLE, Magellan TV, BroadwayHD, WOW Presents Plus, JustWatch TV, Filmzie,
  South Park.

Rules that shaped the list:

- Netflix Kids is a curated Netflix profile, **not** a second entitlement.
- Apple TV+ (SVOD) and Apple TV Store (rent/buy) are **different** services.
- Prime add-on channels stay separate from the base subscription.
- Don't invent provider ids; anything unconfirmed stays unmapped.
- Newly discovered services default to **disabled**.
- A rent/buy storefront must never grant subscription access.

**Acceptance criterion:** every entry on the list must (a) resolve to its
intended canonical slug from its JustWatch `technicalName`, and (b) be
**walkable in NL** — i.e. have a measured NL short name in `PACKAGE_SHORT_NAMES`,
otherwise it would be discovered by the catalogue sync but never collected.
Entries JustWatch does not carry in NL must be recorded as a decision, not
omitted by accident.

---

## 3. What shipped

### 3.1 The executable acceptance list (task 7)

`backend/tracker/tests/test_nl_catalogue_acceptance.py` (5 tests) pins the list
against the static identity tables. It is a `SimpleTestCase`: no network, no DB
seeding, fully deterministic.

| Test | Guarantees |
| --- | --- |
| `test_every_acceptance_service_maps_to_its_canonical_slug` | each service's JustWatch `technicalName`(s) resolve to the intended slug (incl. `netflixkids`→`netflix`, `viaplaysports`→`viaplay`) |
| `test_every_acceptance_slug_is_walkable_in_nl` | every canonical slug has a measured **NL** short name (so it is actually collectable) |
| `test_netflix_kids_is_an_alias_not_a_second_entitlement` | `netflixkids`→`netflix`; no `netflix_kids` slug ever appears |
| `test_apple_tv_store_is_distinct_from_apple_tv_plus` | `itunes`→`apple_tv_store`, `appletvplus`→`apple` |
| `test_absent_services_are_not_silently_mapped` | Google Play Movies stays unmapped (not offered in NL) |

### 3.2 The fix — `rakuten_tv` NL short name

**Symptom.** `test_every_acceptance_slug_is_walkable_in_nl` failed with:

```
AssertionError: 'rakuten_tv' not found in { ... }
Rakuten TV (rakuten_tv) has no measured short-name table
```

**Root cause.** `wuaki → rakuten_tv` existed in the *slug* table
(`PACKAGE_TO_PROVIDER_SLUG`), but `rakuten_tv` had **no entry at all** in the
*short-name* table (`PACKAGE_SHORT_NAMES`). The two tables do different jobs:
the slug table decides *who a package is*, the short-name table decides
*whether that provider's catalogue can be walked in a given market*. A slug with
no short name is discovered by the sync and then never collected — exactly the
"present but invisible" failure mode the acceptance list exists to catch.

**Fix** (`backend/availability/sources/justwatch_providers.py`):

```python
"rakuten_tv": {"NL": "wki"},
```

`wki` is the measured NL short name for Rakuten TV's JustWatch package. Its NL
offers are ad-supported (`ADS → free_with_ads`), so mapping it is also what lets
a free-with-ads Rakuten title surface instead of disappearing.

### 3.3 The catalogue expansion

The old identity table mapped **22** JustWatch technical names. It now maps
**47**, covering **44** canonical providers, **31** of which are walkable in NL.
The additions are 23 NL services measured on the live `GetProviders` endpoint
(each with its NL short name so the catalogue can actually be walked):

| Group | Newly mapped |
| --- | --- |
| Subscription SVOD | Videoland, Viaplay (`viaplay` + `viaplaysports`), SkyShowtime, Canal+, NLZIET, Film1, Hayu, Ziggo TV, NPO Plus |
| Niche / festival | Picl, Sooner, IFFR Unleashed |
| Rent / buy | Pathé Thuis, Apple TV Store (`itunes`) |
| Specialist / documentary | Curiosity Stream, GuideDoc, DOCSVILLE, Magellan TV, BroadwayHD, WOW Presents Plus |
| Free / ad-supported | JustWatch TV, Filmzie, South Park |
| Alias | Netflix Kids → `netflix` (a curated profile, not a second service) |

Deliberately **not** mapped: every `amazon*` add-on channel
(`amazonhayu`, `amazonmubi`, `amazonappletvplus`, …), the near-collision ids
(`amazonprimevideowithads`, `amazonimdbtv`, `amazonhbomax`, `amazonmgm`,
`appletvparamountplus`), and Google Play Movies (absent from NL). Mapping those
would attach offers to the wrong canonical service.

The sync then created the new `Provider` rows **disabled**, so nothing widened
silently; they are listed in the manager for the user to claim (section 5.4).

---

## 4. Evidence

### 4.1 Test run

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py test tracker.tests.test_access_resolver \
                        tracker.tests.test_provider_catalogue_sync \
                        tracker.tests.test_access_modes_api \
                        tracker.tests.test_prime_endpoints \
                        tracker.tests.test_surface_country_agreement \
                        tracker.tests.test_nl_catalogue_acceptance \
                        tracker.tests.test_season_availability \
                        availability.tests.test_aggregators --noinput
```

```
Ran 205 tests in 8.769s

OK
Destroying test database for alias 'default'...
```

### 4.2 Catalogue sync (idempotent re-run)

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py sync_providers --country NL --json
```

```
packages_seen                 114
len(mapped) / mapped packages  33
len(providers_touched)         31   # distinct canonical slugs
stored NL mapping rows         31   # one per (provider, market)
created_providers             []
mappings_created              []
unmapped_count                81
errors                        []
```

The re-run created nothing and re-wrote nothing — the idempotency contract holds
on live data, not just in unit tests.

**Three different counts, three different meanings.** `114` is every JustWatch
package returned for NL. `33` is the number of *packages* whose `technicalName`
matches the identity table. `31` is the number of canonical slugs **and** the
number of rows actually stored: `ExternalProviderMapping` is unique on
`(provider, integration_name, country)`, so the two alias pairs
(`netflix`/`netflixkids`, `viaplay`/`viaplaysports`) collapse into one stored
row each. 81 = 114 − 33 packages deliberately left unmapped.

---

## 5. Reported bugs — root cause, evidence, fix

The follow-up started from two concrete, reproducible complaints. Both turned
out to be *two* different layers disagreeing, not a rendering glitch.

### 5.1 Movie 194 — "Availability not shown"

**Symptom.** `GET /api/prime/movie/194/` returned `availability.state = "available"`
while the page showed no offers at all: "Available" with an empty list.

**Root cause.** `OfficialOffersPipeline._qualify_offers`
(`backend/tracker/services/offer_pipeline.py`) dropped every offer whose provider
the user was not subscribed to. `availability.title_source_report` — the source
that produces the availability verdict — is country- and entitlement-agnostic, so
it correctly said "available" while the offer list had been filtered to nothing.
Two layers, two questions, one empty page.

**Fix.** The `is_subscribed` gate was removed. The pipeline now keeps every offer
from a catalogued provider in the page's category scope (still dropping
un-catalogued slugs except the default provider, and still restricted to
`QUALIFYING_OFFER_TYPES`). `rank_and_present` sorts accessible-first with a
stable order and annotates each offer with `is_entitled`, `is_accessible`,
`access_label`, `locality`, `requires_payment` and `rejection_reasons`.

**Evidence (live).** `GET /api/prime/movie/194/?country=NL`:

```
verdict available_elsewhere 🟠 | offers 7 | purchase 0
availability available
```

7 offers (Videoland NL, Sooner NL, Netflix CA, Max FI/DK/NO/SE), all
`entitled=false`, `accessible=false`, `locality=local` for the two NL offers and
`locality=vpn` for the rest — real availability the user cannot play, surfaced
and labelled rather than hidden. No contradiction remains between the verdict
and the list. (The offer count grows as the catalogue widens; the contract is
what matters, not the snapshot.)

**Frontend half.** `OfferRow` now mutes and labels an unowned-service offer
("Not in your subscriptions — shown for reference", plus the `access_label`
chip) instead of dropping it, takes the locality badge from the verdict rather
than re-guessing from the country, and never crowns an unplayable offer "Best
Match". The detail pages render Best Match only for an accessible offer, else an
amber "None of the offers found is included with your subscriptions — they are
listed for reference." line. The Rent/Buy (`purchase_offers`) block was also
lifted out of the `{unofficialLoading && …}` conditional, where it had only
rendered while scrapers were loading.

### 5.2 Movie 615457 "Nobody" — detail 🟢 but watchlist 🟡

**Symptom.** The detail page said 🟢 "Watchable here" while the same title's
watchlist badge said 🟡 "Watchable with VPN".

**Root cause.** All four list surfaces hard-coded `"US"` as the current country,
while the detail page used the user's saved country (NL). The same Prime Video NL
offer was therefore *local* to the detail page and *foreign* to the watchlist.
This is precisely the "one resolver, two answers" failure the redesign exists to
remove — the resolver was already shared, but it was being fed a different
country.

**Fix.** `views.py::_active_country(request)` resolves the country once
(explicit `current_country` → explicit `country` → saved preference → `"US"`) and
is now used by `homepage_feed`, `unified_catalog`, `catalog` and
`watchlist_report`.

**Evidence (live).** Both surfaces agree:

```
detail     verdict available_here 🟢 | offers 27
watchlist  badge   watchable_here 🟢
```

**Regression guard.** `backend/tracker/tests/test_surface_country_agreement.py`
(2 tests) seeds a saved-country-NL user with an NL offer and asserts the detail
verdict and the watchlist badge carry the same status *and* symbol, plus that an
explicit `current_country` still overrides the saved one.

> Note: `?country=` is also a composable *filter* over offer countries, so the
> override test uses `?current_country=US`; passing `?country=US` filters an
> NL-only title out of the report entirely.

### 5.3 Access Type is a multi-select (part 4)

The Access Type control was a single-choice `<select>`, so "free **or** rent"
was impossible. It is now `frontend/components/AccessTypeFilter.tsx`: a checkbox
dropdown that keeps the existing API encoding — `""` = any access, `"all"` = every
type, otherwise the selected subset comma-joined — so no backend or query-string
change was needed. Covered by 4 tests (`parse`/`serialise` round-trip, summary,
individual toggles, select-all/clear).

### 5.4 Discovered services are selectable (part 3, UI half)

Catalogue sync creates newly discovered providers **disabled** (safe default).
But `GET /api/providers/` filtered `is_enabled=True`, so those services were also
invisible and un-selectable — "complete the catalogue" in the data but not in the
product. The list endpoint now returns providers that are *either* enabled *or*
have a JustWatch mapping, and the subscriptions page renders them in an "Other
Services (from the JustWatch catalogue)" section. Declaring access to one
activates it (`provider_toggle` sets `is_enabled=True` on subscribe), because
owning a service is exactly the signal that its catalogue should be collected;
un-declaring leaves the row enabled. Guarded by 3 tests in
`test_provider_catalogue_sync.py`.

Live effect: **70 providers listed, 47 enabled, 44 with a JustWatch mapping, 23
listed-but-disabled** and ready for the user to claim.

---

## 6. The 31-entry list, entry by entry

`NL short name` is the value the sync uses to walk that provider's NL catalogue.
`Mode` is the access type the service grants when the user owns it (derived from
the service's role; the catalogue sync additionally assigns a best-effort
`ProviderCategory` from JustWatch monetization for the DB row).

| # | Service | JustWatch technical name(s) | Canonical slug | NL short | Walkable | Mode |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Netflix | `netflix`, `netflixkids` | `netflix` | `nfx` | ✅ | subscription |
| 2 | Netflix Kids | `netflixkids` | `netflix` | `nfx` | ✅ (alias) | subscription (alias of #1) |
| 3 | Amazon Prime Video | `amazonprimevideo` | `prime` | `prv` | ✅ | subscription |
| 4 | Disney+ | `disneyplus` | `disney` | `dnp` | ✅ | subscription |
| 5 | Apple TV+ | `appletvplus` | `apple` | `atp` | ✅ | subscription |
| 6 | Videoland | `videoland` | `videoland` | `vil` | ✅ | subscription |
| 7 | Viaplay | `viaplay`, `viaplaysports` | `viaplay` | `vip` | ✅ | subscription |
| 8 | SkyShowtime | `skyshowtime` | `skyshowtime` | `sst` | ✅ | subscription |
| 9 | Canal+ | `canalplus` | `canal_plus` | `cpd` | ✅ | subscription |
| 10 | NLZIET | `nlziet` | `nlziet` | `nlz` | ✅ | subscription |
| 11 | Film1 | `film1nl` | `film1` | `fm1` | ✅ | subscription |
| 12 | Hayu | `hayu` | `hayu` | `hay` | ✅ | subscription |
| 13 | Ziggo TV | `ziggo` | `ziggo` | `zig` | ✅ | subscription |
| 14 | NPO Start | `npostart` | `npo_start` | `npo` | ✅ | free |
| 15 | Picl | `picl` | `picl` | `pcl` | ✅ | rent / buy |
| 16 | Sooner | `sooner` | `sooner` | `snr` | ✅ | subscription |
| 17 | IFFR Unleashed | `iffrunleashed` | `iffr_unleashed` | `ifr` | ✅ | subscription |
| 18 | Pathé Thuis | `pathe` | `pathe_thuis` | `pth` | ✅ | rent / buy |
| 19 | Apple TV Store (iTunes) | `itunes` | `apple_tv_store` | `itu` | ✅ | rent / buy |
| 20 | Rakuten TV | `wuaki` | `rakuten_tv` | `wki` | ✅ **(fixed here)** | free with ads |
| 21 | Crunchyroll | `crunchyroll` | `crunchyroll` | `cru` | ✅ | subscription |
| 22 | Curiosity Stream | `curiositystream` | `curiosity_stream` | `cts` | ✅ | subscription |
| 23 | GuideDoc | `guidedoc` | `guidedoc` | `gdc` | ✅ | subscription |
| 24 | DOCSVILLE | `docsville` | `docsville` | `dsv` | ✅ | subscription |
| 25 | Magellan TV | `magellantv` | `magellan_tv` | `mgl` | ✅ | subscription |
| 26 | BroadwayHD | `broadwayhd` | `broadwayhd` | `bhd` | ✅ | subscription |
| 27 | WOW Presents Plus | `wowpresentsplus` | `wow_presents_plus` | `wow` | ✅ | subscription |
| 28 | JustWatch TV (beta) | `justwatchtv` | `justwatch_tv` | `jwt` | ✅ | free with ads |
| 29 | Filmzie | `filmzie` | `filmzie` | `fmz` | ✅ | free with ads |
| 30 | South Park | `southpark` | `south_park` | `spk` | ✅ | free with ads |
| 31 | Google Play Movies | `googleplay` / `googleplaymovies` | — | — | ❌ absent | n/a |

**Entry 31 is absent by decision, not oversight.** Google Play Movies has no NL
package on the measured JustWatch endpoint, so `googleplay`/`googleplaymovies`
are asserted to stay **unmapped** (`NL_ACCEPTANCE_ABSENT`) — mapping them would
be inventing an id.

So: **30 services mapped and walkable, 1 recorded as carried-nowhere = the
31-entry minimum list.**

---

## 7. Coverage accounting — seen / mapped / selectable / unresolved

From the NL sync:

| Bucket | Count | Meaning |
| --- | --- | --- |
| **Packages seen** | 114 | every JustWatch package returned for NL |
| **Mapped packages** | 33 | packages whose `technicalName` matches the identity table |
| **Mapped slugs (distinct)** | 31 | canonical providers NL can cover |
| **Stored NL mapping rows** | 31 | rows actually persisted — one per `(provider, integration, country)`, so the two alias pairs share a slot |
| **Selectable** | 31 | every mapped slug has a `Provider` + NL `ExternalProviderMapping`; discovered rows are created **disabled** yet still listed and selectable in the service-access manager |
| **Unresolved (unmapped)** | 81 | packages deliberately not mapped — reported, never guessed (114 − 33) |
| **Errors** | 0 | no market fetch failed |

**Provider-level counts are not package counts.** The `/api/providers/` list
returns 70 rows because it includes both enabled providers and mapped-but-disabled
ones. Decomposed: **21** enabled-and-JustWatch-mapped (8 with an NL mapping, 13
with mappings only in other markets) + **23** mapped-NL-but-disabled + **26**
enabled-but-unmapped (seeded/scraper providers) = 70. The 23 disabled rows are
exactly the "discovered but not yet claimed" set.

**Mapped set vs the acceptance list.** The 31 distinct NL slugs are the
acceptance set (29 distinct canonical slugs; Netflix Kids and Viaplay Sports
collapse into their parents) **plus two further mapped, NL-walkable services
outside the minimum list**: `npo_plus` (NPO Plus) and `max` (HBO Max). They are
listed and selectable, just not *required* by the acceptance criterion.

**Unresolved (81) — why they are not mapped.** Unmapped packages fall into
classes that mapping would actively harm:

- **Different services with near-colliding names** — `amazonprimevideowithads`
  (ad tier), `amazonimdbtv` (Free with Ads), `amazonhbomax` (HBO Max under
  Amazon), `amazonmgm`, `appletvparamountplus`. Attaching these to the wrong
  canonical provider is exactly the failure the table exists to prevent.
- **Long-tail NL/niché services** not on the minimum list — left unmapped until
  an operator deliberately adds them.
- **Non-NL storefront packages** that appear in the NL response but belong to
  other markets.

This is the intended steady state: unmapped ≠ broken. It means "we refuse to
guess", and the sync reports it so the table can be extended on purpose.

---

## 8. Design invariants this depends on

1. **Two tables, two jobs.** `PACKAGE_TO_PROVIDER_SLUG` = *who is this package*;
   `PACKAGE_SHORT_NAMES` = *can we walk them in this market*. The Rakuten bug
   was precisely a slug present in one and missing from the other.
2. **No fuzzy matching.** Only ids confirmed on the live endpoint are mapped; an
   unmapped package returns `""` and the caller skips and logs.
3. **Additive & idempotent sync.** `get_or_create` on both providers and
   mappings; existing provider rows (name, category, icon, enabled flag) are
   never mutated; `Subscription` rows are never read or written — so re-syncing
   cannot drop a user's selections (invariant covered by
   `test_provider_catalogue_sync.py`).
4. **Discovery is opt-in.** Newly discovered providers are created **disabled**;
   declaring access is what enables them.
5. **Reach is never guessed.** VPN reach comes only from the user's own
   `Preference.vpn_enabled` + `candidate_countries`, never from IP or locale; a
   cross-country offer is *potentially* reachable (🟡), never a certainty.

---

## 9. Files & migrations

### The NL catalogue acceptance

| File | Change |
| --- | --- |
| `backend/availability/sources/justwatch_providers.py` | +23 NL package→slug identities and their measured NL short names; added `"rakuten_tv": {"NL": "wki"}` |
| `backend/tracker/tests/test_nl_catalogue_acceptance.py` | **new** — 5 tests, the executable 31-entry NL acceptance list |
| `backend/tracker/services/provider_catalogue.py` | **new** — idempotent JustWatch→`Provider`/`ExternalProviderMapping` sync |
| `backend/tracker/management/commands/sync_providers.py` | **new** — `--country` (repeatable) / `--json`, exits 1 on fetch failure |

### The reported bugs

| File | Change |
| --- | --- |
| `backend/tracker/services/offer_pipeline.py` | removed the `is_subscribed` gate in `_qualify_offers`; `rank_and_present` sorts accessible-first and annotates access flags |
| `backend/tracker/views.py` | new `_active_country`; the four list surfaces use it; `providers_list` includes mapped-but-disabled; `provider_toggle` activates on subscribe |
| `backend/tracker/tests/test_surface_country_agreement.py` | **new** — 2 tests pinning detail ↔ watchlist country agreement |
| `backend/tracker/tests/test_prime_endpoints.py` | +2 tests: unowned offers shown but not claimed accessible; accessible offer outranks unowned |
| `backend/tracker/tests/test_season_availability.py` | rewritten to the "surface muted, never drop" contract |
| `frontend/components/OfferRow.tsx` | mutes/labels unowned offers; locality from verdict; Best Match only when accessible |
| `frontend/components/AvailabilityState.tsx` | available copy names `available_on` |
| `frontend/app/movie/[tmdb_id]/page.tsx`, `frontend/app/tv/[tmdb_id]/page.tsx` | accessible-only Best Match + amber fallback; Rent/Buy block moved out of the loading conditional |
| `frontend/components/AccessTypeFilter.tsx` | **new** — multi-select Access Type dropdown |
| `frontend/components/FilterBar.tsx` | uses `AccessTypeFilter` |
| `frontend/app/subscriptions/page.tsx` | "Other Services (from the JustWatch catalogue)" section |
| `frontend/lib/api.ts` | `available_on`, access-mode and catalogue types |

### Related working-tree changes

| Area | Files |
| --- | --- |
| Shared resolver | `backend/tracker/services/access.py` (new), `watchability.py` (facade) |
| Preferences / VPN | `backend/tracker/services/preferences.py`, `models.py`, `serializers.py` |
| Access-type filter | `backend/tracker/services/filtering.py` |
| API | `backend/tracker/urls.py` (`providers_sync`, `subscriptions_list_create`) |
| Migration | `backend/tracker/migrations/0041_preference_vpn_enabled_subscription_access_mode.py` — adds `Preference.vpn_enabled` and `Subscription.access_mode` (applied; `makemigrations --check` clean) |

No new migration was needed for the catalogue work: it is static application
knowledge (the identity tables), not schema.

---

## 10. Tests

### Directly covering this work

| File | Tests | Focus |
| --- | --- | --- |
| `tracker/tests/test_nl_catalogue_acceptance.py` | 5 | the 31-entry NL acceptance list |
| `tracker/tests/test_provider_catalogue_sync.py` | 10 | idempotency, additive-only, user-selection safety, unmapped reported, discovered-but-disabled providers listed & activatable |
| `tracker/tests/test_access_resolver.py` | 14 | the 10 promised resolver behaviours + cross-surface consistency |
| `tracker/tests/test_access_modes_api.py` | 13 | `access_mode` write/read, `vpn_enabled` round-trip, `access=` filter |
| `tracker/tests/test_surface_country_agreement.py` | 2 | detail ↔ list surfaces agree on the saved country |
| `availability/tests/test_aggregators.py` | +1 | both spellings of a shared market slot resolve (incl. a disabled provider) |
| `frontend/components/__tests__/AccessTypeFilter.test.tsx` | 4 | multi-select encoding (`""` / `all` / subset) |
| `frontend/components/__tests__/OfferRow.test.tsx` | 6 | muted unowned offers, reason-aware blocker text, locality badge, Best-Match suppression |

### Totals (final run)

| Suite | Result |
| --- | --- |
| `python manage.py test tracker availability` | 2104 tests · **4 failures, confirmed on an unmodified HEAD worktree** |
| `npx vitest run` | 170 tests · **23 files passed** |
| `npx tsc --noEmit` | 2 pre-existing errors (`error-states.test.tsx` TS2554, `pages.test.ts` TS1501) |
| `makemigrations --check --dry-run` | No changes detected |

The Tubi baseline was not assumed: the four failing tests were run from a
detached worktree at `HEAD` (88658f7) with the same image, and fail identically
there (`0 != 1`, `11 != 12`, `12 != 13`, `12 != 13`).

Updated regression contracts: `tracker/tests/test_prime_endpoints.py`,
`tracker/tests/test_season_availability.py`,
`frontend/__tests__/{AvailabilityState,availability-refresh,series-page}.test.tsx`.

### Reproduce

```bash
# Acceptance + aggregators
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py test tracker.tests.test_nl_catalogue_acceptance \
                        availability.tests.test_aggregators --noinput

# Full catalogue sync report
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py sync_providers --country NL --json

# Full backend suites
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend \
  python manage.py test tracker availability --noinput

# Frontend
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npx vitest run
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npx tsc --noEmit
```

---

## 11. Limitations

- **Static-table assertion, not a live check.** The acceptance test asserts the
  identity tables, not the network. It cannot detect JustWatch *renaming or
  withdrawing* a package until someone re-runs `sync_providers` and the unmapped
  report changes — by design (deterministic, offline), but it means the tables
  need an occasional live re-measure.
- **81 NL packages remain unmapped.** Intentional (section 7), but the app
  therefore does not claim complete NL coverage — it claims complete coverage of
  the 31-service minimum list plus two extra mapped services.
- **NL is the only market with a measured short-name table of this depth.** Other
  enabled countries are asserted far more thinly, so "walkable in NL" does not
  generalise to every market yet.
- **Short names are measured, not derived.** Adding a market means re-measuring
  on the live endpoint; an unmeasured market means "not walkable", never
  "walked with a guess".
- **Rent/buy storefronts and category precision.** The sync assigns a
  best-effort `ProviderCategory` from JustWatch monetization; the access *mode*
  a storefront grants is still an operator/user declaration (`access_mode`), and
  a rent/buy offer only becomes an entitlement once the user declares a
  `rent_buy` account.
- **Pre-existing, unrelated failures** (not caused or fixed by this work):
  4 tests in `backend/availability/tests/test_tubi.py`, and 2 TypeScript errors
  in `frontend/__tests__` (`error-states.test.tsx` TS2554, `pages.test.ts` TS1501).
  Separately, `test_only_one_title_worker_may_hold_the_drain_lock` fails while the
  dev worker is running: it counts `pg_locks` without scoping to the test
  database, so the worker's title-drain lock makes the count `2 != 1`. It passes
  with `worker`/`catalogue-worker` stopped — a test-isolation issue, not a code
  failure, and unrelated to this work.
- **Explicit subscription-set seam.** `resolve_title(..., user_subscriptions=...)`
  still accepts a flat slug set for tests and legacy callers, and that path
  (`build_explicit_access`) cannot express `access_mode` and does not apply
  AVOD/FAST auto-entitlement. Production list and detail endpoints no longer
  use it — they build the same `build_user_access` map from `Subscription`
  rows — so it is a test seam rather than a second product classifier.
- **One mapping slot per provider+market.** `ExternalProviderMapping` is unique on
  `(provider, integration_name, country)`, but two services now share a provider
  in one market (`netflix` + `netflixkids`; `viaplay` + `viaplaysports`). Only one
  technical name is stored per slot (whichever the sync wrote last). Resolution
  no longer depends on which one was stored: `_provider` falls back to the static
  identity table when the precise lookup misses, so both spellings resolve — even
  while the provider is still disabled (verified: `viaplay` and `viaplaysports`
  both → `viaplay`; `netflix` and `netflixkids` both → `netflix`). The stored
  value is still not the "primary" name, so a future need to *store* every
  technical name per market would require splitting the unique constraint; offer
  resolution itself no longer does.
- **Access labels now state the truth.** An unowned offer no longer prints
  "Included with your subscription" next to "Not in your subscriptions"; the
  entitlement-phrased chip is suppressed when `is_entitled` is false, and the
  muted line names the actual blocker (language vs. reach) from
  `rejection_reasons` instead of always saying "not reachable".
- **`is_enabled` now means two things.** It is both "the pipeline collects this"
  and "this card is active". Subscribing to a discovered service flips it on;
  un-subscribing does not flip it back off. That is deliberate (a catalogued
  service stays browsable), but it means the flag is not a pure
  user-preference toggle.

---

## 12. Outstanding / next steps

- **Optional coverage growth.** Extend `PACKAGE_TO_PROVIDER_SLUG` /
  `PACKAGE_SHORT_NAMES` from the 81-package unmapped report for services the
  product actually wants; each addition is a deliberate, reviewable diff.
- **Re-measure markets beyond NL** as deeper coverage is needed, adding short
  names per market rather than guessing.
- **Commit the working tree** — everything described here is currently
  uncommitted (the follow-up work spanned tasks 1–7; this report documents all of
  them).
- **Optional:** schedule `sync_providers` (e.g. via cron) so newly added
  JustWatch packages show up in the unmapped report without a manual run.

---

## 13. Final acceptance audit (post-implementation)

This pass re-checked the user-facing requirements against the running stack
rather than against the design.

### 13.1 Verified live

| Requirement | Evidence |
| --- | --- |
| Movie 194 shows offers | `GET /api/prime/movie/194/?country=NL` → `offers=7`, 🟠 `available_elsewhere`, `availability=available` |
| Each offer carries country/service/type/entitlement/locality | 7 offers: Videoland NL, Sooner NL, Netflix CA, Max FI/DK/NO/SE; all `access_type=subscription`, `is_entitled=false`, `is_accessible=false`, `locality` local/vpn |
| 615457 detail ↔ watchlist agree | detail 🟢 `available_here` (27 offers), watchlist 🟢 `watchable_here` |
| Language/country consistency | NL offered as local; cross-country offers marked `vpn`; language-blocked offers carry `Does not satisfy language rules` |
| Access Type is a checkbox dropdown | `FilterBar` renders `AccessTypeFilter`; 4 component tests; no access-type control on the subscriptions page |
| Access filtering is non-destructive | catalog: `access=subscription` → 412 results incl. 194; `access=free` → 88 excl. 194; `access=all` → 827 (same as unfiltered) — changing filters re-shows excluded types |
| Apple TV+ ≠ Apple TV Store | `apple` (Apple TV+) and `apple_tv_store` (Apple TV Store) are separate rows; `appletvplus→apple`, `itunes→apple_tv_store` |
| Netflix Kids is an alias | no `netflix_kids` provider; `netflixkids→netflix`; test asserts no second slug |
| Add-on can be selected independently | per-card `access_mode` select includes `addon`; `provider_toggle` with `access_mode=addon` creates that service's own Subscription |
| Unowned offers muted and labelled | `OfferRow` keeps the row, adds the muted line + locality badge, suppresses Best Match |
| VPN is explicit only | `FilterBar` toggle PUTs `vpn_enabled`; `build_reach` reads only `Preference.vpn_enabled`/`candidate_countries`; geo-IP supplies *current country* only, never VPN reach |
| 30 mapped NL services selectable (31st absent) | `/api/providers/` returns all 29 canonical acceptance slugs; grouping puts 5 in majors, 1 in BVOD, 23 in "Other Services"; 23 are `is_enabled=false` and still listed |

### 13.2 Defects found and fixed in this pass

1. **Contradictory entitlement label.** An unowned offer rendered both "Not in
   your subscriptions" and the chip "Included with your subscription". The
   entitlement-phrased chip is now suppressed when `is_entitled === false`;
   the `offer_type` chip still states "subscription".
2. **Wrong blocker text for language blocks.** Every entitled-but-inaccessible
   offer said "Not reachable from your settings", including Prime AT/DE offers
   rejected by the language rule. The muted line now derives from
   `rejection_reasons` (`Not available in your languages` vs reach).
3. **Alias spelling dropped at ingestion.** `_provider("viaplay", "NL")`
   returned `None` because the single `(provider, market)` slot held
   `viaplaysports` and the slug fallback requires `is_enabled=True`. The
   resolver now consults the static identity table before the slug fallback, so
   both spellings resolve even while the provider is disabled. Regression test
   added in `availability/tests/test_aggregators.py`.
4. **List surfaces used the legacy flat subscription set.** The composable
   filter defaulted to `get_user_subscriptions()` and passed it into the
   resolver, routing lists through `build_explicit_access`, which misses
   AVOD/FAST auto-entitlement and ignores `access_mode`. Titles **241** and
   **311** read 🟠 / 🟡 in the catalog and 🟢 on their detail pages. The list
   path now lets the resolver build the same `build_user_access` map as the
   detail page (the explicit set remains a test seam), and both titles agree
   live: `241` catalog `watchable_here` ↔ detail `available_here`; `311` the
   same.

### 13.3 Count reconciliation

- **114** packages seen; **33** matched the identity table; **31** distinct
  canonical slugs; **31** stored mapping rows (aliases share a slot); **81**
  unmapped = 114 − 33.
- **70** provider rows listed = **21** mapped+enabled (8 NL, 13 other markets) +
  **23** mapped-NL-but-disabled + **26** enabled-unmapped.
- The **31-entry** acceptance list is **30 mapped services + Google Play Movies
  recorded absent**; those 30 collapse to **29 distinct canonical slugs**.

### 13.4 Remaining, by design

- Google Play Movies is absent from JustWatch NL — asserted unmapped, not invented.
- 81 NL packages are unmapped; only NL has this depth of measured short names.
- 4 Tubi tests fail on `HEAD` as well (verified in a detached worktree); they are
  unrelated to this work and left untouched.
- Nothing is committed; the working tree is safe to resume from.
