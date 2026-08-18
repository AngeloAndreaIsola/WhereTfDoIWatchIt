# Operations Guide

This guide covers operational management, database setup, background commands, and API quota management for WhereTfDoIWatchIt.

---

## 1. Daily API Quota & Cache Strategy

The application integrates with the **Streaming Availability API**, which operates under a free-tier limit of **100 requests per day**.

To protect this budget:
1. **Cache-First Architecture:** Availability data is stored locally in Postgres and considered fresh for 24 hours.
2. **Ad-Hoc Reserve:** On-demand movie availability lookups consume from a reserved pool of daily calls.
3. **Budgeted Batch Refresh:** Watchlist availability refreshes are processed in controlled batches using a daily quota budget (default: 90 calls/day).

---

## 2. Watchlist Bulk Refresh Operations

Batch refreshing watchlist availability ensures that candidate movies are kept up to date without exceeding API rate limits.

### Candidate Priority Order
When batch processing runs, candidate movies are evaluated in the following priority order:
1. Watchlist items with **no existing availability records** (never checked).
2. Watchlist items with the **stalest/oldest availability checks** (`last_checked_at`).
3. Fresh cached items (skipped without making external API calls).

---

### Management Command: `refresh_availability`

The `refresh_availability` Django management command triggers a batch refresh from the CLI or a daily cron job.

#### Usage
```bash
python manage.py refresh_availability [--budget BUDGET]
```

#### Options
* `--budget` (integer, default `90`): Maximum number of live API refresh calls allowed during this run.

#### Examples
```bash
# Run with default budget of 90 calls
python manage.py refresh_availability

# Run with custom budget of 30 calls
python manage.py refresh_availability --budget 30
```

#### Output
Prints a human-readable and structured JSON summary upon completion:
```json
{
  "attempted": 15,
  "refreshed": 5,
  "skipped_cached": 10,
  "skipped_budget_exhausted": 0,
  "skipped_rate_limited": 0,
  "errors": 0,
  "budget": 90,
  "remaining_budget": 85,
  "rate_limited": false
}
```

---

### API Endpoint: `POST /api/watchlist/refresh/`

Trigger an on-demand budgeted batch refresh via HTTP.

#### Request
* **URL:** `/api/watchlist/refresh/`
* **Method:** `POST`
* **Content-Type:** `application/json`

#### Payload
```json
{
  "budget": 10
}
```
*(If `budget` is omitted, defaults to `10`. Maximum allowed budget is `90`.)*

#### Responses
* `200 OK`: Processing completed successfully. Returns structured summary JSON.
* `400 Bad Request`: Invalid budget value (e.g. non-integer, <= 0, or > 90).
* `429 Too Many Requests`: Daily API rate limit/quota reached before or during processing. Returns structured summary JSON indicating `rate_limited: true`.

---

## 3. Running Backend Tests

Run backend tests via Docker Compose or host Python environment:

### Docker Compose
```bash
docker compose exec backend python manage.py test tracker -v 2
```

### Local Test Runner
```bash
python3 test_runner.py
```
