# Operations & Troubleshooting

Short runbook for the importers and the most common failure modes.

---

## Stack

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py test tracker -v 2
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run lint
```

Frontend: http://localhost:3001 — backend health: http://localhost:8000/api/health/ — admin: http://localhost:8000/admin/

Useful logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f backend
```

---

## Container logs: RaiPlay catalogue checkpoint events

RaiPlay walks a large catalogue in bounded batches, so a refresh may stop after
one batch and resume on the next run. Two structured events record that
behaviour, and both are visible in ordinary container logs — no verbose mode or
custom handler is needed:

| Event | Level | Meaning |
| --- | --- | --- |
| `rai_play_walk_resume after=<url> remaining=<n>` | INFO | The stored checkpoint was found in the current sitemap; the walk continues immediately after it, with `n` entries left. |
| `rai_play_cursor_checkpoint_missing after=<url> reason=restarting` | WARNING | The stored checkpoint is no longer in the sitemap (catalogue changed or the programme disappeared). The walk restarts from the top: already-persisted work repeats, but nothing is skipped. |

Inspect them from either container that can run a catalogue refresh
(`catalogue-worker` is the usual one; `backend` shares the same configuration):

```bash
# Follow resume decisions as they happen
docker compose logs -f catalogue-worker | grep -E 'rai_play_(walk_resume|cursor_checkpoint_missing)'

# Both lanes, last 24 hours, including the level/logger prefix
docker compose logs --since 24h backend catalogue-worker \
  | grep -E 'rai_play_(walk_resume|cursor_checkpoint_missing)'

# Context around one resume: the started/truncated/completed events
docker compose logs --since 24h catalogue-worker | grep rai_play_
```

Each line is formatted as `asctime levelname logger message`, for example:

```
2026-10-25 18:31:53,194 INFO availability.sources.rai_play rai_play_walk_resume after=https://www.raiplay.it/programmi/foo remaining=127
```

Configuration lives in `LOGGING` (`backend/config/settings.py`): only
`availability.sources.rai_play` is raised to INFO; the root logger and every
other component stay at WARNING. The RaiPlay logger does not propagate, so each
event appears exactly once. These events only occur on a *resumed* walk — a
first run from an empty checkpoint logs `rai_play_catalogue_started` and
`rai_play_catalogue_completed` instead.

---

## Letterboxd URL import troubleshooting

Endpoint: `POST /api/importers/letterboxd-url/` (frontend: **Watchlist → Import from a Letterboxd URL**).

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Not a Letterboxd URL (expected letterboxd.com)` | Wrong host, e.g. `https://example.com/foo` | Paste a `https://letterboxd.com/<username>/…` URL |
| `'film' is a Letterboxd site page, not a profile` | A film page was pasted (`/film/inception/`) | Paste the profile, `/watchlist/` or `/films/` URL instead |
| `type is required for a bare profile URL` | `https://letterboxd.com/<username>/` alone does not say which list you want | Pass `"type": "watchlist"` or `"watched"`, or use the Import Watchlist / Import Watched buttons |
| `Letterboxd profile or list not found` (HTTP 404) | Typo in the username, or the list does not exist | Check the username on letterboxd.com |
| `Letterboxd blocked the request (bot check or private profile)` (HTTP 502) | Letterboxd/Cloudflare interstitial (`Just a moment…`, HTTP 403), or the profile is private | Wait a minute and retry; make sure the profile is public. No cookies or login can be supplied — this importer is public-pages only |
| `Letterboxd rate limited the request` (HTTP 429) | Too many requests in a short window | Slow down / retry later |
| `Letterboxd request timed out` / `Could not connect to Letterboxd` | Network problem or Letterboxd outage | Retry; check outbound HTTPS from the host |
| `No entries found at this URL` in the UI | Empty list, private profile, or markup change | Verify the list has films and is public; if the profile is fine, Letterboxd likely changed its HTML |
| Import stops earlier than expected | Hard cap `MAX_PROFILE_PAGES = 200` (backend/tracker/services/letterboxd.py) or the first page with zero posters | Raise the cap only if a real profile legitimately exceeds it |

Notes:

- Imports are **one-shot copies, not a live sync**, and re-importing is idempotent.
- Letterboxd exposes no official public API for this; the importer parses public
  HTML, so markup changes are fixed in `backend/tracker/services/letterboxd.py`.
- Requests are sent as browser-like navigations (Chrome UA + Client Hints +
  `Sec-Fetch-*` headers, see `_get_session()`). Without those headers Cloudflare
  challenges paginated `/films/page/<n>/` with HTTP 403 — if Letterboxd changes
  its bot rules, that is the first place to look.
- `watched` imports set `is_watched` only — they never create `WatchlistItem`
  rows. `watchlist` imports create `WatchlistItem` rows with
  `source: "letterboxd_url"`.
- Ambiguous TMDb matches are reviewed at `/watchlist/ambiguous` (shared with the
  CSV importer).

### Ambiguity review hand-off

The review link at `/watchlist/ambiguous` carries its payload through
sessionStorage (`frontend/lib/ambiguousReview.ts`), not the query string: each
ambiguous item includes its TMDb candidates (~2 kB), so the legacy
`?items=<json>` URL exceeded the server's 16 kB header limit once an import
produced more than a handful of ambiguous rows (**HTTP 431 Request Header Fields
Too Large**). The `?items=` parameter is still parsed first, so old links keep
working; if sessionStorage is unavailable the link falls back to it. Items are
session-scoped — a new review click replaces the previous queue.

---

## Letterboxd CSV import troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `file field required` (HTTP 400) | Upload request without a file | Re-select the export file and submit |
| Rows reported as `skipped` (`reason: no_match`) | Title not found on TMDb (typo, obscure title) | Fix the title, or search manually from the movie page |
| Rows reported as `ambiguous` | Multiple TMDb candidates or a year mismatch | Use **Review ambiguous matches** and pick the right candidate |
| Nothing happens on upload | `TMDB_API_KEY` missing/invalid in `.env` | Set the key and restart the backend |

---

## Google Interests import

Endpoint: `POST /api/importers/google-interests/`. Two different Google URL
shapes are supported, and they differ in auth requirements:

| Kind | Example URL | Cookies |
| --- | --- | --- |
| Google Interests / Google TV saved list (older, behind sign-in) | `https://www.google.com/interests/saved/list/<LIST_ID>?q=<query>` | **Required** — pass a `cookies` JSON object (e.g. `CONSENT`, `NID`, `SOCS`) taken from DevTools → Application → Cookies → `www.google.com` |
| Google Collections list (newer, public) | `https://www.google.com/collections/s/list/<LIST_ID>/<ITEM_ID>` | **Not required** — a plain GET with a browser-like User-Agent is enough |

Request shape:

```json
{
  "cookies": {"CONSENT": "...", "NID": "...", "SOCS": "..."},
  "urls": [
    "https://www.google.com/interests/saved/list/<LIST_ID>?q=<query>",
    "https://www.google.com/collections/s/list/<LIST_ID>/<ITEM_ID>"
  ]
}
```

- `urls` may contain several URLs (one per line in the UI), mixed across kinds.
- `cookies` may be omitted or `{}` when every URL is a public Collections page.
- Authenticated Interests pages without cookies fail with a fetch error entry in
  `skipped`; public Collections pages never need cookies.
- Failure of one URL does not abort the others — each failure is reported in the
  `skipped` list as `fetch_failed: …`.

### Cookies must come from `www.google.com`

Google auth cookies (`SID`, `HSID`, `SSID`, `APISID`, `SAPISID`,
`__Secure-1PSID`, …) are domain-scoped. Export them from
DevTools → Application → Cookies → **`www.google.com`** only (the list URLs
above are `www.google.com`). Merging cookies from other Google hosts
(`google.nl`, `google.it`, `accounts.google.com`, …) produces a jar Google
rejects: the request is bounced `accounts.google.com/ServiceLogin` → account
chooser → a support article (`support.google.com/accounts/answer/32050`), which
is no longer parsed as a list — it is reported as `fetch_failed: Google
answered with a sign-in or help page …`.

### Pagination, empty pages, and response time

- Long lists are followed across every page automatically: the importer reads
  Google's own counter (`1-200 of 256`) and requests `pageNumber=2`, `3`, … up
  to `MAX_LIST_PAGES = 10`. Pass the list URL without a `pageNumber`, or with
  `pageNumber=1` — both work.
- A page that parses to zero rows is never silent: it adds a
  `no_items_found: …` entry to `skipped` instead of returning `0/0/0`.
- The import is synchronous and does one TMDb lookup per title (movie + TV), so
  a 250-title list takes roughly 20–30 s. The Next.js proxy to Django defaults
  to 30 s, which would cut the connection off mid-import (`Error: socket hang
  up` in the frontend log); `experimental.proxyTimeout` in
  `frontend/next.config.mjs` raises it to 180 s for exactly this reason. Keep
  that setting if you add other bulk endpoints.
