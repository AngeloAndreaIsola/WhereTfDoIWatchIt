# Tubi remediation — local release and future rollout procedure

Status: **local release verified; not deployed anywhere else**. There is no
production environment for this application yet. Every local step in this
document has been run and recorded in the change set; the deployment-shaped
steps below are the procedure for when a target environment exists, not a
current blocker. See “Deployment status” at the end.

## Change inventory

| Change | Contents | State |
|---|---|---|
| **F1** | `234966a` — Tubi sitemap parsing reads only the direct `<loc>` child of `<url>`/`<sitemap>`, validates title URLs, retries sitemap documents, logs discovery metrics, `tubi-ssr-v2` | committed on `feature/scrapres-improvedment` |
| **F2** | `8b96101` — versioned per-sitemap last-seen-URL `SyncCursor`, priority hints for tracked titles, zero-offer batches advance, non-authoritative semantics preserved | committed on the same branch |
| **F3** | `availability/services/matching.py`, `availability/tests/test_matching.py`, `availability/tests/test_cbc_gem_refresh.py` — a known conflicting movie year returns no match instead of a title-only 0.7 match | **uncommitted** (3 files) |
| **F4** | `sources/watchmode.py`, `services/watchmode_identity.py`, `services/watchmode_identity_repair.py`, `management/commands/repair_watchmode_identity.py`, `management/__init__.py`, `management/commands/__init__.py`, `tests/test_watchmode_identity.py`, `tests/test_aggregators.py`, `docs/REPAIR_WATCHMODE_IDENTITY.md`, `docs/WATCHMODE_UNCORROBORATED_OFFERS.md` | **uncommitted** (10 files; no schema migration) |
| **Historical** | `services/mapping_remediation.py`, `management/commands/remediate_mapping_audit.py`, `tests/test_remediate_mapping_audit.py`, `docs/REMEDIATE_MAPPING_AUDIT.md`, write-barrier change set | uncommitted, pre-existing; canonical digest contract updated to manifest-v3 |

`pytest`/Django require nothing else: `makemigrations --check` reports no
changes and `manage.py check` is clean with all four changes applied together.

## Dependency order

```
0. review + commit F3 (3 files) and F4 (10 files) as separate commits; the
   historical remediation commit depends on F3 and the write barrier
1. deploy app code (F1 + F2 + F3 + F4 source change)          ← no data writes
2. F4 identity repair (data; irreversible retirement step)    ← new code must be live
3. historical mapping remediation (data; destructive, 6/20/1) ← separate approval
4. post-deployment verification
```

Why this order:

* **F3 before the mapping remediation.** The remediation's
  `verify_local_matcher_fix` refuses to run unless the fixed matcher is the code
  loaded in the process that mutates. It also stops the old matcher from
  re-creating a bad mapping on the next refresh.
* **F4 after the new code is live.** The `watchmode-v2` writer must be the only
  writer of Watchmode identities before the collapsed rows are retired,
  otherwise the next refresh would re-create them.
* **F4 and the remediation do not overlap.** F4 touches only
  `provider_content_id` values matching `wm:<provider>`; the remediation's six
  mappings and 20 offers are element IDs and `tubi` native IDs (`608226`, …).
  They can run in the same maintenance window, F4 first, but each needs its own
  approval and its own backup. Re-verified 2026-10-21: the remediation's 20
  target offer ids and the F4 plan's 183 offer ids are disjoint, and none of the
  remediation's target `provider_content_id` values starts with `wm:`.
* **No schema migration is involved.** A previous-image rollback is therefore
  sufficient *before* step 2; after step 2 it is not (see Rollback).

## Change identity

Before deploying, record the exact artifacts:

```bash
git -C <deployment checkout> rev-parse HEAD
git -C <deployment checkout> status --porcelain      # must be empty
docker compose images | grep -Ei 'backend|frontend'
docker compose exec -T backend python -c \
  "import availability, availability.sources.tubi as t, availability.sources.watchmode as w; \
   print(t.SOURCE_VERSION, w.WatchmodeSource.SOURCE_VERSION)"
# expected: tubi-ssr-v2 watchmode-v2
```

The deployed commit must be the one that contains `234966a`, `8b96101`, the F3
matching change and the F4 identity change. `docker compose up -d --build`
rebuilds from the working tree, so a dirty tree silently deploys unrelated
work — verify `status --porcelain` is empty first.

## Gates (maintenance window)

All data steps require workers paused and a verified backup. When a target
deployment exists, `topology_attestation.sh` is the topology evidence script;
run it **from that deployment's checkout** (it is self-contained, writes only
`./topology-attestation-<UTC>.log`, and is read-only). It is not needed for the
local environment:

```bash
bash topology_attestation.sh --attest-writers-disabled
```

| Gate | F4 repair | Mapping remediation |
|---|---|---|
| A–D database identity / pooler | recommended (row locks assume one primary) | **required** (advisory-lock barrier) |
| E deployed image + barrier code | **required** (`watchmode-v2` present) | **required** |
| F writers disabled | **required** | **required** |
| Backup + restore drill | **required** | **required** |

Do not add gates that a step does not depend on: the F4 repair does not use the
advisory barrier, so A–D are advisory for it, while the remediation's exclusive
lock is exactly what A–D validate.

## Step 1 — deploy application code

```bash
cd <deployment checkout>
git fetch && git checkout <release commit or merged master>
git status --porcelain                     # empty
docker compose exec -T db pg_dump -U tracker -d tracker \
    --no-owner --no-privileges > ./backups/pre-rollout-$(date +%Y%m%dT%H%M%S).sql
docker compose up -d --build
docker compose ps
```

Post-deploy smoke (read-only):

```bash
docker compose exec -T backend python manage.py check
docker compose logs --since=10m backend | grep -E "tubi_catalogue_summary|tubi_cursor|watchmode_" | tail
```

The first scheduled Tubi catalogue run should log
`tubi_catalogue_summary … title_urls≈82000 batch=500` and a `cycle` that
advances between runs; the cursor is persisted only after the batch commits.

## Step 2 — F4 identity repair

Follow `docs/REPAIR_WATCHMODE_IDENTITY.md` exactly: stop the scheduler and
`worker`/`catalogue-worker`, back up and restore-check, dry run, review the
digest and blocked rows, apply with `--expect-digest`, verify, restart.

Stop conditions: any `blocked_rows`, a digest that changes between review and
apply, any `watchmode` `SyncRun` still `running`, or a non-empty
`status --porcelain` at deploy time.

## Step 3 — historical mapping remediation

Follow `docs/REMEDIATE_MAPPING_AUDIT.md`. Re-run the dry run immediately before
the window (the digest is data-dependent):

```bash
docker compose exec -T backend python manage.py remediate_mapping_audit \
    --write-manifest ./backups/remediation-manifest.json
```

Expected scope, re-verified after the F3 change on 2026-10-21: **6 mappings,
20 offers, 1 evidence row, 0 prices, 8 protected mappings**, local matcher fix
`present`. Any difference from the approved scope is a stop condition.

The manifest digest is a canonical SHA-256 over the mutation contract: the
approved plan, every target row's columns, the protected mappings and the
database identity. It excludes volatile provenance (`generated_at`), so two
dry runs over the same plan and the same rows produce the same digest. Review
the `--write-manifest` file (its counts, targets and protected rows) and use
*that* manifest's digest in the confirm string. A digest that changes between
review and apply means the live rows or database changed; re-review rather than
reusing the old confirmation. Apply only with the ticket approval and the
confirm string:

```bash
docker compose exec -T backend python manage.py remediate_mapping_audit \
    --manifest ./backups/remediation-manifest.json --apply \
    --confirm <TICKET>@<first-12-hex-of-that-manifest>
```

## Step 4 — post-deployment verification

### 4.1 Corrected acceptance checks

The checks proposed in the earlier report are **not definitive** and were
verified against the running app before relying on them:

* `/api/prime/movie/389/` is **not country-scoped** (US/NL/CA return the same
  payload) and reports provider availability from *raw* offer rows across all
  countries. `tubi` appears because of US/AU/CA/MX `streaming_availability`
  offers, not because of the 1997 Tubi page.
* `/api/prime/movie/550/` has **no Tubi offer at all** in the current data, so
  it cannot demonstrate the 2011 page being suppressed.

Use these instead — they distinguish the two films at the provider level:

```bash
docker compose exec -T backend python manage.py shell -c "
from availability.models import ProviderContent, ContentMapping, Offer
from availability.services.matching import match_local_catalog
for pcid in ('667137', '100056827'):
    c = ProviderContent.objects.filter(provider__slug='tubi', provider_content_id=pcid).first()
    print(pcid, 'discovered=', bool(c),
          'title/year=', (c.title, c.release_date) if c else None,
          'mappings=', ContentMapping.objects.filter(provider__slug='tubi', provider_content_id=pcid).count(),
          'offers=', Offer.objects.filter(provider__slug='tubi', provider_content_id=pcid).count())
"
# 667137  (1997 12 Angry Men): if discovered, title/year must be 1997 and
#          mappings=0, offers=0. It is only discovered when the checkpointed
#          walk reaches it, so "discovered=False" is not a failure — use the
#          matcher check below as the deterministic acceptance test.
# 100056827 (2011 Fight Club): same rule.
```

```sql
-- the 1957 film keeps its own Tubi offers; none came from the 1997 page
select provider_content_id, country_id, source_id
  from availability_offer
 where tmdb_id = 389 and provider_id = (select id from tracker_provider where slug='tubi');
```

Matcher-level (what the collector would do with the live page):

```bash
docker compose exec -T backend python manage.py shell -c "
from datetime import date
from types import SimpleNamespace
from availability.services.matching import match_local_catalog
page = SimpleNamespace(title='12 Angry Men', release_date=date(1997,1,1), content_type='movie')
print(match_local_catalog(page))
"
# None
```

### 4.2 Tubi catalogue progress

```bash
docker compose logs --since=24h backend | grep tubi_catalogue_summary
```

Acceptance: `title_urls` ≈ 82k (not ~41k), `batch` = configured cap, `cycle`
increments across a completed walk, and consecutive runs do not repeat the same
`batch_selected` window.

```bash
docker compose exec -T backend python manage.py shell -c "
from availability.models import SyncCursor
print(list(SyncCursor.objects.filter(source__slug='tubi', provider__slug='tubi')
           .values('country__code','cursor','last_successful_sync')))
"
```

### 4.3 F4 consistency

```bash
curl -s 'http://127.0.0.1:8000/api/prime/movie/5/?country=CA' | python3 -c "
import json,sys; a=json.load(sys.stdin)['availability']
print(a['offer_count'], a['available_on'])"
```

After the repair, the reconciled read and the report agree for this title (the
Tubi/CA offer is present and available). A `quality` conflict between
`streaming_availability` and `watchmode` is expected and is not a failure.

## Rollback matrix

| Point | Rollback |
|---|---|
| After step 1 (code only, no data step) | `git checkout <previous commit> && docker compose up -d --build`. No schema/data change was made. |
| After step 2 (F4 applied) | Restore the pre-window dump, then roll the image back. Rolling the image back **without** the dump re-creates `wm:{provider}` rows on the next Watchmode refresh. Forward recovery (re-run the idempotent repair) is preferred when the code is correct. |
| After step 3 (remediation applied) | The remediation deletes 6 mappings / 20 offers / 1 evidence row and updates mapping `399`; restore the pre-window dump. Re-running the command is not a rollback (the rows are gone). |
| Anytime | `docs/REPAIR_WATCHMODE_IDENTITY.md` §Rollback has the exact `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` restore sequence and its full-database implications. |

Take a fresh dump immediately before each destructive step, not one dump for
the whole window, so each step has its own rollback point.

## Deployment status — not applicable yet

Verified 2026-10-21, local Docker Compose environment:

* `git remote` → `https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt.git`;
  the F1/F2 branch exists on `origin`, the F3/F4 and remediation work is local
  and unpushed.
* `.github/workflows/ci.yml` is the only workflow: tests + migration check on
  `master`/PRs. There is no deploy job, release job or image publication.
* `docker context ls` → `default`, `desktop-linux` only; there is no production
  context or host configuration because **no production server exists**.

This is expected, not a blocker: the release being verified is local. Nothing
in this document requires SSH, a VPS, or a production attestation now. A future
deployment would need a target host and a release process; at that point run
the gates above against that host and keep the same order (code → F4 repair →
historical remediation). Until then the destructive `--apply` steps remain
unapplied by design and require separate approval.
