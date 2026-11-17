# Unofficial scraper integration and completeness notes

Phase 2 adds the explicit checkpointed discovery operation documented in
[`CATALOGUE_DISCOVERY.md`](CATALOGUE_DISCOVERY.md). The bounded execution in
this table describes the normal scraper/refresh button path; it remains
synchronous and is intentionally not used for full discovery. The new
management command is separate, provider-scoped, request-budgeted, and writes
only unverified catalogue observations through the existing canonical bridge.

These are the bounded discovery paths retained by the integration. They describe
the current implementation only; they do not schedule or perform catalogue
backfills.

| Provider | Current discovery path and bound | Full traversal outlook | Metadata and language extraction | Current blockers |
|---|---|---|---|---|
| KissKH | API list endpoint, partitioned by the provider's movie/drama/anime type values; `pageSize=200`, at most 3 pages per type (600 rows per type). Search queries each type separately. | The API exposes paging, so broader traversal appears technically possible, subject to its response contract and request policy. This implementation intentionally stops at page 3. | API title, year, declared content type, URL, and only reported audio/subtitle/quality values. Missing values remain empty. | Domain/API drift, request blocking, and incomplete type/year data. |
| HydraHD | HTML date listings for movies and TV shows, 10 pages per category; tries `hydrahd.gd` then `hydrahd.ws` per page. Search uses the two configured domains. | The date-page URL accepts a page number, so traversal appears possible; retained limit is 10 pages per category. | Card-local title/year and type from the detail URL. JSON search uses only explicit year, languages, and resolution fields. Listing cards do not invent language or quality. | Domain drift, stub/blocked mirrors, and listings may omit metadata. |
| AnimeWorld | Movie and anime listing categories, pages 1–2 each; title search uses the site's search endpoint. | The category URLs accept page numbers, so broader traversal appears possible; current code is bounded to 2 pages per category. | Title and year when present; browse category supplies movie/series type. The legacy parser has site-specific language defaults, which need verification before treating them as offer evidence. | Dynamic/changed HTML and incomplete year/type metadata; some URLs return no HTML. |
| StreamingCommunity | Four bounded listing paths: `/`, `/film/`, `/series/`, and `/archivio/`; no page-number traversal. | Unknown from the current implementation. The site may expose additional archive or pagination routes, but this integration does not follow them. | Image-only tile title, year, type hints, explicit bracketed audio/subtitle tags, and `data-sub`. No resolution is inferred from a tile. | Cloudflare challenges and changing domains/paths; current cards do not verify that playback works. |
| CineBlog01 | `/film/` pages 1–5 and `/serie-tv/` pages 1–3, with fallback domains tried for each request. | Site-supported `/page/N/` paths indicate broader traversal may be possible; this implementation intentionally stays within 5 movie and 3 series pages. | Title from `title`/image alt, year from title text, and explicit bracket tags for audio/subtitle languages. | Domain drift/blocking and sparse year/language tags. |
| AltaDefinizione | Four bounded listing paths: `/`, `/film/`, `/serie-tv/`, and `/archivio/`; no page-number traversal. | Unknown from the current implementation. Listing pagination may exist, but is not currently followed. | Image-only tile title, year/type attributes, and explicit language markers/`data-sub`. No resolution is inferred from a tile. | Domain drift/blocking; older translation filters no longer work, and playback is not verified by listings. |

All six implementations remain bounded. No limits, crawl checkpoints, backfills,
or scheduled full-catalogue jobs were added as part of integration.
