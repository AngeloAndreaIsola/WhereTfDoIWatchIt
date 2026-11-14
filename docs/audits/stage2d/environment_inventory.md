# Stage 2D — Environment Inventory (sanitized)

Observation timestamp (UTC): `2026-11-14T00:23:34Z`.
Code SHA: `00d9dc843c558dc2000f0bb4fba055671c5d012f`.

The audit identified the populated application database as `tracker`, in the
Docker volume `wheretfdoiwatchit_pgdata`. The application Compose project was
not running during the Stage 2D audit. Measurements were SELECT-only; the
audit command ran offline with a PostgreSQL read-only transaction.

No credentials, account rows, private watchlists, or subscription preferences
were inspected or recorded. Host-specific paths, unrelated container names,
and row-level exports are intentionally omitted from this preserved report.

The eight original Stage 2D artifacts were preserved in the detached checkout
at the same code SHA. This documentation copy includes the six aggregate
Markdown reports. The two CSV exports were excluded because they contain
row-level title/provider evidence and are unnecessary to preserve aggregate
findings.

No source calls, worker runs, migrations, or database writes were performed by
the Stage 2D audit.
