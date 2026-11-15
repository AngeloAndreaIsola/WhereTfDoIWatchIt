# Recommendations — Stage 2B Live Availability Verification

Context: Stage 2 baseline (0da80a3f) fixed A1–A4 and A8; A5/A6/A7 remain follow-up; U1 (Watchmode HTTP 200 error-shaped payload) unverified. This stage verified zero application outputs (no DB/runtime access) and 14 real-world title-country availabilities via secondary evidence. Recommendations below are prioritized by observed risk and by the Stage 2 fix-plan dependencies. No code changes are proposed here; this is an evidence/audit document only.

## Prioritized recommended fixes

### P1 — Enable end-to-end observability so app output can be compared to reality (blocker for verification)
This audit could not answer "whether the application agrees" for any case because no application output (serialized offers, API responses, frontend verdicts) and no database/runtime state were accessible.
- Recommendation: expose a read-only, per-title, per-country availability evidence dump (provider, offer type, source, last_seen, confidence) via an admin/audit endpoint, or grant read-only DB access to auditors, so real-world evidence can be diffed against application output.
- Without this, future audits can only establish reality, not correctness of the application. This is the single largest gap.
- Depends on: none (instrumentation). Risk: low. Effort: medium.

### P2 — Enforce title + year + media-type matching before canonicalizing offers (addresses D1: Dune 1984 vs 2021)
Observed: JustWatch `/it/film/dune` returned the 1984 Lynch film, while the 2021 Villeneuve film (TMDb 438631) is only reachable via search. The two have different offer types (1984 = rent/buy only; 2021 = subscription in IT).
- Recommendation: require TMDb ID where supplied by the source; otherwise match on title + year + media type, and reject/flag title-only collisions rather than auto-assigning offers to the wrong title.
- Depends on: title/provider identity resolution (`_resolve_content`, `_provider` in refresh.py).
- Risk: high (offer-type and entitlement inversion). Effort: medium.

### P3 — Make title matching tolerant of localized titles (addresses D2: Dark = "I segreti di Winden", The Platform = "Il buco")
Observed: Italian catalogues use localized titles; exact English-title matching against IT sources misses them.
- Recommendation: match primarily on TMDb ID; fall back to title+year+country with localized-title normalization (e.g., maintain a title-alias map or normalize punctuation/case and try both original and localized titles). Do not fail closed on title-only mismatch when a TMDb ID is present.
- Depends on: `_resolve_content` title resolution.
- Risk: medium (false negatives / missing coverage in IT). Effort: medium.

### P4 — Verify per-country scope of registered collectors against observed catalogues (addresses D3: Prime Video NL vs IT)
Observed: Prime Video is registered as an NL collector. JustWatch IT shows The Godfather NOT on Prime Video IT (on Paramount+/Now TV/Infinity Selection), while Matrix and Inception ARE on Prime Video IT. The app must not assume a registered collector covers all markets or attribute Prime Video IT offers outside its registered scope.
- Recommendation: scope each collector's offers to its declared country(ies); when a source returns offers for an undeclared country, flag for review rather than silently persisting. Cross-check Prime Video IT catalogue against the NL registration before presenting Prime Video as an IT provider.
- Depends on: provider mapping (`refresh.py::_provider`), capabilities country scope.
- Risk: medium (false positives across territories, related to A1-class territorial bugs). Effort: medium.

### P5 — Preserve independent evidence for multi-provider titles; dedupe presentation only (addresses D4)
Observed: The Godfather IT is on 4+ services (Paramount+, Now TV, Infinity Selection Amazon Channel, Paramount+ Amazon Channel) plus TVOD.
- Recommendation: keep independent Offer rows per source/provider and dedupe only at presentation; never let one source's absence or a partial scan deactivate another source's positive offer (consistent with Stage 2 A1–A8 non-authoritative-absence guarantees).
- Depends on: reconciliation (`availability/domain/reconciliation.py`).
- Risk: medium. Effort: low-medium.

### P6 — Complete the Stage 2 follow-up work (A5, A6, A7)
From the Stage 2 fix plan, still outstanding:
- A5 (P2): seed the `internet_archive` provider so the public-domain archive collector can persist content/checkpoints on fresh installs.
- A6 (P2): pass per-source freshness configuration into the scheduler (currently read-only).
- A7 (P2): use the same advisory drain lock for the watchlist refresh path to prevent races with the queue worker.
- Risk: low-medium each. Effort: low-medium.

### P7 — Resolve U1 (Watchmode HTTP 200 error-shaped payloads) against current docs before any code change
This audit made 0 Watchmode calls (per budget policy), so U1 remains unverified. Do not alter code based on speculation.
- Recommendation: review the current Watchmode API documentation for HTTP 200 responses that may contain error-shaped/unexpected payloads, and confirm whether Watchmode's documented behavior supports the offline suspicion (U1). Only then decide whether the client's empty-result conversion (`watchmode_client.py:258-277, 450-466`) needs a guard.
- Risk: low (unverified). Effort: low (documentation review).

## Per-provider evidence coverage and what it means for fixes
| Provider | Evidence in this audit | Coverage | Implication |
|---|---|---|---|
| JustWatch IT | Title pages + search | Medium (IT title pages) | Most reliable accessible secondary evidence; use for cross-checking, not authority |
| JustWatch NL | Search only | Low-Medium | Provider names not extractable; NL verification weak |
| RaiPlay | Homepage only | 0% titles | Search not GET-accessible; IT broadcaster evidence missing |
| Pluto TV IT | Geo-blocked | 0% | Cannot verify free/ad-supported IT catalogue |
| Prime Video | Indirect (via JustWatch IT) | Indirect | NL registration scope; IT scope unverified directly |
| Mediaset Infinity | None | 0% | IT SVOD/AVOD/TVOD evidence missing |
| Netflix | None | 0% | No evidence |
| Streaming Availability / Watchmode | 0 calls | 0% | Metered, per policy not used |

## What could NOT be determined (honest limitations)

The available research supports **external positive observations** (positive external listing evidence collected, primarily from the JustWatch secondary aggregator). It does **not** establish:

1. **External availability advertised by an aggregator** — established for the 14 `EXTERNAL_AVAILABILITY_OBSERVED` cases (JustWatch listing only).
2. **Availability confirmed by an official provider** — NOT established. No official provider page (RaiPlay, Mediaset Infinity, Prime Video, Netflix, Pluto TV) was directly queried for per-title evidence within constraints.
3. **Actual playable availability** — NOT established. No playback was attempted (no accounts, no automated streaming). All evidence is advertised/listing only.
4. **Subscription entitlement** — NOT established. No user/subscription rows were inspected.
5. **Audio/subtitle compatibility** — NOT established. No language evidence was documented by the sources consulted.
6. **Application correctness** — NOT established. No application output (serialized offers, API responses, frontend verdicts) or database/runtime state was accessible. Whether the application agrees with reality is `APP_AGREEMENT_UNVERIFIED` for every case.

Consequences of these limitations:
- Whether the application agrees with reality for any case (no app/DB access).
- Whether playback actually works (no playback attempted; all evidence is advertised/listing only).
- Audio/subtitle language documentation (not provided by the sources consulted).
- Per-user subscription entitlement verdicts (no user/subscription rows inspected).
- Any negative/unavailability conclusion (rules prohibit inferring absence from access failure or aggregator omission).


## Next steps
1. Implement P1 (observability) so the next audit can diff reality vs application.
2. Implement P2–P5 (matching/scope/dedup) to close the structural risks observed.
3. Complete A5/A6/A7 follow-ups.
4. Review U1 against current Watchmode docs; change code only if documentation supports it.
5. Re-run a live audit with direct official-page access (RaiPlay, Mediaset, Prime Video, Netflix, Pluto TV) once access is possible, within the same 60-visit / 0-metered-API-call policy.

## Stage 2C Handoff — Next Application Accuracy Audit

The next audit must assess whether the application agrees with the externally observed availability, using **read-only local application data** and avoiding metered provider requests (Streaming Availability, Watchmode). No application code or production data should be modified; no metered API calls should be made.

For each title-country case the next audit must collect:

| Field | Description |
|---|---|
| TMDb ID | Canonical title identity (TMDb identifier) used for matching. |
| Media type | `movie` or `series`, used to disambiguate titles across media types. |
| Country | Territorial scope of the availability check (e.g., IT, NL). |
| Canonical provider | The provider name as registered in the application, with its declared country scope. |
| Offer type | Subscription (Flat/Abbo), rent/buy (TVOD), or free/ad-supported. |
| Source | Evidence source (e.g., JustWatch IT title page, JustWatch search, official provider page). |
| Observation timestamp | When the external evidence was collected (UTC). |
| Freshness | How current the external evidence is relative to the application's last refresh/seeding. |
| Territorial scope | Whether the provider is registered/enabled for the country being audited. |
| Availability confidence | Confidence level of the external observation (e.g., Medium title page, Low-Medium search). |
| Entitlement | Whether the observing account/subscription is entitled to the offer (if determinable without metered calls). |
| Language evidence | Audio/subtitle language compatibility observed (if documented by the source). |
| Final watchability verdict | Whether the title is watchable in that country on that provider (advertised only; playback not attempted). |
| External reference | URL or other reference to the external evidence page. |
| Agreement assessment | Whether the application's offer set matches the external observation (agree / disagree / unverifiable). |

The next audit should:
- Use read-only local application data (catalogue, offers, provider registration, refresh output) as the application side of the comparison.
- Diff application output against the external evidence collected above.
- Avoid metered provider requests (0 Streaming Availability, 0 Watchmode calls), consistent with Stage 2B policy.
- Not modify application code or production data.
- Preserve D1–D5 as observations and risks, not confirmed application bugs.

