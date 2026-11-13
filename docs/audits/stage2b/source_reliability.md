# Source Reliability — Stage 2B Live Availability Verification

Assessment of each source's usefulness as evidence for this audit, based on actual access attempts (34 page visits, 2026-11-13). Not an endorsement or condemnation of the source itself; reflects what could be verified within the budget and access constraints.

## Per-source evidence coverage

### JustWatch — secondary aggregator (IT and NL)
- Role per source matrix: broad aggregator; non-authoritative; source omission never deactivates.
- IT title pages: ACCESSIBLE and RICH. `/it/film/the-godfather`, `/it/film/the-matrix`, `/it/film/inception`, `/it/film/parasite` returned explicit provider names, offer types (subscription vs rent/buy), prices, and a verification timestamp ("verificato su 104 servizi di streaming"). Best evidence in this audit.
- IT title pages: PARTIAL FAILURE. `/it/tv-show/stranger-things`, `/it/film/the-platform`, `/it/film/dune-part-one` returned 404 — likely localized/alternate slugs. Required falling back to IT search.
- IT search: ACCESSIBLE. Confirmed Dune (2021), Stranger Things, Dark ("I segreti di Winden"), The Platform ("Il buco"), Breaking Bad availability on subscription. Provider names NOT extractable from search text (logos only). Lower confidence.
- NL title pages: ALL FAILED (404) for `/nl/film/<slug>`, `/nl/tv-show/<slug>`, `/nl/titel/<slug>`. URL pattern not this.
- NL search: ACCESSIBLE. Confirmed The Godfather, Inception, Breaking Bad, Parasite, Dark availability + € prices. Provider names NOT extractable. Lowest-confidence NL evidence.
- Reliability for this audit: IT title pages = Medium; IT search and NL search = Low-Medium; NL title pages = unusable (404).
- Limitation: secondary evidence, not final authority. Cannot confirm playback, audio/subtitle language, or entitlement for a specific user.

### RaiPlay (IT) — official broadcaster collector (registered; NOT enabled in local DB snapshot)
- Role per source matrix: collector; IT; movie/series; catalogue walk + targeted native-evidence path; free where available; IT territorial scope.
- Homepage https://www.raiplay.it/: ACCESSIBLE (not geo-blocked). Catalogue navigation present (Film, Serie Italiane, Serie Internazionali, etc.).
- Search: NOT ACCESSIBLE via simple GET. /cerca?q=, /search?q=, /cerca/<q> all returned 404. Search is JS-driven/API-backed and was not reverse-engineered (would risk automation/abuse of undocumented endpoints).
- Reliability for this audit: homepage reachable, but no per-title evidence collected. Cannot confirm any RaiPlay title.
- Limitation: no title-specific catalogue evidence; search endpoint undocumented for GET. Not used as positive or negative evidence.

### Pluto TV — official FAST collector (registered; NOT enabled in local DB snapshot)
- Role per source matrix: collector; free/ad-supported; country derived from response; direct catalogue entries; not user-specific entitlement.
- https://pluto.tv/it-italy/: GEO-BLOCKED — "Pluto TV is not available in your location." No catalogue evidence.
- Reliability for this audit: 0% coverage; access limitation only. Not inferred as negative.
- Limitation: geo-blocked from research location.

### Netflix — subscription aggregator (registered via JustWatch/Watchmode mapping; not a direct code collector in this cohort)
- Not fetched. Title pages require Netflix title IDs and are expected to be geo-blocked/login-gated. Not fetched to conserve budget and avoid auth.
- Reliability for this audit: 0% coverage.

### Prime Video — direct collector (registered; NL collector per code)
- Not fetched (title pages expected geo-blocked/login-gated; budget conservation).
- Indirect evidence via JustWatch IT: Matrix and Inception confirmed on Amazon Prime Video IT; The Godfather NOT on Prime Video IT (on Paramount+/Now TV/Infinity Selection).
- Reliability for this audit: indirect only (via JustWatch secondary). Code registration = NL collector; IT Prime scope not confirmed by direct evidence.

### Mediaset Infinity — direct collector (registered; IT movie; SVOD/AVOD/TVOD)
- Not fetched. Expected geo-blocked/login-gated.
- Reliability for this audit: 0% coverage.

### Streaming Availability — metered aggregator
- Role: metered; non-authoritative absence.
- Calls made: 0. Budget honored (0 calls).
- Reliability for this audit: not used (metered, per task policy).

### Watchmode — broad aggregator
- Role: broad aggregator; NL/CA/JP scope; language unsupported; non-authoritative.
- Calls made: 0. Budget honored (0 calls).
- Reliability for this audit: not used (per task policy; also Watchmode scope is NL/CA/JP — IT is not in Watchmode's declared country scope, so it would not cover IT cases anyway).

## Coverage summary
| Metric | Value |
|---|---|
| Total title-country cases | 30 (15 IT, 15 NL) |
| Cases with real-world evidence | 14 |
| VERIFIED_CORRECT (reality confirmed available) | 14 |
| UNVERIFIED | 16 |
| Official provider pages directly confirming a title | 0 (all geo-blocked, login-gated, or not fetched) |
| Secondary aggregator confirmations (JustWatch IT title pages) | 5 (Godfather, Matrix, Inception, Parasite IT) |
| Secondary aggregator confirmations (JustWatch searches) | 9 |
| Metered API calls (Streaming Availability / Watchmode) | 0 / 0 |
| Paid availability API calls | 0 |

## Reliability conclusions
- JustWatch IT title pages are the most reliable accessible evidence in this audit (explicit providers, offer types, timestamps). JustWatch NL search is usable but provider names are not extractable.
- Official provider pages (RaiPlay search, Pluto TV IT, Netflix, Prime Video, Mediaset) could not be queried for per-title evidence within constraints: geo-block, JS-driven undocumented search, login walls, or budget.
- Therefore no "primary official evidence" was obtained for any title. All 14 verified cases rest on JustWatch secondary evidence. Per task rules, JustWatch is not a final authority, so these 14 are marked VERIFIED_CORRECT for real-world availability but NOT confirmed against the application.
- The 16 unverified cases reflect evidence-collection limits, not application incorrectness.
