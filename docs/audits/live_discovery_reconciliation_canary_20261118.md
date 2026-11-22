# First live discovery-to-reconciliation canary — 2026-11-18

**END_TO_END_CANARY_BLOCKED**

Mandatory preflight did not establish a safe executable discovery command or sufficiently current source permission evidence. No discovery run or reconciliation evaluation was started. No external HTTP requests were made. The no-new-records status would be inappropriate: the authorized source page was never visited.

## Deployment and target

- Running container: `new-sources-backend-1`; Compose project `new-sources`.
- Immutable image: `sha256:5ac32c0066a33187345092c3119241df5af8c68f4937f9667100e9a1a5c1592a`.
- Application: `09684bae89cf20908ddb4a382452b0efb4a6045e`.
- All **525 tracked backend files** in that Git revision match the running image byte for byte, verified using Git blob hashes. The running filesystem is read only and has no mounts. The worktree HEAD is separately `11cc7416503c300b701945c8d39a7a2eaf6f943d`; it was not used as a substitute for the deployed application.
- Configured and actual database: `tracker`, user `tracker`, configured host `db`, database server address `172.25.0.4`. All audit transactions were PostgreSQL-enforced read only.
- Recorded migration leaves: tracker `0058_baseline_completed_catalogue`, availability `0016_contentmapping_match_evidence`. The read-only physical-schema guard reports ready, with no missing requirements and no pending migrations. No migrations executed.
- Backend healthy before and after; failing health streak zero, restart count **0 → 0**, same container ID throughout.
- Scheduler configuration: `{"enabled": false, "providers": {}}`, unchanged. No running `new-sources` title/catalogue worker containers or scheduler processes. The inspected backend processes are Gunicorn. Host crontab is absent. Other Compose projects were not operated on.

## Mandatory preflight findings

| Check | Finding |
|---|---|
| Image, application, migration state | Verified as above; no runtime or schema changes |
| Health and restarts | Healthy; zero restarts |
| Schedulers | All catalogue historical/incremental/reconciliation modes disabled |
| Source domain, provenance, permission, rate policy | **Not fully established**; details below |
| Restriction/cooldown | No persisted HydraHD restriction/error, active discovery lease or reconciliation run/cooldown; no live access probe attempted |
| Historical checkpoint | Movies **132**, TV **77**; expected next movie page **133** |
| Enforceable operation limits | **Failed** for the existing command; details below |
| Discovery write surface | Exactly the two application tables described below for this HydraHD path |
| Pre-execution integrity | Captured all 60 public tables and 57 sequences, including complete per-row hashes |
| Reconciliation dry-run canonical writes | Static inspection confirms no canonical writes; live evaluation not exercised |

### Source evidence and uncertainty

The deployed collector pins `https://hydrahd.gd/movies/date/N`. Persisted `Provider.domain` is `hydrahd.ru`, with an empty provider configuration. This discrepancy was preserved, not corrected during the canary.

The source documentation in the deployed revision says `hydrahd.info` self-identifies `.gd` as primary, explicitly distinguishes this from independent operator proof, and records an earlier robots observation permitting ordinary reference use. All **5,016** existing HydraHD discoveries carry `SELF_ASSERTED` / `PERMITTED`. These are historical observations and persisted labels; the collector also hardcodes those labels for new HydraHD rows. They do not independently establish current domain provenance, permission or a current rate policy.

There is no new evidence of a provenance change or a current block. There is also insufficient current evidence to assert that permission is established under this task's mandatory stop rule. No request to `.ru`, `.gd`, the status site, robots, sitemap or any fallback was made to resolve this uncertainty. The `.gd` sitemap's historical 403 was not retried.

The deployed network is `new-sources_default`, `internal=false`. That establishes connectivity configuration, not a destination allowlist or source permission. No external egress probe was performed.

### Discovery command limits

The natural candidate was reviewed but **NOT EXECUTED**:

```sh
docker exec new-sources-backend-1 python manage.py discover_unofficial_catalogue \
  --provider hydrahd --mode historical --partition movies \
  --request-budget 5 --delay 2 --retry-limit 0
```

Inspection of the exact deployed command/service and installed Requests implementation established:

1. **No page ceiling.** The parser/service has no maximum-pages option. Its loop continues while request budget remains. With one robots request, budget 5 can fetch multiple listing pages, violating the one-page authorization. Lowering the budget alone does not fix the remaining issues.
2. **Redirects bypass accounting and origin checks.** The service increments its counter around `scraper.session.get()`. It does not disable automatic redirects. Installed Requests follows redirects through `Session.send()` / `resolve_redirects()`, without invoking that budgeted `get()` again. Therefore the counter does not bound all actual HTTP attempts, and a redirect can contact an unapproved destination before the collector can stop.
3. **Two-second spacing is incomplete.** `--delay` sleeps between listing pages/retries. The initial robots request and listing request have no unconditional delay between them. The robots crawl-delay branch only sleeps when the declared delay exceeds the configured value; it does not establish the required minimum spacing for every request.

Retries can be disabled and this path is sequential, but those controls do not repair the above gaps. No runtime monkey patch, alternate crawler, deployment, source-policy change or checkpoint manipulation was introduced to work around preflight.

### Legitimate database writes, if discovery had run

For the deployed HydraHD historical movie branch, the only legitimate application-table modifications are:

- `tracker_discoveredcataloguerecord`: URL/provider identity creation, meaningful observation updates, source-observation history and observation timestamps. This path calls `_persist_unclassified_observation`, not the legacy ingestion bridge. New records remain pending classification.
- `tracker_cataloguediscoverystate`: the existing provider row's run status/report, lease/heartbeat, historical cursor/fingerprint/navigation evidence and checkpoint timestamps.

Insertion of discovery records may also advance that table's PostgreSQL sequence. Discovery does not legitimately modify legacy mappings, canonical movies, canonical proofs, provider-content identities, canonical mappings, providers/settings, reconciliation outcomes or playback offers in this HydraHD branch. The registered signal path does not add discovery-save writes.

The deployed reconciliation service calls the same read-only `canonical_association_conflict` validator in dry-run and again under row locks during apply. In dry-run `apply=False`, it constructs the gateway with cache writes disabled, does not create a run/lease/outcome, and never calls the canonical commit/classification writer. This verifies code-path intent, not a live conflict/identity test. Apply additionally rechecks current observations/proofs and concurrent conflicts; a dry-run cannot establish future race-free application.

## Execution accounting and reconciliation

| Measurement | Result |
|---|---:|
| Discovery runs started | 0 |
| Source HTTP attempts, including robots/retries/redirects | **0 / 5 authorized maximum** |
| Listing pages visited | **0 / 1 authorized maximum** |
| Existing identities encountered on source pages | 0 |
| New discovery identities / IDs | 0 / none |
| Meaningful metadata changes | 0 |
| Source-observation-only updates | 0 |
| Duplicate identities created | 0 |
| Naturally eligible HydraHD records before execution | 0 |
| Newly eligible records | 0 |
| Reconciliation evaluations / proposals / discovery IDs | 0 / none / none |
| TMDb HTTP attempts, including retries | **0 / 8 conditional authorized maximum** |
| Canonical writes / playback offers generated | **0 / 0** |

Checkpoint movies **132 → 132**, TV **77 → 77**; the entire checkpoint table is byte-equivalent by row digests. Incremental cursors and all other providers are unchanged. No previously deferred record was forced into eligibility, and no reconciliation dry-run was started because discovery preflight failed.

Movie/series classification, trusted live TMDb identity evidence, ambiguity handling and proposal-specific contradictory-proof rejection were **not exercised**. There are no new reconciliation proposals or external identity proofs to assess. The shared conflict-validation path and dry-run write isolation were inspected statically only.

## Final integrity comparison

All **60 public tables**, including **50 application tables**, have identical counts, full-table SHA-256 digests and per-row hashes before and after. All **57 sequence values and `is_called` flags** are unchanged. There are **no database differences to explain** and no permitted bookkeeping changes occurred.

| Preserved data | Before | After |
|---|---:|---:|
| Discoveries | 5,095 | 5,095 |
| Canonical associations | 4,769 | 4,769 |
| Canonical movies / provider-content rows / bootstrap proofs, each | 4,769 | 4,769 |
| Deferred `BASELINED` records | 326 | 326 |
| Associated reconciliation outcomes | 4,769 | 4,769 |
| Legacy mappings | 3,300 | 3,300 |
| Reconciliation runs | 0 | 0 |
| Playback offers | 0 | 0 |

Every original discovery, association, proof, legacy mapping, deferred outcome and provider-setting row retains its hash. No duplicate nonempty provider-native IDs or normalized provider identity URLs exist. The disabled HydraHD identity-only availability Source and all other Source settings remain unchanged. Existing deferred discovery rows are protected by the unchanged complete discovery table as well as unchanged per-outcome hashes.

The complete integrity capture hash is identical before/after:

`8b2dcb312c78790f9bd25427bf00cae3f0922aee368a1545311ca3ba52f04ce7`

## Exact commands and evidence

Evidence directory, retained locally with restricted permissions:

`.catalogue-staging/live-e2e-canary-20261118T135200Z/`

The authoritative read-only audit was executed with:

```sh
python3 .catalogue-staging/live-e2e-canary-20261118T135200Z/audit.py
python3 .catalogue-staging/live-e2e-canary-20261118T135200Z/finalize.py
```

`commands.json` retains exact audit subprocess argument arrays and stdin source-file references; `executed-commands.sh.txt` provides their shell form. Audit scripts and exact database/preflight stdin sources are retained. Database inspection used:

```sh
docker exec -i new-sources-backend-1 python - \
  < .catalogue-staging/live-e2e-canary-20261118T135200Z/database-before.stdin.py
docker exec -i new-sources-backend-1 python - \
  < .catalogue-staging/live-e2e-canary-20261118T135200Z/preflight.stdin.py
docker exec -i new-sources-backend-1 python - \
  < .catalogue-staging/live-e2e-canary-20261118T135200Z/database-after.stdin.py
```

Other retained artifacts include `RESULT.json`, `table-comparison.json` (every table's before/after digest), database captures with per-row hashes, filtered runtime inspections, deployed source and Requests redirect source, Git tree/blob verification, process/network/scheduler evidence, and `SHA256SUMS.json`. Container environment credentials were filtered before persistence.

Evidence quality is strong for deployment identity and database preservation; historical/self-asserted for source provenance and permission; static for discovery guard defects and dry-run/apply validation parity; absent for a live page or TMDb proposal because execution correctly stopped at preflight.

An eventual attempt needs a reviewed execution path that enforces the page ceiling, counts/controls every actual dispatch, refuses redirects before following them, spaces every request, and checks the applicable source policy. Source-domain permission/provenance uncertainty must also be resolved before external requests. No migrations, container replacements, schedule activation, threshold changes, mapping apply, merge or push occurred. This run is stopped.
