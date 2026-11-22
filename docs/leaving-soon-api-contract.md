# #175 Leaving Soon API contract — final local draft

Status: contract documented for ChatGPT Web review; **not formally accepted** until ChatGPT Web accepts it. Source: PR #180 head after QA fix, SHA `dcb61d24a9b590c1f30feee6d2bff90c6b63fd39`; `backend/tracker/views_leaving_soon.py` and `backend/tracker/tests/test_leaving_soon.py`. Local QA report: `docs/leaving-soon-task2-qa.md`.

## Request

`GET /api/leaving-soon/?country=NL&days=30`

- `country`: required, stripped, uppercased, ASCII ISO-3166-1 alpha-2, exactly one occurrence. Duplicate/invalid/missing -> 400. No default market.
- `days` is optional and defaults to 30. A supplied value must match `[1-9][0-9]?` and be at most 90. Invalid values and repeated `days` parameters return 400.
- Owner: authenticated principal's `pk`; otherwise `settings.WTDW_LEGACY_OWNER_ID` or, if unset, the `WatchlistItem.user_id` field default (currently `1`). No literal owner ID in the view; caller-supplied IDs ignored. `owner_id <= 0` -> 403. Local QA classifies the "no configured legacy owner -> 403" expectation as a test-expectation error against the documented single-owner compatibility contract.

## Response 200

```json
{
  "country": "NL",
  "horizon": {"days": 30, "as_of": "2026-11-22T10:00:00+00:00", "until": "2026-12-22T10:00:00+00:00"},
  "items": [{
    "movie": {"tmdb_id": 12345, "content_type": "series", "title": "Example", "year": 2020, "poster_path": "/p.jpg"},
    "leaving_offers": [{
      "provider": "provider_slug", "provider_display": "Provider", "country": "NL",
      "offer_type": "subscription", "access_type": "subscription", "locality": "local",
      "season_number": 2, "episode_number": null,
      "available_until": "2026-11-29T23:59:59+00:00",
      "last_verified_at": "2026-11-21T08:12:00+00:00",
      "sources": ["source_a"], "evidence": "verified",
      "departure_kind": "leaving_provider"
    }]
  }],
  "coverage": {
    "status": "partial_confirmed_departures", "watchlist_titles": 12,
    "titles_with_confirmed_expiry_data": 4, "offers_with_confirmed_expiry_data": 6,
    "scope": "selected_country_watchlist_only",
    "completeness": "partial_not_all_providers_report_expiry"
  }
}
```

- `movie.content_type` ∈ `movie|series|season|episode`; native scope preserved in `season_number`/`episode_number` (`null` = unnumbered).
- `offer_type` ∈ `subscription|free|free_with_ads|rent|buy`; `access_type` ∈ `subscription|addon|free|free_with_ads|rent|buy|unknown`. Only `is_accessible` and qualifying-offer-type offers are considered; AVOD/free and add-on-with-base count, rent/buy do not.
- `departure_kind` ∈ `leaving_provider|last_option`. `last_option` means no other accessible offer in the same country has a later-or-unknown expiry with overlapping scope; unnumbered parent offers may overlap (agreed conservative policy). No field asserts global unavailability.
- `coverage.status` ∈ `no_confirmed_expiry_data` (includes empty watchlist) | `no_departures_within_horizon` | `partial_confirmed_departures`.
- All timestamps are UTC ISO with `+00:00`. `available_until` is inclusive at `horizon.until`; `expiry <= now` is excluded. Horizon is absolute UTC 24h steps; UI must bucket locally from `available_until`.
- `last_verified_at` is the maximum `last_seen` of supporting observations (observation time, not declaration time). `sources` are deduplicated, sorted source slugs.

## Representative fixtures

1. **No data / empty watchlist:** `items: []`, `coverage.status: "no_confirmed_expiry_data"`, `watchlist_titles: 0`.
2. **Evidence exists, nothing soon:** `items: []`, `status: "no_departures_within_horizon"`, counts > 0.
3. **One provider leaving:** one `leaving_offers` row, `departure_kind: "last_option"` when it is the only accessible option.
4. **Multiple providers:** several rows; an accessible undated/later alternative makes the expiring row `leaving_provider`.
5. **Last option:** a single qualifying offer with verified future expiry -> `last_option`.

## Errors

| Status | Body |
|---|---|
| 400 | `{"error":"country must be an explicit ISO 3166-1 alpha-2 code"}` |
| 400 | `{"error":"days must be an integer between 1 and 90"}` |
| 403 | `{"error":"no valid watchlist owner"}` |
| 405 | DRF default method-not-allowed |

## Evidence and entitlement rules

Future, non-conflicted, `verified` `available_until`; matching verified, non-mapping-doubt, date-specific source observations; `justwatch*` sources, PBS series/season derived windows and Tubi inherited season windows never supply a confirmed date. The QA fix additionally rejects a date whose supporting source has been withdrawn or purged from the source ledger while another source keeps the canonical row present. Country equality is exact; reach/locality comes from the owner's saved preference.

## #176 frontend compatibility checklist

Full response typing; disjoint local-time 0–7 / 8–14 / 15–30 buckets sorted by timestamp; explicit provider/country and `leaving_provider` vs `last_option` wording in EN/IT/JA; seven states (loading, departures, no expiry data, none in horizon, empty watchlist, API error, offline/stale); reuse country/entitlement context with cancellable requests and stale-response suppression; refresh on watchlist mutations; preserve filters/pagination/#160 back-forward state; no writes, scraping, notifications or scheduler.

## Known risks / unresolved

- Conflicting dates overwritten by upsert cannot be distinguished from an undated-then-dated history; persistence follow-up requiring ingestion/schema change, out of scope this wave.
- `last_verified_at` semantics must not be described as "expiry confirmed at".
- Bounded query counts measured to 48 titles / 288 offers (10 -> 11 SELECTs with the fix, constant from 1 title onward); no database-plan profiling at larger scale.
- API contract acceptance belongs to ChatGPT Web; #176 implementation must wait for that acceptance.
