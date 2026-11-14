# Stage 2B Research Log (live availability verification)

Budget: max 60 public page visits. 0 metered API calls (Streaming Availability, Watchmode).
No paid availability APIs. No mass scraping, CAPTCHA/auth/VPN bypass. No production refreshes or DB mutations.
All page visits recorded here. Source restriction: small extracts only, no screenshots.

## Visit counter (34 of 60 used)
| # | Date (UTC) | URL | Purpose | Outcome | Counted |
|---|------------|-----|---------|---------|---------|
| 1 | 2026-11-13 | https://example.com | Connectivity test | OK | Y |
| 2 | 2026-11-13 | https://en.wikipedia.org/wiki/Coherence_(film) | Title identity (TMDb 300602) | Confirmed: 2013 film, James Ward Byrkit | Y |
| 3 | 2026-11-13 | https://en.wikipedia.org/wiki/The_Farewell_(2019_film) | Title identity (TMDb 565310) | Confirmed: 2019 film, Lulu Wang | Y |
| 4 | 2026-11-13 | https://en.wikipedia.org/wiki/The_Holdovers | Title identity (TMDb 840430) | Confirmed: 2023 film, Alexander Payne | Y |
| 5 | 2026-11-13 | https://www.raiplay.it/ | RaiPlay IT official homepage accessibility | ACCESSIBLE (not geo-blocked); catalogue nav present | Y |
| 6 | 2026-11-13 | https://www.justwatch.com/it/film/the-godfather | JustWatch IT title page | Providers listed: Paramount+, Now TV, Infinity Selection Amazon Channel, Paramount+ Amazon Channel. Rent/buy: Apple TV, Timvision, Amazon Video, CHILI. NOT free. NOT Netflix/Prime base/RaiPlay/Mediaset. Verified 13 Nov 2026 08:20 | Y |
| 7 | 2026-11-13 | https://www.justwatch.com/nl/film/the-godfather | JustWatch NL title page | 404 — NL title-page URL pattern not this | Y |
| 8 | 2026-11-13 | https://www.raiplay.it/cerca?q=il%20padrino | RaiPlay IT search | 404 — search endpoint not this | Y |
| 9 | 2026-11-13 | https://www.justwatch.com/it/film/the-matrix | JustWatch IT title page | Amazon Prime Video (sub), Sky Go, Timvision, Now TV, HBO Max channels. Rent/buy: Apple TV, Rakuten TV, Amazon Video, CHILI. NOT free. Verified 13 Nov 2026 03:50 | Y |
| 10 | 2026-11-13 | https://www.justwatch.com/nl/titel/the-godfather | JustWatch NL title page | 404 | Y |
| 11 | 2026-11-13 | https://www.raiplay.it/cerca/il-padrino | RaiPlay IT search | 404 | Y |
| 12 | 2026-11-13 | https://www.justwatch.com/nl/ | JustWatch NL homepage | OK — covers Netflix, Prime Video, Videoland + 7 others | Y |
| 13 | 2026-11-13 | https://www.justwatch.com/it/film/inception | JustWatch IT title page | Amazon Prime Video (sub), Sky Go, Timvision, Now TV, HBO Max channels. Rent/buy: Apple TV, Rakuten TV, Amazon Video, CHILI. NOT free. Verified 13 Nov 2026 03:50 | Y |
| 14 | 2026-11-13 | https://www.justwatch.com/nl/search?q=the%20godfather | JustWatch NL search | The Godfather (1972) available in NL (rent €2.99/€3.99, buy €7.99-9.99). Provider names not extractable from search text. Verified 13 Nov 2026 11:08 | Y |
| 15 | 2026-11-13 | https://www.justwatch.com/nl/search?q=the%20matrix | JustWatch NL search | The Matrix (1999) available in NL (rent/buy). Provider names not extractable. Verified 13 Nov 2026 09:20 | Y |
| 16 | 2026-11-13 | https://www.raiplay.it/search?q=il%20padrino | RaiPlay IT search | 404 — search endpoint not this | Y |
| 17 | 2026-11-13 | https://www.justwatch.com/it/film/parasite | JustWatch IT title page | MYmovies One (sub). Rent/buy: Rakuten TV, Amazon Video, Apple TV. NOT free. Verified 13 Nov 2026 06:10 | Y |
| 18 | 2026-11-13 | https://www.justwatch.com/it/film/dune | JustWatch IT title page | Returned Dune (1984, Lynch) — NOT Dune (2021). TITLE/YEAR AMBIGUITY. Buy/rent only, no sub. | Y |
| 19 | 2026-11-13 | https://www.justwatch.com/nl/film/the-godfather-1972 | JustWatch NL title page | 404 | Y |
| 20 | 2026-11-13 | https://www.justwatch.com/it/search?q=dune%202021 | JustWatch IT search | Dune (2021) available in IT on subscription (Abbo/Flat). Provider names not extracted. Verified 13 Nov 2026 | Y |
| 21 | 2026-11-13 | https://www.justwatch.com/nl/tv-show/breaking-bad | JustWatch NL title page | 404 | Y |
| 22 | 2026-11-13 | https://www.justwatch.com/nl/film/the-matrix | JustWatch NL title page | 404 | Y |
| 23 | 2026-11-13 | https://www.justwatch.com/it/film/dune-part-one | JustWatch IT title page | 404 | Y |
| 24 | 2026-11-13 | https://www.justwatch.com/nl/search?q=breaking%20bad | JustWatch NL search | Breaking Bad (2008) available in NL on subscription (5 seasons). Verified 13 Nov 2026 09:28 | Y |
| 25 | 2026-11-13 | https://www.justwatch.com/nl/search?q=parasite | JustWatch NL search | Parasite (2019) available in NL on subscription. Verified 13 Nov 2026 12:49 | Y |
| 26 | 2026-11-13 | https://www.justwatch.com/it/tv-show/stranger-things | JustWatch IT title page | 404 — slug not this | Y |
| 27 | 2026-11-13 | https://www.justwatch.com/it/film/the-platform | JustWatch IT title page | 404 — slug not this | Y |
| 28 | 2026-11-13 | https://pluto.tv/it-italy/ | Pluto TV IT official page | GEO-BLOCKED: "Pluto TV is not available in your location." | Y |
| 29 | 2026-11-13 | https://www.justwatch.com/it/search?q=stranger%20things | JustWatch IT search | Stranger Things (2016) available in IT on subscription (5 seasons). Verified 12 Nov 2026 21:46 | Y |
| 30 | 2026-11-13 | https://www.justwatch.com/it/search?q=dark | JustWatch IT search | Dark (2017) available in IT on subscription as Italian title "I segreti di Winden" (3 seasons). Verified 12 Nov 2026 21:43 | Y |
| 31 | 2026-11-13 | https://www.justwatch.com/nl/search?q=dark | JustWatch NL search | Dark (2017) available in NL on subscription (3 seasons). Verified 13 Nov 2026 02:59 | Y |
| 32 | 2026-11-13 | https://www.justwatch.com/it/search?q=the%20platform | JustWatch IT search | The Platform (2019) available in IT on subscription as Italian title "Il buco". Verified 12 Nov 2026 21:05 | Y |
| 33 | 2026-11-13 | https://www.justwatch.com/it/search?q=breaking%20bad | JustWatch IT search | Breaking Bad (2008) available in IT on subscription (5 seasons). Verified 12 Nov 2026 21:24 | Y |
| 34 | 2026-11-13 | https://www.justwatch.com/nl/search?q=inception | JustWatch NL search | Inception (2010) available in NL on subscription + rent/buy. Verified 13 Nov 2026 12:43 | Y |

Remaining budget: 26 visits (not needed for report writing).

## Method & limitations
- Title identities verified via Wikipedia (TMDb blocked by bot protection; websearch returned HTTP 402).
- JustWatch IT title pages give explicit provider names + offer types + prices (rich secondary evidence).
- JustWatch NL title pages return 404 for all attempted slugs; only NL search works (confirms availability + prices, provider names not extractable from text).
- RaiPlay homepage accessible; RaiPlay search endpoint not discoverable via simple GET (JS-driven).
- Pluto TV IT geo-blocked from research location.
- Netflix / Prime Video / Mediaset Infinity title pages not fetched: expected geo-block/login walls and budget conservation; JustWatch used as secondary per task rules.
- No production refreshes, no DB/runtime access → "whether the application agrees" not checkable per case.
- Geo-blocked / inaccessible pages documented as limitations, never inferred as negative.

## Final sample: 30 title-country cases (15 IT, 15 NL)
See live_sample.csv for the full register with evidence URLs, timestamps, and results.
