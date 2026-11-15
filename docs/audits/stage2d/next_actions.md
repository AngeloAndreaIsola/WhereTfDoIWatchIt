# Stage 2D — Recommended Next Actions

Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
These are engineering tasks suggested by the measurement; none are defects proven
in code, and none should be merged without a separate implementation task/PR.

## Highest priority (block accuracy establishment)

### 1. Restore fresh canonical evidence
All 10,990 canonical offers are STALE (last observed 2026-09-30 to 2026-11-01;
today 2026-11-14). Zero are VERIFIED.

Task: investigate why no source refresh has landed within the declared refresh
windows (JustWatch 12 h, Watchmode 24 h). Confirm refresh jobs are scheduled and
executing, then run a read-only re-check against the real database to confirm
offers transition to VERIFIED. Do NOT trigger upstream collection from this audit;
a refresh run is a separate operational decision.

Success criterion: at least one canonical offer returns `AvailabilityEvidence.VERIFIED`.

### 2. Close catalogue coverage gap
16 of 30 cohort titles have no `tracker_movie` row (The Godfather, The Matrix,
The Longest Yard, Parasite, The Platform, Coherence, Stranger Things, Dark,
The Wire). These are `APP_DATA_MISSING`, not confirmed unavailability.

Task: determine whether these titles should be in the Movie catalogue (import gap)
or are intentionally absent. If they should be present, backfill Movie rows; then
re-run the Stage 2C audit.

## Medium priority

### 3. Anchor offers to Movie rows
Four NL cases have canonical offers (JustWatch/Netflix) but no Movie row
(The Matrix, The Platform, Stranger Things, Dark). Offers exist for these TMDb IDs
but are unanchored.

Task: reconcile `availability_offer` rows to `tracker_movie` records (or create the
missing Movie rows) so TMDb-keyed cohort lookups succeed.

### 4. Execute never-run sources
`abc_iview`, `legacy_scraper`, `pbs` are enabled and present but have 0 sync runs.

Task: determine whether these sources are intended to run. If yes, schedule and run
them (read-only validation first), then confirm sync runs and any resulting offers.

### 5. Resolve `public_domain_archive` source row
Registered but no persisted `Source` row; enabled state unknown.

Task: decide whether `public_domain_archive` should be persisted and enabled, then
add the row if intended.

## Lower priority / cohort-limited

### 6. Exercise multi-provider path (D4)
The Godfather has only one offer (AU); none in IT/NL, so the multi-provider path
(Prime base vs add-on, Paramount+, rental vs purchase) could not be tested live.

Task: once catalogue coverage and freshness are restored, add The Godfather IT/NL
titles and verify distinct provider offer groups are stored distinctly.

### 7. Re-run Stage 2C audit after data changes
After any of the above, re-run the merged audit command against the real database
(read-only, `--offline`) and compare against Stage 2B:
`python manage.py audit_availability_accuracy --cohort docs/audits/stage2c/cohort.csv
--external-evidence docs/audits/stage2c/external_evidence.csv --output <isolated dir> --offline`

Success criterion for establishing accuracy: at least one `AGREEMENT_CONFIRMED`
case with matching provider, country, offer class, and fresh verified evidence.

## Notes
- Do not consume Streaming Availability / Watchmode API quota during these tasks
  unless explicitly authorized (the ladder is gated behind
  `AVAILABILITY_CONSULT_METERED_ONLY_WHEN_UNRESOLVED=0` by default).
- Do not restore, migrate, or overwrite the existing `tracker` database. Use an
  isolated disposable database with explicit authorization for any backup analysis.
- Keep private data and secrets out of all reports and PRs.
