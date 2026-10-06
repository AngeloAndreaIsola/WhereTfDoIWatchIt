# P9 — Collector and availability UI completeness: audit

Measured before changing anything. A suspicion that measurement disproved is
recorded as disproved rather than quietly dropped.

## Baseline (before P9)

```text
SA quota before any probe: 11
Provider rows: 46 (0 duplicate slugs)
Source rows: 13
```

## Source inventory

14 registered sources, 13 with a DB `Source` row.

| Source | Type | catalogue | tmdb ids | countries | authoritative | incremental | DB | PC | Offers |
|---|---|---|---|---|---|---|---|---:|---:|
| abc_iview | api | yes | no | 1 | no | no | yes | 1594 | 0 |
| bbc_iplayer | web | yes | no | 1 | no | no | yes | 3210 | 71 |
| cbc_gem | api | yes | no | 1 | no | no | yes | 1051 | 156 |
| justwatch | aggregator | yes | yes | unrestricted | no | yes | yes | 954 | 983 |
| mediaset_infinity | api | **no** | no | 1 | no | no | yes | 8 | 8 |
| pbs | api | yes | no | 1 | no | no | yes | 252 | 0 |
| pluto_tv | api | yes | no | 32 | no | no | yes | 2008 | 9 |
| prime_video | web | yes | no | 1 | no | no | yes | 625 | 165 |
| rai_play | web | yes | no | 1 | no | no | yes | 441 | 2 |
| streaming_availability | api | no | yes | unrestricted | no | no | yes | 543 | 5041 |
| tubi | web | yes | no | 1 | no | no | **none** | 0 | 0 |
| watchmode | aggregator | no | yes | 3 | no | no | yes | 7 | 179 |
| youtube | web | **no** | no | 5 | no | no | yes | 0 | 0 |

Scheduling is complete: every catalogue-capable source is in
`_catalogue_source_slugs()`, and no capable source is missed.

## Disproved by measurement

These looked like defects and are not:

- **Low `ContentMapping` rates for first-party collectors.** Measured
  mapping coverage is justwatch 99.7% and 0–7% elsewhere. That is correct:
  first-party collectors discover titles *nobody has asked about yet*
  (825 `Movie`, 191 watchlist items locally). Unmapped `ProviderContent` is
  discovery, not a mapping failure.
- **Provider duplication.** 46 provider rows, zero duplicate slugs. The P8
  canonical forms (`raiplay`, `bbc_iplayer`, `nhk_world`) are what is stored —
  no legacy `rai-play`/`bbc` rows exist.
- **Zero offers for pbs/abc_iview.** Both declare exactly one supported
  country and produce 0 offers while persisting content; this reflects an
  empty local mapping, not a persistence defect. No offer is claimed to exist.

## Confirmed defect 1 — UNKNOWN rendered as UNAVAILABLE

`frontend/app/movie/[tmdb_id]/page.tsx` renders, whenever `offers.length === 0`:

```text
Not currently available on your subscribed streaming services.
```

The movie detail endpoint returns only `offers: []` — no availability state and
no evidence provenance — so the UI *cannot* distinguish the three states the
backend already computes.

Measured over 400 movies that have no local offers:

```text
state distribution: {'unknown': 400}
reasons: not_checked 375, not_applicable 21, title_not_found 4
```

**Zero** of them are genuinely `unavailable`. Every one is currently told
"Not currently available", which is a false statement about a title that was
never checked.

`availability_report.title_source_report()` already computes the correct state
and reason; the detail endpoint simply does not surface it. The fix is to
return the existing verdict, not to invent new classification.

## Confirmed defect 2 — no stale distinction in the title view

The report endpoint exposes `stale_failures`, but the per-title view has no
way to say "this result may be out of date".

## Confirmed decision — unofficial offers are NOT canonical availability

Two deliberately different facts, and the project already separates them:

| Fact | Source of truth | Shown as |
|---|---|---|
| An official availability answer | canonical `Offer` rows with `currently_available=True`, via `title_source_report` | ranked "Best Match" / "All Ranked Offers" |
| A provider-catalogue link we can display | `ProviderContent` + `ContentMapping` (unofficial/scraper) | a separate "Scraper Sources" section with per-row badges |

The detail page already renders them in distinct sections, so the distinction
needs no new mechanism. What P9 adds is only that the *empty* case no longer
claims otherwise.

Concretely, the classifier reads canonical offers, so this state is reachable
and legitimate:

```text
unofficial offer displayed  +  canonical verdict UNKNOWN
```

That means "we can show you a provider-catalogue link" while "no authoritative
resolver has confirmed availability yet". It is **not** promoted to
`AVAILABLE`:

- promoting it would redefine what `availability.state == "available"` means,
  in conflict with the availability report and metrics endpoints;
- the source hierarchy would become inconsistent, because the same title would
  read `available` on the detail page and `unknown` in the report.

The asymmetry is handled honestly instead:

- unofficial offers shown, verdict unknown → the offers render under "Scraper
  Sources"; the ranked-offer area does not claim a best match;
- verdict available, no unofficial offer → `AvailabilityState` renders "not
  shown", never "not available" (covered by
  `does not turn an available-with-no-offers into unavailable`).

No second classifier, no promotion rule, no change to `classify_source` or
`classify_title`.

## Stale semantics — not implemented, documented

The verdict exposes per-source `checked_at` (when that source actually
answered) and `last_attempted_at` in the report payload. A correct stale
determination needs an authoritative freshness policy — which
`availability.freshness` already models per source.

Deliberately **not** added: a client-side threshold such as "older than 24h is
stale". Such a rule would be invented rather than authoritative, and would
disagree with the backend's own freshness model. The UI therefore does not
present `checked_at` as a staleness judgement today. Recorded as a future
enhancement, not a P9 defect.

## Refresh controls — audited, unchanged

- Detail pages use `RefreshAvailabilityButton` → `requestAvailabilityRefresh`
  → the existing backend request path.
- Watchlist uses `WatchlistRefreshButton` → `requestWatchlistRefresh`.
- Both read `refresh_pending` / `refresh_status` from the backend; neither
  keeps authoritative state in React or localStorage.

No duplicate frontend source matrix exists or was added: the pages do not
enumerate sources at all, they read the verdict the backend supplies.

## PBS / ABC iview — audited, no defect demonstrated

```text
PBS:        ProviderContent 252,  Offers 0,  SyncRuns 0
ABC iview:  ProviderContent 1594, Offers 0,  SyncRuns 0
```

The persistence path is:

```text
collector → ProviderContent → ContentMapping → tracked content → Offer
```

An `Offer` is written only for provider-native content that maps to a tracked
title. These collectors have persisted thousands of provider-native titles that
are not in the local catalogue (825 `Movie`, 191 watchlist items), so zero
offers is the expected result of no overlap, not a dropped write. Confirmed by
the same measurement that cleared the mapping-rate question: first-party
collectors legitimately discover titles nobody has asked about yet.

No broad crawl was performed to manufacture overlap, and neither collector was
changed.

## Not changed

- `tubi`: no DB row, 0 data, deliberately deferred. It appears in the
  scheduled-source list, which is inert without a row.
- `mediaset_infinity` / `youtube` `supports_catalogue_sync=False`: title-level
  sources by design, consistent with their usage.
- The availability classifier, source policy, P6a/P6b, country inference, and
  the P8 JustWatch architecture are untouched.