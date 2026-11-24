# Issue #97 — Independent Availability Verification Report (Stage C)

**Agent:** independent-availability-verification  
**Issue:** #97  
**Status:** Draft — offline evidence analysis only  
**Branch:** from master `c2ead76`  
**Do not merge, close #97, or modify production data.**  
**No live provider/API calls. No production database changes.**  
**Agent A's sanitized evidence package is NOT available in this repository; no canary output was obtained.**

---

## 1. Scope and method

Tasked to inspect existing #97 evidence manifests and accepted/rejected observations, identify which preserved first-party provider observations can independently corroborate or contradict the new Inception/Dune canary results (56 source-reported NL offers, all labeled `SAME_SOURCE_ECHO` and `UNLICENSED`), assess evidence independence/provenance/timestamps/comparison window, match exact typed TMDb identity + NL territory + provider/service + offer class, distinguish offer classes (subscription, channel add-on, rent, buy, free, ad-supported), identify missing evidence and stale comparisons, and prepare a corrected reconciliation matrix for population when Agent A's sanitized canary evidence becomes available.

Only existing evidence was used. No application output, no production data, no fresh provider calls.

---

## 2. Timestamp comparison window (Stage C correction)

### 2.1 Reference windows

| Source | Window | Basis |
|---|---|---|
| Official-provider first-party observations | `2026-11-23T15:00:00Z` to `2026-11-23T16:00:00Z` | `issue97-evidence-manifest.json`, `issue97-reconciliation-summary.json` |
| JustWatch canary (Notte function `fe1b97b6`) | `2026-11-24` (resolved `2026-11-24T15:02:43.265875+00:00`; redeployed `2026-11-24T15:04:34.589983Z`) | `spec/fe1b97b6.yaml` |
| Clone DB snapshot date | `2026-11-22` | `issue97-reconciliation-summary.json` |

### 2.2 Elapsed time

- From the **start** of the official-provider reference hour (`2026-11-23T15:00:00Z`) to the canary resolution (`2026-11-24T15:02:43Z`): **24 hours 2 minutes 43 seconds**.
- From the **end** of the official-provider reference hour (`2026-11-23T16:00:00Z`) to the canary resolution: **23 hours 2 minutes 43 seconds**.

### 2.3 Comparison-tolerance rule

No previously established comparison-tolerance rule exists in the retained #97 evidence. The `issue97-evidence-manifest.json`, `issue97-reconciliation-summary.json`, `issue97-final-verification-report.md`, and all stage2b decision documents define the reference hour as `2026-11-23T15:00:00Z`–`2026-11-23T16:00:00Z` and make no provision for tolerating an off-window canary.

### 2.4 Consequence

Without a comparison-tolerance rule, the seven preserved first-party provider/offer pairs are classified as **historical candidates**, not contemporaneous accuracy confirmations. They may be matched against Agent A's canary evidence when it becomes available, but no agreement/conflict/accuracy claim can be made at this time.

---

## 3. Existing evidence inventory

### 3.1 Accepted first-party observations (14 rows)

Source: `docs/audits/stage2b/issue97-accepted-observations.csv` (SHA-256 `560ceaf1f3edf4b2c6b33988dd578acbd76a38c4d18e3a49220e92005961a389`). All are NL territory. All observed within the reference hour.

Relevant to Inception/Dune specifically (rows 5–11):

| ID | Title | TMDb | Media | Year | Country | Provider | Canonical slug | Offer class | Provider title ID | Observed UTC | Price EUR | Django result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| inception_nl-netflix-subscription | Inception | 27205 | movie | 2010 | NL | Netflix | netflix | subscription | 70131314 | 2026-11-23T15:32:01Z | — | APP_DATA_STALE |
| OBS-01-prime-subscription | Inception | 27205 | movie | 2010 | NL | Prime Video | prime | subscription | amzn1.dv.gti.04a9f72c-… | 2026-11-23T15:12:31Z | — | APP_DATA_STALE |
| OBS-01-prime-rent | Inception | 27205 | movie | 2010 | NL | Prime Video Store | prime | rent | amzn1.dv.gti.04a9f72c-… | 2026-11-23T15:12:31Z | 2.99 | INSUFFICIENT_APP_EVIDENCE |
| OBS-01-prime-buy | Inception | 27205 | movie | 2010 | NL | Prime Video Store | prime | buy | amzn1.dv.gti.04a9f72c-… | 2026-11-23T15:12:31Z | 4.99 | INSUFFICIENT_APP_EVIDENCE |
| OBS-01-max_amazon-addon | Inception | 27205 | movie | 2010 | NL | HBO Max via Prime | max_amazon | addon | amzn1.dv.gti.04a9f72c-… | 2026-11-23T15:12:31Z | — | INSUFFICIENT_APP_EVIDENCE |
| OBS-06-rakuten_tv-rent | Dune | 438631 | movie | 2021 | NL | Rakuten TV | rakuten_tv | rent | dune-2021 | 2026-11-23T15:14:34Z | 2.99 | APP_DATA_STALE |
| OBS-06-rakuten_tv-buy | Dune | 438631 | movie | 2021 | NL | Rakuten TV | rakuten_tv | buy | dune-2021 | 2026-11-23T15:14:34Z | 9.99 | INSUFFICIENT_APP_EVIDENCE |

All 13 NL advertised positives are unresolved against a fresh application row. Django audit results across all 13 accepted pairs: 5 APP_DATA_STALE, 3 APP_DATA_MISSING, 5 INSUFFICIENT_APP_EVIDENCE.

### 3.2 Rejected first-party observations (8 rows)

Source: `docs/audits/stage2b/issue97-rejected-observations.csv` (SHA-256 `f2ae67aed74f819ddd0332c671b7fbe01d5856e6ec86bf69859525dcc3261437`). None establish a positive offer; none establish a title-level negative.

Relevant to Inception/Dune:
- OBS-08-netflix-subscription: Inception, NL, Netflix, subscription — AUTH_NO_OBSERVATION (login wall on search page, not a title observation).
- inception_apple_it-apple_tv_store-rent: Inception, IT, Apple TV Store, rent — CATALOGUE_ONLY (wrong territory, no transactional offer).
- dune_it-rakuten_tv-rent: Dune, IT, Rakuten TV, rent — SHELL_ONLY (wrong territory, generic client shell).
- dune_au-max-subscription: Dune, AU, HBO Max, subscription — REGION_CONDITIONAL_UNKNOWN (wrong territory).

### 3.3 Historical Watchmode pilot (166 NL rows, Nov 22 clone)

Source: `issue97-provider-reconciliation.csv` (watchmode_pilot layer, SHA-256 `ac1ec4c8d062a87f656e7356a030ddb64a01d09f2be2188c00c8b7f08606fd81`).

- All 37 stored raw rows have last_seen between Oct 2–24, 2026 — **all stale**, all outside the reference hour.
- Watchmode app IDs explicitly excluded from independent corroboration: 10165, 10166, 6170, 10858.
- All historical offers are outside the reference hour; must not be used as independent confirmation.

### 3.4 Identity manifest (15 titles)

Source: `docs/audits/stage2b/identity_manifest_20261122.csv` (SHA-256 `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925`).

- **27205** = Inception, movie, 2010
- **438631** = Dune, movie, 2021

### 3.5 Evidence hash verification

| Artifact | Path | SHA-256 |
|---|---|---|
| Accepted observations | `docs/audits/stage2b/issue97-accepted-observations.csv` | `560ceaf1f3edf4b2c6b33988dd578acbd76a38c4d18e3a49220e92005961a389` |
| Rejected observations | `docs/audits/stage2b/issue97-rejected-observations.csv` | `f2ae67aed74f819ddd0332c671b7fbe01d5856e6ec86bf69859525dcc3261437` |
| Evidence manifest | `docs/audits/stage2b/issue97-evidence-manifest.json` | `ab585c1bf218c16fd4014983920a07f0dad42570ef352abdcde5259d7ab96bc1` |
| Reconciliation summary | `docs/audits/stage2b/issue97-reconciliation-summary.json` | `3e3e599911ff6363f7aa1ca9b95282923c114f9f385f03d05cababf6df2575c1` |
| Provider reconciliation | `docs/audits/stage2b/issue97-provider-reconciliation.csv` | `ac1ec4c8d062a87f656e7356a030ddb64a01d09f2be2188c00c8b7f08606fd81` |
| Identity manifest | `docs/audits/stage2b/identity_manifest_20261122.csv` | `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925` |
| NL cohort | `docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv` | `abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b` |

All hashes verified against `git` working tree on master `c2ead76`.

---

## 4. Seven candidate pairs provenance (Stage C correction)

The seven accepted first-party observations for Inception/Dune NL derive from **three underlying provider-page captures**, not seven independent captures:

| Capture # | Provider page URL | Observed UTC | Title(s) | Offer observations derived |
|---|---|---|---|---|
| C-1 | `https://www.netflix.com/nl/title/70131314` | 2026-11-23T15:32:01Z | Inception | Netflix subscription (1 observation) |
| C-2 | `https://www.primevideo.com/detail/0FFJSDLK6HUWC5T8XJUQODMFJ3` | 2026-11-23T15:12:31Z | Inception | Prime subscription, Prime rent (€2.99), Prime buy (€4.99), Max Amazon addon (4 observations) |
| C-3 | `https://www.rakuten.tv/nl/movies/dune-2021` | 2026-11-23T15:14:34Z | Dune | Rakuten TV rent (€2.99), Rakuten TV buy (€9.99) (2 observations) |

**Total: 3 captures → 7 offer observations.**

Each capture is a direct official-provider page, anonymous (no login), no playback, no entitlement. Each is independent of aggregators (JustWatch/Watchmode/Streaming Availability) because it comes from the provider's own title page at a distinct UTC instant within the reference hour.

### 4.1 What is NOT independently verifiable

- JustWatch-derived application rows: circular if used to confirm JustWatch canary output.
- Watchmode historical rows: stale (Oct 2–24, 2026); Watchmode app IDs 10165/10166/6170/10858 explicitly excluded.
- Application display `needs_recheck`/`unknown` statuses: display-retrieval measurements on a frozen stale clone, not accuracy findings.
- Rejected observations: none establish a positive offer; none establish a negative.

---

## 5. Agent A evidence status (Stage C correction)

**Agent A's sanitized 56-offer evidence package is NOT available in this repository.** No canary output file, no sanitized evidence package, and no Agent A-produced artifact was found under `docs/`, `spec/`, `scripts/`, or `backend/`. The Notte function spec (`spec/fe1b97b6.yaml`, `spec/fe1b97b6.py`, `spec/fe1b97b6_v2.py`) documents the canary function's interface and labels (`SAME_SOURCE_ECHO`, `UNLICENSED`) but contains no execution results or offer records.

Consequence: the reconciliation matrix below cannot be populated. All `canary_*` fields remain blank pending Agent A's corrected evidence package.

---

## 6. Matching criteria

When Agent A's sanitized canary evidence becomes available, match each canary row against the matrix using **all** of the following, exactly and case-sensitively where applicable:

1. `tmdb_id` — exact typed TMDb identity (27205 for Inception, 438631 for Dune)
2. `media_type` — `movie`
3. `year` — canonical year (2010 for Inception, 2021 for Dune)
4. `country` — `NL`
5. `provider_slug` — canonical provider slug (`netflix`, `prime`, `max_amazon`, `rakuten_tv`)
6. `offer_class` — `subscription`, `addon`, `rent`, or `buy`
7. `price` / variant fields — relevant price or variant fields where observed (e.g. €2.99, €4.99, €9.99)

No match is valid on title alone. No match is valid on provider+title without offer class.

---

## 7. Provisional reconciliation matrix (corrected CSV)

See `docs/audits/stage2b/issue97-reconciliation-matrix-template.csv`.

### 7.1 Matrix columns

| Column | Meaning |
|---|---|
| `pair_id` | Stable row key (e.g. `INC-27205-NL-netflix-subscription`) |
| `tmdb_id` | Exact typed TMDb identity |
| `media_type` | `movie` |
| `year` | Canonical year |
| `country` | `NL` |
| `provider_slug` | Canonical provider slug |
| `offer_class` | `subscription`, `addon`, `rent`, `buy` |
| `provider_title_id` | Provider's own title identifier |
| `first_party_offered` | `yes` if a preserved first-party page capture advertises this exact pair |
| `first_party_capture_id` | Underlying capture (C-1/C-2/C-3) |
| `first_party_observed_utc` | Capture timestamp (reference hour) |
| `first_party_price` | Price EUR if observed |
| `temporal_eligible` | `no` — canary (Nov 24) is outside reference hour (Nov 23 15:00–16:00 UTC) and no tolerance rule exists |
| `canary_reported` | `yes`/`no` — blank pending Agent A evidence |
| `canary_label` | **Left empty** until a specific canary row is matched |
| `canary_source` | Canary source name — blank pending Agent A evidence |
| `canary_observed_utc` | Canary observation timestamp — blank pending Agent A evidence |
| `stale_watchmode_match` | `yes` if a stale (Oct 2026) Watchmode row matches this exact pair |
| `verdict` | `matched` / `unmatched` / `stale` / `unverified` / `unsupported` — blank pending Agent A evidence |

### 7.2 Classification rules

1. Exact match on `(tmdb_id, media_type, year, country, provider_slug, offer_class)` → locate the row.
2. `temporal_eligible` is `no` for all rows because the canary (Nov 24) falls outside the reference hour (Nov 23 15:00–16:00 UTC) and no comparison-tolerance rule exists.
3. If canary label is `SAME_SOURCE_ECHO` or `UNLICENSED`: set `verdict = unsupported` (circular / not independently verifiable). Do not record as agreement or conflict.
4. If canary label is an independent source and matches a `first_party_offered = yes` row with the same offer class: candidate `matched` (advertised-only, not entitlement/playback), subject to temporal eligibility.
5. If canary reports a pair with no first-party row and no stale Watchmode match: `unverified` (no preserved evidence to compare).
6. If canary reports a pair that conflicts with a `first_party_offered = yes` row: `unmatched` (requires source provenance review).
7. Never infer `unmatched` from a missing first-party row (absence of capture ≠ unavailability).

---

## 8. Offer-class distinction

| Class | Meaning | Present in preserved evidence for Inception/Dune NL? |
|---|---|---|
| subscription | SVOD access (base service) | Inception: Netflix (yes), Prime Video (yes). Dune: none accepted (Watchmode stale only). |
| addon / channel | Separately sold channel via another service | Inception: HBO Max via Prime (max_amazon, PV_CHANNEL/wbdmaxnl). Dune: none. |
| rent | TVOD rental | Inception: Prime (€2.99). Dune: Rakuten TV (€2.99). |
| buy | TVOD purchase | Inception: Prime (€4.99). Dune: Rakuten TV (€9.99). |
| free | FAST/ad-supported free | None accepted for Inception/Dune NL. |
| free_with_ads / ad-supported | Ad-supported tier within subscription | Not separately distinguished; Netflix `isPlayableOnAdsPlan=true` noted but not treated as a separate class. |

No free or ad-supported offers are independently evidenced for Inception/Dune NL in the preserved record.

---

## 9. Evidence gaps and stale comparisons

### 9.1 Missing evidence (no preserved first-party capture)

| Gap | Title | Provider | Offer class | Territory | Why missing |
|---|---|---|---|---|---|
| G1 | Dune | Rakuten TV | subscription | NL | No Rakuten subscription row for Dune NL; only rent/buy accepted |
| G2 | Inception/Dune | Any free/ad-supported | free / free_with_ads | NL | No accepted free offers for either title NL |
| G3 | Dune | Prime Video / max / max_amazon | any | NL | Stale Watchmode rows only; no first-party Prime/HBO Max capture for Dune NL |
| G4 | Inception | Rakuten TV / pathe_thuis / meJane / Apple TV | rent/buy | NL | Stale Watchmode rows only; no first-party capture |
| G5 | Inception/Dune | All providers | any | IT | Rejected: dune_it shell only, platform_it market redirect, inception_apple_it catalogue only |
| G6 | Inception/Dune | All providers | any | AU/CA/GB/US | Rejected: dune_au region conditional, office_us regional denial, office_uk wrong work, farewell_ca trailer only |

**Correction from prior draft:** Inception Netflix subscription is NOT missing evidence — it is captured as C-1 (2026-11-23T15:32:01Z) and is a valid first-party observation. The prior draft's G1 misclassified it as missing; that is corrected here.

### 9.2 Stale comparisons (outside reference hour)

All Watchmode historical rows for Inception and Dune NL have last_seen between Oct 2–24, 2026 (~30–40 days before the Nov 23 reference hour). They are **stale overlaps** per the reconciliation summary (`STALE_OR_UNVERIFIED_OVERLAP`, 10 rows; 17 distinct canonical pairs). They provide no current evidence and must not be used to confirm or deny canary output.

### 9.3 Application display state (frozen clone, not current)

- Inception NL: `needs_recheck`, app IDs 7728;10165;5481;10166;10149, last-known pairs `max/subscription/stale;prime/subscription/stale;netflix/subscription/stale`.
- Dune NL: `needs_recheck`, app IDs 7733;5188;6170;10858;2844;2846;2845;10572, last-known pairs `prime/addon/stale;prime/rent/stale;prime/buy/stale;rakuten_tv/rent/stale;max/subscription/stale`.
- Zero current offers returned across all 34 cases. These are display-retrieval results, not accuracy findings.

---

## 10. Most useful independently verifiable title/provider pairs

| Rank | Title | TMDb | Provider (slug) | Offer class | Provider title ID | First-party capture? | Capture | Value |
|---|---|---|---|---|---|---|---|---|
| 1 | Inception | 27205 | Netflix (netflix) | subscription | 70131314 | Yes | C-1 | Direct title-page capture inside reference hour |
| 2 | Dune | 438631 | Rakuten TV (rakuten_tv) | rent | dune-2021 | Yes | C-3 | Direct storefront capture with price inside reference hour |
| 3 | Inception | 27205 | Prime Video (prime) | subscription | amzn1.dv.gti.04a9f72c-… | Yes | C-2 | GTI + NL territory + isPrime=true inside reference hour |
| 4 | Dune | 438631 | Rakuten TV (rakuten_tv) | buy | dune-2021 | Yes | C-3 | Direct storefront capture with price inside reference hour |
| 5 | Inception | 27205 | Prime Video Store (prime) | rent | amzn1.dv.gti.04a9f72c-… | Yes | C-2 | RENTAL action with price inside reference hour |
| 6 | Inception | 27205 | Prime Video Store (prime) | buy | amzn1.dv.gti.04a9f72c-… | Yes | C-2 | PURCHASE action with price inside reference hour |
| 7 | Inception | 27205 | HBO Max via Prime (max_amazon) | addon | amzn1.dv.gti.04a9f72c-… | Yes | C-2 | PV_CHANNEL/wbdmaxnl channel option inside reference hour |

Pairs **not** independently verifiable from preserved evidence (no first-party capture): Dune on Prime/max/max_amazon NL; Inception on Rakuten/pathe_thuis/meJane/Apple TV NL; any free/ad-supported offer for either title NL; all IT/AU/CA/GB/US territory pairs for these titles.

---

## 11. Counts (Stage C correction)

Counts distinguish `0 observed` (explicitly measured as zero), `not evaluated` (could not be evaluated due to missing evidence), and `undefined` (mathematically undefined, no valid denominator). No agreements, conflicts, precision, recall, false positives, or false negatives are manufactured.

| Metric | Value | Basis |
|---|---|---|
| Preserved first-party positives for Inception/Dune NL | 7 observed | Accepted-observations rows for Inception/Dune NL |
| Underlying provider-page captures | 3 observed | C-1 Netflix, C-2 Prime, C-3 Rakuten TV |
| Offer observations derived from captures | 7 observed | 1 from C-1, 4 from C-2, 2 from C-3 |
| Temporally eligible comparisons | 0 not evaluated | Canary (Nov 24) outside reference hour; no tolerance rule |
| Canary rows available for matching | 0 not evaluated | Agent A evidence package not in repository |
| Agreements (canary vs first-party) | undefined | No independent canary evidence received |
| Conflicts (canary vs first-party) | undefined | No independent canary evidence received |
| Unmatched comparisons | not evaluated | Pending canary evidence |
| Unverified comparisons | not evaluated | Pending canary evidence |
| Unsupported (SAME_SOURCE_ECHO / UNLICENSED) | not evaluated | Pending canary evidence |
| Confirmed negative pairs | 0 observed | No first-party or independent source establishes unavailability |
| Entitlement observations | 0 observed | No entitlement evidence was collected |
| Playback observations | 0 observed | No playback was attempted |
| Rates (precision, recall, F1, FP, FN) | undefined | No valid denominator; no fresh independent comparable pairs |

---

## 12. Document corrections (Stage C)

The following inconsistencies from the prior draft are corrected:

1. **Section numbering:** Prior draft used broken numbering (5.1, 5.2, 5.3 nested under section 6). Sections are now sequentially numbered 1–12.
2. **Misleading gap classification:** Prior draft G1 classified Inception Netflix subscription as "missing evidence." Corrected: it is a valid first-party capture (C-1, 2026-11-23T15:32:01Z, provider page `netflix.com/nl/title/70131314`). The only genuine gap related to Inception Netflix is the absence of a *second* independent capture, not absence of any capture.
3. **`canary_label` pre-filled:** Prior draft CSV pre-filled `SAME_SOURCE_ECHO` for all rows. Corrected: `canary_label` is left empty until a specific canary row is matched.
4. **Temporal eligibility:** Prior draft did not address the Nov 23 vs Nov 24 timestamp mismatch. Corrected: `temporal_eligible = no` for all rows; no comparison-tolerance rule exists.
5. **Counts terminology:** Prior draft used `0` ambiguously. Corrected: distinguishes `0 observed`, `not evaluated`, and `undefined`.
6. **Seven candidate pairs provenance:** Prior draft described seven observations as if from seven captures. Corrected: seven observations derive from three provider-page captures (C-1/C-2/C-3).

---

## 13. Conclusions and readiness

1. **No agreement or conflict can be claimed yet.** Agent A's sanitized evidence package is not available; all 56 canary rows are described as `SAME_SOURCE_ECHO`/`UNLICENSED` (circular) and cannot independently confirm or deny the preserved first-party observations.
2. **Seven first-party Inception/Dune NL pairs** are preserved inside the reference hour (15:00–16:00 UTC, Nov 23, 2026), deriving from three provider-page captures. They are historical candidates only.
3. **Timestamp mismatch:** Canary (Nov 24) is ~24 hours after the reference hour (Nov 23 15:00–16:00 UTC). No comparison-tolerance rule exists. Comparisons are not temporally eligible.
4. **The corrected reconciliation matrix** is ready to populate when Agent A's sanitized canary evidence arrives.
5. **#97 remains OPEN; #103 remains ON HOLD.** No production accuracy claim is permitted.

**Readiness for Stage D/E:** Not ready. Stage D/E require Agent A's corrected sanitized evidence package and a comparison-tolerance rule (or a contemporaneous observation window) before reconciliation can proceed.

---

**Report location:** `docs/audits/stage2b/issue97-independent-verification-report.md`  
**Matrix location:** `docs/audits/stage2b/issue97-reconciliation-matrix-template.csv`  
**Draft PR:** None created yet — see delivery step.
