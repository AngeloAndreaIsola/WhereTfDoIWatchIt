# Unofficial Streaming Providers Implementation

> **Integration status:** This document describes parser capabilities. Treat
> listing rows as discoveries, not verified playable offers. The canonical
> ingestion bridge withholds automatic mappings for unverified listing
> observations, and the shared listing parser does not infer resolution. See
> [`docs/sources/INTEGRATION_COMPATIBILITY.md`](sources/INTEGRATION_COMPATIBILITY.md)
> for current bounds and blockers.

## Summary

Implemented three new unofficial streaming provider scrapers for WhereTfDoIWatchIt:

1. **StreamingCommunity** (`streamingcommunity`)
2. **CineBlog01** (`cineblog01`)
3. **AltaDefinizione** (`altadefinizione`)

## Files Created/Modified

### New Scraper Files
- `backend/tracker/scrapers/streamingcommunity.py` - StreamingCommunity scraper with multi-domain fallback
- `backend/tracker/scrapers/cineblog01.py` - CineBlog01 scraper with language detection from title tags
- `backend/tracker/scrapers/altadefinizione.py` - AltaDefinizione scraper with filter-based browsing

### Modified Files
- `backend/tracker/scrapers/__init__.py` - Added new scrapers to registry and exports
- `backend/tracker/management/commands/seed_unofficial_providers.py` - Added new providers to seed data
- `backend/tracker/management/commands/reseed_providers.py` - Added new providers to reseed data
- `backend/tracker/tests/test_new_scrapers.py` - Comprehensive test suite (14 tests)

## Architecture

All scrapers follow the existing `BaseScraper` architecture:

```python
class BaseScraper:
    provider_slug: str = ""
    timeout: int = 10
    max_retries: int = 2

    def fetch_url(self, url: str, headers: dict = None) -> dict | str | None:
        # Retries with timeout and error handling

    def parse_items(self, raw_data) -> list[ScrapedItem]:
        # Override in subclasses

    def run(self, query: str = None) -> list[ScrapedItem]:
        # Graceful error handling
```

### ScrapedItem Output
All scrapers produce normalized `ScrapedItem` objects with:
- `title`, `provider_slug`, `year`, `content_type` (movie/series)
- `source_url`, `audio_languages`, `subtitle_languages`
- `quality`, `availability`, `raw_payload` (provenance, domain, etc.)

Languages are normalized to canonical ISO 639-1 codes (IT, EN, JA, etc.) via `normalize_language_list`.

## Provider Details

### StreamingCommunity
- **Domains**: `streaming-community.army` (primary), `streamingcommunityz.nexus` (Cloudflare fallback)
- **Domain Registry**: Automatic failover with health tracking and failure counting
- **Browse**: `/`, `/film/`, `/series/`, `/archivio/` — the legacy `/en/search` and `/en/browse/latest` paths now 404
- **Title source**: `data-title` attribute (tiles are image-only, no anchor text)
- **Language Detection**: `[ITA]`/`[SUB-ITA]` bracket tags and the `data-sub` attribute
- **Cloudflare Handling**: Detects challenges, marks domain unhealthy, tries fallback
- **User-Agent**: browser UA required; `python-requests` default gets 403

### CineBlog01
- **Domain**: `cineblog001.run` (legacy `cineblog001.tires` redirects here)
- **Language Detection**: Parses title tags like `[ITA]`, `[Sub-ITA]`, `[ENG]`, `[Sub-ENG]`
- **Examples**:
  - `Movie [ITA] [HD] (2024)` → audio: ["IT"]
  - `Movie [Sub-ITA] [HD] (2024)` → subs: ["IT"]
  - `Movie [ITA] [Sub-ENG] [HD] (2024)` → audio: ["IT"], subs: ["EN"]
- **Browse**: `/film/` and `/serie-tv/` with `/page/N/` pagination (up to 5 and 3 pages)
- **Title source**: `title` attribute / nested image `alt`

### AltaDefinizione
- **Domain**: `altadefinizionex.ink` (legacy `altadefinizionex.fit` redirects here)
- **Browse**: `/`, `/film/`, `/serie-tv/`, `/archivio/` — the `traduzione` filter is ignored by the live site
- **Title source**: `data-title` / `title` attributes, with `data-year` and `data-kind` metadata
- **Language Detection**: bracket tags and the `data-sub` attribute

## API Integration

Providers are automatically registered via `SCRAPER_REGISTRY` and accessible through existing v2 endpoints:

```
GET  /api/v2/unofficial/providers/          # List all scraper providers
GET  /api/v2/unofficial/                    # Search catalog with filters
POST /api/v2/unofficial/scrape/             # Trigger scrape for provider
GET  /api/v2/unofficial/movie/<tmdb_id>/offers/  # Get offers for a movie
```

## Capability Matrix

| Capability | StreamingCommunity | CineBlog01 | AltaDefinizione |
|---|---|---|---|
| Movie search | ✅ | ✅ (via browse) | ✅ (via browse) |
| Movie catalogue | ✅ | ✅ | ✅ |
| Series search | ✅ | ✅ (via browse) | ✅ (via browse) |
| Season/episode data | ❌ | ❌ | ❌ |
| Audio languages | ✅ | ✅ | ✅ |
| Subtitle languages | ✅ | ✅ | ✅ |
| Per-title refresh | ✅ | ✅ | ✅ |
| Incremental updates | ❌ | ❌ | ❌ |
| Domain fallback | ✅ (2 domains) | ❌ (single) | ❌ (single) |

## Verified Domains

| Provider | Domain | Status | Notes |
|---|---|---|---|
| StreamingCommunity | streaming-community.army | ✅ Verified | Primary domain |
| StreamingCommunity | streamingcommunityz.nexus | ⚠️ Cloudflare | Fallback only |
| CineBlog01 | cineblog001.run | ✅ Verified | Main domain |
| AltaDefinizione | altadefinizionex.ink | ✅ Verified | Main domain |

## Rejected/Failed Domains

| Provider | Domain | Reason |
|---|---|---|
| StreamingCommunity | streamingcommunityz.com | For sale (GoDaddy) |
| StreamingCommunity | streamingcommunityz.it | Private/internal network |
| StreamingCommunity | streamingcommunityz.buzz | Private/internal network |

## Language Detection

All scrapers implement conservative language detection:

- **Never infer** audio language from site country/UI language
- **Explicit markers only**: `[ITA]`, `[Sub-ITA]`, `ITA`, `SUB-ITA`, `Traduzione: Italiano`
- **Unknown = empty list** (not "unavailable")
- **Normalized** to canonical codes via `tracker.languages.normalize_language_code`

## Error Handling

- **Network errors**: Retries with exponential backoff (max 2 retries)
- **HTTP 403/404/5xx**: Logged, returns empty list
- **Cloudflare challenges**: Detected, domain marked unhealthy, fallback tried
- **Parsing errors**: Graceful degradation, returns empty list
- **No crash**: All exceptions caught in `BaseScraper.run()`

## Tests

Added `test_new_scrapers.py` with 14 tests covering:

- Registry registration and instantiation
- Search/browse parsing with mocked HTML
- Language detection from title tags
- Cloudflare challenge handling
- Domain fallback (StreamingCommunity)
- Filter-based browsing (AltaDefinizione)
- Year/rating extraction
- Error handling (403, network errors)

All 491 tests pass (477 existing + 14 new).

## Known Limitations

1. **No season/episode data**: Sites don't expose structured episode metadata
2. **No incremental updates**: Full catalogue scrape each run
3. **Single domain for CineBlog01/AltaDefinizione**: No verified mirrors found
4. **Blocking**: Sites return 403/Cloudflare in automated contexts
5. **Live validation limited**: Fixtures-only tests; live smoke tests return 0 results due to blocking

## Usage

```bash
# Seed providers (run once)
docker compose exec backend python manage.py seed_unofficial_providers

# Trigger scrape for a title
curl -X POST http://localhost:8000/api/v2/unofficial/scrape/ \
  -H "Content-Type: application/json" \
  -d '{"provider_slug": "streamingcommunity", "query": "Inception"}'

# Search catalog
curl "http://localhost:8000/api/v2/unofficial/?q=inception&audio_lang=IT"

# Get offers for a movie
curl "http://localhost:8000/api/v2/unofficial/movie/27205/offers/"
```

## Future Improvements

1. Add more verified mirror domains for all providers
2. Implement incremental scraping with checkpoints
3. Add Playwright support for JavaScript-heavy pages
4. Implement episode-level scraping for series
5. Add rate limiting per provider
6. Monitor domain health and auto-discover new mirrors
