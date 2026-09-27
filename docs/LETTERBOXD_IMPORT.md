# Letterboxd Import Guide

WhereTfDoIWatchIt's import/export page lives at **`/watchlist/import`**
(entry point: the compact **Import / Export** card on `/watchlist`).

### Import — supported sources

1. **Letterboxd URL import** — paste a public profile/list URL (no export, no login).
2. **Letterboxd CSV import** — upload a Letterboxd export file (`.csv` / `.zip`).
3. **Google Interests** — paste Google Interests / Google TV list URLs (see `docs/OPERATIONS.md`).
4. **WhereTfDoIWatchIt backup CSV** — re-upload a file previously downloaded from the
   Export section (see [§7](#7-export--backup-wheretfdoiwatchit-format)).

All CSV paths produce the same `LetterboxdRow` objects and go through the same
`import_rows()` pipeline, so TMDb matching, ambiguity handling and idempotency
are identical regardless of how the data arrived.

---

## 1. Import from a Public Letterboxd URL

### Steps

1. Open your Letterboxd profile.
2. Copy the public profile URL.
3. Paste it into WhereTfDoIWatchIt (**Watchlist → Import / Export → Import from a Letterboxd URL**).
4. Choose **Import Watchlist**, **Import Watched**, or **Import Both**.
5. The app fetches the public paginated pages.
6. Movies are matched against TMDb.
7. Ambiguous matches can be reviewed manually in the existing review flow.

### Supported URLs

| Pasted URL | Import type |
| --- | --- |
| `https://letterboxd.com/USERNAME/` | requires an explicit choice (`watchlist` or `watched`) |
| `https://letterboxd.com/USERNAME/watchlist/` | `watchlist` |
| `https://letterboxd.com/USERNAME/films/` | `watched` |
| `https://letterboxd.com/USERNAME/films/watched/` | `watched` |

Paginated variants (`/watchlist/page/2/`, `/films/page/3/`, …) are accepted too;
imports always restart from page 1 and walk forward.

### API

```http
POST /api/importers/letterboxd-url/
Content-Type: application/json

{
  "url": "https://letterboxd.com/USERNAME/",
  "type": "watchlist"
}
```

`type` is required when `url` is a bare profile URL — the app never guesses
whether you meant your watchlist or your watched films.

Response (same shape as the CSV importer plus URL metadata):

```json
{
  "source": "letterboxd_url",
  "username": "USERNAME",
  "import_type": "watchlist",
  "pages_fetched": 3,
  "rows_found": 57,
  "resolved": [],
  "ambiguous": [],
  "skipped": [],
  "watched_count": 0,
  "ratings_count": 0,
  "reviews_count": 0,
  "diary_events_count": 0
}
```

### How it works

- **Public pages only**: the importer reads Letterboxd's public paginated HTML
  (`/watchlist/`, `/films/`) with a browser-like User-Agent. **No Letterboxd
  login, password or cookies are required, and none are supported.**
- **The profile must be public.** Private profiles cannot be imported.
- **Pagination**: pages are fetched sequentially and the crawl stops at the
  first page with no poster entries, up to a hard cap of
  `MAX_PROFILE_PAGES = 200` pages (defined in
  `backend/tracker/services/letterboxd.py`) so a broken listing can never
  loop forever.
- **No per-film requests**: titles and years are read from the poster tiles
  themselves (`data-item-name`, e.g. `"Inception (2010)"`, with the poster
  image `alt` as fallback). The importer deliberately does **not** fetch every
  individual film page just to obtain a year; when no reliable year is present
  it stores `year = null` and lets the existing TMDb matching resolve it.
- **Watchlist vs watched semantics are unchanged**:
  - `watchlist` rows create `WatchlistItem` records;
  - `watched` rows set `UserMovieData.is_watched = True` and do **not** create
    a `WatchlistItem` merely because the film was watched.
- **Not a live sync**: this is a one-shot copy. Importing again later re-runs
  the crawl; it does not establish a subscription or background sync.
- **Idempotent**: re-importing the same URL never duplicates `Movie` or
  `WatchlistItem` records.
- **Ambiguous matches** are returned exactly as with CSV import
  (`title`, `year`, `uri`, `reason`, `candidates`) and are reviewed in the same
  **/watchlist/ambiguous** flow — there is no separate review page.
- **Watchlist sources**: rows imported from a URL are stored with
  `source: "letterboxd_url"` (CSV imports use `letterboxd`; manually confirmed
  ambiguous rows use `letterboxd_resolved`).

### Error responses

| Situation | HTTP | Message |
| --- | --- | --- |
| Missing/unsupported URL, missing `type` | 400 | describes the expected URL shape |
| Profile or list does not exist | 404 | `Letterboxd profile or list not found…` |
| Timeout, connection failure, bot challenge, rate limit, non-2xx | 502 | `Letterboxd request timed out…` / `Letterboxd blocked the request…` |

### Caveats

- Letterboxd does **not** offer an official public API for this feature. The
  importer reads public HTML pages instead, so **Letterboxd's HTML is a
  third-party dependency and may change**. If Letterboxd restructures its
  markup, the fix should be confined to
  `backend/tracker/services/letterboxd.py`
  (`parse_profile_page()` and its helpers).
- Letterboxd sits behind bot protection (Cloudflare). Occasional blocked
  requests (`HTTP 403`) are reported as clean application-level errors —
  retrying after a short wait usually works. See `docs/OPERATIONS.md`.

---

## 2. Exporting Your Watchlist from Letterboxd (CSV)

To export your watchlist from Letterboxd:

1. Log in to your account at [Letterboxd](https://letterboxd.com).
2. Navigate to your profile page and click on **Watchlist**.
3. Scroll to the bottom of your watchlist page or go to your account settings (**Settings** > **Import & Export**).
4. Click **Export Watchlist** (or **Export Your Data**). Letterboxd will generate a `.csv` file containing your saved titles.

Upload the resulting file on the **Import / Export** page (**Import from File**);
`.csv`, `.zip`, `.xml`, `.gz` and `.xml.gz` are supported.

---

## 3. Expected CSV Format & Headers

WhereTfDoIWatchIt expects a CSV file containing standard Letterboxd column headers (lookup is case-insensitive):

- `Title` (Required): The name of the movie.
- `Year` (Optional): The release year of the movie.
- `Letterboxd URI` (Optional): The URL pointing to the movie's Letterboxd page.

### Sample CSV Structure

```csv
Title,Year,Letterboxd URI
Inception,2010,https://letterboxd.com/film/inception/
Parasite,2019,https://letterboxd.com/film/parasite-2019/
Spirited Away,2001,https://letterboxd.com/film/spirited-away/
```

Sample CSV fixtures are available in the repository for manual testing:
- Normal sample: `docs/examples/letterboxd-watchlist-sample.csv`
- Ambiguous sample: `docs/examples/letterboxd-watchlist-ambiguous-sample.csv`

---

## 4. How TMDb Matching Works

1. **No External IDs in Letterboxd Exports**: Letterboxd CSV exports do not include TMDb IDs or IMDb IDs. Scraped URL rows carry only the Letterboxd film URI, for the same reason.
2. **TMDb Search API**: When rows are imported, the backend queries TMDb (`/search/movie`) using the title and year provided in each row.
   > **Note**: TMDb search requires `TMDB_API_KEY` to be configured in your environment settings (`.env`).
3. **Match Categorization**:
   - **Resolved**: Entries with a single, unambiguous TMDb match (or an exact year match among candidates) are immediately created in the system database.
   - **Ambiguous**: Entries that produce multiple potential candidates or year mismatches are flagged and returned separately for manual confirmation.
   - **Skipped**: Entries that return no TMDb matches (e.g. invalid titles or typos) are flagged as skipped (`reason: no_match`).

---

## 5. Manual Confirmation of Ambiguous Matches

- Upon importing (CSV or URL), the initial API response returns lists of `resolved`, `ambiguous`, and `skipped` items.
- Ambiguous items include candidate matches (up to 5) with their TMDb ID, title, and release year.
- In the frontend application, ambiguous entries present a selection interface allowing the user to select the correct TMDb entry.
- When confirmed, selections are sent to the `/api/watchlist/resolve/` endpoint to finalize adding the chosen movie to the watchlist.

### Watchlist Source Naming

Watchlist records maintain a `source` field indicating how they entered the system:
- Automatically matched rows from CSV import use `source: "letterboxd"`.
- Automatically matched rows from URL import use `source: "letterboxd_url"`.
- Manually confirmed ambiguous rows resolved via the endpoint use `source: "letterboxd_resolved"`.

---

## 6. Key Operational Behaviors

- **Additive & Idempotent**: Importing is idempotent. Re-importing the same CSV or the same profile URL (or adding an existing title) will not create duplicate `WatchlistItem` or `Movie` database entries.
- **Streaming Availability API Quota**: Importing only queries TMDb for title matching and database creation. It **does not** fetch streaming availability. Streaming availability API quota (capped at 100 requests/day on the free tier) is managed strictly on-demand when inspecting individual movie availability or via background refresh jobs, keeping imports quota-neutral.
- **Rate limits**: Letterboxd URL imports are sequential (one page at a time) and bounded by `MAX_PROFILE_PAGES`; TMDb is only queried for titles that are not already known locally.

---

## 7. Export & Backup (WhereTfDoIWatchIt format)

The **Export** section of `/watchlist/import` can download the current user's
collection as a CSV backup:

- **Export Watchlist** — every movie currently on the watchlist.
- **Export Watched** — every movie explicitly marked watched
  (`UserMovieData.is_watched = True`).
- **Export Both** — one file containing both.

### API

```http
GET /api/export/watchlist/   → wheretfdoiwatchit-watchlist.csv
GET /api/export/watched/     → wheretfdoiwatchit-watched.csv
GET /api/export/             → wheretfdoiwatchit-export.csv
```

Responses are `text/csv` with an attachment `Content-Disposition`. An empty
collection still returns a valid header-only CSV, so the file always remains a
valid round-trip backup. Exports are restricted to the current user's data.

### Canonical format

WhereTfDoIWatchIt uses a single canonical CSV format designed for
**Export → Download → Import → Restore** within this application:

```csv
title,year,letterboxd_uri,status
The Matrix,1999,https://letterboxd.com/film/the-matrix/,watchlist
Inception,2010,https://letterboxd.com/film/inception/,watched
```

- `status` is exactly `watchlist` or `watched`. Anything else is rejected with
  a useful import error (HTTP 400) — an unknown status is never silently
  coerced.
- `letterboxd_uri` is the stored Letterboxd URI (empty when none is known);
  `year` is empty when unknown. Neither is ever invented.
- The importer detects this format by its header (`title,year,letterboxd_uri,status`)
  regardless of filename, so `my-export.csv` is recognized correctly.
- Mixed files are supported: each row is applied according to its status
  (`watchlist` → `WatchlistItem`, `watched` → `UserMovieData.is_watched = True`;
  watched rows never create a `WatchlistItem`).
- Re-uploading the same file is idempotent — no duplicate `WatchlistItem` or
  user/movie records are created.

**This format is an application backup format.** It is not necessarily
compatible with Letterboxd's own CSV import requirements; use the Letterboxd
export format only for importing *from* Letterboxd.

Both formats funnel into the same `import_rows()` pipeline
(`watchlist_export.py` produces `LetterboxdRow` objects just like the
Letterboxd parser), so matching and idempotency behave identically.
