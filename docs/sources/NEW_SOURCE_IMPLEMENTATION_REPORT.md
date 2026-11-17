# Unofficial scraper integration report

The source implementations are integrated with the current provider registry,
legacy scraper ingestion bridge, and canonical catalogue. The three listing
scrapers are eligible for normal provider-scoped scraper execution:

- StreamingCommunity (`streamingcommunity`)
- CineBlog01 (`cineblog01`)
- AltaDefinizione (`altadefinizione`)

KissKH, HydraHD, and AnimeWorld remain registered through their existing
adapters. Bilibili TV and OK.ru remain URL-targeted metadata parsers; provider
scraping selection rejects them and they cannot fall through to the generic
catalogue adapter.

Listing-derived titles flow to canonical `ProviderContent` for catalogue
discovery. The shared parser does not infer resolution. Its unverified
discoveries do not receive automatic canonical TMDb mappings or per-title
offers; manual confirmation remains available. Current bounded page limits,
metadata extraction, traversal feasibility, and blockers are recorded in
[`INTEGRATION_COMPATIBILITY.md`](INTEGRATION_COMPATIBILITY.md).

No catalogue limits, backfills, checkpoints, or scheduled full-catalogue jobs
were added in this integration.
