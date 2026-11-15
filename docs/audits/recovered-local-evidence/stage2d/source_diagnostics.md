# Stage 2D — Source Diagnostics

Source coverage and persistence diagnosis for the real application database.
Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
All 14 registered sources have persisted `Source` rows except `public_domain_archive`.
All persisted sources are `enabled = true`; none are disabled.

## Source registry vs persisted rows

| Source | Type | Registered | Row present | Enabled | Sync runs | Last run status |
|---|---|---|---|---|---:|---|
| abc_iview | api | yes | yes | true | 0 | (never run) |
| bbc_iplayer | web | yes | yes | true | 4 | succeeded |
| cbc_gem | api | yes | yes | true | 18 | succeeded |
| justwatch | aggregator | yes | yes | true | 669 | succeeded |
| legacy_scraper | web | no | yes | true | 0 | (never run) |
| mediaset_infinity | api | yes | yes | true | 39 | succeeded |
| pbs | api | yes | yes | true | 0 | (never run) |
| pluto_tv | api | yes | yes | true | 5 | succeeded |
| prime_video | web | yes | yes | true | 7 | succeeded |
| rai_play | web | yes | yes | true | 7 | succeeded |
| streaming_availability | api | yes | yes | true | 5,575 | succeeded |
| tubi | api | yes | yes | true | 1 | succeeded |
| watchmode | aggregator | yes | yes | true | 870 | succeeded |
| youtube | web | yes | yes | true | 3 | succeeded |

Registered, no persisted row (enabled state unknown):
- `public_domain_archive` (api)

Persisted but disabled: none.

## Sync-run diagnosis (all observed runs `succeeded`; 0 failures)

Dominant sources by sync-run count:
- `streaming_availability`: 5,575 runs (metered aggregator; dominant)
- `justwatch`: 669 runs (broad aggregator)
- `watchmode`: 870 runs (broad aggregator)
- `mediaset_infinity`: 39 runs (collector)
- `prime_video`: 7 runs (collector)
- `rai_play`: 7 runs (collector)

Minor / never-run sources:
- `pluto_tv`: 5 runs; `cbc_gem`: 18; `bbc_iplayer`: 4; `tubi`: 1; `youtube`: 3
- **Never run (0 sync runs) despite being enabled and present:** `abc_iview`, `legacy_scraper`, `pbs`

## Refresh interval configuration

| Source | expected_refresh_interval_seconds | Implication for freshness |
|---|---|---|
| justwatch | 43,200 (12 h) | All justwatch offers older than 12 h → stale |
| watchmode | 86,400 (24 h) | All watchmode offers older than 24 h → stale |

All 10,990 canonical offers were last observed between 2026-09-30 and 2026-11-01;
today is 2026-11-14. Every offer is outside its source's refresh window, hence all
are `STALE`. Zero `VERIFIED` (fresh) offers exist.

## Source attribution for cohort offers (30 canonical raw offer rows)

Sources that contributed offers to the 30 cohort cases:
- `justwatch` (aggregator) — Netflix, Max, Prime, Rakuten TV (IT/NL)
- `streaming_availability` (metered aggregator) — Max, Netflix, Disney+ (IT/NL)
- `watchmode` (aggregator) — Max (NL)
- `prime_video` (collector, provider `prime`) — Prime Video addon/buy/rent (NL)
- `mediaset_infinity` (collector) — Mediaset Infinity subscription (IT)

No cohort offers came from abc_iview, bbc_iplayer, cbc_gem, legacy_scraper, pbs,
pluto_tv, rai_play, tubi, watchmode (for the titles that matched), youtube, or
public_domain_archive.

## Persistence gap classification (D5)

Missing source coverage in this database reflects, per source:

- `abc_iview`, `legacy_scraper`, `pbs`: **source configured and enabled but never run**
  (0 sync runs). Not a code defect; no ingestion has been executed for them.
- `public_domain_archive`: **registered but no persisted Source row** — enabled state
  unknown; not a persistence failure of an existing row, just no row.
- `streaming_availability`, `justwatch`, `watchmode`, `mediaset_infinity`,
  `prime_video`, `rai_play`, `pluto_tv`, `cbc_gem`, `bbc_iplayer`, `tubi`, `youtube`:
  source has run successfully; coverage depends on title/country, not source absence.

No source failures (`failed`/`partial` sync runs) were observed; every recorded sync
run is `succeeded`. No persistence problem (deletion/migration failure) is evident —
10,990 canonical offers and 4,742 content mappings are persisted.

## What this does not establish

A source that has run successfully is not necessarily complete or correct for every
title/country. `INSUFFICIENT_EXTERNAL_EVIDENCE` results arise from the coarseness of
the external Stage 2B listings and the staleness of canonical offers, not from a
demonstrated source defect.
