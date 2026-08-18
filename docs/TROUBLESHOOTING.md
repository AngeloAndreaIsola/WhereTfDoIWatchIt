# Troubleshooting Guide

This guide covers common errors, diagnostic steps, and resolutions for WhereTfDoIWatchIt.

---

## 1. TMDb API Key Missing or Invalid

### Symptom
- Search requests fail or return error responses (`TMDb search failed` / HTTP 502).
- Letterboxd CSV imports skip all titles or fail to resolve matches.

### Cause
The `TMDB_API_KEY` environment variable is either not set or contains an invalid/expired key.

### Resolution
1. Obtain a valid v3 API key from [The Movie Database (TMDb)](https://www.themoviedb.org/settings/api).
2. Set `TMDB_API_KEY` in your `.env` file:
   ```env
   TMDB_API_KEY=your_real_tmdb_api_key
   ```
3. Restart the backend container:
   ```bash
   docker compose restart backend
   ```

---

## 2. RapidAPI / Streaming Availability API Key Missing or Invalid

### Symptom
- Checking movie availability returns errors or unhandled exceptions.
- Logs show authentication errors when communicating with RapidAPI.

### Cause
The `STREAMING_AVAILABILITY_KEY` environment variable is not configured or is incorrect.

### Resolution
1. Subscribe to the [Streaming Availability API on RapidAPI](https://rapidapi.com/movieofthenight/api/streaming-availability/).
2. Copy your RapidAPI key and set `STREAMING_AVAILABILITY_KEY` in `.env`:
   ```env
   STREAMING_AVAILABILITY_KEY=your_rapidapi_key
   ```
3. Restart the backend container:
   ```bash
   docker compose restart backend
   ```

---

## 3. Streaming Availability API Rate Limit Exceeded (HTTP 429)

### Symptom
- Availability requests return HTTP 429 (`rate_limited: true`).
- Background availability refresh jobs fail to fetch new data.

### Cause
The free tier of the Streaming Availability API allows a maximum of 100 requests per calendar day (UTC).

### Resolution
- The application automatically caches availability results in PostgreSQL to minimize API usage.
- Avoid forcing live refreshes for large numbers of movies simultaneously.
- Wait until the quota resets at midnight UTC, or upgrade your RapidAPI plan if higher throughput is needed.

---

## 4. No Matches or No Results for a Movie

### Symptom
- TMDb search returns empty results (`results: []`).
- Movie availability shows no available streaming offers.

### Cause
- **TMDb Search**: The title may have typos or may require a specific release year to disambiguate.
- **Availability**: The movie may not be currently included with any of your configured subscriptions in your candidate countries (rental/purchase offers are intentionally excluded).

### Resolution
1. Verify the exact title and year on TMDb.
2. Check your configured subscriptions and candidate countries on the **Preferences** or **Subscriptions** page.

---

## 5. Ambiguous Letterboxd Matches

### Symptom
- After uploading a Letterboxd CSV, several movies are listed under `ambiguous` rather than added to the watchlist immediately.

### Cause
Letterboxd CSV exports do not include TMDb/IMDb IDs. When multiple movies share the same title (or when the release year in the CSV differs from TMDb), the system flags the row as ambiguous to prevent adding the wrong movie.

### Resolution
1. Use the frontend watchlist review interface to inspect the candidate matches.
2. Select the correct movie from the candidate list and click confirm. This sends a request to `/api/watchlist/resolve/` and saves the selection with `source: "letterboxd_resolved"`.

---

## 6. Docker Compose Stack Not Running or DB Connection Errors

### Symptom
- Health checks return `"db": "unreachable"`.
- Requests fail with connection refused or database connection errors.

### Cause
PostgreSQL container is not running, healthy, or network connectivity between containers is broken.

### Resolution
1. Check container statuses:
   ```bash
   docker compose ps
   ```
2. Inspect container logs:
   ```bash
   docker compose logs db
   docker compose logs backend
   ```
3. Restart the stack:
   ```bash
   docker compose down
   docker compose up -d
   ```

---

## 7. Database Migrations Not Applied

### Symptom
- API requests fail with `relation "tracker_..." does not exist` database errors.

### Cause
New database migrations have not been executed after starting or updating the stack.

### Resolution
Run migrations inside the backend container:
```bash
docker compose exec backend python manage.py migrate
```
