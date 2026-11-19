# Canonical reconciliation parity root cause — 2026-11-18

Investigation recorded before implementation changes. Base: integration/incremental-catalogue-safety at c5e692ee94c68ca644808ed0bcbed2fc02480e18. Runtime application: 11cc7416503c300b701945c8d39a7a2eaf6f943d.

## Exact paths

Both modes select eligible observations, build the same finite TMDb budget and use CanonicalVerifier.evaluate(apply=False, return_details=True), or reuse fresh agreeing record proof. The verifier checks trusted-ID provenance, exact title/year/type/details, complete search, competing source releases and an existing typed canonical identity's year. It then reports VERIFIED. Dry-run returns that result without entering _commit_record.

Apply additionally enters _commit_record: checks provider lease ownership/expiry, locks the discovery, rechecks its matching fingerprint, then locks the provider/native ContentMapping. Only here does a target-ID or media-type mismatch become CONFLICTING. This is the reported movie-to-TV discrepancy. It is reproducible without a concurrent write: preview and apply see equivalent source/TMDb evidence and the same existing mapping, but preview never reads the mapping conflict rule.

Other commit-only validation: persist_verified refuses an existing record proof bound to another canonical Movie, rechecks canonical year and source fingerprint, and evidence_agrees validates the persisted proof before association. _apply_classification preserves an existing mapping and detects a late mapping conflict, rolling back the atomic candidate/proof/association write. Thus a contradictory stored proof can produce the same optimistic preview problem. Linked provider-content identity consistency must also be checked before claiming an existing mapping is safely reusable. Same-identity stale source evidence may legitimately refresh after new exact verification; identity contradictions must not be silently rewritten.

Lease ownership, source edits during HTTP, proof expiry during execution and newly inserted/changed mappings are transactional/concurrent-state checks. Apply must retain these checks under row locks even after sharing static conflict decisions. The final classification writer and uniqueness constraints remain necessary for absent-row insertion races. A preview is a point-in-time decision, not a reservation.

## Accounting and outcomes

The per-record tmdb_requests value and run budget are based on gateway attempts before/after verification. They already include requests spent on rejected associations; conflict detection must not reset or refund these counters. Caches can be written in apply mode before a conflict is known; dry-run cache writes remain disabled. Rejected apply may persist only its normal outcome/run/checkpoint bookkeeping; canonical identity/proof/mapping creation rolls back or is skipped. The report-level status can remain COMPLETED while a record is CONFLICTING: that is an evaluated outcome, not a transport failure.

## Planned correction

Use a shared database-read-only canonical-association validator after candidate/proof verification in both modes, and repeat it with fresh locked state inside apply's atomic commit. Check typed provider mapping, linked provider content, existing proof identity/internally contradictory canonical evidence, trusted target and canonical year. Preserve all matching thresholds and same-identity proof refresh. Keep source/lease/expiry checks and final write guards. Record rejection evidence while retaining the original request accounting. Regression tests will compare equivalent-state preview/apply decisions and inject changes before commit to prove revalidation.
