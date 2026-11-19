# Final bulk canonical audit — 2026-11-17

**BULK_CANONICAL_AUDIT_PASSED**

Audited code: `a7ce47f5a15107ea8df1c39c740e8699f0f6cd68`, branch `new-sources`.

All 16 batches completed. The frozen inventory contains 5,095 discoveries, each with exactly one outcome: 4,769 verified, 236 ambiguous, 89 unmatched, one blocked (Blue 1968, record 1771), zero errors and zero unevaluated. The accepted set contains 3,008 movies and 1,761 series. All 79 AnimeWorld records are excluded (64 ambiguous, 15 unmatched).

All original manifest checksums, checkpoint scope hashes and source fingerprints passed independent verification. All 4,769 retained association proofs passed full mechanical verification and the existing importer's complete validation. The risk-weighted sample reviewed 163 accepted matches and 80 exclusions, including same-title releases, alternative titles, normalization boundaries, missing years and movie/TV numeric-ID overlap; no accepted associations were rejected. The audit used retained source/TMDb evidence without recrawling provider pages or requesting TMDb again.

Focused tests: **227 passed** (4.716s). Full backend: **3,342 passed** (129.419s). Both executed against ephemeral PostgreSQL; exact commands and logs are in the report.

The read-only sentinel JSON diagnostic fixed the Django shell-banner parsing problem. A fresh backup matched live across all 48 application tables before import. Expected reconciliation and cache changes in the completed bulk clone were accounted for separately. Unrelated legacy mappings, discovery identities, provider/Source settings and checkpoints are unchanged.

The complete release imported into a separate fresh PostgreSQL clone: **4,382 canonical identities and associations inserted, 387 reused, 4,769 final**, no duplicates, all 3,300 legacy mappings and 5,095 discoveries preserved, zero offers and zero TMDb requests. SIGKILL after five in-transaction inserts demonstrated rollback and successful recovery. Full replay made zero row changes across all 48 application tables. Live before/after digests are identical across all 48 tables.

Complete canonical association manifest: `/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/release/canonical-associations.jsonl`

SHA-256: `87a0a61ad4fe9f4c6b23a531dd962691185b5a8b6722b88db414ad35fe8d0958`

Importer-ready release: ten bounded JSON parts, nine of 500 associations and one of 269. Bundle SHA-256: `10c0380c74657edbef775f2150511ef5938effd2f6694737ad3d4a5e41d4fa97`. Per-part hashes and original input hashes are in release/bundle.json and release/CHECKSUMS.sha256. Earliest retained proof expiry: **2026-12-17 19:03:32.563 UTC**.

External evidence root: `/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit`

- [Full audit report](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/REPORT.md)
- [Complete release manifest](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/release/canonical-associations.jsonl)
- [Release bundle and checksums](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/release/bundle.json)
- [Independent matching audit](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/matching-audit.json)
- [Focused and full test commands/results](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/test-results.json)
- [Live versus clone comparison](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/state-comparison.json)
- [Clone import/replay/recovery results](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/clone-import-results.json)
- [Exact proposed live procedure](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/LIVE-PROCEDURE.md)

No technical blocker remains for accepted associations. Separate authorization is required for live import; source drift or proof expiry requires renewed validation. All 326 unresolved discoveries remain excluded. Historical per-record request accounting for the prior 1,250 records remains explicitly unknown. The original checksum index omitted the eight F parts and their validation file; those hashes were independently cross-checked and the final release index covers every file. The original F wrappers' fixed created_at and descriptive source_audit_sha256 are not relied on; final release wrappers omit those fields and record real hashes in the bundle.

No live mappings were applied. No live database writes, schedules, crawls, offers, threshold changes or merges occurred. Full private row snapshots and database backups remain outside Git.
