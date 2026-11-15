# Stage 2D — Accuracy Results

Comparison of the 30-case Stage 2C cohort against real application canonical data,
using the merged Stage 2C audit command (`--offline`, read-only transaction).
Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
Code SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f`.

The command made 0 HTTP requests and consumed 0 Streaming Availability / Watchmode API calls.

## Comparison classification counts

| Classification | Count | Meaning |
|---|---:|---|
| `AGREEMENT_CONFIRMED` | 0 | Fresh verified canonical evidence matches external provider, country, offer class |
| `CONFLICT_CONFIRMED` | 0 | Authoritative external negative contradicts a verified same-provider canonical offer |
| `APP_DATA_MISSING` | 4 | Local Movie row absent; no relevant canonical positive offer |
| `APP_DATA_STALE` | 0 | Matching provider/class present only as stale evidence |
| `APP_SOURCE_DISABLED` | 0 | Explicit app source persisted and disabled |
| `INSUFFICIENT_APP_EVIDENCE` | 0 | Same-provider evidence exists but class/freshness insufficient |
| `INSUFFICIENT_EXTERNAL_EVIDENCE` | 26 | External evidence unverified, not provider+class specific, or absent |
| `NOT_COMPARABLE` | 0 | TMDb/media_type/year/country disagreement |

Total: 30 cases.

## Stage 2B external evidence baseline (for reference)

- 30 title-country cases
- 14 explicit positive external listings
- 16 externally unverified cases
- No direct playback confirmation
- No confirmed negative availability

## What the results show

**No confirmed agreements and no confirmed conflicts.**
- `AGREEMENT_CONFIRMED: 0`. Even for the 8 cases where the app has both a local Movie
  and canonical offers, none could be confirmed because (a) all stored canonical
  offers are STALE (none fresh/verified), and (b) the 14 explicitly positive external
  listings are aggregator-level — they name multiple providers and/or multiple offer
  classes without an unambiguous one-provider/one-class mapping — so they fail the
  comparison's "one provider, one offer class" requirement.
- `CONFLICT_CONFIRMED: 0`. Stage 2B contains no confirmed negative availability, so no
  conflict is possible.

**Missing vs insufficient evidence are not errors.**
- `APP_DATA_MISSING: 4` (The Platform IT, Stranger Things IT, Dark IT, Dark NL): the
  exact local Movie identity is absent. These are classified as missing coverage, not
  as confirmed unavailability.
- `INSUFFICIENT_EXTERNAL_EVIDENCE: 26`: the external evidence cannot be used to confirm
  or refute the app. This is a limitation of the external evidence (aggregator-level,
  no provider+class pairing, no playback), not proof the app is wrong.

## Why no agreement is possible with current data

Two independent blockers:

1. **No fresh canonical evidence.** All 10,990 canonical offers are STALE (last observed
   2026-09-30 to 2026-11-01; today is 2026-11-14). `AGREEMENT_CONFIRMED` requires
   `AvailabilityEvidence.VERIFIED` (fresh within the source's declared refresh window).
   Zero offers qualify.

2. **External evidence is not provider+class specific.** All 14 positive Stage 2B listings
   name multiple providers (e.g. Godfather IT: "Paramount+, Now TV, Infinity Selection
   Amazon Channel, Paramount+ Amazon Channel") or multiple offer classes ("subscription +
   rent/buy"), with provider names sometimes not extractable. None associates exactly one
   provider with exactly one offer class, so each returns
   `INSUFFICIENT_EXTERNAL_EVIDENCE` before the app's data is even examined.

## Distinctions preserved (per mission)

- Missing offers are NOT classified as confirmed unavailability.
- JustWatch's omission of a provider is NOT treated as authoritative negative evidence.
- No real playback or language compatibility is claimed from a catalogue listing alone.
- No case was forced into a confirmed comparison where provider→class mapping is ambiguous.
- `APP_DATA_MISSING` (no Movie row) is kept separate from `INSUFFICIENT_EXTERNAL_EVIDENCE`.

## Conclusion on application accuracy

The audit command runs successfully and is read-only/offline, but success does NOT
establish accuracy. With the current real data:

- **Measured accuracy:** 0 confirmed agreements, 0 confirmed conflicts across 30 cases.
- **Establishable accuracy:** None for this cohort, because the external evidence is
  too coarse to confirm anything and the app's canonical offers are all stale.
- **What is measurable:** coverage (14/30 movies present), source enablement, and
  staleness (0 fresh offers). These are data/operations findings, not accuracy proof.

## Files
- `docs/audits/stage2d/comparison.csv` — per-case comparison classifications (command output)
- `docs/audits/stage2d/application_snapshot.csv` — per-offer canonical snapshot (command output)
- `docs/audits/stage2d/database_coverage.md` — aggregate database measurements
- `docs/audits/stage2d/source_diagnostics.md` — source coverage and sync diagnostics
- `docs/audits/stage2d/confirmed_defects.md` — defects and risks
- `docs/audits/stage2d/next_actions.md` — recommended engineering tasks
