# Stage 2Q — Historical identity remediation plan

Audit date: 2026-11-14  
Repository policy baseline reviewed: `origin/master` at `e148003` (merge of PR #85; includes PR #84)  
Checked-out branch: `codex/stage2e-freshness-recovery`  
Database inspected: local Docker PostgreSQL `tracker`, role `tracker`  
Database access: explicit `BEGIN READ ONLY` / `ROLLBACK` SELECT transactions only  
Provider requests, workers, availability experiments, and tests: not run  
Database changes: none

## Decision

Do not remap, delete, or rewrite any of these seven mappings now. Keep their original provider content, mapping, offers, evidence, and sync-run records for historical inspection. Under the integrated PR #84/#85 identity policy, Mediaset Infinity and YouTube mappings from their own title-search adapters are not trusted for canonical identity unless manually verified. Their current `external_id` mapping source is the same title-search adapter, so the mappings are quarantined at read time. This makes their offers unresolved for canonical availability while preserving the raw rows.

The current database values do not require a write to obtain that behavior in code. This recommendation is conditional on the backend and every relevant worker running code at `e148003` or later. This checkout itself is older and does not contain the integrated policy; the only running Compose service inspected was PostgreSQL, so deployed application code could not be verified. Verify that deployment condition before relying on runtime quarantine.

No replacement target is authorized. In particular, do not map Mediaset GUID `F000989101000104` to either Dune film from the evidence currently saved.

## Evidence policy applied

PR #84/#85 central policy (`availability.services.identity_policy`) treats these provider-native rows as unresolved when their mapping is an automatic title match or an `external_id` mapping sourced from the same title-search adapter:

- Sensitive providers: `mediaset_infinity`, `youtube`.
- Allowed identity evidence for those providers: `manual`, or `external_id` from an independent assertion source (`justwatch`, `streaming_availability`, `watchmode`).
- Neither Mediaset nor YouTube's historical collector result is an independent TMDb assertion. Both adapters copied the requested TMDb ID into their old result metadata after a title search; their declared source capability says the provider itself does not supply TMDb IDs.
- Saved title equality is useful corroboration, not canonical identity proof. Provider URLs and request-copied IDs do not become independent identity evidence.
- A successful historical targeted sync is a record of an observation at that time, not proof that the offer remains available now.

The policy is used by mapping-backed catalogue reads, offer recovery, and reconciliation. For these rows reconciliation reports unverified mapping provenance and suppresses them from effective current availability. This does not rewrite raw `Offer.currently_available`. The policy keys on provider plus provider-content ID; offers from other providers and independent trusted source rows are not invalidated by these seven mappings.

## Read-only database inventory

`current_database()` returned `tracker`; `current_user` returned `tracker`. The PostgreSQL server address was blank because this local Docker instance was reached over its container-local socket. Queries were scoped by the listed IDs/native IDs, with no writes.

### Seven mappings and linked ProviderContent

All seven supplied mapping IDs still exist. Every row has `match_method=external_id`, `confidence=0.95`, `revision=1`, and `source` equal to the provider's own adapter. All `last_verified` values reflect old targeted refreshes (October 2026), not independent identity verification.

| Mapping | Provider-native ID | Current target | Saved provider content | Classification |
|---:|---|---|---|---|
| 1006 | `F307480501000102` | 290250 — The Nice Guys (2016) | ProviderContent 11624; title “The nice guys”; no release date; description says Great Britain, 2016; metadata `external_ids.tmdb=290250`; first seen 2026-10-03, last seen 2026-10-24; available flag true | Unverified provenance. Title/year are consistent, but identity is not independently verified. |
| 1007 | `F011273901000102` | 320 — Insomnia (2002) | ProviderContent 11625; title “Insomnia”; no release date; description says USA, 2002; metadata `external_ids.tmdb=320`; first seen 2026-10-03, last seen 2026-10-24; available flag true | Unverified provenance. Title/year are consistent, but identity is not independently verified. |
| 1008 | `Y311540501000101` | 792 — Platoon (1986) | ProviderContent 11626; title “Platoon”; no release date; description says USA, 1986; metadata `external_ids.tmdb=792`; first seen 2026-10-03, last seen 2026-10-24; available flag true | Unverified provenance. Title/year are consistent, but identity is not independently verified. |
| 1009 | `F000989101000104` | 438631 — Dune (2021) | ProviderContent 11628; title “Dune”; no release date; description says USA, 1984; metadata `external_ids.tmdb=438631`; first seen 2026-10-03, last seen 2026-10-24; available flag true | Confirmed identity conflict between the provider-stated 1984 description and the mapped 2021 release. Exact Dune version remains unverified. |
| 4194 | `yVUIRcWTq0g` | 4982 — American Gangster (2007) | ProviderContent 23990; title “American Gangster”; no release date; metadata `external_ids.tmdb=4982`; first/last seen 2026-10-06 | Unverified provenance. No independent identity conflict found. |
| 4227 | `grBcDfdb7SY` | 4553 — The Machinist (2004) | ProviderContent 24023; title “The Machinist”; no release date; metadata `external_ids.tmdb=4553`; first/last seen 2026-10-06 | Unverified provenance. No independent identity conflict found. |
| 4764 | `GwIxcEvRq0g` | 389 — 12 Angry Men (1957) | ProviderContent 25128; title “12 Angry Men”; no release date; metadata `external_ids.tmdb=389`; first/last seen 2026-10-21 | Unverified provenance. No independent identity conflict found. |

The three Mediaset rows 1006–1008 and all three YouTube rows have insufficient evidence to confirm or falsify their canonical identity. Their similar title/year fields cannot upgrade them to independently verified or manually verified. Mapping 1009 is a conflict, but its description alone does not authorize a remap to Dune (1984).

### Offers, evidence, prices, sync provenance, and other native-ID claims

| Mapping | Offer row(s) | Country/type | Raw DB flag | Evidence row(s) | Price rows | Last successful relevant SyncRun |
|---:|---|---|---|---|---:|---|
| 1006 | 4538 | IT / subscription | available=true | 59, present=true, authoritative=false | 0 | 7023, succeeded targeted, 2026-10-24 |
| 1007 | 4539; 10848 | IT / subscription; free_with_ads | both available=true | 108, present=true, authoritative=false (on 10848); none on 4539 | 0 | 6838 succeeded targeted 2026-10-19; 7073 succeeded targeted 2026-10-24 |
| 1008 | 4540 | IT / subscription | available=true | 605, present=true, authoritative=false | 0 | 7219, succeeded targeted, 2026-10-24 |
| 1009 | 4542 | IT / subscription | available=true | 159, present=true, authoritative=false | 0 | 7124, succeeded targeted, 2026-10-24 |
| 4194 | 10114 | US / free_with_ads | available=true | none | 0 | 6237, succeeded targeted, 2026-10-06 |
| 4227 | 10147 | US / free_with_ads | available=true | none | 0 | 6271, succeeded targeted, 2026-10-06 |
| 4764 | 10827 | US / free_with_ads | available=true | 11, present=true, authoritative=false | 0 | 6975, succeeded targeted, 2026-10-21 |

The database contains eight Offer rows for the seven mappings because Mediaset Insomnia has two offer types. All eight associated runs are non-authoritative targeted runs. Their old positive flags and present evidence rows describe persisted observations; they do not establish current provider availability. No OfferPrice rows were found. There are no matching ProviderContentCandidate or CandidateReviewAudit rows for these native IDs/targets, and the schema has no mapping recovery record table. No target-specific availability experiment record was found in the experiment tables.

Searching all Offer rows by the seven provider-native IDs found exactly these eight rows; no unrelated provider row reuses one of those native IDs. Other providers do have offers at the same canonical TMDb targets. Their counts are: TMDb 320: 7, 389: 8, 792: 11, 4553: 4, 4982: 52, 290250: 5, 438631: 41. These are separate provider identities and must remain intact. These rows are themselves historical DB flags, not current availability guarantees.

## Per-record remediation recommendation

| Mapping | Action now | Why | Necessary DB write now? | Behavior with integrated policy deployed |
|---:|---|---|---|---|
| 1006 | Retain mapping as historical but unresolved; do not remap | Request-copied external ID; only matching title/year evidence | No | Provider-content mapping is untrusted; offer 4538 is mapping-doubt/unavailable in effective resolution. |
| 1007 | Retain as historical but unresolved; do not remap | Same unverified provenance; two distinct offer types share the native identity | No | Both offers 4539 and 10848 are quarantined by identity policy. |
| 1008 | Retain as historical but unresolved; do not remap | Same unverified provenance; title/year does not establish canonical identity | No | Offer 4540 is mapping-doubt/unavailable in effective resolution. |
| 1009 | Retain conflict as historical but unresolved; do not map to either Dune target | Provider description conflicts with 2021 target, but no independent evidence establishes 1984 target | No | Offer 4542 is quarantined; current target does not receive Mediaset's unsupported assertion. |
| 4194 | Retain as historical but unresolved; do not remap | Request-copied external ID; title-only evidence | No | Offer 10114 is mapping-doubt/unavailable in effective resolution. |
| 4227 | Retain as historical but unresolved; do not remap | Request-copied external ID; title-only evidence | No | Offer 10147 is mapping-doubt/unavailable in effective resolution. |
| 4764 | Retain as historical but unresolved; do not remap | Request-copied external ID; title-only evidence; preserve present evidence 11 | No | Offer 10827 is quarantined despite raw present evidence. Evidence is retained and does not independently establish canonical identity. |

Removing these mapping rows is unnecessary for runtime protection and would discard useful history. Deleting or deactivating offers/evidence is more destructive, particularly offer 10827 with saved present evidence. Do not use bulk deletion. The mapping remains inspectable in storage, while policy-aware paths treat its target as unresolved. Direct offers from other providers or from trusted independent sources remain separately attributed.

### Evidence required to resolve later

- For each YouTube video ID: independent video identity evidence that goes beyond the title string, such as an authoritative page/rights holder identifier or a human-reviewed video-to-work match with sufficient edition/year details. No provider requests were made in this audit.
- For Mediaset 1006–1008: independently inspect the exact GUID's source record and verify title/version and release identity. Saved matching title/year is not enough to promote the mapping.
- For Mediaset 1009: inspect the exact GUID's primary provider detail and establish the work/version and offer class. The 1984 description conflicts with current target but does not independently prove which TMDb target should be used.
- On verification, record a reviewed manual mapping through the existing human review/acceptance path with its evidence provenance. If the source remains ambiguous, leave it unresolved.

## Existing mechanisms and impact

- `plan_mapping_correction` / `MappingCorrectionImpactPlanner`: read-only impact analysis for a proposed replacement TMDb/type. It is useful after a verified candidate exists; proposing the current target only inventories impact and does not verify identity. It cannot mark a mapping unresolved or apply a correction.
- `mapping_correction_impact`: includes current mapping, provider content, directly associated offers/evidence/prices, corroborating target observations, competing mappings, and fingerprints. It must be run separately for any verified target before authorizing a future change. It is not a mutation mechanism.
- `remediate_mapping_audit`: guarded, two-phase operation for an unrelated fixed historical confidence-0.7 allowlist. Its approved rows do not include these seven IDs. Do not repurpose or broaden it for this audit.
- Central identity trust policy: currently provides sufficient read-time quarantine for these `external_id` mappings, without disturbing unrelated trusted provider rows. It does not change historical DB flags or erase the erroneous provenance; that is intentional.

There is no general audited historical tool here to mark these rows explicitly unresolved in storage. No such write is required while policy-aware resolution is deployed. If future independent evidence proves a mapping false and a persistent database correction is still desired, a separate reviewed, digest-bound manifest/tool should enumerate exact row IDs, old values, evidence IDs, all linked offers/evidence/prices, and reversibility. The existing planner is the preflight; it is not authorization to mutate.

## Exact write authorization manifest

No database writes are authorized by this Stage 2Q plan.

```json
{
  "schema": "stage2q-identity-remediation-authorization-v1",
  "database": "tracker",
  "mode": "read-only-plan",
  "authorized_mutations": [],
  "preserve_mapping_ids": [1006, 1007, 1008, 1009, 4194, 4227, 4764],
  "preserve_offer_ids": [4538, 4539, 4540, 4542, 10114, 10147, 10827, 10848],
  "preserve_evidence_ids": [11, 59, 108, 159, 605],
  "preserve_offer_price_ids": [],
  "replacement_tmdb_ids_authorized": [],
  "precondition": "Independently verified identity evidence and fresh, reviewed impact manifest required before any future write"
}
```

Because no write is planned, DB before/after values should be identical. The reversible execution proposal is therefore to deploy/verify the policy code, retain all records, and re-run the SELECTs below. If later verification justifies an update, create a new separately approved manifest; do not apply this empty authorization to it.

## Verification queries

Run these with the approved deployed code context and a read-only database role/transaction. `ROLLBACK` closes the transaction without committing.

```sql
BEGIN READ ONLY;
SELECT current_database(), current_user;

SELECT m.id AS mapping_id, p.slug AS provider, m.provider_content_id,
       m.content_type, m.tmdb_id, mv.title AS target_title, mv.year AS target_year,
       m.match_method, m.confidence, m.revision, m.first_seen, m.last_verified,
       s.slug AS mapping_source, pc.id AS provider_content_row,
       pc.title AS provider_title, pc.release_date, pc.metadata,
       pc.first_seen AS content_first_seen, pc.last_seen AS content_last_seen,
       pc.currently_available AS content_flag
FROM availability_contentmapping m
JOIN tracker_provider p ON p.id = m.provider_id
JOIN availability_source s ON s.id = m.source_id
LEFT JOIN tracker_movie mv ON mv.tmdb_id = m.tmdb_id
LEFT JOIN availability_providercontent pc ON pc.id = m.content_id
WHERE m.id IN (1006,1007,1008,1009,4194,4227,4764)
ORDER BY m.id;

SELECT o.id AS offer_id, p.slug AS provider, o.provider_content_id,
       o.tmdb_id, o.content_type, c.code AS country, o.offer_type,
       o.currently_available, o.first_seen, o.last_seen, o.source_record_id,
       o.source_version, s.slug AS source, o.sync_run_id
FROM availability_offer o
JOIN tracker_provider p ON p.id = o.provider_id
JOIN availability_country c ON c.id = o.country_id
JOIN availability_source s ON s.id = o.source_id
WHERE (p.slug='mediaset_infinity' AND o.provider_content_id IN
       ('F307480501000102','F011273901000102','Y311540501000101','F000989101000104'))
   OR (p.slug='youtube' AND o.provider_content_id IN
       ('yVUIRcWTq0g','grBcDfdb7SY','GwIxcEvRq0g'))
ORDER BY p.slug, o.provider_content_id, o.id;

SELECT e.id AS evidence_id, e.provider_slug, e.provider_content_id,
       e.tmdb_id, e.offer_identity, s.slug AS source, e.is_present,
       e.authoritative, e.first_seen, e.last_seen, e.observed_at
FROM availability_offerevidence e
JOIN availability_source s ON s.id = e.source_id
WHERE e.provider_content_id IN
      ('F307480501000102','F011273901000102','Y311540501000101','F000989101000104',
       'yVUIRcWTq0g','grBcDfdb7SY','GwIxcEvRq0g')
ORDER BY e.provider_slug, e.provider_content_id, e.id;

SELECT pr.id, pr.provider_slug, pr.provider_content_id, pr.tmdb_id,
       pr.offer_identity, s.slug AS source, pr.amount, pr.currency,
       pr.is_current, pr.first_seen, pr.last_seen
FROM availability_offerprice pr
JOIN availability_source s ON s.id = pr.source_id
WHERE pr.provider_content_id IN
      ('F307480501000102','F011273901000102','Y311540501000101','F000989101000104',
       'yVUIRcWTq0g','grBcDfdb7SY','GwIxcEvRq0g')
ORDER BY pr.provider_slug, pr.provider_content_id, pr.id;

SELECT r.id, p.slug AS provider, s.slug AS source, c.code AS country,
       r.mode, r.status, r.authoritative, r.started_at, r.completed_at,
       r.items_discovered, r.items_processed, r.offers_created, r.offers_updated,
       r.offers_deactivated, r.error_count
FROM availability_syncrun r
JOIN tracker_provider p ON p.id = r.provider_id
JOIN availability_source s ON s.id = r.source_id
LEFT JOIN availability_country c ON c.id = r.country_id
WHERE r.id IN (6237,6271,6838,6975,7023,7073,7124,7219)
ORDER BY r.id;
ROLLBACK;
```

After-value checks should assert the same seven mapping IDs/targets and the same linked offer/evidence rows, since this plan authorizes no writes. Separately, verify the running backend/worker build includes the `identity_policy.py` behavior from `e148003` or later, then use a read-only application-level reconciliation/catalogue inspection to confirm these seven mappings resolve as untrusted/unresolved and that independent-provider offers remain visible. Do not start workers or refresh sources merely to perform this plan.

## Broader provenance audit

The current source scan found these `request.tmdb_id`/TMDb metadata patterns outside the two remediated adapters:

| Adapter | Actual source semantics | Assessment and priority |
|---|---|---|
| JustWatch | `DiscoveredContent.external_ids.tmdb` comes from `title.tmdb_id_int` in the JustWatch title payload; the request ID is used as an offer fallback only after matching logic and is not copied into external-ID metadata when the response lacks an ID. | Not the same synthetic external-ID assertion. Preserve current source-backed mapping behavior; keep regression coverage for conflicting IDs. |
| Watchmode | Search result is selected only with an exact request TMDb ID in the right movie/TV namespace or an exact IMDb match; content metadata then carries request TMDb ID, and provider content IDs are constructed from request identity. | Mostly request-context identity, not literal provider-native TMDb assertion. Follow up: when both request TMDb and IMDb IDs are present, `_select_title` can accept an exact IMDb match even if the same returned result states a conflicting TMDb ID. Require contradiction handling before source remains in the central trusted assertion set. |
| Streaming Availability | The API is called at `/shows/{type}/{request.tmdb_id}`; its content ID is the returned/request TMDb ID combined with service ID, and metadata copies the request TMDb ID. It is a TMDb-keyed aggregator response rather than an independent provider-native content ID assertion. | Different semantics from title-search collision, but metadata is still synthetic. Follow up to document/model it as request-keyed observation or validate the response's own identity before treating this field as an independent external-ID assertion. |
| Mediaset Infinity / YouTube | Title search yields provider-native GUID/video ID; old adapter copied request TMDb ID to offer and `external_ids`. | Confirmed original defect; PR #84/#85 stop new assertion and centrally quarantine historical own-source mappings. |

Priority follow-up PRs:

1. **Streaming Availability provenance contract:** decide whether request-keyed aggregator observations should create `EXTERNAL_ID` mappings at all. If the endpoint response does not independently state/validate the canonical ID, represent it as request-context evidence rather than source-asserted provider mapping. Preserve direct offer attribution only under its documented TMDb-keyed endpoint semantics.
2. **Watchmode contradictory IDs:** tighten `_select_title` so a match that reports a conflicting TMDb ID cannot be accepted solely because its IMDb ID matches. Add regression coverage and re-evaluate its central trust classification.
3. **Historical quarantine observability:** optionally expose a clear unresolved/unverified reason in internal audit output for retained mapping rows; do not make manual state look verified merely by changing confidence/source fields.

These are provenance semantics follow-ups, not authorization for unrelated refactoring or data cleanup.

## Evidence gap and readiness

The database inventory is complete for the seven specified mappings and their direct dependencies. The identity gaps are explicit and safely left unresolved: independent primary evidence for Mediaset GUIDs/YouTube videos is absent; Dune 1984 versus 2021 is not settled. Runtime protection is verified in the integrated source at `origin/master` `e148003`, but the deployed backend/worker version is unverified because this checkout is an older branch and no backend/worker container was running. Verify deployment before relying on read-time quarantine.

**REMEDIATION_PLAN_READY — no database write authorized; independent identity evidence and deployed-code verification remain prerequisites for any later remediation.**
