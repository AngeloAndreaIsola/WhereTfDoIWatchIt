# Stage 2S read-only readiness report

Assessment date: 2026-11-14 (Europe/Amsterdam)  
Candidate baseline: `e1480035d2237c79951f64d1f6e89a4f21dfa61d`  
Candidate branch: `codex/stage2s-reduced-justwatch`

## Scope inspected

The candidate pins exactly two movie-country scopes and a maximum of six low-level HTTP attempts:

| Scope | TMDb ID | Country | Source / operation | Scope ceiling |
|---|---:|---|---|---:|
| Inception (2010) | `27205` | NL | JustWatch / `GetSearchTitles` | 3 |
| Dune (2021) | `438631` | NL | JustWatch / `GetSearchTitles` | 3 |

The global and JustWatch ceilings are six. Mediaset Infinity, Watchmode, Streaming Availability, independent provider-page evidence, `legacy_scraper`, and every other source/operation have a zero ceiling or are rejected. The preflight ran the candidate's compiled-in manifest without an input manifest or quota evidence. It performed read-only database queries; it did not call providers, start workers, issue authorization, or change database state.

## Target database checks

The target was the Docker PostgreSQL service `wheretfdoiwatchit-db-1`, using the `wheretfdoiwatchit_pgdata` application volume and the Docker `desktop-linux` context. The Django preflight connected to database `tracker` as the configured application role. PostgreSQL reported cluster system identifier `7689819453405220898`; the application database identity hash was `c9139f575fcaf416780341d45d7083971b297970f50f2d2f90f4b26b004afbbd`.

Read-only checks found:

- Required migrations are applied: `availability.0015` and `tracker.0047`.
- All durable experiment ledger tables are empty: 0 experiment records, 0 reservations, 0 outcomes, and 0 scope-completion records.
- Pending refresh requests: 0; unfinished refresh runs: 0.
- Pending requests overlapping either exact scope: 0.
- Shared title-drain and JustWatch source advisory locks: not held.
- Both exact Movie rows and NL are present; JustWatch is registered and enabled for both scopes. Its configured refresh interval is 43,200 seconds.
- Existing canonical offer counts are 5 for Inception NL and 8 for Dune NL. Selected JustWatch offer freshness is `stale` for both.
- PostgreSQL preflight cannot observe process presence outside the database.

The exact sanitized preflight JSON is in [read_only_preflight.json](read_only_preflight.json).

## Readiness result

`readiness` is **blocked**. The only blockers are the absence of current JustWatch quota and rate-policy evidence adequate for six requests, separately reported for each scope. No quota evidence was supplied or inferred. Lack of a billed API quota is not treated as unlimited capacity. The candidate requires fresh evidence (within one hour) supporting a burst of at least six, plus a current quota classification; available-quota evidence must also show at least six requests remaining.

This is `EXPERIMENT_READINESS_ASSESSED`, not `EXPERIMENT_READY`. Even after quota evidence is obtained, a later phase must recheck operational conditions and obtain separate explicit authorization before execution. No request or experiment was executed in this assessment.

## Validation performed

- Focused Stage 2G, JustWatch adapter, identity/reconciliation, Mediaset, and YouTube regression labels: 211 tests passed on an isolated disposable PostgreSQL instance.
- Full PostgreSQL-backed backend suite: 3,114 tests passed on the isolated disposable instance; migrations through `availability.0015` and `tracker.0047` were applied there.
- Frontend CI commands: 246 tests passed, lint completed with existing warnings, TypeScript check passed, and production build passed. No frontend contracts changed.
- Network guard in disposable test containers recorded zero outbound attempts.
- `makemigrations --check --dry-run`: no changes detected.

These test results do not constitute quota evidence or execution authorization.
