# Availability Redesign — Final Report

> **Status:** complete for the acceptance criteria; see §14 for the follow-up
> pass (add-on channels, unresolved-package inspection, availability evidence)
> and §15 for the requirement-by-requirement acceptance table.
> **Scope:** the availability follow-up — detail/watchlist agreement, the
> muted-offer contract, the catalogue completion, add-on channels, the
> access-type filter, the 31-service Netherlands acceptance list, and the
> availability-evidence distinction.
> **Branch state:** branch `feature/scrapres-improvedment`. The first pass is
> committed as `e58183d`; the follow-up pass described in §14 is **in the git
> working tree, uncommitted**, so it is preserved for review. Nothing has been
> staged, reset, stashed or amended.

This report closes the loop on the availability follow-up: the two reported
bugs and their root causes, the muted-offer contract, the completed catalogue,
add-on channels, the access-type filter, the executable 31-service Netherlands
acceptance list, the availability-evidence distinction the detail page needs,
the test evidence behind all of it, and the honest limits of the result.

Sections §1–§13 describe the first pass as it shipped in `e58183d`. **Section
§14 is the follow-up pass** (uncommitted) and supersedes any count or claim it
revises — in particular the NL catalogue counts (`33 mapped / 81 unmapped` →
`54 resolved / 60 unresolved`, of which 21 are add-on channels), the
`viaplaysports → viaplay` conflation (now a distinct `viaplay_sports`), and the
`amazon*` add-on channels (previously "deliberately not mapped", now mapped as
gated add-ons). **Section §15** is the requirement-by-requirement acceptance
table; **§16** the outstanding, honestly-blocked items.

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

> These are the **first-pass** figures (`e58183d`). §14.4, §14.8 and §14.9
> supersede the catalogue counts and the test totals: NL is now **114 seen · 54
> resolved · 60 unresolved** and the full suites are **2205 backend / 185
> frontend** tests (4 pre-existing Tubi backend failures, 2 pre-existing tsc
> errors). §14.10 adds the source-aware availability model.

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

`backend/tracker/tests/test_nl_catalogue_acceptance.py` (6 tests) pins the list
against the static identity tables. It is a `SimpleTestCase`: no network, no DB
seeding, fully deterministic.

| Test | Guarantees |
| --- | --- |
| `test_every_acceptance_service_maps_to_its_canonical_slug` | each service's JustWatch `technicalName`(s) resolve to the intended slug (incl. `netflixkids`→`netflix`, `viaplaysports`→`viaplay_sports`) |
| `test_every_acceptance_slug_is_walkable_in_nl` | every canonical slug has a measured **NL** short name (so it is actually collectable) |
| `test_netflix_kids_is_an_alias_not_a_second_entitlement` | `netflixkids`→`netflix`; no `netflix_kids` slug ever appears |
| `test_viaplay_sports_is_not_conflated_with_viaplay` | `viaplaysports`→`viaplay_sports` ≠ `viaplay` — a Viaplay subscription never unlocks Viaplay Sports (§14.2) |
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

The old identity table mapped **22** JustWatch technical names. After the first
pass it mapped **47**, covering **44** canonical providers, **31** of which were
walkable in NL. The **follow-up pass (§14)** added **21 add-on channels** and
split `viaplaysports` into its own provider, so the table now maps **68**
technical names → **66** distinct canonical slugs, **53** walkable in NL
(**32** standalone + **21** add-on). The first-pass additions were 23 NL services
measured on the live `GetProviders` endpoint (each with its NL short name so the
catalogue can actually be walked):

| Group | Newly mapped |
| --- | --- |
| Subscription SVOD | Videoland, Viaplay (`viaplay`), Viaplay Sports (`viaplaysports` → `viaplay_sports`, split out in §14.2), SkyShowtime, Canal+, NLZIET, Film1, Hayu, Ziggo TV, NPO Plus |
| Niche / festival | Picl, Sooner, IFFR Unleashed |
| Rent / buy | Pathé Thuis, Apple TV Store (`itunes`) |
| Specialist / documentary | Curiosity Stream, GuideDoc, DOCSVILLE, Magellan TV, BroadwayHD, WOW Presents Plus |
| Free / ad-supported | JustWatch TV, Filmzie, South Park |
| Alias | Netflix Kids → `netflix` (a curated profile, not a second service) |

Deliberately **not** mapped as their own standalone services: the near-collision
ids (`amazonprimevideowithads`, `amazonimdbtv`, `amazonhbomax`, `amazonmgm`,
`appletvparamountplus`) and Google Play Movies (absent from NL). The Amazon
**Channel** add-ons — previously listed here as "not mapped" — are now mapped as
gated add-on providers in the follow-up pass (§14); mapping them as *standalone*
providers would attach Prime-channel offers to a service the user may not hold,
which is exactly what the add-on parent model prevents.

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

> **Superseded by §14.** These are the *first-pass* numbers (before add-on
> channels and the Viaplay Sports split). The current NL measurement is **114
> seen / 54 resolved / 60 unresolved**; §14.4 gives the full breakdown and
> re-runs the idempotency check on the current tables.

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
| 7 | Viaplay | `viaplay` | `viaplay` | `vip` | ✅ | subscription |
| 7a | Viaplay Sports *(named in the requirements; not one of the 31)* | `viaplaysports` | `viaplay_sports` | `vps` | ✅ | subscription (separate entitlement — §14.2) |
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

From the current NL sync (the follow-up-pass measurement; §14.4 re-verifies the
idempotency of these numbers):

| Bucket | Count | Meaning |
| --- | --- | --- |
| **Packages seen** | 114 | every JustWatch package returned for NL |
| **Resolved packages** | 54 | packages whose `technicalName` matched the identity table (as *mapped*, *alias* or *add-on*) |
| **Mapped packages** | 32 | resolved as a standalone subscription/storefront provider |
| **Alias packages** | 1 | `netflixkids` → `netflix` (a curated profile, not a second service) |
| **Add-on packages** | 21 | an Amazon Channel, resolved to its own provider gated on a base (§14) |
| **Distinct resolved slugs** | 53 | canonical providers NL can cover (32 mapped + 21 add-ons, of which `viaplay_sports` is the split from `viaplay`) |
| **Stored NL mapping rows** | 53 | rows actually persisted — one per `(provider, integration, country)`, so the alias shares its parent's slot |
| **Selectable** | 53 | every resolved slug has a `Provider` + NL `ExternalProviderMapping`; discovered rows are created **disabled** yet still listed and selectable in the service-access manager |
| **Unresolved** | 60 | packages deliberately not mapped — reported with a structured reason, never guessed (114 − 54) |
| **Errors** | 0 | no market fetch failed |

The 60 unresolved decompose by the closed reason vocabulary (§14.4):

| Reason | Count | Meaning |
| --- | --- | --- |
| `needs_manual_review` | 57 | no source-declared identity; a human must decide |
| `unsupported_offer_type` | 3 | only ever a cinema (`CINEMA`) offer — not a streamable package |
| `addon_parent_unresolved` | 0 | (live NL) every Amazon Channel is in the verified table; the code still flags an unknown one |

**Provider-level counts are not package counts.** The `/api/providers/` list
returns **92** rows because it includes both enabled providers, mapped-but-disabled
ones, and the new add-on channels. Decomposed: **21** enabled-and-JustWatch-mapped
(the first-pass figure; 8 with an NL mapping) + the discovery set + **21**
add-on channels (all `is_enabled=false`, parent `prime`) = 92. Add-on rows are
exactly the "discovered but not yet claimed" set plus the gated channels.

**Mapped set vs the acceptance list.** The standalone NL slugs are the
acceptance set **plus further mapped, NL-walkable services outside the minimum
list** — `npo_plus` (NPO Plus), `max` (HBO Max) and now `viaplay_sports`
(Viaplay Sports, §14.2). They are listed and selectable, just not *required* by
the acceptance criterion.

**Unresolved (60) — why they are not mapped.** Unmapped packages fall into
classes that mapping would actively harm:

- **Different services with near-colliding names** — `amazonprimevideowithads`
  (ad tier), `amazonimdbtv` (Free with Ads), `amazonhbomax` (HBO Max under
  Amazon), `amazonmgm`, `appletvparamountplus`. Attaching these to the wrong
  canonical provider is exactly the failure the table exists to prevent.
- **Cinema-only packages** (`CINEMA` monetization) — not streamable, so
  `unsupported_offer_type`.
- **Long-tail NL/niché services** not on the minimum list (DAZN, MUBI,
  FOUND TV, …) — left unmapped until an operator deliberately adds them, now
  **inspectable** through `/api/providers/packages/` with the reason code.

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
| `frontend/components/__tests__/OfferRow.test.tsx` | 19 | muted unowned offers, reason-aware blocker text, locality badge, Best-Match suppression, price/currency display, missing/stale fallback |
| `availability/tests/test_offer_prices.py` | 50 | price normalization, persistence lifecycle, source-isolated deactivation, orphan cleanup, refresh, multi-source conflict, monetization partitioning, currency comparability, API shape, entitlement invariance |
| `availability/tests/test_offer_evidence.py` | 29 | source-aware evidence: isolation, conflicting evidence, order independence, stale evidence, source retirement, legacy fallback, country scope, non-authoritative/partial/failed runs, idempotency, atomic rollback, purge, price-is-not-evidence, entitlement invariance, API |
| `tracker/tests/test_purge_orphan_offer_evidence.py` | 6 | orphan/source evidence cleanup command (dry-run default); other sources' rows never removed |

### Totals (final run)

| Suite | Result |
| --- | --- |
| `python manage.py test tracker availability` | **2205 tests** · **4 failures**, all pre-existing Tubi tests, re-run identically at `88658f7` |
| `npx vitest run` | **185 tests · 23 files passed** |
| `npx tsc --noEmit` | 2 pre-existing errors (`error-states.test.tsx` TS2554, `pages.test.ts` TS1501) |
| `makemigrations --check --dry-run` | No changes detected |
| Fresh + upgrade + reverse/reapply migrations | `availability.0009`/`0010` apply from zero, upgrade over a seeded pre-change database, and reverse/reapply; `Offer`/`Subscription`/legacy `Offer` survive with no fabricated evidence (see §14.8.3, §14.10) |

The first pass measured 2104 backend / 170 frontend tests. The follow-up pass
(§14) added **13 backend tests**; the source-aware availability pass (§14.10)
added a further **35 backend tests** — none of them failures.

The Tubi baseline was not assumed: the four failing tests were run from a
detached worktree at `HEAD` (88658f7) with the same image, and fail identically
there (`0 != 1`, `11 != 12`, `12 != 13`, `12 != 13`). They are unchanged in the
follow-up pass.

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
- **60 NL packages remain unmapped.** Intentional (section 7), but the app
  therefore does not claim complete NL coverage — it claims coverage of the
  31-service minimum list, the add-on channels, and the extra mapped services
  (`npo_plus`, `max`, `viaplay_sports`).
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
  `(provider, integration_name, country)`. After §14.2 split `viaplaysports` into
  its own provider, the only remaining shared slot is `netflix` + `netflixkids`.
  Only one technical name is stored per slot (whichever the sync wrote last).
  Resolution no longer depends on which one was stored: `_provider` falls back to
  the static identity table when the precise lookup misses, so both spellings
  resolve — even while the provider is still disabled (verified: `netflix` and
  `netflixkids` both → `netflix`). The stored value is still not the "primary"
  name, so a future need to *store* every technical name per market would require
  splitting the unique constraint; offer resolution itself no longer does.
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
  `PACKAGE_SHORT_NAMES` from the 60-package unresolved report (§14.4, queryable
  via `/api/providers/packages/?country=NL&resolved=false`) for services the
  product actually wants; each addition is a deliberate, reviewable diff.
- **Re-measure markets beyond NL** as deeper coverage is needed, adding short
  names per market rather than guessing.
- **Commit the working tree.** The first pass is committed as `e58183d`; the
  follow-up pass described in §14 is uncommitted and left for the user to
  review and commit (§16 lists the exact files and the new migration).
- **Optional:** schedule `sync_providers` (e.g. via cron) so newly added
  JustWatch packages show up in the unresolved report without a manual run.

---

## 13. Final acceptance audit (post-implementation)

> **First pass.** This audit was run against `e58183d`. The follow-up pass
> (§14) revises the catalogue counts and adds add-on channels, unresolved-package
> inspection and the availability-evidence distinction; §15 is the consolidated
> acceptance table.

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

### 13.3 Count reconciliation (first pass)

- **114** packages seen; **33** matched the identity table; **31** distinct
  canonical slugs; **31** stored mapping rows (aliases share a slot); **81**
  unmapped = 114 − 33. **(Superseded by §14.4: 54 resolved / 60 unresolved.)**
- **70** provider rows listed = **21** mapped+enabled (8 NL, 13 other markets) +
  **23** mapped-NL-but-disabled + **26** enabled-unmapped.
  **(Superseded: 92 rows including 21 add-on channels.)**
- The **31-entry** acceptance list is **30 mapped services + Google Play Movies
  recorded absent**; those 30 collapse to **29 distinct canonical slugs**
  (Viaplay Sports is now split out, §14.2).

### 13.4 Remaining, by design (first pass)

- Google Play Movies is absent from JustWatch NL — asserted unmapped, not invented.
- NL packages are unmapped; only NL has this depth of measured short names.
- 4 Tubi tests fail on `HEAD` as well (verified in a detached worktree); they are
  unrelated to this work and left untouched.
- First pass committed as `e58183d`; the §14 follow-up is uncommitted.

---

# 14. Follow-up pass — add-on channels, unresolved packages, availability evidence, price/currency

> **Uncommitted.** Everything in §14 sits in the working tree on
> `feature/scrapres-improvedment` (HEAD `e58183d`). It is left for review; no
> file has been staged, reset, stashed or amended.

This pass addressed the remaining requirements that the first pass left open:
add-on channels (§14.1), the Viaplay Sports distinction (§14.2), persisted
inspection of unresolved packages with structured reasons (§14.5), and the
availability-evidence distinction the detail page needed so a bare source claim
could never render as a playable offer (§14.6).

## 14.1 Add-on channels

An add-on channel (e.g. **Hayu on Prime Video**) is a service sold *through* a
base platform. Modelled wrongly as a standalone provider, its offers would look
like an entitlement the user might not hold; modelled as pure metadata, it would
be unselectable. It is now a **distinct canonical provider gated on a base**.

**Identity is source-declared, never guessed.** The source names an Amazon
Channel as `amazon<hayu>` with a display name ending "Amazon Channel". The
identity table `PACKAGE_TO_PROVIDER_SLUG` gained 21 such packages, each mapped to
a stable canonical slug (`hayu_amazon`, `mubi_amazon`, …), and their
base platform is recorded separately in `ADDON_PACKAGE_PARENT` (all → `prime`).
Only the 21 names confirmed on the live `GetProviders` endpoint are tabulated;
an *unknown* `… Amazon Channel` is flagged, not guessed:

| Verified add-on channels (package → slug, parent `prime`) |
| --- |
| `amazonacorntv`→`acorn_tv_amazon`, `amazonappletvplus`→`apple_tv_amazon`, `amazonciplay`→`crime_investigation_play_amazon`, `amazoncjenmselects`→`cj_enm_selects_amazon`, `amazoncrunchyroll`→`crunchyroll_amazon`, `amazoncuriositystreamstandard`→`curiosity_stream_amazon`, `amazondiscoveryplus`→`discovery_plus_amazon`, `amazondizi`→`dizi_amazon`, `amazondocubay`→`docubay_amazon`, `amazonfear`→`fear_amazon`, `amazonhayu`→`hayu_amazon`, `amazonhbomax`→`max_amazon`, `amazonhopster`→`hopster_amazon`, `amazonlionsgateplus`→`lionsgate_plus_amazon`, `amazonlovenature`→`love_nature_amazon`, `amazonmgmplus`→`mgm_plus_amazon`, `amazonmubi`→`mubi_amazon`, `amazonnbalp`→`nba_league_pass_amazon`, `amazonshorts`→`shorts_tv_amazon`, `amazonstingraykaraoke`→`stingray_karaoke_amazon`, `amazonviaplaysport`→`viaplay_tv_amazon` |

**The relationship is one-directional and non-granting.** It is stored in the
existing `Provider.config` JSON (`config["addon_parent"]`), *not* a new
`Provider` column — see §14.7 for why that mattered. Holding the base never
grants a channel; holding a channel never grants the base.

**Entitlement requires both.** `tracker.services.access.classify_offer` — the
single resolver — now requires:

- **channel entitlement** (`Subscription.access_mode = "addon"` for that
  channel), **and**
- **base entitlement** (`Subscription` for the parent platform),

and rewrites an add-on subscription offer's `access_type` to `addon`
(`AccessType.ADDON`). The offer carries `is_addon`, `required_base` and
`required_base_display`, and its rejection reasons name what is missing
(`Requires Amazon Prime Video (add-on channel)` / `No active subscription for
<channel> (add-on channel)`). The offer is still **shown** (muted and labelled),
never hidden.

| Base held | Channel held | Result |
| --- | --- | --- |
| ✗ | ✗ | `available_elsewhere` 🟠; reason "Requires Prime Video" |
| ✓ | ✗ | `available_elsewhere` 🟠; reason "Add-on channel …" |
| ✗ | ✓ | `available_elsewhere` 🟠; reason "Requires Prime Video" |
| ✓ | ✓ | `available_here` 🟢; `is_accessible=true`, `present_access_types` includes `addon` |

**UI.** Each add-on provider is listed on `/subscriptions/` as its own selectable
card (so declaring `access_mode=addon` for it is possible), labelled
`Add-on · needs <base>` (`ProviderGrid`). `OfferRow` renders an `Add-on · needs
<base>` chip and, when unowned, the muted line
`Add-on channel — requires <base>`. The `ProviderSerializer` exposes
`is_addon`, `parent`, `parent_name`.

Live: **21 add-on providers**, all `is_enabled=false` and `is_subscribed=false` by
default, listed through `GET /api/providers/`.

## 14.2 Viaplay Sports is not Viaplay

The first pass conflated `viaplaysports` into `viaplay`. The requirement forbids
that: Viaplay Sports carries its own `FLATRATE` entitlement and the source does
not confirm it shares Viaplay's subscription. It is now a **separate canonical
provider** `viaplay_sports` (NL short name `vps`), standalone (no base), and a
regression test asserts the two slugs differ. A Viaplay subscription never
unlocks Viaplay Sports, and vice versa.

## 14.3 Netflix Kids stays an alias

`netflixkids` remains a `KNOWN_ALIASES` entry → `netflix`: it is a curated
profile, not a second entitlement, and no `netflix_kids` provider exists. This is
unchanged from the first pass and re-asserted in §14.9.

## 14.4 Catalogue sync — idempotency and counts (current)

Re-run twice against the live NL endpoint after the changes:

```
packages_seen    114
resolved_count    54        # mapped 32 + alias 1 + addon 21
unresolved_count  60        # 114 - 54
created_providers []        # second run
mappings_created  []        # second run
errors            []
unresolved_by_reason {needs_manual_review: 57, unsupported_offer_type: 3}
```

The second run created nothing and rewrote nothing; `ProviderCataloguePackage`
rows exist for **all 114** NL packages (60 of them unresolved), and re-syncing
advances `last_seen` without duplicating a row.

**Definitions.** *Seen* = every JustWatch package for NL. *Resolved* = the
`technicalName` matched the identity table (as a standalone *mapped* provider, an
*alias*, or an *add-on*). *Unresolved* = deliberately not mapped, recorded with a
reason. These are **package** counts; **provider** counts are different: 92
`Provider` rows = the first-pass set + 21 add-on channels. A successful sync is
not evidence of coverage — 60 packages remain unresolved on purpose.

## 14.5 Unresolved packages are inspectable

Every package the sync sees is persisted in the new `ProviderCataloguePackage`
model — a **catalogue observation**, never an availability signal. Unresolved
rows carry a closed reason vocabulary (`PackageReasonCode`):

| Reason | Meaning | Emitted today |
| --- | --- | --- |
| `known_alias` | a known alias spelling (resolved via its parent, recorded) | ✅ |
| `addon_parent_unresolved` | source declares an add-on channel whose base is not in the verified table | ✅ |
| `unsupported_offer_type` | only ever a `CINEMA` offer — not streamable | ✅ |
| `missing_identity` | no technical name to key on | ✅ |
| `needs_manual_review` | none of the above; a human must decide (default) | ✅ |
| `duplicate_provider` | duplicates an existing provider identity | reserved |
| `content_brand_not_provider` | a content brand, not a service (e.g. South Park) | reserved |
| `ambiguous_identity` | one name, more than one plausible provider | reserved |

The five ✅ codes are what `classify_unresolved_package` emits; the three
`reserved` codes exist in the closed vocabulary so a future classifier can return
them without a migration, but nothing emits them yet (documented, not implied).

`classify_unresolved_package` is a pure function of the package (technical name,
display name, monetization types) — no network, no heuristics beyond the
source's own markers. The sync report gains `addons`, `resolution_counts`,
`unresolved_by_reason`, and `unmapped` (aggregated by technical name with a
`countries` list). Inspection is exposed read-only:

```
GET /api/providers/packages/?country=NL&resolved=false
GET /api/providers/packages/?country=NL&reason=unsupported_offer_type
GET /api/providers/packages/?country=NL&resolved=true
```

Live: `country=NL&resolved=false` → 60 rows, reasons
`needs_manual_review` 57 / `unsupported_offer_type` 3, each with a human-readable
`reason` and `resolved_provider_slug=""`. No fake provider is ever created for an
unresolved package; the record is preserved for revisiting.

## 14.6 Availability evidence — a claim is not an offer

**Problem.** `title_source_report` reported `state = available` only when a
concrete canonical offer existed, and `unknown` otherwise. A source that reports
a title as available (a scraper row with `is_available=True`, a legacy
`Availability` row) with no displayable offer was therefore indistinguishable
from "never checked". The detail page could not tell a source's *claim* from a
*playable option*.

**Fix.** Each verdict now carries an `evidence` class plus `offer_count` and
`title_signals`:

| `evidence` | When | UI meaning |
| --- | --- | --- |
| `offers` | ≥1 currently-available canonical offer | the actionable case |
| `title_signal` | no offer, but a source claims availability | **reported available — no current offer to show** |
| `absence` | sources checked, none offered it | genuinely unavailable |
| `none` | no evidence resolved | unknown |

`title_signals` are read from the legacy scraper (`ScrapedContentMapping`) and
`Availability` rows — the only places a source claims availability *without* an
offer. Their providers are merged into `available_on`, so the claim names the
service. `AvailabilityStateMessage` renders the `title_signal` case **before** the
`unknown` branch and explicitly says *"That is a report, not yet a playable
option."* — it never reads as an offer and never falls through to "unknown". The
`_title_availability` endpoint (`/api/v2/unofficial/.../offers/`) and the prime
detail payload both carry the new fields additively; no existing field changed.

## 14.7 Files & migration (follow-up pass)

| File | Change |
| --- | --- |
| `backend/availability/sources/justwatch_providers.py` | `viaplaysports`→`viaplay_sports`; +21 add-on entries; `ADDON_PACKAGE_PARENT`; `KNOWN_ALIASES`; NL short names; helpers `addon_parent_slug_for_package`, `is_addon_package`, `is_alias_package` |
| `backend/tracker/models.py` | `PackageResolutionStatus`/`PackageReasonCode` choices; new `ProviderCataloguePackage` model; `Provider.is_addon` / `Provider.addon_parent_slug` (read `config["addon_parent"]`) |
| `backend/tracker/services/provider_catalogue.py` | rewritten sync: add-on parents, unresolved reason codes, package persistence, `addons`/`resolution_counts`/`unresolved_by_reason` in the report; idempotent and additive-only |
| `backend/tracker/services/access.py` | add-on gating (channel **and** base), `access_type=ADDON`, `is_addon`/`required_base`/`required_base_display` on `ClassifiedOffer` + `to_dict` |
| `backend/tracker/services/offer_pipeline.py` | `rank_and_present` copies the add-on fields onto each offer dict |
| `backend/tracker/services/availability_report.py` | `evidence`/`offer_count`/`title_signals`/`available_on` in `title_source_report`; `_active_offer_count`, `_title_availability_signals` |
| `backend/tracker/api_v2.py` | `_title_availability` passes the evidence fields through |
| `backend/tracker/serializers.py` | `ProviderSerializer` gains `is_addon`, `parent`, `parent_name` |
| `backend/tracker/views.py` | `provider_packages` (read-only); add-on fields in the prime offer payload |
| `backend/tracker/urls.py` | `providers/packages/` route |
| `backend/tracker/migrations/0042_providercataloguepackage.py` | **new** — creates `ProviderCataloguePackage` (no `Provider` column added; see note) |
| `backend/tracker/tests/test_addon_channels.py` | **new** — 8 tests (sync identity, idempotency, unresolved reasons, gating matrix, endpoint) |
| `backend/tracker/tests/test_detail_availability_state.py` | +4 evidence-class tests |
| `backend/tracker/tests/test_nl_catalogue_acceptance.py` | Viaplay Sports de-conflation assertion |
| `frontend/components/ProviderGrid.tsx` | add-on label + `is_addon`/`parent`/`parent_name` on `ProviderItem` |
| `frontend/components/OfferRow.tsx` | add-on chip + "requires base" muted line |
| `frontend/components/AvailabilityState.tsx` | `title_signal` copy |
| `frontend/lib/api.ts` | `PrimeOffer` add-on fields; `TitleAvailabilityVerdict` evidence fields |
| `frontend/__tests__/AvailabilityState.test.tsx` | +2 evidence tests |

**Why no `Provider.parent` column.** The first attempt added a self-FK. It broke
*fresh* databases: migration `0033_merge_duplicate_providers` deliberately replays
using the **live** model registry, so any new `Provider` column (or a new
`ForeignKey(Provider)`) makes its "iterate every provider / cascade delete"
step reference a table or column that does not exist yet at that point in the
migration chain. `config["addon_parent"]` avoids both hazards and needs no
`Provider` migration; `ProviderCataloguePackage` deliberately stores
`resolved_provider_slug` (a `CharField`) rather than a FK, for the same reason.

**Price/currency is now implemented** in a separate, additive table rather than
a new `Offer` column, for the same live-model migration reason. See §14.8 for
the schema, semantics, migration proof, API/UI shape and tests.

## 14.8 Price & currency persistence (implemented)

### 14.8.1 Original limitation and root cause

JustWatch already returns a **structured** price for rent/buy offers —
`retailPriceValue` (a JSON number) and `currency` (an explicit code) — alongside
a localized `retailPrice` display string. `JustWatchOffer` carried all three,
but `ProviderOffer`, canonical `Offer`, reconciliation, and the API dropped
them, so the UI could not show what a rental or purchase costs.

The obvious fix — add `amount`/`currency` columns to `availability.Offer` — is
not safe on a fresh database:

* On an empty database, the migration plan places `availability.0009_offerprice`
  **after** `tracker.0033_merge_duplicate_providers` (verified plan indices:
  `0033` = 54, `0009` = 62; `0010_offerevidence` follows `0009` immediately, so
  both tables are created after the migration that walks the live registry).
* `tracker.0033` is a data migration that imports and queries the **live**
  model registry (`from availability.models import Offer`), specifically
  `Offer.objects.filter(provider=duplicate)` while repointing rows. A column
  added by a later `availability` migration would be selected before it exists,
  so every fresh install would fail.
* Editing `0033`'s dependencies is unsafe for existing databases: Django's
  `check_consistent_history` raises `InconsistentMigrationHistory` when `0033`
  is already applied but its new dependency is not. Rewriting `0033` to
  historical models is also high-risk: the live `Offer.save()` is what rebuilds
  `Offer.identity`, and the legacy bridge needs live `ProviderContent` /
  `ContentMapping` instances.
* A new model with a `ForeignKey` to `Provider` is equally unsafe:
  `duplicate.delete()` runs the live collector, which would query the new table
  before it exists.

So the fix had to be additive, ordered after `0033`, and invisible to both its
direct `Offer` query and its cascade.

### 14.8.2 Chosen architecture and trade-offs

**`availability.OfferPrice`** — one row per **`(offer_identity, source)`**.
`offer_identity` is exactly `Offer.identity` (provider slug, country, content
type, TMDb id, season/episode, offer type, provider content id); the slots are
also denormalized onto the row for inspection.

* **FK to `Source` only** (`PROTECT`). No FK to `Offer`, `Provider`, or
  `Country`, so `0033` cannot see the table at all. This is the migration-safety
  trade-off: integrity is enforced by the write path (a price is only written
  beside the offer it was observed for, in the same transaction) and by the read
  path joining on the exact canonical identity — not by a database cascade.
* **Why not columns on `Offer`:** besides the migration hazard, `Offer.identity`
  deliberately excludes the source (two sources observing one provider offer are
  one canonical offer, and `Offer.source` is last-writer-wins). A single column
  would let the last refreshing source overwrite another source's price, which
  the requirements explicitly forbid.
* **Why not a `DO_NOTHING` FK to `Offer`:** it is invisible to the collector,
  but a later manual offer deletion would raise a database `IntegrityError`, and
  offers are never deleted by design. Identity keying plus an explicit lifecycle
  is the safer, more inspectable option. Orphan rows (only possible via manual
  deletion) never display because reads match identity exactly.
* **Deduplication:** the unique constraint on `(offer_identity, source)` makes
  repeated observations idempotent — one row, not history rows per refresh.
* **Multi-source:** each source has its own row; one source never overwrites
  another. The read layer exposes all of them and selects a display value
  deterministically.
* **Types:** `amount` is a `DecimalField(max_digits=12, decimal_places=4)`
  (never a float); `currency` is a 3-character explicit source code (never
  inferred from a symbol or a localized string); `source_offer_id`,
  `first_seen`, `last_seen`, `observed_at` and `validation_error` preserve
  provenance and freshness.

### 14.8.3 Migration strategy and verification

`availability/0009_offerprice.py` is additive (creates one table, no column on
`Offer`/`Provider`). It depends on `0008`, which the fresh plan already orders
after `tracker.0033`.

* **Fresh database:** a disposable DB migrated from zero applied all migrations
  including `availability.0009` and `availability.0010`; `availability_offerprice`
  and `availability_offerevidence` both exist and are empty. The fresh plan
  orders `tracker.0033` at position 165, `availability.0009` at 186 and
  `availability.0010` at 188 — the hazard is preserved, and the evidence table is
  created after the migration that walks the live registry, exactly like the
  price table.
* **Existing database upgrade:** a fully migrated disposable DB was seeded with
  an `Offer`, a `Subscription` and an `OfferPrice`; `migrate availability 0008`
  reversed only `0009` (table dropped) while the offer and subscription
  survived; `migrate` re-applied `0009`, the offer and subscription still
  survived, and a new price row was written successfully. The evidence table was
  verified the same way: `migrate tracker` on a disposable DB left `availability`
  at `0008`, a legacy `Offer` was seeded, `migrate availability` applied `0009`
  then `0010`, the offer survived, **0** evidence rows were fabricated, and the
  read path still returned the legacy offer (§14.10).
* `python manage.py makemigrations --check --dry-run` → **No changes detected**.
* **Reverse/reapply:** on a disposable DB, `migrate availability 0009` reversed
  only `0010` (evidence table dropped; offers and the price table survived);
  re-applying recreated the empty evidence table and the read path still
  returned the offered title through the legacy fallback. `migrate availability
  0008` reversed both `0010` and `0009`; offers survived, and re-applying
  recreated both tables. Evidence is a disposable projection; canonical offers
  are not.
* The dev database also applied `0009`/`0010` cleanly.

### 14.8.4 Refresh and conflict semantics

| Situation | Result |
| --- | --- |
| Valid structured amount + 3-letter currency | Row upserted; `is_current=True`; `observed_at`/`last_seen` updated; `first_seen` stable |
| Same price observed again | Idempotent: same row, values unchanged, `last_seen` advances |
| Price changes on a later refresh | Same row updated to the new amount/currency, still current |
| Later observation has no price | `is_current=False`; last amount/currency **preserved** (never zero); `validation_error="price not reported"` |
| Malformed/ambiguous price (non-numeric, negative, NaN/Inf, bad/missing currency, display-only `retailPrice`) | `is_current=False`; last known value preserved; `validation_error` records why; **no fabricated amount**. The display string is never parsed |
| Offer disappears from an authoritative complete snapshot | `deactivate_unseen()` records an authoritative **absence for this run's source only** (§14.10), recomputes effective availability from all present evidence (the offer survives if a co-reporting source still observes it) and marks **only this run's source's** price rows not current (see below) |
| Offer removed then re-created | Same identity → re-observation flips the same row current again; `first_seen` retained |
| Different sources observe the offer | Separate rows, both preserved; no source can overwrite another, and one source's removal never stales another's still-valid price |
| Duplicate observations of the same source offer | Collapsed by `(offer_identity, source)`; reads also dedupe identical price values |
| Partial/failed refresh | Price writes share the offer transaction; a failed refresh writes nothing. `PARTIAL`/`FAILED` runs cannot deactivate, so they never mark prices stale |
| Missing price | `price=None`; never rendered as `0` or "free" |

**Display selection** (deterministic, documented in
`AvailabilityReconciliationService._select_price`):

1. only **current, fully validated** prices are eligible (stale rows are kept
   for inspection but never displayed as fresh);
2. the currency reported by the most sources wins; ties break on the ISO code;
3. within that currency the **lowest** amount wins ("from €X");
4. amount ties break on `(source, source_offer_id)`.

When current prices disagree, a `ConflictKind.PRICE` conflict records each
source's fact; no averaged or invented single "canonical" price is produced.
`price_is_from=True` tells the UI to say "from".

**Source isolation on removal.** `OfferPrice` provenance is per source, so
`deactivate_unseen()` scopes its price update to `source=<run source>`: an
authoritative snapshot from source A that no longer lists an offer marks only
A's own price row not current. A still-current price observed by source B for
the same canonical identity is left untouched (its row is never zeroed or
deleted). The shared canonical `Offer` row's availability is likewise
source-aware now: `deactivate_unseen()` records A's absence in
`OfferEvidence` and recomputes effective availability from all present evidence
(§14.10), so B's still-valid observation keeps the offer, and its price, alive.

Price never feeds availability, country reach, language eligibility, VPN,
entitlement, or add-on base gating. `ReconciledOffer.currently_available` and
`ClassifiedOffer.is_entitled`/`is_accessible` are computed exactly as before;
tests assert that adding a price changes none of them.

### 14.8.5 API and UI behaviour

`tracker.services.offers.Offer` carries a selected `price` and the full `prices`
list; `RankedOffer`, `ClassifiedOffer`, their serializers, and the prime detail
payload pass them through. Amount is a **decimal string** (precision survives
JSON), currency is explicit:

```json
{
  "offer_type": "rent",
  "price": {
    "amount": "2.9900",
    "currency": "EUR",
    "source": "justwatch",
    "source_offer_id": "…",
    "observed_at": "2026-10-19T14:24:00+00:00",
    "is_current": true,
    "is_from": false,
    "validation_error": ""
  },
  "prices": [
    {"amount": "2.9900", "currency": "EUR", "source": "justwatch", "is_current": true},
    {"amount": "3.9900", "currency": "EUR", "source": "other", "is_current": true}
  ]
}
```

`OfferRow` shows the price for **rent, buy and add-on** offers only when a
current amount and currency exist, formatted with `Intl.NumberFormat`
(`currencyDisplay: "narrowSymbol"`) and prefixed with "from " when
`is_from`. A missing or stale price renders the muted
**"Price not currently confirmed"** chip — never "Free". Subscription/free
offers show no per-title price. The muted unowned styling, entitlement labels,
add-on chip and "requires base" line are unchanged, and a price never marks an
offer accessible or Best Match.

### 14.8.6 Files (price pass)

| File | Change |
| --- | --- |
| `backend/availability/models.py` | **new `OfferPrice`** (identity+source unique, Decimal amount, explicit currency, lifecycle timestamps, no FK to Offer/Provider) |
| `backend/availability/migrations/0009_offerprice.py` | **new** additive migration; ordered after `tracker.0033`, invisible to it |
| `backend/availability/services/normalize.py` | `NormalizedPrice`, `normalize_price_amount`/`normalize_currency`/`normalize_price` (Decimal, ISO-shaped code, display-only rejection) |
| `backend/availability/domain/models.py` | `ProviderOffer` price fields |
| `backend/availability/sources/justwatch.py` | structured JustWatch price → `ProviderOffer` |
| `backend/availability/persistence/repositories.py` | `OfferPriceRepository.record`/`for_identities`/`purge_orphans`; `deactivate_unseen` marks **the run source's** price rows stale |
| `backend/tracker/management/commands/purge_orphan_offer_prices.py` | **new** explicit orphan cleanup (dry-run default) for rows left by a manual `Offer` deletion |
| `backend/availability/services/refresh.py` | writes the price beside the offer in the same transaction |
| `backend/availability/domain/reconciliation.py` | `PriceObservation`; price/provenance on observations and reconciled offers |
| `backend/availability/domain/enums.py` | `ConflictKind.PRICE` |
| `backend/availability/services/reconciliation.py` | bulk price read; deterministic display selection; PRICE conflict |
| `backend/tracker/services/offers.py` | `price`/`prices` on the internal offer and its serialization |
| `backend/tracker/services/availability_reads.py` | reconciled price → tracker offer (decimal string) |
| `backend/tracker/services/ranking.py` | price carried through ranking |
| `backend/tracker/services/access.py` | price carried through classification; no effect on entitlement |
| `backend/tracker/serializers.py` | ranked-offer serializer includes price/prices |
| `backend/availability/tests/test_offer_prices.py` | **new** 50 tests |
| `backend/availability/tests/test_reconciliation_queries.py` | query bound 4→5 (one bulk price read; still constant) |
| `backend/tracker/tests/test_unofficial_api.py` | additive contract keys asserted |
| `backend/tracker/tests/test_purge_orphan_offer_prices.py` | **new** 3 tests (dry run writes nothing; only orphans deleted; idempotent) |
| `frontend/components/OfferRow.tsx` | price chip, "from", missing/stale fallback, `formatPrice` |
| `frontend/components/__tests__/OfferRow.test.tsx` | +13 price/format tests |
| `frontend/lib/api.ts` | `OfferPrice` type and `PrimeOffer.price`/`prices` |

### 14.8.7 Remaining price limitations

* No price **history**: the row holds the latest observation per source;
  `first_seen`/`observed_at`/`last_seen` are the audit trail.
* Currency validation is structural (three ASCII letters), not a full ISO-4217
  registry lookup; the source supplies the code and no symbol is ever inferred.
* If sources disagree on **currency**, the majority currency is selected and the
  disagreement is exposed via `prices` and a PRICE conflict; there is no FX
  conversion.
* A price is only as fresh as the source observation. Freshness is exposed
  (`observed_at`, `is_current`), but there is no price-only background refresh.
* Only JustWatch currently supplies structured prices; other adapters leave
  `price=None` until they pass amounts through the same normalizer.
* The table has no DB FK to `Offer` by design (migration safety). A manual offer
  deletion therefore leaves an orphan price row; it never displays (reads match
  the live identity exactly) and
  `manage.py purge_orphan_offer_prices` removes it explicitly (`--apply`;
  dry-run by default), so orphans cannot accumulate silently.
* Deactivation is source-scoped and evidence-driven (§14.10). A source's
  authoritative removal records that source's *own* absence and marks only that
  source's price rows stale; effective availability is recomputed from all
  present evidence, so a co-reporting source keeps the offer (and its price)
  alive. The canonical `Offer` row's *descriptive* provenance (`Offer.source`,
  `quality`, …) is still last-writer-wins — only the availability decision is
  per source. Season/episode offers are not deactivated by the current
  lifecycle.

## 14.9 Tests (follow-up pass)

| File | Tests | Focus |
| --- | --- | --- |
| `tracker/tests/test_addon_channels.py` | 8 | add-on provider created disabled with the right base; idempotent re-sync; unresolved reason codes persisted; gating matrix (neither/base-only/channel-only/both); standalone ≠ add-on; `providers/packages/` endpoint |
| `tracker/tests/test_detail_availability_state.py` (new class) | 4 | evidence `offers` / `title_signal` / `none` / `absence` |
| `tracker/tests/test_nl_catalogue_acceptance.py` | 6 | + Viaplay Sports de-conflation |
| `frontend/__tests__/AvailabilityState.test.tsx` | 25 | +2: `title_signal` copy, `offers` copy preserved |
| `backend/availability/tests/test_offer_prices.py` | 50 | normalization, repository lifecycle, source-isolated deactivation, orphan cleanup, refresh, multi-source reconciliation and partitioning, read/API, entitlement invariance, JustWatch parsing |
| `backend/availability/tests/test_offer_evidence.py` | 29 | source isolation (A removes / B keeps), both remove → unavailable, reappearance, country scope, non-authoritative/partial/failed runs, idempotency, atomic rollback, present-wins conflict, order independence, stale evidence, source retirement, legacy fallback vs evidence, orphan purge, price-is-not-evidence, entitlement/reach invariance, API end-to-end |
| `tracker/tests/test_purge_orphan_offer_evidence.py` | 6 | dry-run writes nothing; apply deletes only orphans; idempotent re-apply; `--source` purges and recomputes; other sources' rows (incl. live-offer evidence) are never removed |
| `frontend/components/__tests__/OfferRow.test.tsx` | 19 | +13: amount/currency, rent vs buy, other currency, "from", missing/stale, no false "free", unowned/add-on |

Focused source-aware batch (`test_offer_evidence` +
`test_purge_orphan_offer_evidence`): **35 tests, OK**. Full backend: **2205
tests, 4 failures** — all four are the pre-existing
`test_tubi.py` failures, reproduced **identically** at baseline `88658f7` in a
detached worktree (same assertions, same `0 != 1` / `11 != 12` / `12 != 13`
counts), and none of the changed files are imported by `test_tubi.py`. Frontend:
**185 tests, 23 files passed**. `tsc`: the same 2 pre-existing
errors (`error-states.test.tsx` TS2554, `pages.test.ts` TS1501).
`makemigrations --check`: no changes. `0042`, `0009` and `0010` apply cleanly;
fresh-database and existing-database upgrade paths were verified on disposable
databases (§14.8.3).

**Final adversarial review pass (code-level).** Two defects were found and
fixed, with regression tests: (1) `deactivate_unseen` marked *every* source's
price row stale for a deactivated identity — now scoped to the run's own source,
so one source's removal never discards another source's still-current price
(`PriceDeactivationSourceIsolationTests`, 6 tests); (2) orphan price rows had no
cleanup path, now `OfferPriceRepository.purge_orphans` +
`manage.py purge_orphan_offer_prices` (3 tests). Three comparability tests
(rent vs buy kept separate, add-on ≠ subscription, cross-currency not
numerically cheapest) and one rollback-atomicity test were added. Net: **+13
backend tests** (2157 → 2170).

**Source-aware availability pass (this pass).** The defect found in the
adversarial review was the *shared canonical `Offer` row itself*: availability
was last-writer-wins. It is now fixed with a per-source evidence table
(§14.10), net **+35 backend tests** (2170 → 2205), including a second adversarial
pass over evidence precedence, staleness, source retirement and the legacy
fallback. The residual shared-canonical-row limit no longer hides a valid
observation; see §14.10 for what remains.

---

## 14.10 Source-aware offer evidence — effective availability is per source

> Naming note: this is not §14.6's *evidence class* (which classifies a title's
> verdict from existing signals). `OfferEvidence` here is a per-source
> availability-observation row behind the canonical `Offer`.

**Problem.** `Offer.identity` deliberately excludes the source, so sources A and
B reporting the same provider offer converge on **one** canonical `Offer` row
(`test_identical_provider_content_ids_converge_on_one_canonical_row`). The last
writer owned `Offer.currently_available`, so when A published an authoritative
complete snapshot that omitted the offer, `deactivate_unseen()` flipped the
shared row unavailable — even though B still reported the same offer as
available. B's valid observation (and its price) was silently hidden until B's
next refresh happened to re-activate the row. Source-isolated *price* staleness
did not fix this: a price is not availability evidence.

**Fix — `availability.OfferEvidence`.** One row per
**`(offer_identity, source)`**, the same stable key as `OfferPrice`:

* **State:** `is_present` (this source currently observes the offer) and
  `authoritative` (whether the observation that last set the row came from an
  authoritative complete snapshot). `first_seen`/`last_seen`/`observed_at`
  preserve freshness.
* **Scope:** provider slug, country, content type, TMDb id, season/episode,
  offer type and provider content id are denormalized so a row is inspectable
  without joining back to `Offer`, and so country scope is enforced: the identity
  already carries the country, and removal is filtered by `sync_run.country`.
* **No FK to `Offer` or `Provider`** — `Source` only (`PROTECT`), exactly like
  `OfferPrice` and for the same migration-safety reason (§14.8.1–14.8.3). A new
  relation into `Provider` (or a `post_delete` receiver querying this table)
  would make `tracker.0033` query a table that does not exist yet on a fresh
  database. Integrity is maintained by the write path and
  `OfferEvidenceRepository.purge_orphans()` /
  `manage.py purge_orphan_offer_evidence`.

**Effective availability.** `Offer.currently_available` is now *derived* from
evidence instead of the last writer:

1. any source with `is_present=True` keeps the offer available (present evidence
   from any eligible source wins);
2. an authoritative absence decides the outcome only when **no** source has
   present evidence;
3. an offer with **no** evidence rows at all keeps its stored value — the
   compatibility path for rows written before this table existed. No evidence is
   not proof of absence, and nothing is backfilled or fabricated.

**Write path.**

* `OfferRepository.upsert()` records this source's present evidence beside the
  offer, in the same transaction.
* `OfferRepository.deactivate_unseen()` (authoritative, successful runs only)
  records an absence **for `sync_run.source` only**, recomputes
  `currently_available` from all present evidence for the candidates, and marks
  only this source's price rows stale.
* Non-authoritative/incremental refreshes never record absence: absence is not
  evidence, so an incomplete catalogue can never deactivate anything.
* Because all of this runs inside the refresh `_persist` transaction, a failed
  refresh rolls back evidence, canonical state and prices together.

**Documented policy edges (all tested, `availability/tests/test_offer_evidence.py`).**

| Situation | Result |
| --- | --- |
| A removes, B still observes (same canonical offer) | Offer stays available; A's evidence `is_present=False`, B's stays `True`; the read path still returns it |
| B also removes | Offer becomes unavailable (`any present?` is false); read path drops it |
| A reappears | Only A's row flips `is_present=True`; B's remembered absence is not fabricated away or re-observed on B's behalf |
| Removal in country X | Only the country-X evidence/offer is touched (identity + `sync_run.country` scope) |
| Non-authoritative refresh that omits the offer | No absence recorded, no deactivation, other sources untouched |
| `PARTIAL`/`FAILED` run | `deactivate_unseen()` refuses (`InactiveSyncError`); no absence evidence written |
| Failed refresh mid-way | Evidence + canonical + price writes roll back together |
| Repeated refresh / repeated removal | Idempotent: one evidence row, same state |
| Removal/observation order | Order-independent: whichever of present/absence lands first, the converged state is the same (`EvidencePrecedenceTests`); absence can only be recorded against an *existing* row, so a removal of an offer nobody ever observed records nothing |
| Stale present evidence | Stays available: evidence is surfaced (`last_seen`/`observed_at`), never auto-expired, matching the pre-existing offer lifecycle. Only an authoritative absence or an explicit purge revokes it |
| Legacy `Offer` with no evidence | Keeps stored availability; not treated as unavailable |
| Evidence appears after a legacy offer | Evidence governs from then on; a stale/legacy write of the stored flag cannot win, because a recompute derives availability from evidence (`LegacyFallbackTests`) |
| Legacy `Offer` an authoritative snapshot omits | Deactivated (that absence is genuine evidence) |
| Source deleted while evidence exists | Refused (FK `PROTECT`), matching `OfferPrice` |
| Source retired | `purge_for_source` / `purge_orphan_offer_evidence --source` removes only that source's evidence and recomputes; another source still observing keeps the offer up |
| Orphan evidence after a manual `Offer` delete | `manage.py purge_orphan_offer_evidence` (`--apply`, dry-run default) deletes exactly the orphans |
| Price recorded / stale price | Never creates evidence and never makes an offer available |
| Entitlement / country / VPN | Unchanged: evidence only decides availability; `classify_offer` still gates access |

**What remains (honest).** The canonical `Offer` row still stores last-writer
*descriptive* fields (`source`, `quality`, URLs, languages); evidence governs
only the availability decision. The domain has no "explicit unavailable result"
signal distinct from authoritative absence, so a source's explicit
"unavailable" (if a future source emits one) is not yet representable — only
authoritative absence is, and non-authoritative absence is never recorded. A
disabled `Source`'s evidence is not auto-revoked (disabling stops refreshes, as
it does today); retiring it explicitly runs
`purge_orphan_offer_evidence --source <slug>`, which recomputes affected offers.
Exceptional data (offer or price changed outside the refresh path) is repaired by
those explicit commands, not by a scheduler — consistent with the rest of the
pipeline, which has no auto-expiry.

**Transition behaviour, stated precisely.** `Offer.currently_available` is a
*maintained projection* of evidence, not an independent fact. The only
production writers are `OfferRepository.upsert` (writes the observing source's
present evidence and sets the row available), `deactivate_unseen` (records the
run source's absence and recomputes from all present evidence) and
`recompute_availability` (re-derives after a purge). Because every write that
sets the flag also writes or reads evidence, the projection cannot drift from
evidence through the pipeline. The **legacy fallback is consulted only while an
identity has zero evidence rows**: once any evidence exists, availability is
evidence-derived, and a stale direct write of the flag is corrected by the next
authoritative run or explicit recompute (`LegacyFallbackTests`). Reversing
`0010` drops the evidence table; on re-apply it is recreated empty, so every
offer falls back to its stored flag until a source re-observes — the evidence
projection is disposable, the canonical offers are not (verified on a
disposable database).

**Files (source-aware availability pass).**

| File | Change |
| --- | --- |
| `backend/availability/models.py` | **new `OfferEvidence`** (identity+source unique, per-source `is_present`/`authoritative`, denormalized scope, `Source` FK only) |
| `backend/availability/migrations/0010_offerevidence.py` | **new** additive migration; depends on `0009`, ordered after `tracker.0033` |
| `backend/availability/persistence/repositories.py` | **new `OfferEvidenceRepository`** (`record`, `record_absent_bulk`, `present_identities`, `recompute_availability`, `purge_orphans`, `purge_for_source`); `upsert` records present evidence; `deactivate_unseen` records this source's absence and recomputes effective availability |
| `backend/tracker/management/commands/purge_orphan_offer_evidence.py` | **new** orphan/source evidence cleanup (dry-run default) |
| `backend/availability/tests/test_offer_evidence.py` | **new** 29 tests (write path → reconciliation → read → API; precedence, staleness, retirement, legacy fallback) |
| `backend/tracker/tests/test_purge_orphan_offer_evidence.py` | **new** 6 tests |
| `backend/availability/tests/test_offer_prices.py` | updated the removal comment to the evidence-driven model (price is not availability evidence) |

---

# 15. Requirement-by-requirement acceptance

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Movie 194 traced, fixed, tested; no title-level signal misread as an offer | ✅ | §5.1, §14.6; `test_prime_endpoints`, `test_detail_availability_state` |
| 2 | 615457 consistent detail ↔ watchlist, tested | ✅ | §5.2; `test_surface_country_agreement` (2) |
| 3 | 31-service NL list accounted entry by entry | ✅ | §6 + §14.2; `test_nl_catalogue_acceptance` (6) |
| 4 | Verified add-on channels independently selectable and correctly gated | ✅ | §14.1; `test_addon_channels` (8); live `/api/providers/` shows 21 add-ons with `parent` |
| 5 | Unresolved packages inspectable with structured reasons | ✅ | §14.5; `ProviderCataloguePackage` + `/api/providers/packages/`; `test_addon_channels` |
| 6 | Access Type non-destructive multi-select | ✅ | §5.3; `AccessTypeFilter` (4); live filter counts re-show excluded types |
| 7 | Unowned offers visible with accurate labels | ✅ | §5.1, §14.1; `OfferRow` tests; live 194 shows 7 muted offers |
| 8 | VPN/country per explicit preferences only | ✅ | §8.5; `build_reach` reads only `Preference.vpn_enabled`/`candidate_countries`; geo-IP sets current country only |
| 9 | All checks run and accurately reported | ✅ | §10, §14.9, §16; 2205 backend (4 pre-existing Tubi) / 185 frontend / 2 pre-existing tsc / migration check |
| 10 | Report matches implementation and its limits | ✅ | this document; §11 + §16 state the limits |
| A | Distinguish confirmed offer / title-level evidence / unavailable / unknown / partial | ✅ | §14.6 `evidence` class; `AvailabilityStateMessage` |
| B | No second classifier or source of truth | ✅ | one `tracker/services/access.py`; evidence class is derived from existing sources |
| C | Don't infer availability from provider presence; don't classify missing data as unavailable | ✅ | `evidence=none` ≠ `absence`; unresolved packages are catalogue observations only |
| D | Don't infer VPN reach from IP/locale; don't invent identities | ✅ | §8.5; add-on parents only from the verified table, else `addon_parent_unresolved` |
| E | Idempotent, non-destructive sync; new providers disabled by default | ✅ | §14.4; 21 add-ons disabled; `Subscription` never touched |
| F | Never claim full coverage from counts or a successful sync | ✅ | §7, §14.4: 60 unresolved; "sync success ≠ coverage" |

### 15.1 Price/currency requirements (this pass)

| Requirement | Status | Evidence |
| --- | --- | --- |
| Diagnose the migration hazard from code, not speculation | ✅ | §14.8.1: `tracker.0033` imports the live `Offer`; fresh-plan indices 54 (`0033`) vs 62 (`0009`) |
| Choose a backward-compatible design and state trade-offs | ✅ | §14.8.2: identity-keyed `OfferPrice`, `Source` FK only, no `Offer`/`Provider` FK |
| Fresh DB from zero to latest; existing DB upgrade; offer/subscription data survives; `makemigrations --check` | ✅ | §14.8.3: both disposable-DB paths verified; no changes detected |
| Decimal amount, explicit ISO-4217 code, rent/buy/subscription context, source + source-offer id + last-observed | ✅ | `OfferPrice`, `ProviderOffer` price fields, `JustWatchOffer.retail_price_value`/`currency` |
| No symbol inference; no display-string parsing; missing price ≠ zero; no fabricated prices | ✅ | `normalize_price`; `test_display_only_price_is_ambiguous_not_parsed`, `test_missing_amount_is_not_zero`, repository stale tests |
| Repeated price / changed price / price disappears / different sources / duplicate observations / multiple monetization types / bad currency / offer disappears / partial refresh failure | ✅ | §14.8.4 table; `test_offer_prices` repository, refresh and reconciliation classes |
| Never overwrite one source with another; preserve provenance; no invented canonical price; deterministic display | ✅ | `(offer_identity, source)` unique; `_select_price`; `ConflictKind.PRICE`; multi-source tests |
| Price never affects availability, reach, language, VPN, entitlement, add-on base | ✅ | `test_price_does_not_make_an_unowned_offer_accessible`, `test_price_presence_does_not_change_the_title_verdict` |
| End-to-end JustWatch parse → normalize → persist → reconcile → API → TS types → `OfferRow` | ✅ | §14.8.5–14.8.6; `test_offer_prices` parsing/read tests; `OfferRow` price tests |
| Backend tests cover parse, rent/buy, precision, missing/malformed, idempotency, changes, cross-source, provenance, source-isolated deactivation, failure, recreation, API, entitlement, add-ons | ✅ | `availability/tests/test_offer_prices.py` (50); `tracker/tests/test_purge_orphan_offer_prices.py` (3) |
| Frontend tests cover amount/currency, rent vs buy, missing fallback, currencies, stale, no false "free", no entitlement claim, unowned/add-on | ✅ | `OfferRow.test.tsx` 19 total (+13) |
| Missing/stale price never shown as fresh or free; unowned/add-on styling preserved | ✅ | `is_current` gate + "Price not currently confirmed"; unowned/add-on tests |

### 15.2 Source-aware availability requirements (this pass)

| Requirement | Status | Evidence |
| --- | --- | --- |
| A source's authoritative removal removes only that source's evidence | ✅ | §14.10; `SourceIsolationTests`; absence recorded per `(identity, source)` |
| Effective availability derived from valid current evidence, not last-writer `Offer` | ✅ | `Offer.currently_available` recomputed from evidence in `upsert`/`deactivate_unseen`; `test_a_removal_keeps_b_evidence_and_effective_availability` |
| Any eligible source's valid current observation keeps the offer available | ✅ | present-wins policy; `test_present_evidence_wins_over_another_sources_absence` |
| Absence vs explicit-unavailable distinguished if the domain supports it | ✅ (documented limit) | only authoritative absence is representable; non-authoritative absence is never recorded; §14.10 "What remains" |
| Non-authoritative refresh cannot deactivate others or imply exhaustiveness | ✅ | `test_non_authoritative_refresh_records_no_absence`; `may_deactivate` gate unchanged |
| Country/source scope correct (removal in X doesn't invalidate Y) | ✅ | identity carries country + `sync_run.country` filter; `test_removal_in_one_country_does_not_touch_another` |
| Price lifecycle stays source-specific | ✅ | `deactivate_unseen` stales only `source=<run source>`; price-isolation tests (6) |
| Failed/partial refreshes atomic; no partial evidence/canonical/price writes | ✅ | `_persist` transaction; `test_failed_refresh_rolls_back_evidence_canonical_state_and_price`; `test_partial_and_failed_runs_record_no_absence` |
| Reappearance restores only the observing source's evidence | ✅ | `test_reappearance_restores_only_the_observing_sources_evidence` |
| Legacy `Offer` rows without evidence handled predictably | ✅ | compatibility fallback (keep stored value); `test_legacy_offer_without_evidence_is_not_treated_as_unavailable`; no backfill/fabrication |
| Ranking/access use effective availability while enforcing entitlement/country/VPN/reach/subscription/add-on | ✅ | reads consume the derived view; `AccessRemainsIndependentTests` (entitlement + reach) |
| Catalogue metadata is not availability evidence | ✅ | evidence written only from offer upsert/deactivation, never from `ProviderContent`/catalogue |
| Price is never availability evidence | ✅ | `PriceIsNotEvidenceTests`; recording a price creates no evidence and does not reactivate an offer |
| Migration safety (no new dependency for `tracker.0033`; fresh + upgrade verified) | ✅ | §14.8.3; `Source`-only FK; fresh plan 0033→0009→0010; upgrade seed survived with 0 fabricated evidence; reverse/reapply keeps offers and recreates the projection |
| Regression tests through the real resolver/read/API pipeline | ✅ | `test_offer_evidence` drives `AvailabilityRefreshService` → reconciliation → `get_local_offers` → `/api/movie/550/availability/` |
| Source delete/disable explicit, tested policy | ✅ | delete refused (`PROTECT`); `purge_for_source` + `purge_orphan_offer_evidence --source`; §14.10 |

---

# 16. Outstanding & blocked (honest)

- **Price/currency is implemented; the old blocker is removed.** The hazard was
  real (`availability.0009` runs after the live-registry `tracker.0033`), so the
  fix is an additive identity-keyed `availability.OfferPrice` table with no FK to
  `Offer`/`Provider`; see §14.8 for the design, migration proof and semantics.
  The remaining price limitations (no price history, structural currency check,
  no FX conversion, no price-only refresh, JustWatch-only coverage, no DB-level
  FK to `Offer`) are listed in §14.8.7 — they are deliberate scope limits, not
  an unresolved blocker. Price staleness on removal is **source-scoped** (one
  source's authoritative removal never stales another source's still-current
  price), and orphan price rows left by a manual `Offer` deletion are removed by
  the explicit `purge_orphan_offer_prices` command. The residual
  shared-canonical-`Offer` limit from the price pass is **fixed by §14.10**: a
  per-source `OfferEvidence` table makes effective availability the union of
  current observations, so one source's authoritative removal no longer hides a
  co-reporting source's valid observation or price. What remains there is that
  the canonical row's *descriptive* provenance is still last-writer-wins and that
  a source's explicit "unavailable" signal is not representable beyond
  authoritative absence.
- **Second adversarial pass (evidence precedence / staleness / retirement /
  legacy).** Verified and regression-tested: availability is order-independent
  for present-vs-absence evidence; stale present evidence is surfaced but never
  auto-expired (the only revokers are an authoritative absence or an explicit
  purge); retiring a source removes only that source's evidence and recomputes
  the affected offers; the default orphan purge never touches a live offer's
  evidence; and the legacy no-evidence fallback is consulted only while an
  identity has zero evidence rows, so it cannot override evidence once it exists
  (`EvidencePrecedenceTests`, `StaleEvidenceTests`, `SourceRetirementTests`,
  `LegacyFallbackTests`). Reverse/reapply of `0010`/`0009` was verified on a
  disposable DB: canonical offers survive, the evidence projection is recreated
  empty and falls back to stored availability.
- **Worker-lane test isolation (environmental).** 
  `test_only_one_title_worker_may_hold_the_drain_lock` counts `pg_locks`
  cluster-wide and fails while the dev worker holds the drain lock; it passes
  with `worker`/`catalogue-worker` stopped. Not caused by, and not fixed in,
  this work. (The final full-suite run did not hit it.)
- **Pre-existing failures left untouched.** 4 `backend/availability/tests/test_tubi.py`
  tests (re-run at baseline `88658f7` in a detached worktree: same assertions
  and counts) and 2 TypeScript errors (`error-states.test.tsx` TS2554,
  `pages.test.ts` TS1501).
- **60 NL packages remain unresolved.** Deliberately: each is now inspectable
  with a reason, and mapping one is a reviewable diff, not a guess.
- **Add-on parent inference is Amazon-only in practice.** The verified table
  covers the 21 Amazon Channels in NL; other platforms' add-ons (if any) would
  arrive as `addon_parent_unresolved` until verified — never guessed.
- **Uncommitted.** The §14 follow-up (add-on channels, unresolved packages,
  evidence, price/currency) is in the working tree on
  `feature/scrapres-improvedment` (HEAD `e58183d`). It is preserved for review;
  committing is left to the user.
