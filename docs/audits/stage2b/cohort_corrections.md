# Stage 2B cohort correction record

## NL pilot v2 (2026-11-22)

| Title | Release year | Media type | Historical TMDb ID | Corrected TMDb ID |
| --- | --- | --- | --- | --- |
| Coherence | 2013 | movie | 300602 | 220289 |

The original pilot inherited TMDb 300602 from the Stage 2B sample. That ID resolves to *Capture the Flag* (2015), so the pilot's exact-ID hit for that row is invalid evidence about *Coherence*.

The ACMI collection identifies *Coherence* (2013) as TMDb movie 220289, as recorded in the corrected pilot report. The saved Watchmode sanity check for that ID returned `name=Coherence`, `year=2013`, `type=movie`, and Watchmode ID `178814`. The correction returned zero NL listings and leaves availability unverified.

| Evidence from the 2026-11-22 pilot | SHA-256 |
| --- | --- |
| `wtdw97-pilot-raw-evidence-20261122-v1-original.json` | `2b30ab7d78a20a308cafcbda1c495e37d30b4ead4d3bd012affc175e96cea204` |
| `wtdw97-pilot-raw-evidence-20261122-v2-correction.json` | `e815d65784795b944bbca7abe3d7cfd6b06ae6fc395a45750e372edfddd31f42` |

[NL pilot cohort v2](nl_pilot_cohort_20261122_v2.csv) is a new 15-case NL-only input for the offline audit. The historical sample and all 15 historical files containing TMDb 300602 are deliberately unchanged. The evidence digests refer to preserved pilot files.

## Offline audit identity policy

The audit checks the cohort title and year against the local Movie row's persisted TMDb `canonical_title` and release year. A matching ID and matching labels in the two CSV inputs cannot establish identity. A missing canonical title or year, an unexplained mismatch, or a quarantined identity prevents confirmation.

The localized title allowlist accepts **The Platform** / **Il buco** for TMDb **619264**, **movie**, **2019** only. Stage 2B recorded the Italian title for that film. [Stage 2B source evidence](source_evidence.md#it--the-platform-2019-tmdb-619264-movie)
