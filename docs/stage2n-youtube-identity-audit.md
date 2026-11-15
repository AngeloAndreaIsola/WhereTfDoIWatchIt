# Stage 2N: YouTube identity provenance audit

Audit date: 2026-11-14

## Scope and method

PR #84 (`codex/stage2m-mediaset`, `f50e3c6`) was open during this work. The
correction is based on an isolated worktree at that exact HEAD. The historical
review queried the existing local PostgreSQL service with `BEGIN READ ONLY` and
`ROLLBACK`; it did not update or delete records. No provider requests, worker
startup, refresh, or experiment ran for this audit.

The identity check compared each YouTube `ContentMapping` with its source,
provider-native `ProviderContent`, attached `Offer`, and any current
`OfferEvidence`. A conflicting year was counted only when provider-reported
year evidence existed and differed from the mapped tracker movie's year.

## Findings

| Measure | Result |
| --- | ---: |
| YouTube `ContentMapping` rows | 3 |
| Rows using `match_method=external_id` and `source=youtube` | 3 |
| Rows whose provider metadata `external_ids.tmdb` equals the mapping target | 3 |
| Attached canonical YouTube offers | 3 |
| Offer-to-mapping TMDb ID mismatches | 0 |
| Confirmed provider-year conflicts | 0 |
| Provider release years present | 0 |
| Present YouTube evidence rows | 1 (on one offer) |

All three provider titles match their mapped tracker titles. Each mapping target
is repeated in `ProviderContent.metadata.external_ids.tmdb`, but the YouTube
collector's historical implementation copied the request's TMDb ID into that
field. Because the rows have no provider release year or independent identity
evidence, title agreement does not confirm identity. These are **unverified
provenance**, not confirmed identity conflicts.

| Offer ID | Mapping ID | YouTube video ID | TMDb ID | Provider title | Mapped title/year | Present evidence |
| ---: | ---: | --- | ---: | --- | --- | --- |
| 10114 | 4194 | `yVUIRcWTq0g` | 4982 | American Gangster | American Gangster / 2007 | none |
| 10147 | 4227 | `grBcDfdb7SY` | 4553 | The Machinist | The Machinist / 2004 | none |
| 10827 | 4764 | `GwIxcEvRq0g` | 389 | 12 Angry Men | 12 Angry Men / 1957 | YouTube |

All three raw offer rows currently have `currently_available=true` and country
US. Under the Stage 2N read-side identity policy, the YouTube-sourced
`external_id` mappings are untrusted, so reconciliation will report them as
unverified mapping provenance and will not treat them as currently watchable.

## Read-only query

The audit used read-only PostgreSQL transactions. A repeatable core inventory
query is:

```sql
BEGIN READ ONLY;
SELECT cm.id AS mapping_id,
       cm.provider_content_id,
       cm.tmdb_id,
       cm.match_method,
       mapping_source.slug AS mapping_source,
       pc.title AS provider_title,
       pc.release_date,
       pc.metadata->'external_ids' AS provider_external_ids,
       pc.metadata->'identity_evidence' AS identity_evidence,
       movie.title AS mapped_title,
       movie.year AS mapped_year,
       offer.id AS offer_id,
       offer.currently_available,
       country.code AS offer_country
FROM availability_contentmapping cm
JOIN tracker_provider provider ON provider.id = cm.provider_id
JOIN availability_source mapping_source ON mapping_source.id = cm.source_id
LEFT JOIN availability_providercontent pc
  ON pc.provider_id = cm.provider_id
 AND pc.provider_content_id = cm.provider_content_id
LEFT JOIN tracker_movie movie ON movie.tmdb_id = cm.tmdb_id
LEFT JOIN availability_offer offer
  ON offer.provider_id = cm.provider_id
 AND offer.provider_content_id = cm.provider_content_id
LEFT JOIN availability_country country ON country.id = offer.country_id
WHERE provider.slug = 'youtube'
ORDER BY cm.id, offer.id;
ROLLBACK;
```

The zero-conflict check compares non-null `ProviderContent.release_date` years
against the mapped `tracker_movie.year`. No row had a stored release year, so
that check yielded zero confirmed conflicts; it does not prove the mappings are
correct.

## Read-only remediation proposal

No historical record was changed. The code correction blocks new YouTube title
searches from asserting the request's identity, and existing YouTube-sourced
external-ID mappings are no longer trusted by recovery, reconciliation, or
legacy catalogue attribution.

For the three listed records:

1. Keep the raw `ProviderContent`, `ContentMapping`, `Offer`, and evidence rows
   available for audit.
2. Verify each YouTube video ID against an independent identity source or
   operator review. A title match alone is insufficient; year or other
   source-specific evidence should agree.
3. If verified, replace the untrusted mapping with an explicitly reviewed
   manual mapping. If not verified, remove the mapping and retire the
   YouTube-only canonical offer assertion while preserving its audit history.
4. Recompute availability through the existing evidence/reconciliation path
   and confirm no other source's present evidence was removed.

The only offer with present evidence is `10827`; the other two have no present
evidence row. A future remediation should account for that difference rather
than applying one bulk deletion/deactivation to all three. This is a proposal,
not an executable write script.
