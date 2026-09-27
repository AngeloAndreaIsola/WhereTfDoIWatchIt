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
   - To respect this constraint, all availability requests use a cache-first strategy via `AvailabilityCache`.

2. **Caching Strategy**:
   - `GET /api/movie/<tmdb_id>/availability/` and `GET /api/watchlist/report/` check the database cache for movie availability before making any external API calls.
   - Cached responses return cache status metadata (`refreshed`, `reason`, `offers`).
   - If a refresh is required but the daily rate limit count has been reached, the backend returns HTTP **429 Too Many Requests** with `{"error": "...", "rate_limited": true}`.

3. **External API Calls**:
   - `GET /api/search/` makes external calls to TMDb API using `TMDB_API_KEY`. Network failures return HTTP **502 Bad Gateway**.

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
  - Uses `AvailabilityCache` to serve cached data or fetch fresh offer data if stale.
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
      "reason": "fresh",
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
  - `429 Too Many Requests`: Streaming Availability API rate limit exceeded during cache refresh attempt. Example: [`docs/examples/api/error_429_response.json`](examples/api/error_429_response.json)

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
- **Purpose**: Upload a Letterboxd export CSV file (`file` field in `multipart/form-data`) to parse and import movies.
- **Request Content Type**: `multipart/form-data`
- **Form Data Fields**:
  - `file` *(required, file)*: The uploaded Letterboxd watchlist CSV file containing `Title`, `Year`, and `Letterboxd URI`.
- **Behavior**:
  - Exact matches automatically create `Movie` and `WatchlistItem` records (returned in `resolved`).
  - Ambiguous matches (e.g. multiple TMDb search candidates) are returned in `ambiguous`.
  - Unmatched rows are returned in `skipped`.
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

---

### 6. Watchlist Resolve

#### `POST /api/watchlist/resolve/`
- **Purpose**: Resolve user-selected movie candidates (from ambiguous Letterboxd matches or manual searches) into database `Movie` and `WatchlistItem` records (`source="letterboxd_resolved"`).
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
  - Iterates through watchlist movies and checks `AvailabilityCache`.
  - Determines all available country codes (uppercase), the best country match, and best streaming service for each movie.
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
        "best_service": "Netflix"
      }
    ],
    "summary": {
      "total_movies": 2,
      "available_movies": 1,
      "unavailable_movies": 1,
      "countries": [{"country": "US", "count": 1}],
      "services": [{"service": "Netflix", "count": 1}]
    }
  }
  ```
- **Error Responses**:
  - `429 Too Many Requests`: Triggered if a fresh lookup is required and the Streaming Availability API rate limit is reached. Example: [`docs/examples/api/error_429_response.json`](examples/api/error_429_response.json)

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
- **Environment**: `PRIME_LIVE_FETCH` (default `true`). When `true`, a cache miss triggers a live Streaming Availability API call. When `false`, only cached `Availability` rows are returned.
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
