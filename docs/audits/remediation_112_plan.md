# Historical remediation plan — #112 canonical identity corruption

**Status: PLAN ONLY — no live record has been modified. Every write below
requires separate explicit authorization, a fresh backup + verified restore,
and an independent TMDb identity check at apply time.**

Serving environment (read-only probes 2026-11-19/20): Compose
`wheretfdoiwatchit`, PostgreSQL `tracker` in `wheretfdoiwatchit-db-1`
(volume `wheretfdoiwatchit_pgdata`). Post-P0-migration state: tracker 0058 /
availability 0016, backend healthy, 828 movies, identity digest
`9d72258e0e333853bc914dbfb0774b6c`.

## Confirmed affected record

### pk 830 — `tmdb_id=164`, `content_type=movie`
- **Before (row-level evidence):** title `Natural Born Killers`, year `1961`,
  overview begins "Holly Golightly is an eccentric New York City playgirl…"
  (Breakfast at Tiffany's). TMDb 164 is Breakfast at Tiffany's (1961).
- **Expected corrected identity:** title `Breakfast at Tiffany's`, year 1961
  (unchanged), same `(164, movie)` identity. No re-key.
- **Referencing records (read-only inventory):**
  - `tracker_refreshrequest`: 14 rows (historical; some predate the provider
    observations and carry the wrong title in request-keyed evidence — they
    are **not** independent identity proof; preserve untouched).
  - `availability_providercontent`: 24701 and 24702, created after refresh
    history already carried the incorrect title. Preserve both observations.
  - `availability_contentmapping`: 4602 (provider 7), 4603 (provider 5),
    both `native=164`, `match_method=external_id`, confidence 0.95 —
    request-keyed Streaming Availability mappings; per Stage 2Q policy these
    are not independent provider confirmation. **Preserve; do not re-key.**
  - `availability_offer`: 7 rows keyed `tmdb_id=164` (provider 7).
    Preserve the evidence and existing keys. Offer entitlement still
    requires an independent provenance review.
  - watchlist / user_data / watch_events / legacy availability: **0 rows**.
- **Required update:** `title` → verified TMDb title; `canonical_title` →
  same; audit row `operation="repair"`, `provenance="guarded_repair"`.
- **Tool:** `python manage.py repair_canonical_title --movie-id 830`
  (dry-run by default; `--apply --confirm-target tracker@db` performs the
  live TMDb check + guarded write on the confirmed database alias; refuses
  override-flagged rows and changes to the reviewed row during verification;
  idempotent).
- **Rollback:** the audit row records old/new; reverse with a reviewed
  restore of the pre-repair backup, or a guarded inverse edit (also audited).
- **Preserved separate record:** pk 187 (`tmdb_id=241`, genuine Natural Born
  Killers, 1994) — must remain untouched; the relink conflict guard already
  prevents clobbering it.

## Additional records reviewed (same-pattern scan)

Duplicate-title scan found 5 groups; classification:

| Row(s) | Identity | Classification | Action |
|---|---|---|---|
| 187 / 830 | NBK tmdb 241 / tmdb 164 | 187 genuine; 830 corrupted | repair 830 (above) |
| 824–828 | Neg ×5 (875001–875004, 876001) | synthetic — see #113 plan | quarantine track |
| 5 / 223 | Ready or Not tmdb 1290264 (2025) / tmdb 567609 (2019) | id 5 metadata internally consistent (2025 coming-of-age overview); **likely a legitimate same-title work** | verify against TMDb at review time; no action until verified |
| 513 / 514 | Reservoir Dogs tmdb 500 (1992) / tmdb 443129 (1991) | 513 genuine; **514 suspicious** (empty overview, year off by one) — **carries `user_data.is_watched=true`** | independent TMDb check of 443129 required; if false identity, plan a user-state-preserving correction (never delete the watched record); separate approval |
| 725 / 817 | The Gentlemen tmdb 522627 (movie, 2020) / tmdb 236235 (series, 2024) | legitimate cross-type same-title pair | none |

No cross-type duplicate `(tmdb_id, content_type)` identities exist (verified).

## Execution gate (per record)

1. Fresh `pg_dump -Fc` + SHA-256; restore rehearsal into a disposable clone.
2. Review dry-run output (row-level before evidence + reference counts) and
   capture exact refresh request and offer PKs for comparison after repair.
   Review offer and mapping provenance independently of the copied request
   title and request-keyed TMDb ID.
3. Independent TMDb identity check (performed by the command at apply time).
4. Explicit per-record operator authorization.
5. Apply via the guarded command; verify after-evidence + audit row.
6. Offer/mapping/refresh-history row counts unchanged (integrity check).

Root-cause attribution for pk 830 remains **unproven** (logs insufficient);
the preventive guards (PR #135) close all three candidate write paths
regardless of which one produced the corruption.
