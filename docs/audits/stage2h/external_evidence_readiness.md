# Stage 2H — External evidence readiness

No external retrieval was performed. The URLs below are historical references
preserved in Stage 2B/2C reports; they are not current availability checks and
are not primary-provider evidence.

| Case | Existing evidence record | Exact primary-provider URL available? | Assessment |
|---|---|---|---|
| Inception (27205), NL | [JustWatch NL search](https://www.justwatch.com/nl/search?q=inception), recorded 2026-11-13 12:43:28Z. It reports subscription plus rent/buy; provider names were not extractable. | No | Secondary aggregator evidence only. It cannot identify one primary provider/offer class for comparison. |
| Dune (438631), NL | Stage 2C row is unverified and has no URL; the Stage 2B sample says it was not checked because of budget/geo limitations. | No | No saved NL source page or official provider page. Do not infer a negative or positive. |
| Dune (438631), IT | [JustWatch search for Dune (2021)](https://www.justwatch.com/it/search?q=dune%202021), recorded 2026-11-13. Provider names were not extractable. The direct `/it/film/dune` slug was documented as Dune (1984). | No | Secondary search evidence only; the direct slug is title/year ambiguous and is not a safe 2021 identity URL. No official Mediaset page URL was preserved. |

The Stage 2E plan reserved up to three requests for one primary-provider page
per case, but did not list exact destinations. The Mediaset GraphQL endpoint
`https://frontier.api-graph.mediaset.it/` is an API destination, not a saved
human-readable primary-evidence page, and this audit did not call it. The
JustWatch endpoints are also secondary evidence, not a substitute for the
requested primary-provider evidence.

## Evidence collection gate

Independent evidence capacity remains a proposed maximum of three page
requests total. None is authorized or executable until a separately authorized
evidence task identifies exact official title-page URLs, the relevant provider
and offer class, and a safe no-redirect/no-retry request policy. A missing or
unavailable page must remain “unknown”; it must not be turned into a negative
availability claim.

No page request, provider API call, redirect, or background asset request was
made. Actual external request count: **0**.
