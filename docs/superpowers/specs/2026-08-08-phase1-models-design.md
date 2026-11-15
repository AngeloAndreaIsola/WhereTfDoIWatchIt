# Phase 1 — Data Model & Admin Design

Date: 2026-08-08
Status: Approved

## Purpose

Add the Phase 1 data model and Django admin configuration to the Phase 0 scaffold (see `docs/SPEC.md` §Data Model, `docs/PLAN.md` §Phase 1). Deliver the six core entities plus the `LanguageInfo` admin data-entry screen, and seed an editable, empty `Preference` row.

## Models

All models live in the `tracker` app (`backend/tracker/models.py`).

| model | fields | key constraint |
|---|---|---|
| `Movie` | tmdb_id:int, title, year | tmdb_id unique + indexed |
| `Region` | provider, country(ISO 2) | unique(provider, country) |
| `Availability` | movie FK, region FK, last_checked_at, is_available, offer_type(choice: subscription/free/rent/buy), quality(nullable) | unique(movie, region) |
| `LanguageInfo` | movie FK, region FK | unique(movie, region) |
| `LanguageEntry` | language_info FK, kind(choice: audio/subtitle), language_code(ISO 2) | unique(language_info, kind, language_code) |
| `Subscription` | user_id:int default 1, name | name unique |
| `Preference` | user_id:int default 1, audio_languages[], subtitle_languages[], candidate_countries[] | single row per user |
| `WatchlistItem` | user_id:int default 1, movie FK, source default "letterboxd", added_at | unique(movie, source) |

Decisions:
- **Language storage normalized** (Q1): `LanguageEntry` rows rather than arrays on `LanguageInfo`. Enables SQL ordering by the user's language priority (the core ranking feature) and clean admin inlines for data entry.
- **Region is a standalone entity** per SPEC: provider+country pair, shared FK from `Availability` and `LanguageInfo`. Streaming catalogs are per-region, so availability attaches to the pair.
- **user_id seam**: plain int, default 1, on user-owned tables (`Subscription`, `Preference`, `WatchlistItem`). No auth in v1; column exists so real accounts can be added later without a rewrite.
- `ArrayField` (Postgres) for the ordered priority lists; `django.contrib.postgres` added to `INSTALLED_APPS`.

## Admin (`backend/tracker/admin.py`)

- `LanguageInfoAdmin` with `LanguageEntry` inline — the Phase 1 manual data-entry screen: pick movie + region, add audio/subtitle rows.
- `Subscription` and `Preference` get full CRUD — the user manages their services and language/country priorities here. ArrayField fields carry help text: comma-separated ISO 639-1 codes, first = highest priority.
- Remaining models (`Movie`, `Region`, `Availability`, `WatchlistItem`) registered simply with list_display / list_filter / search.

## Seeding (data migration `0002`)

- Creates one empty `Preference` row (user_id=1, all lists empty) so the single-row invariant holds and there is an edit target in admin.
- **No `Subscription` rows seeded** — nothing personal or hardcoded; the user adds/removes their own services via admin.

## Out of scope

- No API endpoints (Phase 2), no ranking logic, no availability fetching (Phase 2+).
- No language validation beyond ISO-length storage; enrichment/validation of TMDb data comes later.

## Definition of done

- `migrate` runs clean; schema includes all eight tables.
- Admin shows `LanguageInfo` with inline `LanguageEntry` rows and CRUD for `Subscription` + `Preference`.
- Exactly one empty `Preference` row exists for user 1; zero `Subscription` rows.
