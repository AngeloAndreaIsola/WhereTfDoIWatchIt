# Canonical identity semantics (#112)

## Identity model

A canonical title is `(tmdb_id, content_type)` — movie and TV TMDb IDs live in
separate namespaces and may collide numerically. Endpoints that accept a bare
`tmdb_id` must disambiguate with `content_type` when both types exist (HTTP 409
otherwise); guessing is forbidden.

## Title fields

| Field | Meaning | Written by |
|---|---|---|
| `Movie.title` | Display title shown to the user. | Creation, manual edit, relink, guarded repair. Verified candidate acceptance can also fill an empty title. |
| `Movie.canonical_title` | Latest trusted TMDb title evidence for review. | Enrichment from TMDb details, verified candidate acceptance, relink, guarded repair. |
| `Movie.title_is_override` | `True` records an explicit display choice. `False` also covers older titles with unknown provenance. | Manual title edit (`PATCH /api/movie/<tmdb_id>/`), explicit relink. |

1. Enrichment and re-resolution preserve every existing display title and the
   override flag. They refresh canonical evidence and other TMDb metadata, so
   opening PK 830 (TMDb 164) can record “Breakfast at Tiffany's” as evidence
   while its display title remains “Natural Born Killers” pending repair.
2. A manual title edit sets the override flag and changes only display fields.
   An explicit relink without an override selects the linked TMDb title and
   clears the flag.
3. Historical corrections use the guarded repair command after review and
   approval. The command refuses a confirmed override.

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

`MovieIdentityAudit` records `{field: {old, new}}`, provenance, timestamp,
and the original local `movie_pk` for manual edits, relinks, and guarded
repairs. Deleting a movie clears the live foreign key and preserves the audit
entry and its original `movie_pk`. Verified candidate acceptance records
`canonical_title` evidence and keeps its own `CandidateReviewAudit` trail.

## Historical data

Historical display titles remain in place during normal enrichment.
Remediation is a gated, dry-run-first operation with per-record approval
(see the issue #112/#113 remediation plans). Migration `0059` adds the title
fields and audit table. Migration `0060` copies the local PK from each
existing audit's movie relationship and makes that relationship nullable.
Both migrations leave existing display titles and override flags intact.
