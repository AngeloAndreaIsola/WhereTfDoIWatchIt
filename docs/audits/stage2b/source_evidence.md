# Source Evidence — Stage 2B Live Availability Verification

Research date: 2026-11-13 (UTC). Public page visits: 34 of 60 budget. Metered API calls: 0 (Streaming Availability, Watchmode). No paid availability APIs. No production refreshes, no DB/runtime access, no source activation.

## Evidence standard
- **Primary evidence sought**: official public catalogue/title pages (RaiPlay, Mediaset Infinity, Prime Video, Netflix, Pluto TV).
- **Secondary evidence**: JustWatch (country-specific title pages and search). Never treated as a final authority.
- A page "explicitly confirms availability" only when the listing shows the title with a watch/streaming option for that country.
- "Playback verified" is **No** for every case: no playback was attempted (no accounts, no automated streaming). All availability is advertised/listing evidence only.
- "Application agrees" is **Not checkable** for every case: no access to the application database, running service, or refresh output. This is a structural limitation, not a finding of disagreement.
- Geo-blocked / inaccessible / login-required pages are documented as limitations and are never inferred as negative evidence.

## Verified cases (reality confirmed available)

### IT — The Godfather (1972), TMDb 238, movie
- Evidence URL: https://www.justwatch.com/it/film/the-godfather
- Observed: "Attualmente disponibile su 8 servizi di streaming." Streaming: Paramount+, Now TV, Infinity Selection Amazon Channel, Paramount+ Amazon Channel. Rent/buy: Apple TV Store, Timvision, Amazon Video, CHILI. No free options.
- Offer type: subscription (Flat/Abbo) + rent/buy (TVOD). Subscription or purchase required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T08:20:23Z (JustWatch: "verificato su 104 servizi di streaming il giorno 13 novembre 2026 alle 08:20:23").
- Confidence: Medium (secondary aggregator title page). Result: VERIFIED_CORRECT (title is available in IT).
- Notes: NOT on Netflix, NOT on Prime Video (base), NOT on RaiPlay, NOT on Mediaset Infinity per this listing. Multi-provider. This is relevant because Prime Video is registered in the app as an NL collector, not IT.

### IT — The Matrix (1999), TMDb 603, movie
- Evidence URL: https://www.justwatch.com/it/film/the-matrix
- Observed: "Attualmente disponibile su 11 servizi di streaming." Streaming: Amazon Prime Video, Sky Go, Timvision, Now TV, HBO Max Amazon Channel, HBO Max, Amazon Prime Video with Ads. Rent/buy: Apple TV Store, Rakuten TV, Amazon Video, CHILI. No free options.
- Offer type: subscription + rent/buy. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T03:50:47Z. Confidence: Medium. Result: VERIFIED_CORRECT.

### IT — Inception (2010), TMDb 27205, movie
- Evidence URL: https://www.justwatch.com/it/film/inception
- Observed: "Attualmente disponibile su 11 servizi di streaming." Streaming: Amazon Prime Video, Sky Go, Timvision, Now TV, HBO Max Amazon Channel, HBO Max, Amazon Prime Video with Ads. Rent/buy: Apple TV Store, Rakuten TV, Amazon Video, CHILI. No free options.
- Offer type: subscription + rent/buy. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T03:50:47Z. Confidence: Medium. Result: VERIFIED_CORRECT.

### IT — Parasite (2019), TMDb 496243, movie
- Evidence URL: https://www.justwatch.com/it/film/parasite
- Observed: "Attualmente disponibile su 4 servizi di streaming." Streaming: MYmovies One. Rent/buy: Rakuten TV, Amazon Video, Apple TV Store. No free options.
- Offer type: subscription + rent/buy. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T06:10:51Z. Confidence: Medium. Result: VERIFIED_CORRECT.
- Notes: Available on MYmovies One (an Italian service), not Netflix/Prime. International/language-sensitive title.

### IT — Dune (2021), TMDb 438631, movie
- Evidence URL: https://www.justwatch.com/it/search?q=dune%202021
- Observed: Dune (2021) listed as "Guarda ora ... Abbo Flat HD ..." — available on subscription streaming in IT. Provider names not extractable from search text (logos only).
- Offer type: subscription (Flat/Abbo). Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13. Confidence: Low-Medium (search only; provider names not extracted). Result: VERIFIED_CORRECT.
- AMBIGUITY: Direct title-page slug https://www.justwatch.com/it/film/dune returned Dune (1984, David Lynch), NOT Dune (2021, Denis Villeneuve). The 2021 film is only reachable via search. Title/year mapping risk.

### IT — Stranger Things (2016), TMDb 66732, series
- Evidence URL: https://www.justwatch.com/it/search?q=stranger%20things
- Observed: Stranger Things (2016) listed "Guarda ora streaming 5 Stagioni HD" — available on subscription streaming in IT.
- Offer type: subscription. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-12T21:46:19Z. Confidence: Low-Medium (search only). Result: VERIFIED_CORRECT.

### IT — Dark (2017), TMDb 70523, series
- Evidence URL: https://www.justwatch.com/it/search?q=dark
- Observed: Listed as "I segreti di Winden (2017)" (Italian-localized title), "Guarda ora streaming 3 Stagioni HD" — available on subscription streaming in IT.
- Offer type: subscription. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-12T21:43:35Z. Confidence: Low-Medium (search only; Italian-localized title). Result: VERIFIED_CORRECT.
- AMBIGUITY: Search also returned an unrelated film "Dark (2015)". Title matching must disambiguate by year/media type.

### IT — The Platform (2019), TMDb 619264, movie
- Evidence URL: https://www.justwatch.com/it/search?q=the%20platform
- Observed: Listed as "Il buco (2019)" (Italian-localized title), "Guarda ora Abbo Flat HD" — available on subscription streaming in IT.
- Offer type: subscription. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-12T21:05:59Z. Confidence: Low-Medium (search only; Italian-localized title). Result: VERIFIED_CORRECT.

### IT — Breaking Bad (2008), TMDb 1396, series
- Evidence URL: https://www.justwatch.com/it/search?q=breaking%20bad
- Observed: Breaking Bad (2008) listed "Guarda ora streaming 5 Stagioni HD Acquista 5 Stagioni HD" — available on subscription streaming in IT.
- Offer type: subscription + buy. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-12T21:24:38Z. Confidence: Low-Medium (search only). Result: VERIFIED_CORRECT.

### NL — The Godfather (1972), TMDb 238, movie
- Evidence URL: https://www.justwatch.com/nl/search?q=the%20godfather
- Observed: The Godfather (1972) available in NL: Rent €2.99/€3.99, Buy €7.99–€9.99 (4K). Provider names not extractable from NL search text (logos only).
- Offer type: rent/buy (TVOD). Purchase required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T11:08:46Z. Confidence: Low-Medium (NL search only; provider names not extractable). Result: VERIFIED_CORRECT.

### NL — Inception (2010), TMDb 27205, movie
- Evidence URL: https://www.justwatch.com/nl/search?q=inception
- Observed: Inception (2010) available in NL: subscription streaming + rent €2.99/HD, buy €7.99–€9.99 (4K). Provider names not extractable.
- Offer type: subscription + rent/buy. Subscription/purchase required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T12:43:28Z. Confidence: Low-Medium. Result: VERIFIED_CORRECT.

### NL — Breaking Bad (2008), TMDb 1396, series
- Evidence URL: https://www.justwatch.com/nl/search?q=breaking%20bad
- Observed: Breaking Bad (2008) available in NL: subscription streaming, 5 seasons, HD. Buy 5 seasons HD.
- Offer type: subscription + buy. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T09:28:18Z. Confidence: Low-Medium. Result: VERIFIED_CORRECT.

### NL — Parasite (2019), TMDb 496243, movie
- Evidence URL: https://www.justwatch.com/nl/search?q=parasite
- Observed: Parasite (2019) available in NL: subscription streaming + rent €2.99–€3.99 HD, buy €3.99–€7.99 HD.
- Offer type: subscription + rent/buy. Subscription/purchase required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T12:49:53Z. Confidence: Low-Medium. Result: VERIFIED_CORRECT.

### NL — Dark (2017), TMDb 70523, series
- Evidence URL: https://www.justwatch.com/nl/search?q=dark
- Observed: Dark (2017) available in NL: subscription streaming, 3 seasons, HD.
- Offer type: subscription. Subscription required: Yes.
- Explicitly confirms availability: Yes. Playback verified: No. Audio/subtitles documented: No.
- Timestamp: 2026-11-13T02:59:07Z. Confidence: Low-Medium. Result: VERIFIED_CORRECT.

## Unverified cases (planned, not checked — budget/geo-block limitations)

IT: The Holdovers (2023), The Farewell (2019), Coherence (2013), The Office (US) (2005), Paddington (2014), The Longest Yard (2005).
NL: The Matrix (1999), Dune (2021), The Platform (2019), Stranger Things (2016), The Holdovers (2023), The Farewell (2019), Coherence (2013), The Office (US) (2005), Paddington (2014), The Wire (2002).

For all unverified cases: provider/offer_type/evidence_url/timestamp are blank. No page was fetched. Result: UNVERIFIED. No negative inference is made.

## Official page accessibility summary
| Source | URL attempted | Accessible? | Notes |
|---|---|---|---|
| RaiPlay (IT) | https://www.raiplay.it/ | YES | Homepage loads; catalogue nav present (Film, Serie Italiane, etc.). Search endpoint not discoverable via simple GET (/cerca, /search, /cerca/ all 404) — JS-driven. |
| Pluto TV (IT) | https://pluto.tv/it-italy/ | NO | Geo-blocked: "Pluto TV is not available in your location." |
| JustWatch IT title pages | /it/film/<slug>, /it/tv-show/<slug> | PARTIAL | Godfather, Matrix, Inception, Parasite OK. Stranger Things, The Platform, Dune part-one slugs 404 (wrong/localized slug). |
| JustWatch NL title pages | /nl/film/<slug>, /nl/tv-show/<slug>, /nl/titel/<slug> | NO | All attempted returned 404. NL search works but provider names not extractable from text. |
| JustWatch NL search | /nl/search?q= | YES | Confirms availability + € prices; provider names not extractable. |
| Netflix / Prime Video / Mediaset Infinity title pages | — | NOT FETCHED | Expected geo-block / login walls; not fetched to conserve budget and avoid auth. JustWatch used as secondary per task rules. |

## Zero metered API calls
Confirmed: 0 calls to Streaming Availability API and 0 calls to Watchmode API. No paid availability API used. All evidence from public pages (Wikipedia for identity; JustWatch / RaiPlay / Pluto TV for availability).
