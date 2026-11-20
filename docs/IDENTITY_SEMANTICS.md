# Canonical identity semantics (#112)

## Identity model

A canonical title is `(tmdb_id, content_type)` — movie and TV TMDb IDs live in
separate namespaces and may collide numerically. Endpoints that accept a bare
`tmdb_id` must disambiguate with `content_type` when both types exist (HTTP 409
otherwise); guessing is forbidden.

## Title fields

| Field | Meaning | Written by |
|---|---|---|
| `Movie.title` | Display title shown to the user. | Creation, enrichment (when not overridden), manual edit, relink. |
| `Movie.canonical_title` | Last **trusted** TMDb title evidence. Never shown instead of `title`; used to detect drift. | Enrichment/sync from TMDb details, verified candidate acceptance, relink. |
| `Movie.title_is_override` | `True` when `title` is a deliberate user display choice (e.g. a localized alias). | Manual title edit (`PATCH /api/movie/<tmdb_id>/`), relink with explicit `title`. |

Rules:

1. **Enrichment heals unflagged drift.** `sync_movie_fields` updates
   `canonical_title` from trusted TMDb details and, when
   `title_is_override=False`, corrects `title` to match. A historically wrong
   title (issue #112's PK 830 pattern: "Natural Born Killers" on TMDb 164,
   whose real title is "Breakfast at Tiffany's") is therefore corrected on the
   next enrichment instead of persisting forever.
2. **Legitimate overrides survive.** A flagged override is never silently
   overwritten by enrichment or re-resolution. It is cleared only by an
   explicit relink without a title override, or by verified candidate
   acceptance filling an empty title.
3. **Manual edits are labeled, not canonical.** `update_movie` stores a manual
   title as an override; it does not change `tmdb_id`, `year`, or
   `canonical_title`, so display preference can never masquerade as identity.

## Conflict validation

- `link_tmdb` refuses (409) when another row already owns the target
  `(tmdb_id, content_type)` identity — a relink can never clobber or duplicate
  an existing canonical record (protects e.g. the genuine Natural Born Killers
  row, TMDb 241).
- `link_tmdb` refuses (409) a content-type flip on a row carrying availability
  evidence (legacy `Availability` cache or `availability.Offer` rows for the
  current identity) unless `confirm_type_change=true` is supplied.
- Automatic import/reconciliation paths keep their existing guards: typed
  search (#107), exact-title evidence with conflict deferral (#108), identity
  trust policy for sensitive providers (PRs #84/#85), and the conservative
  fill-only-empty enrichment in `safe_enrich_or_create_movie`.

## Audit trail

`MovieIdentityAudit` is append-only: every manual edit (`operation="update"`,
`provenance="manual_api"`) and relink (`operation="link"`,
`provenance="manual_link"`) records `{field: {old, new}}` for changed identity
and display fields, with a timestamp. No application code updates or deletes
audit rows. Verified candidate acceptance keeps its own `CandidateReviewAudit`
trail and additionally records `canonical_title` evidence.

## Historical data

Existing corrupted/synthetic rows are **not** rewritten by these guards.
Remediation is a separate, gated, dry-run-first operation with per-record
approval (see the issue #112/#113 remediation plans). Migration `0059` is
additive only: two defaulted columns plus the audit table; no data backfill.
