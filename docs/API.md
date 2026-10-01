# WhereTfDoIWatchIt — Backend API Specification

This document provides the complete API contract specification for the **WhereTfDoIWatchIt** Django / Django REST Framework (DRF) backend service.

---

## Overview

- **Base URL Prefix**: `/api/`
- **Authentication**: Single-user mode (`user_id = 1`), no authentication or authorization headers required for v1.
- **Data Format**: `application/json` for request bodies and responses (except `POST /api/watchlist/` which requires `multipart/form-data`).

---

## Rate Limiting and Caching Rules

1. **Streaming Availability API Quota**:
   - The Streaming Availability API provider has a strict free-tier daily rate limit of **100 requests per day**.
   - The quota is consumed only by budgeted refreshes (the `refresh_availability` cron job and `POST /api/watchlist/refresh/`), never by a read and never by merely *requesting* a refresh: event-driven `RefreshRequest` rows (watchlist adds, imports, refresh-on-view) are drained inside those same budgeted runs and share their budget, rate limiter and `RefreshRun` audit row.

2. **Reads are local**:
   - Read endpoints (`GET /api/movie/<tmdb_id>/availability/`, `GET /api/watchlist/report/`, `GET /api/watch/<tmdb_id>/`, `GET /api/catalog/unified/`, …) answer from canonical Postgres rows — reconciled offers for availability, `ProviderContent`/`ContentMapping` for the catalogue. They never call an external API, never trigger a refresh, and cannot return HTTP 429 for rate-limit reasons.
   - Title-detail reads (`GET /api/movie/<pk>/`, `GET /api/prime/movie|series/<id>/`) may *queue* an asynchronous refresh for a stale or never-fetched title: that enqueue is a local database insert inside the request, so the response is still served entirely from local rows and never waits on a provider.
   - The availability response still carries a `cache` status block (`refreshed`, `reason`, `offers`); `reason` is `"local"` because the read is served from local canonical offers.

3. **Where 429 can still happen**:
   - `POST /api/watchlist/refresh/` performs a budgeted live refresh; when the daily quota is exhausted it returns HTTP **429 Too Many Requests** with a summary body whose `rate_limited` flag is `true`. Example: [`docs/examples/api/error_429_response.json`](examples/api/error_429_response.json)

4. **External API Calls**:
   - `GET /api/search/` makes external calls to TMDb API using `TMDB_API_KEY`. Network failures return HTTP **502 Bad Gateway**.
   - `POST /api/scrapers/run/` executes a scraper against its public site on demand; this is unrelated to the Streaming Availability quota.

---

## Endpoints Summary

| Method | Endpoint Path | Description |
|---|---|---|
| `GET` | `/api/health/` | Liveness and database connectivity check. |
| `GET` | `/api/health_full/` | Legacy / compatibility alias for `/api/health/`. |
| `GET` | `/api/search/` | Search TMDb for movies and TV series by title (optional `type`: `movie`/`series`/`all`). |
| `GET` | `/api/movie/<tmdb_id>/availability/` | Fetch cached, ranked streaming availability for a movie. |
| `GET` | `/api/preferences/` | Read user streaming preferences. |
| `PUT` | `/api/preferences/` | Update user streaming preferences. |
| `GET` | `/api/watchlist/` | List user watchlist items. |
| `POST` | `/api/watchlist/` | Upload and parse Letterboxd CSV watchlist file. |
| `POST` | `/api/watchlist/resolve/` | Resolve selected ambiguous/confirmed movies into watchlist. |
| `GET` | `/api/watchlist/report/` | Generate aggregated availability report for watchlist items. |
| `POST` | `/api/watchlist/refresh/` | Run a budgeted live availability refresh (returns `429` when rate-limited). |
| `GET` | `/api/catalog/unified/` | Unified catalogue: official titles with watchability merged with scraper sources, filtered and paginated. |
| `GET` | `/api/prime/popular/` | Return trending/popular movies from TMDb for the homepage browse grid. |
| `GET` | `/api/prime/movie/<tmdb_id>/` | Return ranked Prime Video availability offers for a single movie. |
| `GET` | `/api/prime/series/<tmdb_id>/` | Return ranked Prime Video availability offers for a single TV series. |
| `GET` | `/api/watch/<tmdb_id>/` | Resolve the top-ranked watch destination for a movie or series. |

---

## Endpoints Detail

### 1. Health Check

#### `GET /api/health/`
- **Purpose**: Verify backend service status and database connectivity.
- **Parameters**: None.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/health_response.json`](examples/api/health_response.json)
  ```json
  {
    "status": "ok",
    "db": "ok"
  }
  ```

#### `GET /api/health_full/`
- **Purpose**: Legacy compatibility alias for `/api/health/`. Performs the identical health and database connectivity check.
- **Parameters**: None.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/health_full_response.json`](examples/api/health_full_response.json)

---

### 2. Movie Search

#### `GET /api/search/`
- **Purpose**: Search TMDb for movies by query string and optional release year.
- **Query Parameters**:
  - `q` or `query` *(required, string)*: The title search query.
  - `year` *(optional, integer)*: Filter results by release year.
- **Response**: `200 OK`
  - Returns up to 10 matching movies.
  - Example: [`docs/examples/api/search_response.json`](examples/api/search_response.json)
  ```json
  {
    "results": [
      {
        "tmdb_id": 27205,
        "title": "Inception",
        "year": 2010
      }
    ]
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: Missing query parameter `q` or `query`. Example: [`docs/examples/api/error_400_response.json`](examples/api/error_400_response.json)
  - `502 Bad Gateway`: Failure talking to TMDb API (e.g. invalid API key or timeout). Example: [`docs/examples/api/error_502_response.json`](examples/api/error_502_response.json)

---

### 3. Movie Availability

#### `GET /api/movie/<tmdb_id>/availability/`
- **Purpose**: Retrieve ranked streaming availability offers for a tracked movie based on user preferences.
- **Path Parameters**:
  - `tmdb_id` *(required, integer)*: The TMDb ID of the movie.
- **Notes on Caching & Quota**:
  - The movie must already be tracked in the local database (e.g., added via watchlist or resolved).
  - Served entirely from local canonical offers (reconciled `Offer` rows) — a read never triggers a live upstream fetch; availability refreshes run separately via cron.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/movie_availability_response.json`](examples/api/movie_availability_response.json)
  ```json
  {
    "movie": {
      "tmdb_id": 27205,
      "title": "Inception",
      "year": 2010
    },
    "cache": {
      "refreshed": false,
      "reason": "local",
      "offers": 2
    },
    "offers": [
      {
        "country": "US",
        "service": "Netflix",
        "offer_type": "subscription",
        "quality": "4k",
        "audio_languages": ["EN"],
        "subtitle_languages": ["EN", "ES"],
        "ranking": {
          "score": 100,
          "reasons": ["matches_candidate_country", "matches_audio_language", "matches_subtitle_language"]
        }
      }
    ]
  }
  ```
- **Error Responses**:
  - `404 Not Found`: Movie is not tracked in the database yet. Example: [`docs/examples/api/error_404_response.json`](examples/api/error_404_response.json)

---

### 4. User Preferences

#### `GET /api/preferences/`
- **Purpose**: Retrieve current language and candidate country preferences for `user_id=1`.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/preferences_get_response.json`](examples/api/preferences_get_response.json)
  ```json
  {
    "audio_languages": ["EN"],
    "subtitle_languages": ["EN", "ES"],
    "candidate_countries": ["us", "gb"]
  }
  ```

#### `PUT /api/preferences/`
- **Purpose**: Update language and candidate country preferences for `user_id=1` (supports partial updates).
- **Request Body**: JSON object
  - Example Request Body: [`docs/examples/api/preferences_put_request.json`](examples/api/preferences_put_request.json)
  ```json
  {
    "audio_languages": ["EN", "IT"],
    "subtitle_languages": ["EN"],
    "candidate_countries": ["us", "gb", "it"]
  }
  ```
- **Response**: `200 OK`
  - Example Response: [`docs/examples/api/preferences_put_response.json`](examples/api/preferences_put_response.json)

---

### 5. Watchlist

#### `GET /api/watchlist/`
- **Purpose**: List all watchlist items for `user_id=1`, ordered by addition date descending (`-added_at`).
- **Response**: `200 OK`
  - Example: [`docs/examples/api/watchlist_get_response.json`](examples/api/watchlist_get_response.json)
  ```json
  [
    {
      "id": 1,
      "movie": {
        "id": 101,
        "tmdb_id": 27205,
        "title": "Inception",
        "year": 2010
      },
      "source": "letterboxd_resolved",
      "added_at": "2025-01-15T12:00:00Z"
    }
  ]
  ```

#### `POST /api/watchlist/`
- **Purpose**: Upload a CSV file (`file` field in `multipart/form-data`) to parse and import movies.
- **Request Content Type**: `multipart/form-data`
- **Form Data Fields**:
  - `file` *(required, file)*: A Letterboxd export CSV (`Title`, `Year`, `Letterboxd URI`), a MyAnimeList export, a ZIP of Letterboxd CSVs, **or** a WhereTfDoIWatchIt backup export (`title,year,letterboxd_uri,status`).
- **Behavior**:
  - The format is detected from the CSV header (not the filename): a file with the canonical `title,year,letterboxd_uri,status` header is parsed by the backup importer, anything else by the Letterboxd parser. Both funnel into the same matching/import pipeline.
  - Exact matches automatically create `Movie` and `WatchlistItem` records (returned in `resolved`).
  - Ambiguous matches (e.g. multiple TMDb search candidates) are returned in `ambiguous`.
  - Unmatched rows are returned in `skipped`.
  - Canonical rows apply their `status`: `watchlist` rows create `WatchlistItem`s, `watched` rows set `UserMovieData.is_watched = True`.
  - Titles *entering* the catalogue (new `Movie` or new `WatchlistItem`) — or titles with no canonical availability at all — queue an asynchronous availability refresh (`RefreshRequest`); fresh titles and already-seen stale titles are left to the scheduled sweep, so a large import never floods the refresh budget. The response itself never waits on a provider.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/watchlist_import_response.json`](examples/api/watchlist_import_response.json)
  ```json
  {
    "resolved": [ ... ],
    "ambiguous": [ ... ],
    "skipped": [ ... ]
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: Missing `file` field in upload. Example: [`docs/examples/api/error_400_response.json`](examples/api/error_400_response.json)
  - `400 Bad Request`: Canonical CSV with an invalid `status` value (must be `watchlist` or `watched`). Example: `{"error": "Line 2: invalid status 'banana' — must be 'watchlist' or 'watched'."}`

#### `GET /api/export/watchlist/`
- **Purpose**: Download every movie on the current user's watchlist as a canonical WhereTfDoIWatchIt CSV backup. Every row has `status=watchlist`.
- **Response**: `200 OK`, `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="wheretfdoiwatchit-watchlist.csv"`.
  ```csv
  title,year,letterboxd_uri,status
  The Matrix,1999,https://letterboxd.com/film/the-matrix/,watchlist
  ```

#### `GET /api/export/watched/`
- **Purpose**: Download only movies explicitly marked watched (`UserMovieData.is_watched == True`) as a canonical CSV backup. Every row has `status=watched`.
- **Response**: `200 OK`, `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="wheretfdoiwatchit-watched.csv"`.
  ```csv
  title,year,letterboxd_uri,status
  Inception,2010,https://letterboxd.com/film/inception/,watched
  ```

#### `GET /api/export/`
- **Purpose**: Download watchlist **and** watched movies in one combined canonical CSV backup (a movie in both states legitimately appears twice, once per status).
- **Response**: `200 OK`, `Content-Type: text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="wheretfdoiwatchit-export.csv"`.
  ```csv
  title,year,letterboxd_uri,status
  The Matrix,1999,https://letterboxd.com/film/the-matrix/,watchlist
  Inception,2010,https://letterboxd.com/film/inception/,watched
  ```
- **Notes**: All export endpoints return a valid header-only CSV for empty datasets and are restricted to the current user's data. See `docs/LETTERBOXD_IMPORT.md` §7 for the full backup format description.

---

### 6. Watchlist Resolve

#### `POST /api/watchlist/resolve/`
- **Purpose**: Resolve user-selected movie candidates (from ambiguous Letterboxd matches or manual searches) into database `Movie` and `WatchlistItem` records (`source="letterboxd_resolved"`).
- **Behavior**: each resolved title also queues an asynchronous availability refresh (`RefreshRequest`, watchlist priority). The enqueue is a local insert — repeated resolves collapse into one pending request, fresh titles are skipped, and no provider is contacted while answering the request.
- **Request Body**: JSON object with `selections` list.
  - Example Request Body: [`docs/examples/api/watchlist_resolve_request.json`](examples/api/watchlist_resolve_request.json)
  ```json
  {
    "selections": [
      {
        "original_title": "Dune",
        "year": 1984,
        "tmdb_id": 841
      }
    ]
  }
  ```
- **Response**: `201 Created`
  - Example Response: [`docs/examples/api/watchlist_resolve_response.json`](examples/api/watchlist_resolve_response.json)
  ```json
  {
    "resolved_count": 1,
    "items": [
      {
        "id": 3,
        "movie": {
          "id": 103,
          "tmdb_id": 841,
          "title": "Dune",
          "year": 1984
        },
        "source": "letterboxd_resolved",
        "added_at": "2025-01-15T12:05:00Z"
      }
    ]
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: Missing or invalid `selections` field, or elements lack valid integer `tmdb_id`. Example: [`docs/examples/api/error_400_response.json`](examples/api/error_400_response.json)

---

### 7. Watchlist Report

#### `GET /api/watchlist/report/`
- **Purpose**: Return an aggregated availability overview for all watchlist movies belonging to `user_id=1`.
- **Behavior & Performance**:
  - Iterates through watchlist movies reading local canonical offers (reconciled `Offer` rows) — no external API calls.
  - Determines all available country codes (uppercase), the best country match, and best streaming service for each movie; supports the same composable filters as the unified catalogue (`q`, `content_type`, `year_min`/`year_max`, `country`, `providers`, `status`, `quality`, `genre`, `actors`, `score_min`) plus pagination (`page`, `page_size`).
- **Response**: `200 OK`
  - Example: [`docs/examples/api/watchlist_report_response.json`](examples/api/watchlist_report_response.json)
  ```json
  {
    "report": [
      {
        "movie": {
          "id": 101,
          "tmdb_id": 27205,
          "title": "Inception",
          "year": 2010
        },
        "available_countries": ["GB", "US"],
        "best_country": "US",
        "best_service": "Netflix",
        "watchability": { "status": "watchable_here", "status_label": "…", "best_offer": {} },
        "is_watched": false,
        "user_data": null
      }
    ],
    "pagination": { "count": 2, "page": 1, "page_size": 24, "total_pages": 1, "has_next": false, "has_previous": false },
    "summary": {
      "total_movies": 2,
      "available_movies": 1,
      "unavailable_movies": 1,
      "countries": [{"country": "US", "count": 1}],
      "services": [{"service": "Netflix", "count": 1}]
    }
  }
  ```

---

### 8. Prime Video Popular (Homepage Browse)

#### `GET /api/prime/popular/`
- **Purpose**: Return trending/popular movies from TMDb for the homepage browse grid.
- **Query Parameters**:
  - `limit` *(optional, integer)*: Number of movies to return. Default 24, max 30.
- **Caching**: Results are cached in-memory for 10 minutes to avoid hitting the TMDb API on every page load.
- **Categories**: Derived from TMDb `genre_ids` via a hardcoded lookup table (Action, Comedy, Drama, Sci-Fi, Horror, etc.).
- **Response**: `200 OK`
  - Example: [`docs/examples/api/prime_popular_response.json`](examples/api/prime_popular_response.json)
  ```json
  {
    "results": [
      {
        "tmdb_id": 27205,
        "title": "Inception",
        "year": 2010,
        "poster_path": "/9gk7ARbtY8864fKE6tKjW8aZbZc.jpg",
        "overview": "A thief who steals corporate secrets...",
        "content_type": "movie",
        "categories": ["Action", "Sci-Fi", "Thriller"]
      }
    ],
    "count": 1,
    "cached": false,
    "source": "tmdb_trending"
  }
  ```

---

### 9. Prime Video Movie Offers (Detail Page)

#### `GET /api/prime/movie/<tmdb_id>/`
- **Purpose**: Return ranked Prime Video (provider=`amazon`) availability offers for a single movie, filtered to `subscription`/`free` offer types only, ranked by strict priority: audio language match (JA > EN) → subtitle language match (EN > JA) → quality (4K > HDR > HD > SD) → country code (alphabetical tiebreak).
- **Path Parameters**:
  - `tmdb_id` *(required, integer)*: The TMDb ID of the movie.
- **Data source**: served entirely from cached availability rows — a detail request never triggers a live upstream fetch. If the title is stale or was never fetched, the read *queues* an asynchronous refresh (`RefreshRequest`, detail-view priority) that the budgeted refresh run drains later; the response still returns the local rows immediately. This applies to `GET /api/prime/movie/<tmdb_id>/`, `GET /api/prime/series/<tmdb_id>/` and `GET /api/movie/<pk>/` alike.
- **Response**: `200 OK`
  - Example: [`docs/examples/api/prime_movie_response.json`](examples/api/prime_movie_response.json)
  ```json
  {
    "movie": { "tmdb_id": 27205, "title": "Inception", "year": 2010 },
    "offers": [
      {
        "country": "JP",
        "service": "amazon",
        "offer_type": "subscription",
        "quality": "4k",
        "audio_languages": ["JA", "EN"],
        "subtitle_languages": ["EN", "JA"],
        "ranking": { "audio_rank": 0, "subtitle_rank": 0, "quality_rank": 0 }
      }
    ]
  }
  ```
- **Error Responses**:
  - `404 Not Found`: Movie not available on Prime Video in any country (or not tracked in DB and live fetch is disabled).
  - Example: [`docs/examples/api/error_prime_not_available.json`](examples/api/error_prime_not_available.json)
  ```json
  {
    "movie": { "tmdb_id": 27205, "title": "Inception", "year": 2010 },
    "offers": [],
    "message": "Not available on Prime Video in any country."
  }
  ```

---

### 10. Prime Video Series Offers (Series Detail Page)

#### `GET /api/prime/series/<tmdb_id>/`
- **Purpose**: Same offer pipeline as `GET /api/prime/movie/<tmdb_id>/`, for a TV series. The row is resolved through the TMDb **TV** details endpoint (never the movie endpoint) and the payload is keyed on `series`.
- **Path Parameters**:
  - `tmdb_id` *(required, integer)*: The TMDb TV ID of the series.
- **Query Parameters**:
  - `current_country` *(optional, ISO 3166-1 alpha-2)*: Active country used for ranking and the "Local vs VPN required" badge. Falls back to the saved preference.
  - `season` *(optional, integer)*: Restrict `offers` to that season's availability. Read from the upstream `seasons[]` payload and stored per row in `Availability.seasons_available`; season numbers are resolved against the TMDb season list, so an unknown season simply has no rows. A non-integer value is ignored (as if omitted); a well-formed but unknown season returns `200` with `offers: []` and the value echoed in `selected_season`. When `season` is set, show-level scraper (BVOD) offers are left out, because scraper rows carry no season information. Without the param the unfiltered result is returned unchanged.
- **Response**: `200 OK`
  ```json
  {
    "series": { "id": 7, "tmdb_id": 1396, "title": "Breaking Bad", "year": 2008, "content_type": "series" },
    "offers": [],
    "seasons": [
      { "season": 1, "name": "Season 1", "episode_count": 7, "poster_path": "/s1.jpg" },
      { "season": 2, "name": "Season 2", "episode_count": 13, "poster_path": "/s2.jpg" }
    ],
    "selected_season": null,
    "is_in_watchlist": false,
    "user_data": null,
    "watch_events": []
  }
  ```
  - `seasons` *(series responses only)*: TMDb season metadata for the selector, ordered by season number; empty when TMDb enrichment never stored it. It is metadata-driven, so a season with no streaming option still appears — selecting it yields `offers: []`.
  - `selected_season` *(series responses only)*: The season the request was filtered to, or `null` when unfiltered.
- **Error Responses**:
  - `404 Not Found`: the id is not tracked and cannot be resolved on TMDb (or the tracked row is a movie, which is never served as a series).
  ```json
  {
    "series": { "tmdb_id": 99999, "title": null, "year": null },
    "offers": [],
    "message": "Series not tracked and could not be resolved via TMDb."
  }
  ```

---

### 11. Watch Destination (Movie or Series)

#### `GET /api/watch/<tmdb_id>/`
- **Purpose**: Resolve the top-ranked watch URL for a movie or series under the user's subscriptions and preferences (same ranking path as the detail endpoints).
- **Resolution**: An existing row is always used as-is and its `content_type` is echoed back. When the id is not tracked yet the movie details endpoint is tried first; if TMDb reports that the id does not exist as a movie, the TV details endpoint is retried, so a series id is not assumed to be a movie.
- **Response**: `200 OK`
  ```json
  {
    "found": true,
    "watch_url": "https://www.primevideo.com/detail/1396",
    "movie": { "tmdb_id": 1396, "title": "Breaking Bad", "year": 2008, "poster_path": "/bb.jpg", "content_type": "series" },
    "offer": { "country": "US", "service": "prime", "offer_type": "subscription" }
  }
  ```
- **Error Responses**:
  - `404 Not Found`: neither a movie nor a series could be resolved for the id.
  ```json
  { "found": false, "movie": { "tmdb_id": 999999, "title": null, "year": null }, "message": "Movie not found" }
  ```

---

### 12. Watchlist Availability Refresh

#### `POST /api/watchlist/refresh/`
- **Purpose**: Run a budgeted live refresh of stale availability for watchlist movies (`user_id=1`). This is the only endpoint that can consume Streaming Availability API quota at request time — all GET reads stay local. The run first drains pending event-driven `RefreshRequest` rows (watchlist adds, imports, refresh-on-view) in priority order, then refreshes the background stale sweep candidates with whatever budget remains.
- **Request Body**: JSON (`budget` optional).
  - `budget` *(optional, integer, default 10, max 90)*: number of live refreshes to perform. Values outside `1..90`, booleans and non-integers are rejected with `400`.
- **Response**: `200 OK`
  ```json
  {
    "attempted": 12,
    "refreshed": 9,
    "skipped_cached": 3,
    "skipped_budget_exhausted": 0,
    "skipped_rate_limited": 0,
    "errors": 0,
    "budget": 10,
    "remaining_budget": 1,
    "rate_limited": false,
    "requests_drained": 2,
    "requests_pending": 1
  }
  ```
  `requests_drained` counts `RefreshRequest` rows settled by this run (`done` or `failed`); `requests_pending` is the backlog left queued for a later run. Both fields are additive — `refreshed`/`skipped_*` also include drained work, and `attempted` still counts only sweep candidates.
- **Error Responses**:
  - `400 Bad Request`: invalid `budget` (non-positive, > 90, non-integer, or boolean).
  - `429 Too Many Requests`: daily API quota exhausted before/at the start of the run; body is the same summary shape with `"rate_limited": true`. Example: [`docs/examples/api/error_429_response.json`](examples/api/error_429_response.json)

---

### 13. Unified Catalogue

#### `GET /api/catalog/unified/`
- **Purpose**: One paginated feed merging the official catalogue (tracked `Movie` rows with watchability) and the unofficial scraper catalogue, powering the catalog page.
- **Data source**: fully local — official items come from canonical offers/reconciliation, scraper items from canonical `ProviderContent`/`ContentMapping` rows. The endpoint never calls TMDb, never fetches availability and never runs a scraper; stale rows are served as-is until the scheduled refresh updates them. Legacy `ScrapedContentMapping` rows reach the response only through the ingestion bridge (`availability.services.legacy_ingest`), not by being read.
- **Query Parameters** *(all optional)*:
  - `q` / `query`: case-insensitive substring match on title (and overview on the official half).
  - `year`, `year_min`, `year_max`: exact year or range (digits; non-digits ignored).
  - `country`: ISO country code; matches a scraper row's own country or its provider's country, and offer countries on the official half.
  - `content_type` (`movie`/`series`/`tv`), `genre` (id or name), `actors` (JSON array) / `actor_id` (comma-separated), `status`, `quality`, `watchable`, `score_min`: official-half composable filters.
  - `providers` / `provider`: comma-separated provider slugs. Selecting only scraper providers returns only scraper items; selecting only official providers returns only official items; selecting both (or neither) returns both.
  - `provider_categories`: comma-separated categories — `major`, `bvod`/`free`, `scraper`/`unofficial`, or a raw provider category; expanded to slugs like `providers`.
  - `current_country` / `country`: user's country for watchability ranking (defaults to `US`).
  - `sort_by` / `sort`: `rating_desc`/`rating_high`, `rating_asc`/`rating_low`, `year_desc`/`year_newest`, `year_asc`/`year_oldest`, `title_asc`/`title`, `title_desc`/`-title`.
  - `page` (default 1), `page_size` (default 24, max 100).
- **Deduplication**: a scraper row whose `tmdb_id` already appears in the official half is suppressed (one title, one row).
- **Response**: `200 OK` — pagination envelope (`count`, `page`, `page_size`, `total_pages`, `has_next`, `has_previous`) plus `results[]`:
  ```json
  {
    "count": 2,
    "page": 1,
    "page_size": 24,
    "total_pages": 1,
    "has_next": false,
    "has_previous": false,
    "results": [
      {
        "id": "official_101",
        "tmdb_id": 27205,
        "source": "official",
        "source_indicator": "SVOD",
        "title": "Inception",
        "year": 2010,
        "poster_path": "/9gk7adHYeDvHkCSEqAvQNLV5Uge.jpg",
        "content_type": "movie",
        "provider": { "slug": "netflix", "name": "Netflix", "category": "official" },
        "watchability": {
          "status": "watchable_here",
          "status_label": "Available",
          "status_symbol": "🟢",
          "best_offer": { "country": "US", "service": "netflix", "url": "https://...", "is_current_country": true },
          "watchable_offers": [],
          "excluded_offers": [],
          "explanation": []
        },
        "unofficial_details": null,
        "watch_url": "https://..."
      },
      {
        "id": "scraper_42",
        "tmdb_id": null,
        "source": "scraper",
        "source_indicator": "Scraper",
        "title": "Some Unresolved Show",
        "year": 2024,
        "poster_path": "",
        "content_type": "series",
        "provider": { "slug": "hydrahd", "name": "HydraHD", "category": "scraper" },
        "watchability": null,
        "unofficial_details": {
          "scraped_url": "https://hydrahd.ws/watch/123",
          "confidence_score": 0.0,
          "is_confirmed": false,
          "audio_languages": [],
          "subtitle_languages": []
        },
        "watch_url": "https://hydrahd.ws/watch/123"
      }
    ]
  }
  ```
- **Item semantics**:
  - Official items: `watchability` carries the full evaluation (`status`, `best_offer`, `watchable_offers`, `excluded_offers`, `explanation`); `unofficial_details` is `null`.
  - Scraper items: `watchability` is always `null`; `unofficial_details` carries the scraped URL, TMDb match confidence (`confidence_score`), whether the match was manually confirmed (`is_confirmed`), and any known audio/subtitle languages.
  - For a scraper row resolved to a tracked movie, display fields (`title`, `year`, `poster_path`, `content_type`) come from the canonical `Movie` row — never the scraped strings.
