# Discrepancies — Stage 2B Live Availability Verification

Scope: 30 title-country cases (15 IT, 15 NL). 14 cases verified correct (real-world availability confirmed via secondary evidence); 16 unverified (not checked due to budget/geo-block limits). No case is reported as "unavailable" merely because it is absent from one source, per audit rules. No production data was changed; no application defect is claimed without corroboration.

## Confirmed observations (reality, secondary evidence)

### D1 — Title/year ambiguity: Dune (1984) vs Dune (2021)
- Case: Dune, TMDb 438631, 2021, movie, IT and NL.
- Observation: https://www.justwatch.com/it/film/dune returns Dune (1984, David Lynch) — buy/rent only, no subscription. The 2021 Denis Villeneuve film is only reachable via search (https://www.justwatch.com/it/search?q=dune%202021), where it appears as available on subscription in IT.
- Title matching: A system matching on title alone ("Dune") without year disambiguation can attach 1984 offers to the 2021 title or vice versa. The 1984 film has no subscription offer; the 2021 film does. Conflating them flips offer type (subscription vs TVOD) and provider.
- Provider mapping: not determined (provider names not extractable from search text).
- Country/territorial scope: IT confirmed (2021 on subscription); NL unverified.
- Subscription entitlement: 2021 = subscription; 1984 = rent/buy only. Conflation changes entitlement verdict.
- Offer type: at risk of inversion.
- Language evidence: not documented.
- Freshness: JustWatch IT search verified 2026-11-13; 1984 film page verified 2026-11-13 03:50:52.
- Source confidence: JustWatch secondary only.
- Cache state: not inspected (no app access).
- UI/API verdict: not inspectable (no app access).
- Classification: Source/data-quality risk (title+year disambiguation), not a confirmed application defect. Recommendation: enforce title+year+media-type matching before canonicalizing offers.

### D2 — Italian-localized titles break exact title/year matching
- Cases: Dark (2017, series) IT listed as "I segreti di Winden"; The Platform (2019, movie) IT listed as "Il buco".
- Observation: JustWatch IT search for "dark" surfaces "I segreti di Winden (2017)" (subscription, 3 seasons); search for "the platform" surfaces "Il buco (2019)" (subscription). An exact English-title match against an Italian catalogue would miss these.
- Title matching: fails closed if the matcher requires exact English title. Italian catalogues use localized titles.
- Provider mapping / country scope: IT confirmed available.
- Subscription entitlement: both are subscription (Flat/Abbo).
- Offer type: subscription.
- Language evidence: titles are Italian-localized; audio/subtitle language not documented by JustWatch text.
- Freshness: verified 2026-11-12 (21:05–21:46).
- Source confidence: JustWatch secondary, search only.
- Classification: Source/data-quality risk (localized titles). Not a confirmed application defect. Recommendation: match on TMDb ID where supplied; fall back to title+year+country with localized-title awareness.

### D3 — Prime Video scope: The Godfather NOT on Prime Video IT
- Case: The Godfather (1972), TMDb 238, movie, IT.
- Observation: JustWatch IT title page (https://www.justwatch.com/it/film/the-godfather, verified 2026-11-13 08:20:23) lists Paramount+, Now TV, Infinity Selection Amazon Channel, Paramount+ Amazon Channel; rent/buy on Apple TV, Timvision, Amazon Video, CHILI. Prime Video (base) and Netflix are NOT listed. No free options.
- Registration fact (code): Prime Video is registered as an NL collector (`prime_video.py`, country=NL). It is not registered as an IT collector.
- Title matching: title matches (The Godfather 1972).
- Provider mapping: if the app were to surface Prime Video IT for The Godfather, that would contradict both the registration scope (NL-only) and the observed IT catalogue (Godfather not on Prime Video IT).
- Country/territorial scope: Prime Video registered NL → IT Prime offers would be out of scope.
- Subscription entitlement: Godfather in IT is on Paramount+/Now TV/Infinity Selection (subscription) — not Prime base.
- Offer type: subscription (Paramount+/Now TV/Infinity Selection) or TVOD.
- Language evidence: not documented.
- Freshness: verified 2026-11-13 08:20:23.
- Source confidence: JustWatch IT title page (explicit providers).
- Classification: Cross-reference risk between registration scope and observed catalogue. Not a confirmed defect (app data not inspected). Recommendation: verify per-country Prime Video catalogue against registration scope; do not assume a registered collector covers all markets.

### D4 — Multi-provider title: The Godfather (IT) on 4+ services
- Case: The Godfather IT.
- Observation: Available on Paramount+, Now TV, Infinity Selection Amazon Channel, Paramount+ Amazon Channel (subscription), plus TVOD on Apple TV, Timvision, Amazon Video, CHILI.
- Provider mapping: multiple canonical providers for one title in one country. The app must preserve source provenance and not collapse/overwrite offers.
- Offer type: mixed subscription + TVOD.
- Classification: Presentation/dedup risk. Not a confirmed defect. Recommendation: dedupe presentation while retaining independent evidence rows; do not let one source's absence deactivate another's positive offer (consistent with Stage 2 findings A1–A8).

### D5 — Access limitations (not availability findings)
| Source | Limitation | Effect |
|---|---|---|
| Pluto TV IT (https://pluto.tv/it-italy/) | Geo-blocked: "not available in your location" | Could not verify Pluto TV IT catalogue. Not inferred as negative. |
| RaiPlay IT search | /cerca, /search, /cerca/<q> all 404; homepage accessible but JS-driven | Could not query RaiPlay catalogue by title via GET. |
| JustWatch NL title pages | All /nl/film/<slug>, /nl/tv-show/<slug>, /nl/titel/<slug> returned 404 | NL provider names not extractable; only NL search (availability + € prices) usable. |
| Netflix / Prime Video / Mediaset Infinity title pages | Not fetched (expected geo-block/login walls; budget conservation) | No direct primary evidence from these official pages. |
| Application (app DB / runtime) | No access | "Whether the application agrees" not checkable for any case. |

These are evidence-collection limitations, not discrepancies in the application. They cap verification coverage: 14 of 30 cases verified; 16 unverified.

## Confirmed incorrect / correct
- VERIFIED_CORRECT (reality confirmed available): 14 cases (9 IT, 5 NL) — listed in live_sample.csv.
- VERIFIED_INCORRECT: 0 (no negative availability could be confirmed; rules prohibit inferring unavailability from absence).
- CONFLICTING: 0 (no two sources disagreed on a verified title).
- UNVERIFIED: 16 cases.

## Application defects vs source failures vs incomplete evidence
- No application defect is confirmed, because no application output (API response, serialized offer, frontend verdict) was observed. The application was not queried, no refresh was run, and no DB row was read.
- Source failures: none confirmed. JustWatch IT title pages and searches returned data; JustWatch NL title pages 404 (a JustWatch URL/access issue, not an application issue). Pluto TV IT and RaiPlay search are access limitations.
- Incomplete external evidence: the dominant category. 16 cases unverified; provider names not extractable from JustWatch NL search; audio/subtitle language not documented anywhere; playback never verified.

## Distinguishing per audit rule
Per Stage 2 guidance, aggregator omission, failed/geo-blocked access, incomplete scans, and missing provider mappings are NOT negative evidence and do not deactivate independent positive offers. None of the above limitations are reported as "title unavailable in country X."
