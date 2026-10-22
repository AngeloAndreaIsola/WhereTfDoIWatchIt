# Watchmode offers without a second source (deferred verification)

Status: 2026-10-21, local Docker Compose environment. Watchmode upstream
re-verification is **deferred** because the account's **monthly plan quota is
spent**, not because of a transient rate window. This was proven, not assumed:
a live request returned HTTP 429 with body
`{"success": false, "errorMessage": "Over plan quota on this API Key."}`,
`retry-after: 60`, `x-ratelimit-limit: 120` and
`x-ratelimit-remaining: 0`; a retry **70 seconds later returned the same
answer**, so the `Retry-After` header is misleading. The account's
`x-account-quota*` headers were absent.

The 2,500-request/month allowance is consistent with the local history: 1,241
successful Watchmode refreshes at two calls each (2,482) plus 21 failed
searches = **2,503 calls** before the first 429. No further probes were made;
the plan period is not confirmed, so the reset is expected around
**2026-11-01** but that date is an inference, not a measurement.

This report records what the local catalogue can prove, what it cannot, and the
exact recheck procedure for when quota is available again.

**Historical `blocked/quota_exhausted` rows are not evidence of this upstream
quota.** The 3,288 Watchmode rows carrying that reason were written before
commit `88658f7` (2026-10-17) and are internal run-budget artifacts (see
`docs/TROUBLESHOOTING.md`); they must not be cited as proof that the upstream
plan was spent at the time they were written.

## Scope

After the F4 identity repair scope was established, 183 active Watchmode offers
were grouped by identity. Each offer was checked for an independent,
non-Watchmode `Offer` row with the same
`(provider, country, content_type, tmdb_id)` key:

| classification | offers | meaning |
|---|---:|---|
| corroborated by another source's offer | 141 | another source currently reports the same title at the same provider and region |
| corroborated by an evidence row only | 0 | no additional offers |
| **uncorroborated** | **42** | no local second source, but no local contradiction either |
| contradicted by local negative evidence | 0 | no non-Watchmode `OfferEvidence(is_present=False)` for these keys |

Corroborated offers by provider: disney 59, netflix 44, tubi 17, max 14,
paramount 7. The 42 uncorroborated offers cover 36 distinct
`(tmdb_id, content_type)` titles: hulu 25, tubi 8, netflix 4, max 2,
paramount 2, pluto_tv 1 (JP 26, CA 12, NL 4).

## What is verified locally for the 42

Every one of the 42 passes the checks the local catalogue can make:

* **Requested title exists and agrees.** 42/42 have a local
  `tracker.Movie` row with the same `tmdb_id` and `content_type`, with a
  non-empty title and year. Example: TMDb 597 → *Titanic* (1997), TMDb 670 →
  *Oldboy* (2003).
* **The source returned a per-title provider link, not a provider root.**
  42/42 have a non-empty `provider_url` pointing at a title-specific page
  (for example `https://www.hulu.jp/watch/100217992`,
  `https://tubitv.com/movies/100049651`,
  `https://www.netflix.com/title/60034587`). This is the shape Watchmode emits
  for a matched title/provider edge; it is not derived from a name search on
  our side.
* **The observations are recent and internally consistent.** `last_seen` ranges
  from 2026-10-05 to 2026-10-19; offer type, region, provider, content type and
  parent identity agree with the row.
* **No local contradiction.** No non-Watchmode source has recorded
  `is_present=False` for these keys, and no duplicate active offer exists at the
  same key.

What is **not** verified: that Watchmode still returns these provider edges
today. `Offer.source_record_id` stores Watchmode's *provider* id (296 = Tubi,
372 = Disney+, …), not its title id, so a local row cannot be replayed against
the title endpoint without a fresh title lookup. The page URLs were not fetched
as a substitute for API verification.

## What the persisted evidence can and cannot corroborate

A read-only audit asked whether any *persisted* row from another source
independently supports one of the 42. It does not:

* **0/42** have a same-provider + same-country + same-title offer from another
  source. That is the only combination that would corroborate the exact claim.
* JustWatch's persisted offers cover only **NL and AU**. It therefore cannot
  speak to the 26 JP or 12 CA offers without new per-title calls.
* **4** of the 42 (all NL) have a same-title offer at a *different* provider.
  That is evidence the title exists in the market, not that this provider
  carries it.
* Streaming Availability has **16** same-title/different-provider rows. Same
  limitation.
* The first-party collectors cannot close the gap either: the Tubi collector is
  US-only, Pluto CA does not carry *The Lighthouse*, and there is no Hulu JP
  collector.

So "corroborated" in the table above means **a second source agrees on the
coarse `(provider, country, content_type, tmdb_id)` key**, not that upstream
independence was proven. The key deliberately ignores offer type, season,
episode and deep link, and `Offer.source` records the *last writer*. The 42 are
therefore genuinely unverified, and the 141 are agreement rather than proof.

## Disposition

Absence of a second source is **not** evidence that an offer is false. These 42
offers remain active and are not deleted or deactivated by this release. The F4
repair moves them to their correct per-title `ProviderContent` row and preserves
`provider_url`, `tmdb_id`, region, offer type and observation timestamps. They
are labelled *uncorroborated*, not *invalid*, until the deferred recheck below
runs.

The F4 identity correction itself is supported by the full 183-offer plan and
by the 141 offers that another source already corroborates. Release proceeds
with this explicit uncertainty record.

## Deferred recheck procedure (when quota is available)

1. Confirm quota from the client's `last_quota` map
   (`x-account-quota` / `x-account-quota-used`) before starting. If the quota is
   spent, stop; retrying a spent quota cannot succeed and hides the real state.
   On a 429, classify before deciding: the live plan-quota body
   (`"Over plan quota on this API Key."`) and/or exhausted `x-account-quota*`
   headers mean **terminal until the plan resets**; a body that actually says
   "rate limit" is retryable. Do **not** treat `Retry-After: 60` as proof of a
   short window — it is emitted for the monthly block too. The client now raises
   `WatchmodeQuotaExceeded` with `kind="plan_quota"`/`"rate_limit"` and the
   scheduler settles a plan block as `blocked`/`quota_exhausted` instead of
   failing it after three retries. A terminal plan refusal also trips a
   **tick-scoped circuit**: no further Watchmode request is issued in that
   tick, while other sources remain eligible; the next tick gets one fresh
   probe, so a plan reset is detected and no permanent disable is persisted.
   The detailed, read-only 42-offer/36-title plan is in
   `docs/WATCHMODE_RECHECK.md`.
2. Deduplicate by `(tmdb_id, content_type)` — 42 offers map to 36 titles — and
   for each title call `WatchmodeClient.find_title(tmdb_id=…, tmdb_type=…)`,
   then `WatchmodeClient.title_sources(watchmode_title_id, regions=[…])`.
3. Match the returned provider row by numeric `source_id` (and region), then
   compare `web_url` with the stored `provider_url`.
4. Record one of:
   * **confirmed** — same provider/region and link: mark corroborated;
   * **link moved** — same provider/region, different link: normal refresh
     updates it; the offer is still real;
   * **not returned** — record as "not currently offered by Watchmode"; let the
     normal refresh deactivate it through evidence, do **not** delete history;
   * **rate-limited** — persist the partial result and resume in the next
     window.
5. Never infer falsity from another provider's absence, and never fabricate a
   confirmation. Re-run the F4 dry run after the refresh if identities or offer
   counts changed.

## The 42 offers

| provider | region | TMDb | title | year | type | offer | last seen | provider URL |
|---|---|---:|---|---:|---|---|---|---|
| hulu | JP | 170 | 28 Days Later | 2002 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100217992 |
| hulu | JP | 194 | Amélie | 2001 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100006183 |
| hulu | JP | 278 | The Shawshank Redemption | 1994 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60165934 |
| hulu | JP | 597 | Titanic | 1997 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100021715 |
| hulu | JP | 601 | E.T. the Extra-Terrestrial | 1982 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60482696 |
| hulu | JP | 670 | Oldboy | 2003 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100113542 |
| hulu | JP | 694 | The Shining | 1980 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60049722 |
| hulu | JP | 1246 | Rocky Balboa | 2006 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60778413 |
| hulu | JP | 1791 | The Piano Teacher | 2001 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100135070 |
| hulu | JP | 3176 | Battle Royale | 2000 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60023962 |
| hulu | JP | 4232 | Scream | 1996 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/40001135 |
| hulu | JP | 4553 | The Machinist | 2004 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60086710 |
| hulu | JP | 8835 | Legally Blonde | 2001 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60784494 |
| hulu | JP | 9693 | Children of Men | 2006 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60024194 |
| hulu | JP | 10494 | Perfect Blue | 1998 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100060301 |
| hulu | JP | 11645 | Ran | 1985 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100023544 |
| hulu | JP | 11660 | Following | 1999 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100206652 |
| hulu | JP | 14537 | Harakiri | 1962 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/60041148 |
| hulu | JP | 324786 | Hacksaw Ridge | 2016 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100058077 |
| hulu | JP | 376867 | Moonlight | 2016 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100024520 |
| hulu | JP | 559907 | The Green Knight | 2021 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100137960 |
| hulu | JP | 666277 | Past Lives | 2023 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100198463 |
| hulu | JP | 693134 | Dune: Part Two | 2024 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100181748 |
| hulu | JP | 1233413 | Sinners | 2025 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100235395 |
| hulu | JP | 1244492 | Look Back | 2024 | movie | subscription | 2026-10-05 | https://www.hulu.jp/watch/100246985 |
| max | NL | 674324 | The Banshees of Inisherin | 2022 | movie | subscription | 2026-10-19 | https://play.hbomax.com/show/7e05abfb-5f40-49c4-ad2d-2a240328b049 |
| max | NL | 1061474 | Superman | 2025 | movie | subscription | 2026-10-05 | https://play.hbomax.com/show/b22d075e-d037-4c61-9b03-2e406f293b0e |
| netflix | CA | 12153 | White Chicks | 2004 | movie | subscription | 2026-10-05 | https://www.netflix.com/title/60034587 |
| netflix | JP | 391713 | Lady Bird | 2017 | movie | subscription | 2026-10-05 | https://www.netflix.com/title/80205227 |
| netflix | NL | 335 | Once Upon a Time in the West | 1968 | movie | subscription | 2026-10-06 | https://www.netflix.com/title/60031884 |
| netflix | NL | 391713 | Lady Bird | 2017 | movie | subscription | 2026-10-05 | https://www.netflix.com/title/80205227 |
| paramount | CA | 4232 | Scream | 1996 | movie | subscription | 2026-10-05 | https://www.cbs.com/movies/scream/yh8qG9949D8fvJa0dFmY1C_SilOYt2hS |
| paramount | CA | 64690 | Drive | 2011 | movie | subscription | 2026-10-05 | https://www.cbs.com/movies/drive-2011/ALVE01KWX8GPXWE8B89Q5WC72C967S |
| pluto_tv | CA | 503919 | The Lighthouse | 2019 | movie | free | 2026-10-05 | https://pluto.tv/on-demand/movies/the-lighthouse-ca-2020-1-1 |
| tubi | CA | 5 | Four Rooms | 1995 | movie | free | 2026-10-05 | https://tubitv.com/movies/100049651 |
| tubi | CA | 539 | Psycho | 1960 | movie | free | 2026-10-05 | https://tubitv.com/movies/660351 |
| tubi | CA | 670 | Oldboy | 2003 | movie | free | 2026-10-05 | https://tubitv.com/movies/100033628 |
| tubi | CA | 8346 | My Big Fat Greek Wedding | 2002 | movie | free | 2026-10-05 | https://tubitv.com/movies/100020635 |
| tubi | CA | 11013 | Secretary | 2002 | movie | free | 2026-10-05 | https://tubitv.com/movies/316454 |
| tubi | CA | 324786 | Hacksaw Ridge | 2016 | movie | free | 2026-10-05 | https://tubitv.com/movies/100040523 |
| tubi | CA | 376867 | Moonlight | 2016 | movie | free | 2026-10-05 | https://tubitv.com/movies/100040490 |
| tubi | CA | 503919 | The Lighthouse | 2019 | movie | free | 2026-10-05 | https://tubitv.com/movies/100021964 |

## Reproduction

Read-only, from the local stack:

```bash
docker compose exec -T backend python manage.py shell -c "
from availability.models import Offer
other = set(Offer.objects.exclude(source__slug='watchmode').values_list(
    'provider_id', 'country_id', 'content_type', 'tmdb_id'))
wm = Offer.objects.filter(source__slug='watchmode')
unc = [o for o in wm if (o.provider_id, o.country_id, o.content_type, o.tmdb_id) not in other]
print(len(wm), len(unc))
"
```

Expected at the time of writing: `183 42`.
