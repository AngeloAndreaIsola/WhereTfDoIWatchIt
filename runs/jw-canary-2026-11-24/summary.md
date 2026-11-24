# JW-CANARY-v1b — canary run summary

Verdict: **complete after retry** — 2 of 2 requested title/market attempts returned records. The first Inception attempt failed on a deployed-function import error and was retried after redeploy; batch-01 remains as historical evidence.

- Collection calls: 3 of 6. Validation probes: 1 of 3.
- Function version event: v20261124_135310 -> v20261124_150434; source code hash unchanged.
- Raw records: 56. Offers merged: 56 (0 exact duplicates removed). Rejects: 0. Near-duplicate price groups: 0. Country normalizations: 56.
- Every offer carries `SAME_SOURCE_ECHO` and `UNLICENSED`.
- Metrics class: `MEASURED_AGAINST_UNVERIFIED_SOURCE`; **never PROVEN**.
- Collected market: NL only. Discovery locale `us` used because `run()` rejects `reference_market=nl`.
- No rate-limit flags; no non-NL market requested.
- Dedup key extended with `title`/`jw_id`: the literal brief key would collapse 22 cross-title price/provider matches between Inception and Dune.

## Top providers by country
- NL: Amazon Video — 6
- NL: Apple TV Store — 6
- NL: Rakuten TV — 6
- NL: Pathé Thuis — 4
- NL: HBO Max — 3
- NL: HBO Max Amazon Channel — 3
- NL: Amazon Prime Video — 2
- NL: Netflix — 2
- NL: meJane — 2

## UNKNOWN buckets by field
- audio_languages: 2
- subtitle_languages: 2

## Limitations
- JustWatch is the sole source; no independent confirmation.
- No entitlement or playback observed; advertised offers only.
- No accuracy rate computed without an independent comparator.
