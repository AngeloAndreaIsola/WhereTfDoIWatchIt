# Stage 2D — Database Coverage

Aggregate, SELECT-only measurements of the real application database
(`tracker` from the `wheretfdoiwatchit_pgdata` volume).
Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
No user subscriptions, watchlists, or secrets were inspected.

## Canonical table counts (application models / migrations)

| Table (model) | Rows | Notes |
|---|---:|---|
| `tracker_movie` (Movie) | 828 | Canonical title catalogue |
| `tracker_provider` (Provider) | 92 | Providers |
| `availability_source` (Source) | 14 | All rows present |
| `availability_offer` (Offer — canonical) | 10,990 | All `currently_available = true` |
| `availability_contentmapping` (ContentMapping) | 4,742 | Provider-to-TMDb mappings |
| `availability_country` (Country) | 34 | IT and NL both present |
| `availability_offerevidence` (OfferEvidence) | 613 | All `is_present=true`; 0 authoritative |
| `availability_syncrun` (SyncRun) | 7,198 | All observed runs `succeeded` |

## Legacy / non-canonical tables (distinguished, not treated as canonical)

| Table | Rows | Interpretation |
|---|---:|---|
| `tracker_availability` | 1,541 | Legacy availability cache — not canonical Offer storage |
| `tracker_scrapedcontentmapping` | 808 | Historical scraper records — not canonical mappings |

The existence of legacy rows does NOT make the canonical Stage 2C query able to see
them; the audit reads `availability_offer` only.

## Offer lifecycle / freshness (all 10,990 canonical offers)

| Metric | Value |
|---|---|
| Offers `currently_available = true` | 10,990 |
| Offers `currently_available = false` | 0 |
| Distinct countries with offers | 30 |
| Offers observed in Nov 2026 | 1 |
| Offers observed before Nov 2026 | 10,989 |
| `last_seen` range | 2026-09-30 to 2026-11-01 |
| Fresh (VERIFIED) offers | 0 |
| Stale offers | all 10,990 (last observed Sep 30 – Nov 1, 2026; today is Nov 14, 2026) |

All canonical offers are STALE relative to their source's declared refresh window
(JustWatch 12 h, Watchmode 24 h). There are zero fresh/verified offers in the
entire database, so no case can reach `AGREEMENT_CONFIRMED` (which requires fresh
verified canonical evidence) regardless of external evidence.

## Source registry vs persisted rows

All 14 registered sources have a persisted `Source` row except `public_domain_archive`
(registered, no row → enabled state unknown). All persisted rows are `enabled = true`;
none are disabled.

Enabled sources: `abc_iview, bbc_iplayer, cbc_gem, justwatch, legacy_scraper,
mediaset_infinity, pbs, pluto_tv, prime_video, rai_play, streaming_availability,
tubi, watchmode, youtube`.

Registered with no persisted row: `public_domain_archive`.

## Cohort coverage (30 Stage 2C cases, countries IT and NL)

| Metric | Value |
|---|---|
| Cohort cases | 30 |
| Local Movie rows found | 14 (7 distinct titles × 2 countries) |
| Local Movie rows missing | 16 |
| Canonical raw offer rows (scoped to cohort country+type) | 30 |
| Cases with a local Movie plus canonical offers | 8 |
| Countries represented by stored offers | IT, NL |

Titles present in `tracker_movie` (found in both IT and NL):
Breaking Bad (1396), The Office (2316), Inception (27205), Paddington (116149),
Dune (438631), The Farewell (565310), The Holdovers (840430).

Titles with NO `tracker_movie` row (missing from catalogue):
The Godfather (238), The Matrix (603), The Longest Yard (9291), Parasite (496243),
The Platform (619264), Coherence (300602), Stranger Things (66732), Dark (70523),
The Wire (1438).

## Notable coverage gap: offers without a Movie anchor

Four NL cases have canonical `availability_offer` rows (all from JustWatch/Netflix,
subscription) but NO corresponding `tracker_movie` row: The Matrix (603), The
Platform (619264), Stranger Things (66732), Dark (70523). Offers exist for these
TMDb IDs but the titles are not anchored in the Movie catalogue, so they cannot be
matched to the cohort identity and are reported missing.
