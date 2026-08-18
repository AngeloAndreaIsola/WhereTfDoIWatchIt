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
| `GET` | `/api/search/` | Search TMDb for movies by title and optional year. |
| `GET` | `/api/movie/<tmdb_id>/availability/` | Fetch cached, ranked streaming availability for a movie. |
| `GET` | `/api/preferences/` | Read user streaming preferences. |
| `PUT` | `/api/preferences/` | Update user streaming preferences. |
| `GET` | `/api/watchlist/` | List user watchlist items. |
| `POST` | `/api/watchlist/` | Upload and parse Letterboxd CSV watchlist file. |
| `POST` | `/api/watchlist/resolve/` | Resolve selected ambiguous/confirmed movies into watchlist. |
| `GET` | `/api/watchlist/report/` | Generate aggregated availability report for watchlist items. |

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
        "country": "us",
        "service": "Netflix",
        "offer_type": "subscription",
        "quality": "4k",
        "audio_languages": ["en"],
        "subtitle_languages": ["en", "es"],
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
    "audio_languages": ["en"],
    "subtitle_languages": ["en", "es"],
    "candidate_countries": ["us", "gb"]
  }
  ```

#### `PUT /api/preferences/`
- **Purpose**: Update language and candidate country preferences for `user_id=1` (supports partial updates).
- **Request Body**: JSON object
  - Example Request Body: [`docs/examples/api/preferences_put_request.json`](examples/api/preferences_put_request.json)
  ```json
  {
    "audio_languages": ["en", "it"],
    "subtitle_languages": ["en"],
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
