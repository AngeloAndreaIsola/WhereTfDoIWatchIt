# PR #182 review evidence

All screenshots below were captured from a real Google Chrome window (CDP-attached, not headless Hermes Chromium) running the local Next.js dev server at `localhost:3010`. They contain **synthetic fixture data only** — no real account, cookie, token or provider data.

Fixture injection replaced only `/api/leaving-soon/` (and stubbed unrelated `/api/` calls with empty JSON). Browser timezone resolved to `Europe/Amsterdam`; timestamps are rendered with that timezone label. No console errors were recorded in any capture (`__consoleErrors` was empty), and `document.documentElement.scrollWidth === clientWidth` in every capture (no horizontal overflow).

| File | Viewport | Locale | State |
|---|---|---|---|
| `screenshot-desktop-departures.png` | 1440×900 | en | Three confirmed departures across 0–7 and 15–30 day groups, `last_option` and `leaving_provider`, date+time+timezone |
| `screenshot-mobile-departures.png` | 375×812 | en | Confirmed departures at mobile width; no horizontal overflow |
| `screenshot-desktop-no-evidence.png` | 1440×900 | en | `No confirmed departure data` (not “Watchlist is empty”) |
| `screenshot-desktop-error.png` | 1440×900 | en | `Could not load departure dates: upstream unavailable` with Retry |
| `screenshot-italian-departures.png` | 390×844 | it | `In scadenza`, `Prossimi 7 giorni`, `Scade il`, `Ultima opzione disponibile in NL` |
| `screenshot-japanese-departures.png` | 390×844 | ja | `配信終了予定`, `今後7日間`, `NLで視聴可能な最後の配信` |
| `screenshot-season-tv-link.png` | 390×844 | en | Native season departure (`content_type: season`, season 2) rendered with `Season 2` and a link to `/tv/777/?season=2`, captured with ego-browser |

Second-review browser check (ego-browser, fresh fixture): the native season card displayed `Season 2` and linked to `/tv/777/?season=2`; document `scrollWidth === clientWidth === 390`, console errors empty.

The departure fixture uses `as_of = 2026-11-22T10:00:00+00:00` and expiries at +3, +7 and +18 days. A separate DST-boundary fixture is asserted in `frontend/__tests__/leaving-soon.test.tsx`.

Evidence scripts and raw logs remain in the Hermes wave ledger on the owner's machine; the committed images above are the GitHub-accessible copies.
