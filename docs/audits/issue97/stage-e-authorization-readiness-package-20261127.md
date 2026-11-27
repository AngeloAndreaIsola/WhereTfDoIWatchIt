# Issue #97 Stage D/E Offline Authorization Package — Corrected Report

**Date:** 2026-11-27 UTC  
**Issue:** #97 ([P1] Close the real-world availability accuracy verification gap)  
**Status:** DRAFT APPROVAL PACKAGE / PENDING OWNER DECISION / NO EXECUTION AUTHORIZED  
**Base commit (this worktree and `origin/master` at time of writing):** `488654a22b1837acdd0acbcd810fc495449fc66d`

---

## Approval package (read this first)

**Exact scope — what this package asks the owner to decide, and nothing more:**
1. Decide whether to authorize a **bounded Stage D Batch 1** of exactly two typed NL title searches (`movie-238-nl` = The Godfather, 1972; `series-1396-nl` = Breaking Bad, 2008), against the single pinned `GetSearchTitles` document, with zero retries, one in-flight request, at least five seconds between sends, on a verified disposable PostgreSQL target. This is a **decision gate only**; no authorization is granted by this document.
2. Decide whether to authorize read-only first-party source-access reviews and, only after those reviews are complete, a separate exact-scope decision for **three anonymous single HTTPS GETs** (Netflix Inception document `E-C1`, Prime Video Inception document `E-C2`, Rakuten TV Dune document `E-C3`) plus **two bounded JustWatch attempts** (Stage E Batch A). Batch B remains a separate future decision.
3. Decide whether to retain issues #97 and #221 open while the draft PR is reviewed, and request a ChatGPT Web review of the package.

**Costs:** This offline authoring and verification session cost **$0.00** (native OpenCode route, see Identity below). Proposed execution ceilings are at most **12 Stage D title first-sends** and at most **3 + 2 Stage E Batch A sends**; these are planning ceilings, not spend authority. No paid request, Watchmode or Streaming Availability call is proposed. Actual upstream cost is **UNKNOWN** until the owner supplies a current quota/balance receipt.

**Risks:** (a) The historical 100 requests/day Streaming Availability API free-tier cap is **not a verified current quota class for JustWatch** — no current receipt exists; (b) 12 typed `Movie` identity rows and one current NL-search runtime receipt are **missing on every clean target tested** (13 blockers: 12 identities + 1 receipt); (c) no fresh, independently adjudicated comparison pair exists (0 pairs; accuracy **UNDEFINED**); (d) raw first-party captures and private manifests must stay out of Git; (e) the fixed runtime rejects Stage E batch caps and first-party scopes, so an integrated preflight needs a separately reviewed entry point; (f) one expired SigV4 presigned URL exists in restricted historical custody — no active credential was identified and no revocation is justified on that basis alone.

**Safeguards:** mocked transport only in offline rehearsals; disposable, isolated PostgreSQL with read-only transaction and disposable-identity guard; durable reservation before each send; serial sends, burst 1, zero retries/redirects; append-only ledgers; restricted owner-controlled custody for raw evidence; redaction of personal identifiers, cookies and signed URLs; explicit stop conditions on denial, challenge, rate-limit, clock failure or scope/identity mismatch. **No live provider, TMDb, first-party, playback, HMAC-grant, quota/scope-expansion, runtime-permission, production-write or deployment operation was performed or is authorized here.**

**Specific decisions required from the owner:** (1) approve or reject Stage D Batch 1 (first two scopes) under a separately signed HMAC manifest bound to a verified disposable target; (2) substantiate or withhold each of the 11 source-specific access/retention decisions (N1–N11, P1–P11, R1–R11) and nine cross-source decisions (X1–X9); (3) authorize or withhold Stage E Batch A exact scope after those reviews and a clean integrated preflight; (4) keep #97 and #221 open and request ChatGPT Web review. **Until all four are answered, every live path stays BLOCKED.**

---

## 0. Route, model identity, effort and cost

A fresh in-session identity probe was run with native OpenCode, exactly as instructed:

```sh
mkdir -p .scratch-issue97-recovery
opencode --version
opencode run --format json -m opencode/hy4-flash-free \
  'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>' \
  > .scratch-issue97-recovery/identity-probe.json
```

Observed values:

| Field | Value |
| --- | --- |
| Requested model | `opencode/hy4-flash-free` |
| Actual model in reply | `opencode/hy4-flash-free` (`MODEL_IDENTITY_OK opencode/hy4-flash-free`) |
| Provider | `opencode` (native OpenCode CLI, version `1.19.6`) |
| Effort | **MEDIUM, unpinned** — OpenCode exposes no reliable HIGH/effort flag; no effort override was set |
| Cost | `0` (probe JSON `step_finish.cost: 0`); session total **$0.00** |
| Scratch capture | `.scratch-issue97-recovery/identity-probe.json` in this worktree — **removed after capture** |

This corrects the prior attempts, which reported `omniroute/ag-gemini-3-flash` / `gemini-3-flash`. That route was **not used** for this corrected package.

---

## 1. The 12 missing typed title identities — six correction groups, exact entries

**Source of truth:** `docs/audits/issue97/stage-d-remaining-cohort.csv` (SHA-256 `f573eff980127bb08943ea2c101d8354c6f18a6fd659e24ca727ae0576354e30`) cross-referenced with `docs/audits/stage2b/identity_manifest_20261122.csv` (SHA-256 `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925`).

The 13-row cohort contains 12 admissible NL titles and one held row (`movie-220289-nl`, Coherence, `BLOCKED_YEAR_MISMATCH`: pilot year 2013 vs direct TMDb year 2014). The 12 admissible rows are grouped below into six exact entries (two titles per group). **Every cohort row is NL market.** The prior report's invented titles/markets (`movie-27205-nl` Inception NL, `movie-438631-au` Dune AU, `series-2316-gb` The Office GB, `movie-565310-ca` Parasite CA, `series-1438-nl` The Wire NL as a separate group) are **wrong**: 27205 and 438631 are the excluded canaries, 565310 is The Farewell (not Parasite), and all series rows are NL. All entries below are `VERIFIED_EXACT` except `series-2316-nl`, which is `VERIFIED_REVIEWED_ALIAS`.

| # | Exact scope_id · title · media · year · market | Retained SHA-256 (identity_evidence_sha256) · retained path | Confidence | Unresolved fields | Why live/source-authorized data is still required |
| --- | --- | --- | --- | --- | --- |
| 1a | `movie-238-nl` · The Godfather · movie · 1972 · NL | `f81b13ca6d16aa8029de1cfeb83c3bb02cfc691c4a4b3ebbd9f39f4ca8f8ee2b` · `docs/audits/stage2b/identity_manifest_20261122.csv` → `movie_238.json` (restricted raw payload not in cleaned checkout) | HIGH — `VERIFIED_EXACT` (TMDb ID/year/title) | Offer stream URI, time-of-check price, service entitlement | Retained evidence establishes work identity only, not current provider entitlement or offer freshness |
| 1b | `movie-496243-nl` · Parasite · movie · 2019 · NL | `2abf986aa2a804acf87a7261b737b2a53b63b0f2c64d955c4b233bb52efd5e1c` · same manifest → `movie_496243.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price, service entitlement | Same as 1a |
| 2a | `movie-603-nl` · The Matrix · movie · 1999 · NL | `56b9dac0a197019f736dacffbaf3c4e1cfb1eb817d580c282421b75ae3ae0597` · same manifest → `movie_603.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price | Same as 1a |
| 2b | `movie-619264-nl` · The Platform · movie · 2019 · NL | `ed42eb367dcde44e78a4e44cecf28f65dd0ddcbfb1b6fdc9131fc27a3de57200` · same manifest → `movie_619264.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price | Same as 1a |
| 3a | `movie-840430-nl` · The Holdovers · movie · 2023 · NL | `b4ba95858b37471f0c0821bf901f78346e189e8706e1bf0fb75014eb9d9a3db2` · same manifest → `movie_840430.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price; Stage E Batch B candidate E14/E15 | Same as 1a |
| 3b | `movie-565310-nl` · The Farewell · movie · 2019 · NL | `3ed2c0c878ca14e9def342178d1927f2bf32a9ee739fec69ec1258ba5a9a9004` · same manifest → `movie_565310.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price | Same as 1a. (The prior CA/Parasite claim is UNSOLVED_OFFLINE — unsupported.) |
| 4a | `movie-116149-nl` · Paddington · movie · 2014 · NL | `f6279b0663a8e62c1706f87f20c872030d0e9a8e7b94b71b711220b334ce4da1` · same manifest → `movie_116149.json` | HIGH — `VERIFIED_EXACT` | Offer stream URI, time-of-check price | Same as 1a |
| 4b | `series-1396-nl` · Breaking Bad · series · 2008 · NL | `7685411f05c8b2389ac7379f72b03912c5b3ce49f36b3a21c9e61fd6674c1f14` · same manifest → `series_1396.json` | HIGH — `VERIFIED_EXACT` | Season/episode availability matrix; exact offer stream URI; Stage E Batch B candidate E10 | Season-level `GetTitleNode` is outside the reviewed title-only scope; entitlement needs live source |
| 5a | `series-70523-nl` · Dark · series · 2017 · NL | `e029ca87bbb50a427bc545986c4e60a39f7301debbdc1d9f97973a9f3ae949f9` · same manifest → `series_70523.json` | HIGH — `VERIFIED_EXACT` | Season node tree; exact offer URI; Stage E Batch B candidate E11 | Same as 4b |
| 5b | `series-66732-nl` · Stranger Things · series · 2016 · NL | `444e7addf56f5fea77d732bda4a0952b70e5ad67aa404987125e0b3ffb8fa220` · same manifest → `series_66732.json` | HIGH — `VERIFIED_EXACT` | Season node tree; exact offer URI; Stage E Batch B candidate E12 | Same as 4b |
| 6a | `series-2316-nl` · The Office (US) · series · 2005 · NL — canonical title `The Office`, reviewed alias | `02188370e858933c0624382abf16edae9c5f7c0dd4a52586024a1ca8fa2af278` · same manifest → `series_2316.json` | MEDIUM — `VERIFIED_REVIEWED_ALIAS`; alias decision at `docs/audits/stage2b/issue97-identity-decisions.md` | UK original/edition disambiguation; season tree; exact offer URI | The alias applies only to this typed identity; the UK edition and BBC programme ID are out of scope and require a separate reviewed decision |
| 6b | `series-1438-nl` · The Wire · series · 2002 · NL | `b15d8b5538d446f7794eb59c3baa66f8658efcafa709de37f5225e45f3bb62e4` · same manifest → `series_1438.json` | HIGH — `VERIFIED_EXACT` | Season node tree; exact offer URI | Same as 4b |

**Held row (not one of the 12):** `movie-220289-nl` · Coherence · 2013 pilot vs 2014 TMDb · SHA `e33f4aba5e64cb5de4cb23285bacaa107d962f5f9ef6b705abc40f0fd188eaaa` · `BLOCKED_YEAR_MISMATCH`. **UNSOLVED_OFFLINE** — a reviewed year decision with evidence is required before it can enter any signed allowlist.

**Identity conclusion:** work identity is established offline for all 12 rows; **provider entitlement, offer freshness, price, stream URI and playback verdicts are UNSOLVED_OFFLINE** without separately authorized live/source evidence. No identity was invented. The raw TMDb payloads are deliberately absent from the cleaned checkout (`scripts/test_issue97_stage_d.py` skips one test for this reason).

---

## 2. Missing natural-language-search runtime receipt

- **Existence in retained evidence:** **NOT PRESENT.** No current, enabled JustWatch NL-search-only runtime receipt exists in the archive. Multiple independent reports agree: `stage-d-runtime-authorization-verification.md` (batch-1 preflight returned `readiness=blocked`, 13 blockers), `stage-d-offline-verification.md` (same), and `stage-de-independent-readiness-review.md` (both disposable targets returned `readiness=blocked`, no authorization scope).
- **What is missing:** enabled source configuration receipt for operation `search` / `GetSearchTitles`, country NL only, with a valid active time window, reviewed reviewer/reference, endpoint binding, per-scope attempt ceiling, and reconciled quota/spend. The historical canary provides no such receipt.
- **Bounded reproduction when separately authorized (NOT RUN in this session):**
  ```sh
  # read-only preflight on a disposable, isolated PostgreSQL target
  # (commands retained in docs/audits/issue97/stage-d-offline-verification.md §Local reproduction)
  docker run --pull never --rm --network wtdw97-stage-d-test-net \
    --mount "type=bind,source=$PWD,target=/repo,readonly" --workdir /repo/backend \
    -e WTDWI_DISPOSABLE_TEST_DB=1 -e DJANGO_SECRET_KEY=wtdwi-test-only \
    --entrypoint sh wtdwi-stage-d-backend-test:latest -ec '
    PGOPTIONS="-c default_transaction_read_only=on" AVAILABILITY_STAGE_D_DISPOSABLE=1 \
      python manage.py issue97_stage_d --batch 1'
  ```
- **Gate:** receipt must be verified again on the actual retained disposable target immediately before any authorization decision. No live search was run here.

---

## 3. Disposable-target and quota preflights

- **Target isolation (offline rehearsals only):** unique internal Docker network, tmpfs PostgreSQL `wtdwi_test`, read-only source mount, `WTDWI_DISPOSABLE_TEST_DB=1` identity guard, database-default read-only transaction for preflight. The two independent review clusters were destroyed after verification.
- **Quota:** the project constraint is the Streaming Availability API (movieofthenight via RapidAPI) **100 requests/day free tier**. That cap applies to Streaming Availability lookups via the Postgres cache (`last_checked_at`), **not** a verified current JustWatch quota. No current JustWatch quota class, balance, rate policy or cooldown receipt exists. `tracker.services.rate_limit` publishes Streaming Availability/Watchmode limits only; it declares no JustWatch plan limit.
- **Planning ceilings:** Stage D title-only run = 12 first-sends, zero retries; with separately approved seasons = 17 cumulative; all 13 after Coherence review = 18 cumulative. Stage E Batch A = 3 first-party GETs + 2 JustWatch attempts, zero retries. All are study ceilings, not permission.
- **Offline rehearsal accounting (captured this session):** one independent connection observed a committed reservation before handoff; spent reservations without responses stayed `transmission unknown`; denied/challenged/rate-limited responses were charged once with zero retries. See §6.
- **Safeguards:** durable reservation before send, append-only ledgers, serial single in-flight request, burst 1, ≥5 s gap (stricter reviewed pace wins), no redirects/implicit HTTP retries/browser/fallback, stop on denial/challenge/rate-limit/clock/identity mismatch, cooldown honored (`Retry-After` fixture persisted 901 s in the retained test).

---

## 4. Bounded Stage D Batch 1 scope

- **Exact Batch 1 scope:** only the first two ordered scopes, `movie-238-nl` (The Godfather, 1972) and `series-1396-nl` (Breaking Bad, 2008), TMDb IDs 238 and 1396, media types `movie`/`series`, market NL, operation `GetSearchTitles` only. No season operations. Coherence and all other cohort rows are outside Batch 1.
- **Batch 1 signing gate:** a separate owner-signed HMAC authorization token bound to the exact disposable target identity, prior checkpoints digest, manifest digest, evidence digest, receipt digest, and valid expiry. The offline suite verifies this gate matches the ordered policy (`test_report_batch1_signing_scope_matches_ordered_policy`, passed this session).
- **Pinned document:** `GetSearchTitles` GraphQL document SHA-256 `997e0b1f6ce3bafba0fd722cf62936762a2dfd5d0c2ad3305ad9a382da373f51`; manifest digest `d7308681ae99376352eeedf9d271e63fa2a6e02e63a6e801d7e9b6d749fb8727` (both from retained independent review).
- **Comparison windows:** historical retained snapshot (November 23 2026 `[15:00,16:00) UTC` observation bounds and November 24 index observations) vs. a fresh, separately authorized live window with an absolute half-open 30-minute UTC slot per title and ≤10-minute expanded span.
- **Independent evidence protocol:** typed identity binding (native node ID, media type, exact title/alias, year, NL market), canonical service and offer-class separation, original observation clocks preserved, append-only attempt ordinals and digests, restricted custody of raw payloads.
- **Success criteria (to be applied only if authorized):** unambiguous native match per scope, **zero unauthorized or out-of-scope network egress**, zero unhandled exceptions, strict priority ranking (audio language match → subtitle match → quality 4K/HDR), and a clean reconciled ledger. **Zero unauthorized or out-of-scope network egress** means that when live execution is separately authorized, only requests matching the signed, pinned scope, endpoint, target, quota and ledger may execute; the batch itself may include up to two allowlisted live searches, but no other request is permitted. This document grants no such authorization. **Current status: not executed; criteria unmet.**
- **Failure/stop criteria:** any unmocked egress, schema/identity mismatch, conflicting territory, redirect, challenge, HTTP 401/403/429/503, rate limit, clock failure or accounting mismatch stops the batch after at most one charged send.
- **Unknowns:** live entitlement, playback, price, exact latency and accuracy remain **UNKNOWN / UNDEFINED** until live authorization and fresh adjudication exist.

---

## 5. Stage E source-access and retention requirements (Netflix / Prime Video / Rakuten TV)

The offline preparation is complete but **live authorization is BLOCKED**; the checklist has **33 source-specific entries + 9 cross-source entries = 42**, of which **0 of 3 source decisions are complete**. Frozen NL Batch A panel: nine candidates across three native documents (matrix SHA-256 `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d`).

| Source | Document / candidates | Required source decisions | Offline disposition |
| --- | --- | --- | --- |
| Netflix | `E-C1` Inception (27205, 2010) — candidates E01 subscription, E10–E12 Batch B series | N1 one anonymous HTTPS GET; N2 legitimate NL context; N3 typed identity (top-level video ID, work type, title); N4 extraction conditions + subscription/plan signal; N5 raw retention + deletion deadline; N6 sanitized publication fields; N7 private allowlist (origin/path/ID/method/format); N8 source allowance/pacing/cooldown; N9 UTC slot + clock; N10 custody/readers/deletion custodian; N11 separate owner approval | Pending; historical access supports feasibility only |
| Prime Video | `E-C2` Inception (27205, 2010) — E02 subscription, E03 rent, E04 buy, E05 addon (HBO Max under Prime), E08 free control | P1–P11 analogous; P4 requires validating base Prime benefit, rent and buy actions and the separate channel benefit on the same GTI; E08 requires an explicit full-film zero-payment offer without membership or ads | Pending |
| Rakuten TV | `E-C3` Dune (438631, 2021) — E06 rent, E07 buy, E09 free-with-ads control | R1–R11 analogous; R3 binds the 2021 Villeneuve film; E09 requires an explicit full-film zero-payment ad-funded offer without subscription | Pending |

**Cross-source gates X1–X9:** bind signed scope to the private allowlist digest; record frozen matrix digest; record signed scoped manifest digest; verify exact runner/app revisions, NL egress and disposable DB identity with integrated preflight; identify owner/operator/reviewer/custodian; bind expiry and one absolute half-open 30-minute UTC slot per title with clock offset uncertainty ≤5 s and span ≤10 min; verify serial accounting and ≥3 s between first-party sends; bind ceilings 3 GETs + 2 JustWatch attempts with zero retries/redirects/paid requests; verify current JustWatch runtime receipt, endpoint/operation binding, quota/accounting and clean read-only preflight. **All pending.**

**Retention/redaction:** seal originals and raw manifests in restricted owner-controlled storage (the historical canary custody model uses mode 0700 directory / 0600 files at `~/.hermes/private/issue97-canary-ff15058/`, per `docs/audits/issue97/evidence-custody-verification.md`); record digest, size, timestamps and redacted scope; publish only approved sanitized IDs, counts, structured outcomes and hashes; keep raw documents, cookies/headers, signed links, private identifiers and private path inventories out of Git and public handoffs. Owner approval cannot substitute for missing source evidence.

**Correction to the prior report's revocation/cleanup citation:** the prior report cited `ops/deployment/recover_database.py` as an "automated purge script". That file **exists but is a separately approved database restore-recovery tool** ("retain the failed database, never drop it"), **not** a token-revocation or evidence-purge script. The retained cleanup artefact is the **custody record and forward-tip cleanup at `88c8e25182a1f1b0700830e9b65685313f55a89a` / `cleanup/97-evidence-tip-cleanup`**, documented in `docs/audits/issue97/evidence-custody-verification.md` and `docs/audits/issue97/swarm-reports/integration-final-review-a5.md` — and that cleanup does **not** delete restricted files. **No automated token-revocation/purge script exists in this checkout; one still needs to be written and owner-authorized before any credentials are issued.** No chmod/permission change was performed.

---

## 6. Mocked / offline rehearsal results — honestly marked

All commands below were run **in this session** on the local worktree; outputs are quoted. Nothing here is a live provider call.

| Check | Exact command | Result from this session |
| --- | --- | --- |
| Fresh model identity probe | `opencode run --format json -m opencode/hy4-flash-free 'Reply with exactly: MODEL_IDENTITY_OK <your actual model id>'` | JSON text part: `MODEL_IDENTITY_OK opencode/hy4-flash-free`; `step_finish.cost: 0` |
| Stage E provenance/scope audit | `python3 -BE scripts/audit_issue97_stage_e.py` | `CSV PASS: 15 candidates (A=9, B=6), 13 accepted observations, 7 Stage C pairs, 2 controls`; `Scope PASS: all 15 candidates resolve to 8 distinct historical native documents (A=3, B=5)`; `Fresh adjudicated pairs: 0. Accuracy metrics: UNDEFINED.`; candidate matrix SHA `ac1d37ec…a4891d`. Exit 0 |
| Stage E Batch A rehearsal module | `STAGE_E_TEST_IMAGE=wtdw-issue97-offline:latest sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal` | `Ran 11 tests in 2.250s` — `OK` |
| Stage E full offline rehearsal | `STAGE_E_TEST_IMAGE=wtdw-issue97-offline:latest sh scripts/rehearse_issue97_stage_e.sh availability.tests.test_issue97_stage_e_rehearsal availability.tests.test_experiment_runner.GuardedTransportTests availability.tests.test_experiment_runner.PostgreSQLReservationTests availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked availability.tests.test_justwatch_reviewed_transport availability.tests.test_issue97_collection tracker.tests.test_issue97_availability_contract` | `Ran 60 tests in 5.463s` — `OK` |
| Stage D cohort/manifest suite | `PYTHONPATH="$PWD/backend" python3 -B scripts/test_issue97_stage_d.py -v` | `Ran 7 tests in 0.005s` — `OK (skipped=1)`; skip = `Pass --tmdb-evidence-dir for the retained raw responses` |
| Shell syntax | `sh -n scripts/rehearse_issue97_stage_e.sh` | exit 0 |
| Git whitespace | `git diff --check 488654a22b1837acdd0acbcd810fc495449fc66d HEAD` | **exit 2**: three trailing-whitespace findings on the added file's Markdown hard-break header lines 3–5 (`**Date…**`, `**Issue…**`, `**Status…**`, each ending in two spaces). The earlier "exit 0" claim was inaccurate and is corrected here. `git diff --check HEAD^ HEAD` and `git diff --check` (working tree) both exit 0 because they do not re-check those added lines. The trailing double spaces are intentional Markdown hard breaks, not code whitespace; they are left unchanged so no content other than this correction is altered. |

**Explicitly NOT RUN in this session (do not treat as verified):**
- Full Django 91-test suite from `stage-d-offline-verification.md`: attempted with `docker run … --network none … python manage.py test --noinput -v 1 <5 modules>` → `CommandError: Movie fixtures and database tests require the disposable WTDWI PostgreSQL environment. Use scripts/test-backend-isolated.sh or the dedicated db-test service.` Status: **NOT RUN** (requires the dedicated disposable DB stack). The retained report's "91 passed" is a historical claim, not a result of this session.
- The prior report's **"47 tests passed"** and **"109 allowlisted files"**: **NOT RUN / unsupported**. No command in this session produced either number. They are removed.
- All live provider / TMDb / first-party / playback paths: **NOT RUN — forbidden without owner authorization**.

**Historical context (not re-verified here):** `stage-e-offline-rehearsal.md` reports 57 passed for its full local command; `stage-d-offline-verification.md` reports 91 Django tests. These are retained claims from earlier dispatches, reproduced here only as citations, not as this session's results.

---

## 7. Forbidden operations and risk statement

**FORBIDDEN — none performed and none authorized here:** live provider requests; first-party evidence capture against live endpoints; playback checks or automated browser driving for streaming validation; live TMDb API calls; HMAC grant generation; quota or scope expansion; runtime permission or configuration changes; production database writes; deployments; branch cleanup; PR merges/closures; issue closures; chmod/permission changes; credential rotation or revocation. Issue #97 and PR #221 remain open; all targeted PRs remain in their existing state (no merge, no closure).

**Residual risks:** unverified current quota/rate receipt; missing typed identity rows and runtime receipt on clean targets; zero fresh adjudicated pairs (accuracy UNDEFINED); restricted raw evidence in historical custody including one expired presigned URL; fixed runtime rejects Stage E caps/scopes. Safeguards listed in the approval package mitigate but do not eliminate these; the owner must accept them explicitly.

**Hosted CI status for this documentation package:** [GitHub Actions run 46000269064](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/46000269064) completed successfully — 3 of 3 jobs succeeded (Backend Tests, Frontend Lint/Build/Test, Browser Extension Build/Typecheck/Tests), and each job executed its steps. Earlier retained CI chronology: head 88a87af with run 45977744444 (historical pre-repair review), head 05a3f1c with run 45993985854 (first repair), head af9f70a with run 45996466451 (continuation). The current PR head is recorded in GitHub pull-request metadata and is intentionally not restated in this file to avoid self-reference. Offline preparation results remain separate from hosted CI and historical test claims.

---

## 8. Exact owner decisions required

1. **Stage D Batch 1:** approve or reject live execution of exactly `movie-238-nl` and `series-1396-nl` (2 of the 12 planning slots, 0 retries), only after a fresh signed HMAC manifest, verified disposable target, current NL-search receipt and reconciled quota are supplied.
2. **Stage E source reviews:** substantiate or withhold each N1–N11 / P1–P11 / R1–R11 source decision and X1–X9 cross-source decision; reduce the panel explicitly if any source review cannot be substantiated.
3. **Stage E Batch A:** after (2) and a clean integrated preflight, sign or withhold the exact 3-GET + 2-attempt scope with UTC slots, custody, retention and deletion dates. Batch B needs a separate decision.
4. **Review disposition:** keep #97 and #221 open; accept this corrected report as the authorization package for ChatGPT Web review; do not merge, close, clean up or change permissions.

---

## 9. GitHub verification evidence (verified this session)

Publication and verification were performed with `gh api`. Marker used: `VERIFICATION_MARKER_ISSUE97_AUTH_PACKAGE_20261127_HY4`.

**Draft PR verification — exact command and result:**
```sh
gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/pulls/228 \
  --jq '{number,state,draft,base_sha:.base.sha,head_sha:.head.sha,url:.html_url}'
# {"base_sha":"488654a22b1837acdd0acbcd810fc495449fc66d","draft":true,
#  "head_sha":"812502e7529a35765b363ee234ed37b6a9d49273","number":228,
#  "state":"open","url":"https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/228"}
```

**Issue #97 comment verification — exact command and result:**
```sh
gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97/comments --paginate \
  --jq '.[].body' | grep -c 'VERIFICATION_MARKER_ISSUE97_AUTH_PACKAGE_20261127_HY4'
# 1
```
Comment URL: `https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97#issuecomment-7215247968` (id `7215247968`).

**All comment locations verified:**
```sh
for n in 97 206 208 209 212 213 214 216 217 221; do
  c=$(gh api repos/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/$n/comments --paginate \
      --jq '.[].body' | grep -c 'VERIFICATION_MARKER_ISSUE97_AUTH_PACKAGE_20261127_HY4')
  echo "issue/PR #$n marker_count=$c"
done
# issue/PR #97 marker_count=1
# issue/PR #206 marker_count=1
# issue/PR #208 marker_count=1
# issue/PR #209 marker_count=1
# issue/PR #212 marker_count=1
# issue/PR #213 marker_count=1
# issue/PR #214 marker_count=1
# issue/PR #216 marker_count=1
# issue/PR #217 marker_count=1
# issue/PR #221 marker_count=1
```

Posted comment IDs: #97 `7215247968`; #206 `7215248328`; #208 `7215248606`; #209 `7215248965`; #212 `7215249236`; #213 `7215249529`; #214 `7215249848`; #216 `7215250156`; #217 `7215250407`; #221 `7215250733`.

**State check:** PR #228 is `open`, `draft: true`; issue #97 and PR #221 remain open. No merge, closure, cleanup or permission change was made. The head SHA above (`812502e…`) is the publication commit; any later evidence-only commit to this report advances the head and is recorded in the PR body.
