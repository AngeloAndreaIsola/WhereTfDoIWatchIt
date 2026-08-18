# Letterboxd Watchlist Import Guide

This document explains how to export your watchlist from Letterboxd, import it into WhereTfDoIWatchIt, and test the CSV import process manually using provided sample fixtures.

---

## 1. Exporting Your Watchlist from Letterboxd

To export your watchlist from Letterboxd:

1. Log in to your account at [Letterboxd](https://letterboxd.com).
2. Navigate to your profile page and click on **Watchlist**.
3. Scroll to the bottom of your watchlist page or go to your account settings (**Settings** > **Import & Export**).
4. Click **Export Watchlist** (or **Export Your Data**). Letterboxd will generate a `.csv` file containing your saved titles.

---

## 2. Expected CSV Format & Headers

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

## 3. How TMDb Matching Works

1. **No External IDs in Letterboxd Exports**: Letterboxd CSV exports do not include TMDb IDs or IMDb IDs.
2. **TMDb Search API**: When a CSV is uploaded, the backend queries TMDb (`/search/movie`) using the title and year provided in each row.
   > **Note**: TMDb search requires `TMDB_API_KEY` to be configured in your environment settings (`.env`).
3. **Match Categorization**:
   - **Resolved**: Entries with a single, unambiguous TMDb match (or an exact year match among candidates) are immediately created in the system database.
   - **Ambiguous**: Entries that produce multiple potential candidates or year mismatches are flagged and returned separately for manual confirmation.
   - **Skipped**: Entries that return no TMDb matches (e.g. invalid titles or typos) are flagged as skipped (`reason: no_match`).

---

## 4. Manual Confirmation of Ambiguous Matches

- Upon uploading a CSV, the initial import API response returns lists of `resolved`, `ambiguous`, and `skipped` items.
- Ambiguous items include candidate matches (up to 5) with their TMDb ID, title, and release year.
- In the frontend application, ambiguous entries present a selection interface allowing the user to select the correct TMDb entry.
- When confirmed, selections are sent to the `/api/watchlist/resolve/` endpoint to finalize adding the chosen movie to the watchlist.

### Watchlist Source Naming

Watchlist records maintain a `source` field indicating how they entered the system:
- Automatically matched rows from CSV import use `source: "letterboxd"`.
- Manually confirmed ambiguous rows resolved via the endpoint use `source: "letterboxd_resolved"`.

---

## 5. Key Operational Behaviors

- **Additive & Idempotent**: CSV import is idempotent. Re-importing the same CSV or adding an existing title will not create duplicate `WatchlistItem` or `Movie` database entries.
- **Streaming Availability API Quota**: CSV importing only queries TMDb for title matching and database creation. It **does not** fetch streaming availability. Streaming availability API quota (capped at 100 requests/day on the free tier) is managed strictly on-demand when inspecting individual movie availability or via background refresh jobs, keeping watchlist import quota-neutral.
