# Authorized bulk canonical import — 2026-11-18

**LIVE_IMPORT_COMPLETE**

The explicitly authorized audited release was imported into Compose project `new-sources`, target `tracker@db`, using the existing guarded importer. Applied migrations remained availability `0016` and tracker `0056`.

Before writes, all 48 application tables matched the audited baseline. All release hashes and proof expiries passed; a fresh backup restored successfully into an isolated PostgreSQL database, matching all application tables and migration records. All ten read-only dry runs passed.

Final state: **4,769 canonical associations**, comprising **4,382 inserted and 387 reused unchanged**. All **5,095 discoveries** and **3,300 legacy mappings** remain preserved. The **236 ambiguous, 89 unmatched and one blocked** discovery are excluded and retain their original states. No unexpected association, duplicate or playback offer exists. Provider/Source settings and checkpoints remain unchanged.

Ten bounded atomic transactions imported the release, with integrity verification after each part. A complete guarded replay succeeded with zero row changes across all 48 application tables after every replay part. `/api/providers/` returned **HTTP 200**; a subsequent snapshot confirmed the request made no application changes.

Approved manifest SHA-256: `87a0a61ad4fe9f4c6b23a531dd962691185b5a8b6722b88db414ad35fe8d0958`

Fresh backup SHA-256: `4bbe437f467e7e41f8e1f8a28560e2272e6c56b324a8521c3b6455612c6f2ebd`

Discovery identity/observation SHA-256 (unchanged): `d5ea9bc917ff8929c269b402703b0e3118e78ad4f990301f41a6568d72fac9fd`

Evidence directory: `/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z`

- [Execution report and integrity hashes](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/REPORT.md)
- [Final machine-readable result](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/RESULT.json)
- [Mandatory preflight and restore test](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/preflight-passed.json)
- [Committed transaction results](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/transaction-results.json)
- [Replay transaction results](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/replay-transaction-results.json)
- [Final association and preservation checks](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/final-integrity.json)
- [Exact commands and exit statuses](/Users/angeloandreaisola/Documents/WhereTfDoIWatchIt-BulkCanonical-20261117/final-audit/live-rollout-20261117T230134Z/commands.json)

Two preflight diagnostic formatting issues were corrected before any live write; both are recorded in the execution report. No database precondition, import transaction or integrity check failed. No remaining error or recovery action is pending.

No catalogue crawls, schedules, language extraction, migrations, threshold changes, availability inference or merges occurred. Private backups and row snapshots remain outside Git.
