# Stage 2D — Confirmed Defects and Risks

Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
Code SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f`.

## Confirmed defects (reproducible against real application data)

**None.** No application code defect was reproduced against the real `tracker`
database. All observed shortfalls are data/operations or evidence-coverage gaps,
not proven defects in the availability logic or collectors.

The audit command itself ran successfully, read-only and offline; command success
is not a correctness claim.

## Measured gaps (data / operations, not code defects)

These are reproducible observations against the real database:

### G1 — Zero fresh (verified) canonical offers
All 10,990 canonical `availability_offer` rows are STALE
(`last_seen` 2026-09-30 to 2026-11-01; today 2026-11-14). Zero offers are
`AvailabilityEvidence.VERIFIED`.

Consequence: `AGREEMENT_CONFIRMED` is impossible for every cohort case, because the
comparison requires fresh verified canonical evidence matching provider, country,
and offer class. This is the single largest blocker to establishing accuracy.

Likely cause: no source refresh has run recently enough relative to the declared
refresh windows (JustWatch 12 h, Watchmode 24 h). Not proven to be a collector code
defect.

### G2 — Catalogue coverage gap
16 of 30 cohort titles have no `tracker_movie` row:
The Godfather (238), The Matrix (603), The Longest Yard (9291), Parasite (496243),
The Platform (619264), Coherence (300602), Stranger Things (66732), Dark (70523),
The Wire (1438).

These are `APP_DATA_MISSING` (missing coverage), not confirmed unavailability.

### G3 — Offers without a Movie anchor
Four NL cases have canonical offers (JustWatch/Netflix subscription) but no
`tracker_movie` row: The Matrix (603), The Platform (619264), Stranger Things
(66732), Dark (70523). Offers exist for these TMDb IDs but the titles are not
anchored in the Movie catalogue, so they cannot be matched to cohort identity.

### G4 — Sources enabled but never run
`abc_iview`, `legacy_scraper`, `pbs` are present and enabled but have 0 sync runs.
No ingestion has been executed for them.

### G5 — Source with no persisted row
`public_domain_archive` is registered but has no `Source` row; enabled state is
unknown.

## Risk review (D1–D5)

All risks investigated against real data; none reproduced as a code defect:

- **D1 — Dune (1984 vs 2021):** `tracker_movie` contains Dune (438631, 2021),
  Dune: Part Two (693134, 2024), Dune: Part Three (1170608, 2026). No Dune 1984
  record exists. The cohort's 438631 is correctly Dune 2021. **No title conflation
  reproduced.**
- **D2 — Localized titles (Dark / I segreti di Winden; The Platform / Il buco):**
  Neither Dark (70523) nor The Platform (619264) has a `tracker_movie` row. NL
  offers exist (JustWatch/Netflix) but without a Movie anchor (G3). Localized
  titles are not resolvable to a catalogue identity. **Unverified coverage risk,
  not a defect.**
- **D3 — Prime Video territory:** Prime Video (provider `prime`) has IT offers
  (24) and NL offers (236), correctly country-scoped. Prime Video IT does NOT
  carry cohort Dune (438631); Prime Video NL does. No NL→IT territorial leakage
  detected for cohort titles. **No leakage reproduced.**
- **D4 — Multi-provider (The Godfather 238):** The Godfather has exactly one
  canonical offer, in AU (Australia); none in IT or NL. Cannot exercise the
  multi-provider (Prime base/add-on, Paramount+, rental/purchase) path with live
  cohort data. **Untestable against this cohort.**
- **D5 — Source availability:** Covered by G4/G5 above. `abc_iview`, `legacy_scraper`,
  `pbs` never run; `public_domain_archive` has no row. All other sources ran
  successfully with 0 failures. **No source failure or persistence defect.**

## Unverified risks (hypothetical, not reproduced)

- Whether collectors would correctly map localized titles (Dark, The Platform) if
  Movie rows existed — not testable without Movie rows and without new collection.
- Whether Prime Video add-on vs base entitlement is distinguished correctly — no
  Godfather IT/NL data to exercise it.
- Whether a refreshed source would produce VERIFIED (fresh) offers — staleness is a
  data/operations observation; fixing it requires a refresh run, which is out of
  scope for this audit and would be a separate task.

No source collectors or availability logic were modified during this audit.
