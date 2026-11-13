# Stage 2C coverage summary

Baseline: `0da80a3f86555206670e74d7b4344bb21fa22111`

Cohort cases: 30. Movie rows found: 0; missing: 30. Canonical raw offer rows: 0; lifecycle-current rows: 0; stale rows: 0. Synthetic resolver results returned: 30 cases across Profiles B and C. Cases with a local Movie plus canonical offers to evaluate: 0.

Stage 2B explicit positive listings: 14; cases without explicit positive external evidence: 16.

Countries in cohort: IT, NL. Countries represented by stored offers: none. Enabled source rows: justwatch, watchmode. Registered sources with no persisted Source row: abc_iview, bbc_iplayer, cbc_gem, mediaset_infinity, pbs, pluto_tv, prime_video, public_domain_archive, rai_play, streaming_availability, tubi, youtube. Persisted but disabled sources: none.

## Comparison states

- AGREEMENT_CONFIRMED: 0
- APP_DATA_MISSING: 4
- APP_DATA_STALE: 0
- APP_SOURCE_DISABLED: 0
- CONFLICT_CONFIRMED: 0
- INSUFFICIENT_APP_EVIDENCE: 0
- INSUFFICIENT_EXTERNAL_EVIDENCE: 26
- NOT_COMPARABLE: 0

The local database is an offline developer database, not production. No user subscriptions or preferences were read. Profile verdicts use in-memory synthetic preferences and the canonical `resolve_title` resolver. The audit command made 0 HTTP requests and consumed 0 Streaming Availability or Watchmode API calls.

## Reproduction

From `backend/`, run `python manage.py audit_availability_accuracy --cohort ../docs/audits/stage2c/cohort.csv --external-evidence ../docs/audits/stage2c/external_evidence.csv --output ../docs/audits/stage2c --offline`.
