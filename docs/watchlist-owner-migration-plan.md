# Watchlist ownership and future migration

The installation selects its personal data through `resolve_personal_owner_id()`.
Set `WTDW_LEGACY_OWNER_ID` to the same positive PostgreSQL integer for Django and
all worker processes. The resolver uses owner `1` when the setting is absent and
fails on invalid configuration. Leave the existing installation on owner `1`.
Changing the setting selects a different existing owner and carries no records over.

Personal requests ignore client-supplied owner identifiers in query parameters,
forms, JSON and imports. A Django session does not change the data owner. Django
admin permissions still govern access to admin pages, and the owner field in
personal admin forms is server-controlled. This is a single-owner installation
policy. It does not provide authenticated isolation.

Watchlist membership uses the canonical Movie primary key, including the
movie/series namespace distinction. The lock shared by manual adds, ambiguity
resolution, imports and admin writers came from [#106][106]. It looks for an
existing membership for the resolved owner and reuses the lowest PK, including
its first-entry `source` and `added_at`. The existing database constraint on
`(movie_id, source)` still applies. A row for another historical owner can
therefore block insertion with the same source for the same Movie. Take that
collision to reconciliation review without reassigning the existing row.

Availability workers derive watchlist candidates and preference fallbacks from
the same installation owner. Queued availability work carries its selected
market and canonical movie identity. Refresh runs, catalogue evidence and queue
rows are shared availability infrastructure. Future authenticated status reads
and jobs will need explicit owner authorization and persisted attribution where
they represent personal requests. Previously queued, marketless work uses the
configured installation owner's market in this phase.

Review catalogue-level Movie and Provider deletion before authenticated
rollout. Those shared operations can cascade to personal records through
existing database relationships.

## Read-only review on an isolated restore

Run `python manage.py audit_watchlist_memberships` against the approved, isolated
PostgreSQL restore using an account that can read the membership and referencing
tables. Standard output is a JSON manifest from one repeatable-read, read-only
transaction. The command fails if a row security policy would filter the audit.
It scans every historical owner for duplicate `(user_id, movie_id)`
groups. Each group includes original membership IDs, source, full timestamps,
the lowest-PK survivor proposal, inbound foreign-key rows discovered through
the PostgreSQL catalogue, and owner/movie-level watched data and event references.
The manifest includes all discovered direct inbound constraints, including
composite keys. For a referencing table without a primary key it lists one
entry per matching row with the foreign-key values.

Keep the manifest with the backup and review evidence in controlled storage.
Review logical references in jobs, archives and external systems separately.
Check single rows with overlapping provenance or conflicting legacy IDs as
part of the full ownership inventory. The duplicate manifest is review evidence
and covers duplicate groups only.

## Gates before any live schema or data change

1. **Verify the backup and the restore.** Capture a fresh PostgreSQL backup
   and checksum, verify the checksum independently, and restore to an isolated
   PostgreSQL instance. Record backup identity, deployed application version,
   database version, row counts and restore verification. Establish the
   rollback command sequence and measure the restore time in rehearsal.
2. **Review ownership and disposition.** Inventory memberships and duplicates,
   physical and logical inbound references, and all associated personal data.
   Review an explicit legacy-owner-to-profile mapping with the owner. No
   account automatically inherits legacy owner 1. Review each proposed
   survivor, every old-to-new ID mapping, and exactly where original IDs,
   provenance, timestamps and references will remain available. Preserve
   ratings, watched state, `UserMovieData` and `MovieWatchEvent` independently.
3. **Rehearse on disposable PostgreSQL.** Use the isolated restored copy to
   exercise the proposed ID mapping, backfill, reference preservation and
   final `UNIQUE(owner_id, movie_id)`. Compare before and after manifests
   and counts. Exercise concurrent inserts, imports, retries and worker jobs,
   including overlapping raw TMDb IDs in different media namespaces.
   Verify rollback with the exact backup and matching application build.
4. **Obtain explicit production approval.** Present the checksum, verified
   restore, signed-off manifest and ownership mapping, concurrency results,
   maintenance window, change steps and rollback decision points to the
   owner. Phase 1 approval covers preparation and draft review only.
5. **Quiesce writers and abort on drift.** Stop every web, admin, import,
   scheduled and worker writer and verify quiescence. Refresh the pre-change
   backup as required by the rollback procedure. Re-run the inventory on the
   approved target and compare with the reviewed manifest. Abort on owner,
   row, reference, schema or provenance drift and return the new evidence
   for review before resuming the change.
6. **Verify before reopening writes.** After an approved change, check that
   zero duplicate groups remain, every reference resolves, and original
   provenance, timestamps and watched records are recoverable. Verify the
   corresponding app and workers against the intended owner context. On any
   failed check, keep writers paused, restore the verified backup with its
   matching application build, and verify counts, references and worker
   state before reopening traffic.

The future authorization seam is the personal entry point. The request
principal must resolve through an explicitly approved account-to-profile
mapping and be authorized before the first personal read or write. If a
principal or mapping is missing, deny access. An authenticated deployment
must never fall back to the installation owner for anonymous requests.
Pass authorized context through serializers, import/export services and job
ownership and enforce it before status reads and worker execution.

[#153][153] covers the deferred profiles and sharing design. [#167][167] must
design separate per-source associations for recurring sync while retaining
first-entry provenance on the membership. [#96][96] remains open until
authenticated owner enforcement has been implemented and verified.

[96]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/96
[106]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/106
[153]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/153
[167]: https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/167
