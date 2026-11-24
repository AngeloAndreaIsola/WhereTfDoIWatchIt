# Fresh application-side observation report — #97

**Verdict: BLOCKED_ON_SPECIFIC_ACCEPTANCE_GATE.** Zero fresh independent comparable
pairs were obtainable in this session. No accuracy rate is claimed.

## Why this gate exists

The 2026-11-23 frozen clone proved that the displayed catalogue was stale, not
that current availability is accurate. An accuracy measurement needs an
application observation and an independent observation of the **same** typed
work, media type, country, provider and offer class, inside a compatible window.
Neither a fresh dump of stale rows nor a same-source offer can supply that.

## Read-only preflight (measured this session)

| Probe | Command / source | Measured result |
|---|---|---|
| Base revision | `git rev-parse origin/master` | `e3dd825c4e6f3fd0657a863cd6915387e36a1070` (PR #189) |
| Ancestor check | `git merge-base --is-ancestor e3dd825 origin/master` | `YES` |
| Isolated worktree | `git worktree add -b orca/issue97-e3dd825 … e3dd825` | clean, HEAD `e3dd825` |
| Authorized clone | `psql -lqt` | `tracker_97_audit_20261122` present and distinct from serving `tracker` |
| Read-only gate | `PGOPTIONS='-c default_transaction_read_only=on'` | `current_database()=tracker_97_audit_20261122`, `current_user=angeloandreaisola` |
| Clone size / newest row | `SELECT count(*), max(last_seen) FROM availability_offer` | 11,003 rows; newest `2026-11-20T18:33:02+01` |
| Cohort freshness | 7 priority TMDb ids, `count(*)`, `max(last_seen)`, `fresh within 24h` | 242 rows; newest `2026-10-24T22:47:59+02`; **0** within 24h |
| Whole-clone freshness | `count(*) WHERE last_seen >= now()-interval '24 hours'` | **0** |
| Serving database | `docker ps` labels | `tracker` belongs to `wheretfdoiwatchit`; **never queried or mutated** |
| Provider requests this session | offline tools only | **0** |
| Metered / paid calls | none | **0** |

The clone is a legitimate *historical* evidence source. It cannot be described as
a current observation: every row is older than the freshness window.

## Legitimate paths and their status

| Path | Status | Reason |
|---|---|---|
| Frozen `tracker_97_audit_20261122` clone | Used, read-only | Proves stale display; 0 fresh rows |
| New serving snapshot | Not authorized / not taken | Would require a production backup or export; none supplied |
| Live provider pages by agent | Not authorized | No fresh per-source permission or request budget was granted |
| Metered aggregator refresh | Not authorized | No paid calls, no new subscription, prior budget exhausted |
| Owner-supplied manual observations | Prepared, not yet supplied | [Manual verification sheet](manual-verification-sheet.md) + offline intake tool |
| JustWatch preflight | Not authorized | Partner gate must not be bypassed |

## Measured comparison state

- Fresh application observations: **0**
- Fresh independent comparable pairs: **0**
- Confirmed agreements / conflicts / true positives / false positives /
  false negatives / true negatives: **0 each — NOT PROVEN**
- Precision, recall, F1: **undefined** (no valid denominator)
- Advertised positives from preserved first-party evidence: **13**, all unresolved
  against a fresh application row in this session
- Entitlement observations: **0**; playback observations: **0**

The earlier 34-case panel result (0 current offers returned, 12 needs-recheck,
6 unknown, 16 no visible typed movie) remains a **display-retrieval** measurement
on the frozen clone. It is not an accuracy rate.

## What unblocks this gate

1. Owner supplies at least one row per target in the
   [manual verification sheet](manual-verification-sheet.md), with a saved raw
   artifact (hash can be computed by `scripts/ingest_owner_observations.py`).
2. A **current, sanitized, disposable** canonical snapshot is supplied and named
   independently of connection configuration; the audit is run with the database
   identity guard and `SET TRANSACTION READ ONLY`.
3. The offline comparator joins only exact `(tmdb_id, media_type, country,
   provider_slug, offer_type)` and excludes same-source echoes. It then reports
   counts with explicit `unknown`, `needs_recheck` and `insufficient_evidence`
   buckets.

Until then, #97 stays **OPEN**, #103 stays **ON HOLD**, and no production accuracy
claim is permitted.
