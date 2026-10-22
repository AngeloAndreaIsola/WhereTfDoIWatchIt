# Watchmode identity and mapping-remediation readiness

**Date:** 2026-10-22  
**Status:** No canonical repair applied. No Watchmode API requests made. Live database unchanged.  
**Verdict:** The filtered identity repair is ready to request separate authorization. The mapping remediation passes its technical gates but remains blocked on a missing ticket/approval. The Watchmode recheck remains blocked on the missing plan-reset date.

---

## 1. Scope and safety posture

- Preserved the existing working tree and all pre-existing modifications/untracked files.
- Did not reset, stash, clean, discard, overwrite unrelated work, or commit.
- Did not expose any API key or other secret.
- Did not apply either canonical repair.
- Did not mutate `ProviderContent`, `ContentMapping`, `Offer`, `OfferEvidence`, or `OfferPrice` in the live database.
- Used isolated PostgreSQL databases for the test suite and restore/repair rehearsals. All scratch databases were dropped.

---

## 2. Offer verification

All **183** v1 historical Watchmode offers were checked. Result:

| Status | Count |
|---|---:|
| Verified | **179** |
| Unresolved | **4** |
| Contradicted | **0** |

### Verification methods

| Method | Offers |
|---|---:|
| Local JustWatch corroboration only | 3 |
| Local Streaming Availability corroboration only | 94 |
| Local JustWatch + Streaming Availability corroboration | 44 |
| TMDb watch/providers, exact provider per region | 36 |
| Provider page title match (Tubi, HBO Max) | 2 |
| **Verified total** | **179** |

The 141 local corroborations are active, same-provider, same-country, same-content-type, same-TMDb non-Watchmode offers. Evidence age is **3–18 days** (observed 2026-10-04 through 2026-10-19).

The 36 TMDb checks were exact provider checks for the specific region; there were 36 requests and 0 request errors.

Provider-page matches:

- Tubi offer `6217`: page title `Four Rooms`.
- Max offer `5535`: HBO Max page title `Superman`.

### Unresolved offers

| Offer | Provider / country | TMDb | Title | Evidence |
|---:|---|---:|---|---|
| 6038 | Netflix NL | 335 | *Once Upon a Time in the West* (1968) | TMDb NL does not list Netflix; `netflix.com/title/60031884` redirects to Netflix's `unsupportedbrowser` page. |
| 6081 | Netflix JP | 391713 | *Lady Bird* (2017) | TMDb JP does not list Netflix; `netflix.com/title/80205227` redirects to Netflix's `unsupportedbrowser` page. |
| 6082 | Netflix NL | 391713 | *Lady Bird* (2017) | TMDb NL does not list Netflix; same browser-gated Netflix URL. |
| 6090 | Max NL | 674324 | *The Banshees of Inisherin* (2022) | TMDb NL does not list Max; HBO Max show/movie and play.max.com show/movie URLs all return **404** (`Oops! | HBO Max`). |

These four are excluded from the filtered repair allowlist and must not receive new high-confidence mappings until independently resolved.

### Audit artifacts

- `docs/WATCHMODE_OFFER_VERIFICATION.csv` — 183-row per-offer inventory with evidence, status, and disposition.
- `docs/WATCHMODE_VERIFIED_OFFER_IDS.json` — 179 verified IDs, 4 unresolved IDs/reasons, method counts, and request counts.

---

## 3. Watchmode quota / recheck

- **Watchmode requests made: 0.**
- User-provided dashboard evidence: **0 / 2,500**, date 2026-10-22.
- Exact UTC observation time: **not supplied**.
- Plan reset date: **not supplied**.
- Local Watchmode ledger rows: **0**.
- Local Watchmode counter: **none** (the only counter is Streaming Availability, 2026-10-21, count 1).
- Pending Watchmode refresh requests: **0**.
- Running/pending `SyncRun`: **0**.

No Watchmode request is scheduled. The original 36-offer recheck plan is superseded for 36 offers by the independent TMDb checks. Only **3 title groups / 4 offers** remain candidates, and a v2 Watchmode check verifies only the Watchmode search result, not the provider's own record. It would not be sufficient on its own to resolve these four.

---

## 4. Filtered identity repair

The existing all-offer plan remains unchanged:

- Digest: `16cff63984bf9c48fd577c59ee9e3857db3d7a79ae06ea7f0bb116d15b2c487d`
- 7 collapsed rows, 141 content rows, 183 offers, 7 mappings moved, 134 created.

Do **not** use that digest for an apply: it would move the four unresolved offers.

The verification-filtered plan:

- **Digest:** `bd6123cab705c56f64baf5c9ea4112413268344c46e61872a90452201ff2b985`
- Offers moved: **179**
- Content rows created/updated: **138**
- Mappings moved: **6**
- Mappings created: **132**
- Rows retired: **5**
- Offers retained: **4**
- Rows retained: **2**
- Blocked rows: **0**

Retained rows:

| Row | Provider | Content id | Retained offers | Retained mappings | Reason |
|---:|---|---|---|---|---|
| 20014 | Max | `wm:387` | 6090 | none | Offer 6090 unverified. |
| 20489 | Netflix | `wm:203` | 6038, 6081, 6082 | 999 (TMDb 335) | Three unverified offers; mapping 999 belongs to the unresolved 335 title. |

Rows retired: Tubi `20490`, Hulu `20567`, Disney `20568`, Paramount `20569`, Pluto TV `20570`.

### Scratch rehearsal

A backup restore was used as an isolated scratch database. The filtered plan was applied there, not live:

- 179 offers split onto per-title identities.
- 4 unresolved offers remained on collapsed rows.
- 138 split content rows, 2 collapsed content rows.
- 138 split mappings, 1 collapsed mapping (mapping 999 on `wm:203`).
- 5 collapsed rows retired; 2 retained.
- Second filtered dry run: **blocked = 0**, **0 offers to move**, 2 retained rows only.
- Scratch database dropped.

The second dry-run digest in that end state was `a70e4491922f29cf19c5b4e66a5fc924ef6577995d7643e316b15b61f30ec79f`; it is the idempotent no-op state, not an apply plan.

### Apply readiness

A separate explicit authorization could be requested for the exact filtered digest, but none has been granted. The command must be run with the reviewed allowlist and workers paused:

```bash
python manage.py repair_watchmode_identity --apply \
  --expect-digest bd6123cab705c56f64baf5c9ea4112413268344c46e61872a90452201ff2b985 \
  --assume-workers-paused \
  --include-offer-ids-file docs/WATCHMODE_VERIFIED_OFFER_IDS.json
```

Operational caveat: the running backend/worker containers do **not** yet contain the filtered-plan code. The rehearsal used the reviewed working-tree code in a one-off container. An authorized apply must use exactly that reviewed code (or a deployment of it), not the running image.

This is a **partial** repair. It deliberately leaves two invalid collapsed rows for four unresolved offers. It is not a complete identity repair and does not make reconciliation clean for those four titles.

---

## 5. Mapping remediation

### Current manifest

- **Digest:** `sha256:24d890328a9243140c8a0e346b41573b96197c2c7fb5d7f1f1d5d4456a183018`
- Confirmation prefix: `24d890328a92`
- Schema: `remediate_mapping_audit/manifest-v3`
- Database: `postgresql:tracker`, cluster strength, cluster identifier `7689819453405220898`, configured `db:5432`, server `172.18.0.2:5432`.
- Target mappings: **6**
- Target offers: **20**
- Target evidence: **1**
- Target prices: **0**
- Protected mappings: **8**
- Local matcher fix: present
- Writer-barrier code: present
- Other advisory locks: **0**

Target review:

| Mapping | Provider / content | Current TMDb | Action |
|---:|---|---:|---|
| 399 | `bbc_iplayer/b00jd68z` series | 2316 *The Office* (2005) | Set to TMDb **2996** (UK *The Office*). |
| 400 | `bbc_iplayer/b04v89fn` movie | 10601 *Peter Pan* (2003) | Delete. |
| 386 | `bbc_iplayer/b06s2h64` movie | 12092 *Alice in Wonderland* (1951) | Delete. |
| 388 | `bbc_iplayer/m000c74q` movie | 527634 *Hansel and Gretel* (2007) | Delete. |
| 1 | `cbc_gem/drive` movie | 64690 *Drive* (2011) | Delete. |
| 4760 | `tubi/608226` movie | 1247 *The Good Shepherd* (2006) | Delete. |

Protected mappings reviewed and left out of scope:

`128` *Ocean's Eleven* (2001), `133` *The Wolf of Wall Street* (2013), `146` *Billy Elliot* (2000), `151` *The Blind Side* (2009), `419` *1900* (1976), `4369` *Fantastic Mr. Fox* (2009), `4375` *Poor Things* (2023), `4376` *Dumb and Dumber* (1994). None overlap the target IDs.

### Backup and restore drill

- Backup: `/private/var/folders/dg/jx2907jd1cx9q2dhyljb8fxm0000gn/T/wtf_backups/tracker-20261022T155046Z.dump`
- Size: 5,557,357 bytes (5.3 MB), mode `0600`, directory mode `0700`.
- SHA-256: `3530a372efe347aaf6c6471384869c0d153db2686afc6d2a2a0433032b0724c6`
- Restore destination: isolated scratch database, then dropped.
- Restore result: counts matched live for offers (10,795), provider content (15,574), mappings (4,506), evidence (12), prices (9), movies (827), migrations (72), Watchmode offers (183), collapsed rows (7), and Watchmode mappings (7).
- Orphans: mapping **0**, evidence **0**, price **0**.
- Offer-identity fingerprint matched exactly: `141dadcf2d7fa16f78fa00ea5275dab7`.
- One first restore attempt stopped before restore because of a shell path-variable error; the empty scratch database was dropped. The successful drill used a fresh scratch database. No live rows were touched.

### Writer isolation and topology

- Cross-connection PostgreSQL barrier tests passed:
  - Shared writer blocks the exclusive maintenance barrier.
  - Exclusive maintenance blocks a new writer.
  - Barrier acquisition and release work.
- Matcher and barrier sentinels are present in backend, worker, and catalogue-worker.
- Topology is direct to `db:5432`; no PgBouncer or transaction pooler is configured.
- Django's connection lifetime is the default (`CONN_MAX_AGE=0`); advisory locks are session locks acquired outside transactions and explicitly released.
- Source presence and `other advisory locks = 0` were treated as diagnostics only, not proof. The concurrency proof came from the cross-connection tests.

### Apply blocker

No ticket or approval has been supplied. The command requires:

```text
--confirm <ticket>@24d890328a92
```

Until that exact confirmation is provided, mapping remediation must not be applied. A fresh preflight/dry run and a current backup should be repeated immediately before any later apply.

---

## 6. Tests

| Suite | Result |
|---|---|
| `availability.tests.test_watchmode_identity` | **36 tests, OK** |
| Relevant availability/tracker/barrier modules | **172 tests, OK** |
| Full suite | **2,385 tests, OK** |

The 172-test subset included the PostgreSQL `PostgresBarrierIntegrationTests`.

---

## 7. Mutations and integrity

- Live canonical rows: unchanged.
- Live Watchmode offers: **183**, all still on the 7 collapsed rows.
- Live Watchmode mappings: **7**, including mapping 999 still on `wm:203` with TMDb 335.
- No scratch/test databases remain.
- No Watchmode requests; local ledger remains empty.
- No secrets in the verification artifacts.
- Our working-tree changes:
  - `backend/availability/services/watchmode_identity_repair.py` — filtered plan support, retained-row audit, idempotent rerun handling.
  - `backend/availability/management/commands/repair_watchmode_identity.py` — `--include-offer-ids-file`.
  - `backend/availability/tests/test_watchmode_identity.py` — filtered plan/apply/rerun regression tests.
  - `docs/WATCHMODE_OFFER_VERIFICATION.csv` — new evidence inventory.
  - `docs/WATCHMODE_VERIFIED_OFFER_IDS.json` — new allowlist/audit artifact.
  - `docs/WATCHMODE_REPAIR_READINESS_REPORT.md` — this report.
- All pre-existing modifications and untracked files were preserved.

---

## 8. Required next decisions

1. **Watchmode reset date and exact dashboard observation timestamp.** Provide both in UTC before any Watchmode request. No request is scheduled.
2. **Four unresolved offers.** Recommended: keep 6038, 6081, 6082, and 6090 excluded. Netflix pages are browser-gated; Max URLs are 404; TMDb disagrees with the v1 provider association. A selective v2 Watchmode check is possible but is not authoritative for provider metadata.
3. **Filtered identity apply.** To authorize, explicitly approve the exact digest `bd6123cab705c56f64baf5c9ea4112413268344c46e61872a90452201ff2b985`, with workers paused and a fresh backup. This is a partial repair leaving two collapsed rows.
4. **Mapping remediation apply.** Supply a ticket and explicit approval for `sha256:24d890328a9243140c8a0e346b41573b96197c2c7fb5d7f1f1d5d4456a183018` (confirmation prefix `24d890328a92`). Technical gates pass; approval is missing.
5. If either repair is later applied, repeat the dry run immediately beforehand and refuse on any digest change.
