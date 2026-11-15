# Confirmed discrepancies

No application-versus-external conflict was confirmed. Stage 2B evidence is a secondary listing source and contains no confirmed negative availability; absent local offers are classified as missing coverage, never as an app error.

## Classification precedence

1. `NOT_COMPARABLE` when TMDb identity, media type, year, or country disagrees.
2. `INSUFFICIENT_EXTERNAL_EVIDENCE` when no matching row exists, availability is not explicitly positive, provider names are absent, or a negative is secondary/unspecified.
3. `APP_DATA_MISSING` when the exact local Movie identity is absent.
4. For explicit primary/official negatives, `CONFLICT_CONFIRMED` requires a verified same-provider canonical offer in the same country; otherwise `INSUFFICIENT_APP_EVIDENCE`.
5. `APP_SOURCE_DISABLED` requires an explicit `app_source_slug` in the external evidence and a persisted local Source row with `enabled=false`. An absent/unconfigured row has unknown state and is never inferred disabled from the research site's name. Without that explicit source attribution, no-offer cases are `APP_DATA_MISSING`. A stale matching provider/class is `APP_DATA_STALE`, even if a different provider has fresh evidence.
6. Positive `AGREEMENT_CONFIRMED` requires external evidence that unambiguously names one provider and one offer class, plus fresh verified canonical evidence matching provider, country, and class. Aggregated provider/class lists without a pairing stay `INSUFFICIENT_EXTERNAL_EVIDENCE`; a same-provider offer of another class is insufficient. An aggregator's omission of other app providers is not a conflict. Remaining mismatches are `INSUFFICIENT_APP_EVIDENCE`.

## Targeted edge cases

- **D1 — Dune (1984) vs (2021):** no local Movie or offer row was found for TMDb 438631. Source TMDb IDs are persisted as exact external-ID mappings; fallback matching constrains media type and known movie year, and canonical reads are keyed by TMDb ID and media type. No defect reproduced.
- **D2 — localized titles:** no local rows existed for Dark (70523) or The Platform (619264). TMDb IDs bypass title matching; sources without IDs use exact title/year matching, so aliases remain an unverified coverage risk. No speculative alias was added.
- **D3 — Prime Video country scope:** the Prime Video collector declares NL only; refresh scope rejects IT requests and canonical offer reads filter by country. JustWatch may independently record IT offers. No matching local rows existed to test leakage.
- **D4 — multi-provider offers:** canonical identity retains provider and country; reconciliation merges same-provider observations while keeping source facts and source-specific evidence. Non-authoritative aggregator omission does not deactivate another source's positive. No Godfather rows existed to test live behavior.
- **D5 — unavailable external pages:** Stage 2B's JustWatch NL 404s, RaiPlay search 404s, and Pluto TV geo-block remain unverified evidence limitations, not negative findings.
