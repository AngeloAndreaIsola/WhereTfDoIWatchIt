# Watchmode recheck plan: 42 uncorroborated offers / 36 titles

Status: **prepared, not executed**. This is the read-only recheck plan for the
42 Watchmode offers that have no local same-provider/same-country second source.
Live Watchmode verification remains deferred until the account's monthly plan
quota resets. The current block is a confirmed terminal plan-quota refusal, not
a short rate window (see `docs/WATCHMODE_UNCORROBORATED_OFFERS.md`).

This document does **not** authorize or apply any identity repair, offer
deletion/deactivation, mapping 399 remediation, or source-precedence change. It
also does not treat the absence of corroboration as proof that an offer is
false. The 42 offers remain active and uncorroborated.

## Identity and deduplication

The smallest valid deduplication key for this recheck is:

```text
(tmdb_id, content_type)
```

It is the key used to call Watchmode's title search and title-sources endpoints.
It deliberately does **not** include provider or country: one title may need
several provider/region claims checked, and those claims must never be merged.
The 42 offers therefore produce **36 title groups**; six titles have claims at
more than one provider or region:

| TMDb | title | claims in this set |
|---:|---|---|
| 4232 | Scream | Hulu JP; Paramount+ CA |
| 670 | Oldboy | Hulu JP; Tubi CA |
| 324786 | Hacksaw Ridge | Hulu JP; Tubi CA |
| 376867 | Moonlight | Hulu JP; Tubi CA |
| 503919 | The Lighthouse | Pluto TV CA; Tubi CA |
| 391713 | Lady Bird | Netflix JP; Netflix NL |

Provider and country remain separate evidence dimensions. A match at Hulu JP
never corroborates a Paramount+ CA row, and a Netflix JP match never
corroborates Netflix NL.

The local Watchmode identity for each offer is the repaired per-title form:

```text
wm:{watchmode_provider_source_id}:{content_type}:tmdb-{tmdb_id}
```

Provider source ids: Hulu `157`, Max `387`, Netflix `203`, Paramount+ `444`,
Pluto TV `391`, Tubi `296`. The identity is provider-content identity, not a
region key; Netflix JP and Netflix NL Lady Bird intentionally share
`wm:203:movie:tmdb-391713` while remaining separate offers.

## Mapping and confidence

* **Canonical target:** the `tmdb_id` shown in each row, with the local
  `tracker.Movie` title/year. All 42 have a non-empty local title and year.
* **Title identity confidence:** high. The Watchmode row was requested by TMDb
  id and the local canonical row agrees on id, content type, title and year.
* **Provider-edge confidence:** **unverified / medium at best**. No other source
  currently reports the same `(provider, country, content_type, tmdb_id)` key.
  That is the exact fact this recheck is meant to test.
* **Mapping 399:** not touched by this plan. It remains a separate, unapplied
  remediation question.

## Request-cost estimate

The existing Watchmode endpoint costs were confirmed before this estimate:

| call | cost |
|---|---:|
| `GET /search/?search_field=tmdb_movie_id&search_value=…` | 1 request |
| `GET /title/{watchmode_title_id}/sources/?regions=…` | 1 request |

Nominal deduplicated plan:

* 36 title searches = 36 requests.
* 36 title-sources calls with the title's relevant regions combined in one
  `regions=` parameter = 36 requests.
* **Total: 72 requests**, or **2.88%** of the measured 2,500-request monthly
  allowance.

Upper bounds and caveats:

* If the client/endpoint requires one region per title-sources call, there are
  40 unique title/region pairs (25 JP, 4 NL, 11 CA), so the total is
  **36 + 40 = 76 requests**.
* Without title deduplication, the same procedure on all 42 offers would cost
  **84 requests**. Deduplication saves 12 requests.
* No retry is added for a Watchmode 429. A confirmed terminal plan-quota
  response stops the run. Transport-level 5xx retries, if they occur, are
  additional requests; budget a small margin but never retry a terminal quota
  block.
* Confirm the live `x-account-quota*` / response body again before spending the
  72 requests. The local estimate is not a provider quota reading.

## Exact versus same-title evidence

The exact corroboration key is:

```text
(provider, country, content_type, tmdb_id)
```

An offer is **exact-corroborated** only when another source reports the same
provider, country, content type and title. By that definition, **0/42** are
exact-corroborated. The 141 offers outside this set are exact matches; these 42
are not.

Same-title evidence is weaker and is kept separate:

* **4/42** (all NL) have a JustWatch same-title offer at a **different
  provider**.
* **16/42** have a Streaming Availability same-title row at a different
  provider. The snapshot does not attribute those 16 rows to individual
  candidates, so the per-candidate table records "no exact SA row" rather than
  claiming a per-row SA match.
* No first-party collector supplies an exact row for any of the 42: the Tubi
  collector is US-only, Pluto CA does not carry *The Lighthouse*, and there is
  no Hulu JP collector.

A same-title/different-provider row proves only that the title is present in a
market. It does not corroborate the provider edge, so it never upgrades a row
to `exact`.

## Per-candidate snapshot

Evidence codes:

* **E-Out** — Watchmode offer plus a title-specific provider URL; JustWatch has
  no persisted coverage for this market (JP/CA); Streaming Availability has no
  *exact* provider/country row (an aggregate 16/42 have same-title rows at a
  different provider); first-party collectors have no exact row. All 42 have
  `exact = no`.
* **E-NL** — same Watchmode evidence; JustWatch has a **same-title,
  different-provider** row for this NL title, which is not exact
  corroboration; Streaming Availability and first-party collectors have no
  exact row.

`Live?` is `yes` for every row: the local catalogue cannot replay Watchmode's
title id from the stored provider URL, so each title group needs a fresh search
and sources call. The group column is the deduplication unit; rows sharing a
group are checked in the same title lookup but judged separately by provider
and country. Every row in this set has `content_type = movie`; series would
need the same treatment but are not part of this 42-offer scope.

| # | group | provider (source id) | region | TMDb | title (year) | offer | Watchmode identity | evidence | exact? | live? |
|---:|---|---|---|---:|---|---|---|---|---|---|
| 1 | T01 | hulu (157) | JP | 170 | 28 Days Later (2002) | subscription | `wm:157:movie:tmdb-170` | E-Out | no | yes |
| 2 | T02 | hulu (157) | JP | 194 | Amélie (2001) | subscription | `wm:157:movie:tmdb-194` | E-Out | no | yes |
| 3 | T03 | hulu (157) | JP | 278 | The Shawshank Redemption (1994) | subscription | `wm:157:movie:tmdb-278` | E-Out | no | yes |
| 4 | T04 | hulu (157) | JP | 597 | Titanic (1997) | subscription | `wm:157:movie:tmdb-597` | E-Out | no | yes |
| 5 | T05 | hulu (157) | JP | 601 | E.T. the Extra-Terrestrial (1982) | subscription | `wm:157:movie:tmdb-601` | E-Out | no | yes |
| 6 | T06 | hulu (157) | JP | 670 | Oldboy (2003) | subscription | `wm:157:movie:tmdb-670` | E-Out | no | yes |
| 7 | T07 | hulu (157) | JP | 694 | The Shining (1980) | subscription | `wm:157:movie:tmdb-694` | E-Out | no | yes |
| 8 | T08 | hulu (157) | JP | 1246 | Rocky Balboa (2006) | subscription | `wm:157:movie:tmdb-1246` | E-Out | no | yes |
| 9 | T09 | hulu (157) | JP | 1791 | The Piano Teacher (2001) | subscription | `wm:157:movie:tmdb-1791` | E-Out | no | yes |
| 10 | T10 | hulu (157) | JP | 3176 | Battle Royale (2000) | subscription | `wm:157:movie:tmdb-3176` | E-Out | no | yes |
| 11 | T11 | hulu (157) | JP | 4232 | Scream (1996) | subscription | `wm:157:movie:tmdb-4232` | E-Out | no | yes |
| 12 | T12 | hulu (157) | JP | 4553 | The Machinist (2004) | subscription | `wm:157:movie:tmdb-4553` | E-Out | no | yes |
| 13 | T13 | hulu (157) | JP | 8835 | Legally Blonde (2001) | subscription | `wm:157:movie:tmdb-8835` | E-Out | no | yes |
| 14 | T14 | hulu (157) | JP | 9693 | Children of Men (2006) | subscription | `wm:157:movie:tmdb-9693` | E-Out | no | yes |
| 15 | T15 | hulu (157) | JP | 10494 | Perfect Blue (1998) | subscription | `wm:157:movie:tmdb-10494` | E-Out | no | yes |
| 16 | T16 | hulu (157) | JP | 11645 | Ran (1985) | subscription | `wm:157:movie:tmdb-11645` | E-Out | no | yes |
| 17 | T17 | hulu (157) | JP | 11660 | Following (1999) | subscription | `wm:157:movie:tmdb-11660` | E-Out | no | yes |
| 18 | T18 | hulu (157) | JP | 14537 | Harakiri (1962) | subscription | `wm:157:movie:tmdb-14537` | E-Out | no | yes |
| 19 | T19 | hulu (157) | JP | 324786 | Hacksaw Ridge (2016) | subscription | `wm:157:movie:tmdb-324786` | E-Out | no | yes |
| 20 | T20 | hulu (157) | JP | 376867 | Moonlight (2016) | subscription | `wm:157:movie:tmdb-376867` | E-Out | no | yes |
| 21 | T21 | hulu (157) | JP | 559907 | The Green Knight (2021) | subscription | `wm:157:movie:tmdb-559907` | E-Out | no | yes |
| 22 | T22 | hulu (157) | JP | 666277 | Past Lives (2023) | subscription | `wm:157:movie:tmdb-666277` | E-Out | no | yes |
| 23 | T23 | hulu (157) | JP | 693134 | Dune: Part Two (2024) | subscription | `wm:157:movie:tmdb-693134` | E-Out | no | yes |
| 24 | T24 | hulu (157) | JP | 1233413 | Sinners (2025) | subscription | `wm:157:movie:tmdb-1233413` | E-Out | no | yes |
| 25 | T25 | hulu (157) | JP | 1244492 | Look Back (2024) | subscription | `wm:157:movie:tmdb-1244492` | E-Out | no | yes |
| 26 | T26 | max (387) | NL | 674324 | The Banshees of Inisherin (2022) | subscription | `wm:387:movie:tmdb-674324` | E-NL | no | yes |
| 27 | T27 | max (387) | NL | 1061474 | Superman (2025) | subscription | `wm:387:movie:tmdb-1061474` | E-NL | no | yes |
| 28 | T28 | netflix (203) | CA | 12153 | White Chicks (2004) | subscription | `wm:203:movie:tmdb-12153` | E-Out | no | yes |
| 29 | T29 | netflix (203) | JP | 391713 | Lady Bird (2017) | subscription | `wm:203:movie:tmdb-391713` | E-Out | no | yes |
| 30 | T30 | netflix (203) | NL | 335 | Once Upon a Time in the West (1968) | subscription | `wm:203:movie:tmdb-335` | E-NL | no | yes |
| 31 | T29 | netflix (203) | NL | 391713 | Lady Bird (2017) | subscription | `wm:203:movie:tmdb-391713` | E-NL | no | yes |
| 32 | T11 | paramount (444) | CA | 4232 | Scream (1996) | subscription | `wm:444:movie:tmdb-4232` | E-Out | no | yes |
| 33 | T31 | paramount (444) | CA | 64690 | Drive (2011) | subscription | `wm:444:movie:tmdb-64690` | E-Out | no | yes |
| 34 | T32 | pluto_tv (391) | CA | 503919 | The Lighthouse (2019) | free | `wm:391:movie:tmdb-503919` | E-Out | no | yes |
| 35 | T33 | tubi (296) | CA | 5 | Four Rooms (1995) | free | `wm:296:movie:tmdb-5` | E-Out | no | yes |
| 36 | T34 | tubi (296) | CA | 539 | Psycho (1960) | free | `wm:296:movie:tmdb-539` | E-Out | no | yes |
| 37 | T06 | tubi (296) | CA | 670 | Oldboy (2003) | free | `wm:296:movie:tmdb-670` | E-Out | no | yes |
| 38 | T35 | tubi (296) | CA | 8346 | My Big Fat Greek Wedding (2002) | free | `wm:296:movie:tmdb-8346` | E-Out | no | yes |
| 39 | T36 | tubi (296) | CA | 11013 | Secretary (2002) | free | `wm:296:movie:tmdb-11013` | E-Out | no | yes |
| 40 | T19 | tubi (296) | CA | 324786 | Hacksaw Ridge (2016) | free | `wm:296:movie:tmdb-324786` | E-Out | no | yes |
| 41 | T20 | tubi (296) | CA | 376867 | Moonlight (2016) | free | `wm:296:movie:tmdb-376867` | E-Out | no | yes |
| 42 | T32 | tubi (296) | CA | 503919 | The Lighthouse (2019) | free | `wm:296:movie:tmdb-503919` | E-Out | no | yes |

The table is a snapshot of the 42-offer scope. It does not assert that a live
Watchmode response will still contain any row.

## Read-only dry run (no upstream calls)

Run this before authorizing a live recheck. It reads local state only and
prints the scope, title-group count and nominal request cost. It never imports
the Watchmode client and never makes an HTTP request.

```bash
docker compose exec -T backend python manage.py shell -c "
from collections import defaultdict
from availability.models import Offer

wm = list(
    Offer.objects.filter(source__slug='watchmode', currently_available=True)
    .values('provider_id', 'country_id', 'content_type', 'tmdb_id',
            'provider_url', 'last_seen')
)
other = set(
    Offer.objects.exclude(source__slug='watchmode').values_list(
        'provider_id', 'country_id', 'content_type', 'tmdb_id'
    )
)
unc = [
    row for row in wm
    if (row['provider_id'], row['country_id'], row['content_type'], row['tmdb_id'])
    not in other
]
groups = defaultdict(list)
for row in unc:
    groups[(row['tmdb_id'], row['content_type'])].append(row)
print('watchmode_active', len(wm))
print('uncorroborated', len(unc))
print('title_groups', len(groups))
print('nominal_requests', 2 * len(groups))
for (tmdb_id, content_type), rows in sorted(groups.items()):
    print(tmdb_id, content_type, len(rows))
"
```

Expected at the time of writing: `watchmode_active 183`, `uncorroborated 42`,
`title_groups 36`, `nominal_requests 72`. The snippet is read-only; it does not
invoke the Watchmode client.

## Live recheck procedure (only after quota is confirmed)

1. **Preflight.** Confirm the Watchmode dashboard/response says quota is
   available. Do not infer a reset from `Retry-After`. Run the dry run above;
   stop if it no longer reports 42 offers / 36 groups, because the scope changed.
2. **Search each title group once.** Use
   `find_title(tmdb_id=…, tmdb_type=…)`. Require an exact TMDb id match. If the
   search result is ambiguous or the TMDb id differs, stop that group as
   **ambiguous**; do not guess.
3. **Fetch sources for the matched title.** One call per title with all
   regions relevant to that group (`NL,CA,JP` or the subset). Match the returned
   provider row by numeric `source_id`, then by region. Compare `web_url` with
   the stored `provider_url`.
4. **Judge each offer separately.** A title match is not provider corroboration.
   Record one of the outcomes below for every offer, and never merge provider
   or country rows.
5. **Persist nothing during the dry read.** The live phase may use the normal
   refresh path, whose evidence rules decide whether to update or deactivate an
   offer. This report itself does not write to the database.

### Outcome handling

| outcome | meaning | handling |
|---|---|---|
| **confirmed exact** | same provider, same country, same content type, same title; link matches or is a redirect to the stored link | corroborated. No deletion. Keep the offer. |
| **link moved** | same provider/country/title, different `web_url` | still real. Let the normal refresh update the link; do not treat it as a contradiction. |
| **not returned** | Watchmode no longer returns that provider/region for the title | record "not currently returned"; do **not** delete history and do **not** infer falsity from one absence. The normal evidence policy may deactivate later only with the usual rules. |
| **same title, different provider** | Watchmode returns the title at another provider, or only JW/SA have same-title rows | not corroboration. Leave the original offer active and uncorroborated. |
| **ambiguous** | multiple title candidates, mismatched TMDb id, duplicate provider ids, or an unparseable region | stop that group, record the ambiguity, keep the offer, and escalate. Do not choose the nearest match. |
| **failed / transient** | network, timeout, 5xx, auth or a short rate window | retry only within the normal policy; persist partial progress and resume. Do not convert a failure into a negative result. |
| **terminal plan quota** | body says "Over plan quota on this API Key." and/or monthly quota headers are exhausted | stop the whole Watchmode phase. Do not retry, do not mark offers false, and do not disable Watchmode permanently. The next tick gets one fresh probe after the plan resets. |

## Guardrails

* Read-only by default: no Watchmode request during the dry run.
* No identity repair, mapping 399 remediation, offer deletion/deactivation, or
  source-precedence change is part of this plan.
* No promotion of JustWatch or Streaming Availability to universal authority.
  Their same-title rows are context, not exact corroboration.
* No database mutation or repair application by this report.
* Provider and country are never merged; a title-level match is not an
  offer-level match.
