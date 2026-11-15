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
The free tier of the Streaming Availability API allows a maximum of 100 requests per calendar day (UTC) and a **1,000-request monthly hard limit**. The local guard now enforces both. Because the provider's exact quota period is not confirmed (calendar month vs billing-cycle anniversary), the monthly guard is a conservative **rolling 31-day window**: at most 1,000 reservations in any 31 consecutive UTC days. This cannot under-count a 28–31-day provider period and does not increase request volume.

Budget is consumed at the HTTP boundary in `StreamingAvailabilityClient.get_availability`, immediately before the one upstream request. Every attempted request consumes one unit, including 404s, 5xx responses and timeouts; a local refusal consumes nothing. No SA retries were added. The coordinator reserves the same call before `refresh_content` and marks it context-locally so one request is never charged twice.

The guard requires migration `tracker.0043_apicallledger` (`ApiCallLedger`, one row per source per UTC day).

- **Local development stack:** migration `0043` **has** been applied. The `tracker_apicallledger` table and the `uniq_apicallledger_service_day` unique constraint are present, and `django_migrations` records the row. The backend entrypoint reported "No migrations to apply" on the rebuilt image, confirming it was already applied.
- **Other environments:** migration status must be checked independently (`python manage.py showmigrations tracker` or inspecting `django_migrations`). Do not assume the local state applies elsewhere.

Pre-ledger calls are unknown, not zero; `monthly_accounting_since` exposes the earliest day the guard can see, and `monthly_used`/`monthly_remaining` report the local conservative window. An **empty ledger means pre-ledger usage is unknown** — it is not proof that upstream quota is available. If the daily counter and ledger disagree, the larger value is used (fail closed). If the ledger cannot be read, the request is refused rather than allowed through.

### Resolution
- The application automatically caches availability results in PostgreSQL to minimize API usage.
- Avoid forcing live refreshes for large numbers of movies simultaneously.
- Check the status endpoint's `source_quotas.streaming_availability` fields: `monthly_used`, `monthly_remaining`, `monthly_limit`, `monthly_window_days`, `monthly_accounting_since` and `monthly_resets_at`. A local budget refusal is **not** an upstream 429.
- Wait for the daily reset at midnight UTC, or for `monthly_resets_at` (the local rolling-window reset), or upgrade your RapidAPI plan if higher throughput is needed.
- If 429s persist across days, check the RapidAPI dashboard for the monthly usage before assuming the daily reset will help.
- If the deployment has not applied `tracker.0043_apicallledger`, apply it with `python manage.py migrate tracker`. Until then, SA requests fail closed and the monthly diagnostics cannot be trusted.
- Do not roll back `0043` while this code is running: the ledger table disappears, and an SA reservation raises `ProgrammingError` before the HTTP request (fail-closed, but not a supported runtime state). Roll back code and schema together.

---

## 4. Watchmode Monthly Plan Quota Exhausted (HTTP 429)

### Symptom
- Watchmode requests return HTTP 429 and the run reports that source as rate-limited.
- The response body is `{"success": false, "errorMessage": "Over plan quota on this API Key."}`.
- `Retry-After: 60` is present, but the block does not clear in a minute.
- With the current code the refresh request settles as `blocked`/`quota_exhausted`, and `last_error` starts with `SourceQuotaExceeded(kind=plan_quota, ...)`.

### Cause
The Watchmode account's **monthly plan quota** is spent (measured plan: 2,500 requests/month). This is not a transient rate window. The 2026-10-21 diagnosis recorded `x-ratelimit-limit: 120`, `x-ratelimit-remaining: 0` and the body above; a retry **70 seconds later returned the same answer**. The local history matches the allowance: 1,241 successful Watchmode refreshes at two calls each (2,482) plus 21 failed searches = **2,503 calls** before the first 429.

The historical `blocked/quota_exhausted` rows (3,288 Watchmode, 3,291 YouTube, 3,274 Mediaset, 187 JustWatch) predate commit `88658f7` (2026-10-17) and were caused by the worker's **run budget**, not by upstream quota. They are not evidence for this section.

### Resolution
- **Do not retry.** A spent monthly plan cannot succeed until it resets. The scheduler now records the refusal as terminal (`blocked`/`quota_exhausted`) instead of retrying it `MAX_ATTEMPTS` times and marking the title failed. A genuine short rate window (`kind=rate_limit`) still stays `pending` and consumes no attempt.
- **Do not treat `Retry-After: 60` as the reset time.** It is emitted for the monthly block too. Confirm the plan period in the Watchmode dashboard; the reset is expected around the start of the next month, but that date has not been measured.
- While the quota is spent, Watchmode adds no offers. First-party collectors and JustWatch are independent; their coverage is unaffected.
- When quota returns, follow `docs/WATCHMODE_RECHECK.md` for the read-only 42-offer/36-title plan and `docs/WATCHMODE_UNCORROBORATED_OFFERS.md` for the underlying evidence. The terminal refusal trips the tick-scoped Watchmode circuit; a future reset is detected by one probe in the next tick, not by a persisted disable. The circuit is process-local and tick-local, so two concurrent ticks each get one probe; the catalogue lane cannot reach Watchmode (`supports_catalogue_sync=False`), which is why the title drain is the only path that needs it.

---

## 5. No Matches or No Results for a Movie

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

## 6. Ambiguous Letterboxd Matches

### Symptom
- After uploading a Letterboxd CSV, several movies are listed under `ambiguous` rather than added to the watchlist immediately.

### Cause
Letterboxd CSV exports do not include TMDb/IMDb IDs. When multiple movies share the same title (or when the release year in the CSV differs from TMDb), the system flags the row as ambiguous to prevent adding the wrong movie.

### Resolution
1. Use the frontend watchlist review interface to inspect the candidate matches.
2. Select the correct movie from the candidate list and click confirm. This sends a request to `/api/watchlist/resolve/` and saves the selection with `source: "letterboxd_resolved"`.

---

## 7. Docker Compose Stack Not Running or DB Connection Errors

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

## 8. Database Migrations Not Applied

### Symptom
- API requests fail with `relation "tracker_..." does not exist` database errors.

### Cause
New database migrations have not been executed after starting or updating the stack.

### Resolution
Run migrations inside the backend container:
```bash
docker compose exec backend python manage.py migrate
```
