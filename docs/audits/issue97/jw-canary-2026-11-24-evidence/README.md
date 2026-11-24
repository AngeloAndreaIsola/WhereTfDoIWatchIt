# Issue #97 — R2b Notte canary evidence package (`JW-CANARY-v1b`, 2026-11-24)

**Verdict: SANITIZED EVIDENCE PREPARED — `READY_FOR_CHATGPT_REVIEW`.** This PR is a review
artifact only. It does not close #97, does not prove availability accuracy, contains no
provider calls, and changes no collection code, runtime permission check, production data
or other agent's branch. Metrics class is `MEASURED_AGAINST_UNVERIFIED_SOURCE` — never
`PROVEN`. Every collected record carries `SAME_SOURCE_ECHO` and `UNLICENSED`.

Base: `origin/master` `c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7` (fresh isolated worktree).
The uncommitted edit to `docs/operations/issue97_justwatch_collection_preflight.md` and the
untracked `runs/`, `spec/`, `docs/source-permission/` trees present in the main worktree were
observed, left untouched, and are **not** part of this PR; all source facts below come from
the committed base plus byte-preserved hashes of those uncommitted evidence files.

## 1. Acceptance items — measured status

| # | Item | Status | Measured result |
|---|---|---|---|
| 1 | Inspect run dir, manifest and Notte specs | **PASS** | 22 evidence files inventoried with SHA-256 + byte counts in `manifest.json`; 2 source specs copied byte-identically; 1 redacted derivative. |
| 2 | Checkpoint continuity | **PARTIAL** | batches 1→2→3 consistent with `raw_ref`s; failed batch-01 retained; batch-03 `retry_of=batch-01`; counts 0/27/29. Numbering namespace conflict noted as D4. |
| 3 | Function versions | **PARTIAL** | event `v20261124_135310`→`v20261124_150434` agrees across both manifests; two downloaded sources are byte-identical (`b512a7be…`), so `source_hash_unchanged` is locally verifiable. Deployed artifact hash and 4 omitted versions are not evidenced (D3, M6). |
| 4 | Source hashes | **PARTIAL** | spec YAML, both source-code copies and both manifests reproduce exactly (22/22 inventory hashes). `params_hash`, `show_json_sha256`, `previous_spec_sha256`, `tool_list_schema_sha256` have no retained payload/serialization and are **not reproducible** (D2). |
| 5 | Call accounting | **PARTIAL** | Notte invocations measured exactly: 3/6 collection, 1/3 probes. Underlying JustWatch HTTP attempts have **no ledger**; only a static lower bound (D5). |
| 6 | Typed title identities | **PARTIAL** | within each batch, title / object_type / release_year / JustWatch id are internally consistent (one value each). TMDb 27205 and 438631 are **not present** in any retained record, so the TMDb↔JustWatch link is asserted, not evidenced (D6). |
| 7 | Reproduce totals and dedup offline | **PARTIAL** | all headline totals reproduce (56 = 27+29, 0 rejects, 0 extended-key duplicates, 22 literal-key collapses). `summary.md` provider table is drawn from the 34-row literal-dedup set, not the 56-row set (D1). |
| 8 | Sanitized reproducible package | **PASS** | no API keys, presigned URLs, session/run UUIDs or raw provider payloads in the PR; source hashes and provenance preserved; offline verifier + synthetic fixture included. |
| 9 | Draft PR, no merge/close | **PASS** | draft PR from this branch against `master`; #97 left open and untouched. |

Status vocabulary: **PASS | PARTIAL | FAIL | BLOCKED**. No item is claimed `PASS` where a
supporting artifact was not re-measured.

## 2. Reproduced counts

| Metric | Reported | Independently measured | Match |
|---|---:|---:|---|
| Dune / Inception per-title records | 27 / 29 | 27 / 29 | yes |
| Raw records / offers | 56 / 56 | 56 / 56 | yes |
| Rejects | 0 | 0 (`[]`) | yes |
| Extended-key duplicates removed | 0 | 0 (56 kept) | yes |
| Literal-brief-key collapses | 22 | 22 (56 → 34) | yes |
| Country normalizations | 56 | 56, all `nl`→`NL`, field `country` | yes |
| Near-duplicate price groups | 0 | 0 | yes |
| `audio_languages = ['UNKNOWN']` | 2 | 2 | yes |
| `subtitle_languages = ['UNKNOWN']` | 2 | 2 | yes |
| Countries / object types | NL / movie | `{'NL': 56}` / `{'movie': 56}` | yes |
| `available = true` | — | 56/56 | measured |
| Monetization mix | — | buy 20, rent 20, subscription 16, free 0 | measured |
| Source labels | both on all rows | one distinct set on all 56 | yes |
| `offers.json` = normalized raw results | not stated | **true** as a multiset | measured |

The raw→offers derivation verified offline is: uppercase `country` (`nl`→`NL`), append flags
`country_uppercased`, `SAME_SOURCE_ECHO`, `UNLICENSED`, add `source_labels`, and wrap the
scalar language sentinel `"UNKNOWN"` as `['UNKNOWN']`. The last step is not recorded in the
run manifest (D7). All 56 rows match after this transform; record order differs
(`offers.json` starts with Inception, raw concatenation starts with Dune), which is why the
verifier compares multisets, not list equality.

### Provider counts — the source of D1

| Provider | Full 56-row set | Literal-dedup 34-row set | `summary.md` |
|---|---:|---:|---:|
| Amazon Video | 12 | 6 | 6 |
| Apple TV Store | 12 | 6 | 6 |
| Rakuten TV | 10 | 6 | 6 |
| Pathé Thuis | 4 | 4 | 4 |
| HBO Max | 6 | 3 | 3 |
| HBO Max Amazon Channel | 6 | 3 | 3 |
| Amazon Prime Video | 2 | 2 | 2 |
| Netflix | 2 | 2 | 2 |
| meJane | 2 | 2 | 2 |

`summary.md` headlines 56 records but its provider table is exactly the 34-row literal-key
dedup view. Both views are reproducible; the table is simply mislabelled by its own context.

### Typed identity (per batch, all 56 records)

| Batch | Title | object_type | release_year | JustWatch id | country (raw → merged) |
|---|---|---|---:|---|---|
| 2 (Dune) | Dune — 1 distinct value | movie — 1 | 2021 — 1 | `tm305113` — 1 | `nl` → `NL` |
| 3 (Inception retry) | Inception — 1 distinct value | movie — 1 | 2010 — 1 | `tm92641` — 1 | `nl` → `NL` |

These are JustWatch-internal content ids, **not** TMDb ids. No field named `tmdb_id` exists
in `offers.json`, the raw call results, the batch files or the validation log.

## 3. Notte invocations vs JustWatch HTTP attempts

| Layer | Measured | Budget | Evidence |
|---|---:|---:|---|
| Notte validation function invocations | 1 | 3 | `probe-01.json`: invalid `reference_market=nl`, HTTP 200 / status `failed`, parameter error before any browser session per the decision record. |
| Notte collection function invocations | 3 | 6 | `call-01` (failed import), `call-02` (closed, Dune), `call-03` (closed, Inception retry). Failed attempts count as spent. |
| **Total Notte invocations** | **4** | 9 across both buckets | Distinct from HTTP attempts below. |
| JustWatch HTTP attempts | **not ledgered** | owner's #97 proposal: 25 low-level attempts | No per-request ledger, response-status record or rate-limit header was retained. Static reading of `spec/fe1b97b6.py` shows, per successful invocation: 1 US search-page navigation, 1 US discovery-page navigation, 1 NL market-page navigation, 1 `apis.justwatch.com/graphql` POST inside `query_offers()` — **≥4**, with a retry loop and an embedded-data fallback that may add more. Two successful calls ⇒ **≥8 static lower bound, NOT MEASURED**. The probe and the import-error call made 0 by code position. |

Do not equate "3 of 6 collection calls" with the 25-attempt low-level ceiling in #97: they
are different units. The exact JustWatch attempt count is **Not proven**.

## 4. Discrepancies

| ID | Severity | Finding |
|---|---|---|
| D1 | medium | `summary.md` provider breakdown is computed on the 34-row literal-dedup set while its totals are on 56 rows (§2). No row was lost; the table is mis-scoped. |
| D2 | high | `params_hash d4c2880e…`, `show_json_sha256 123aa900…` and `previous_spec_sha256 05449a1c…` cannot be reproduced. An exhaustive scan hashed every retained file as raw bytes and every JSON object with four serializations (compact/sorted, spaced/sorted, indent-2/sorted, `str()`) — zero matches. `tool_list_schema_sha256 0e688edd…` likewise has no retained payload. These are **missing artifacts**, not evidence of a wrong hash. |
| D3 | low | `function_show_redeploy.json` lists six versions (`…135051`, `…135131`, `…135152`, `…135310`, `…150411`, `…150434`); the version event names only `…135310` and `…150434`, omitting `…150411`. |
| D4 | low | `coverage.json.attempt_history` numbers Inception attempts 1 and 3 (global call index), while `batch-03.json.attempt` is 2 (per-title retry index). Both describe the same retry; the namespaces are unlabelled. |
| D5 | medium | No JustWatch HTTP attempt ledger; the ≥8 figure is a static lower bound only. |
| D6 | medium | TMDb ids 27205 / 438631 in the task context do not appear in any retained record; the title/type/year/JW-id chain is self-consistent but the TMDb↔JW mapping is unverified. |
| D7 | low | `offers.json` wraps scalar `"UNKNOWN"` language sentinels into `['UNKNOWN']` for 2 rows; this derivation is not listed in the run manifest's deviations. Schema-valid (array branch), but undocumented. |
| D8 | info | `version_event.detected_at` (15:36:40.818929Z) is ~14.6 s **after** the retry call's `at` (15:36:26.263905Z); the event was recorded at report-generation time, not before the retry. Redeploy `updated_at` (15:04:34.589983Z) precedes the successful Dune call (15:05:03.492511Z). |

## 5. Missing evidence / blocked claims

1. Bytes that produced `show_json_sha256`, `previous_spec_sha256`, `tool_list_schema_sha256`, and the exact serialization that produced `params_hash` (D2).
2. Per-request JustWatch ledger: URLs/methods, statuses, retry attempts, rate-limit headers, actual attempt total (D5).
3. TMDb identity export tying 27205→Inception(2010)/`tm92641` and 438631→Dune(2021)/`tm305113` (D6).
4. Deployed function source artifact hash. The only remote handle is a presigned S3 URL inside `function_show_redeploy.json`; it is redacted here, and no content hash was retained. `source_hash_unchanged` is verified only between the two locally downloaded copies.
5. Independent first-party comparator, entitlement or playback observation — none exists, so no accuracy rate is computable and none is claimed.
6. Raw provider payloads are deliberately withheld from the PR (see §6); they remain available to the owner at the paths in `manifest.json` with hashes preserved.

## 6. Sanitization

**Included:** this report; `manifest.json` (source inventory with hashes/byte counts, hashes,
budgets, checkpoints, expected metrics, discrepancies); `verify_evidence.py`; a fully
synthetic fixture; byte-identical copies of the function spec and source code (own code,
no credentials); a redacted function-show derivative with `url` removed.

**Excluded:** `offers.json`, `offers.csv`, `call-01/02/03` raw results (unlicensed provider
payloads); `probe-01.json` and raw call headers (invocation metadata / identifiers);
`function_show_redeploy.json` raw presigned URL; per-record rows of `validation_log.json`.
No API keys appeared in any retained file: headers carry `Bearer ***` / a `<NOTTE...>`
placeholder, and a regex sweep found the only credential-shaped artifact was the presigned
URL. Run/session UUIDs are represented as SHA-256 prefixes where provenance needs
correlation; the published function id is a public, non-credential identifier.

## 7. Reproduction commands

```bash
BASE=c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7
git fetch origin && git rev-parse origin/master          # $BASE

# Synthetic self-test — no raw evidence, no network, no DB:
python3 docs/audits/issue97/jw-canary-2026-11-24-evidence/verify_evidence.py --self-test

# Full offline verification — owner supplies the private evidence root containing
# runs/jw-canary-2026-11-24/ and spec/. Reads only; writes nothing.
python3 docs/audits/issue97/jw-canary-2026-11-24-evidence/verify_evidence.py \
  --manifest docs/audits/issue97/jw-canary-2026-11-24-evidence/manifest.json \
  --evidence-root /path/to/private/evidence/root

# Package integrity:
cd docs/audits/issue97/jw-canary-2026-11-24-evidence && shasum -a 256 -c SHA256SUMS
```

The verifier uses only the Python 3 standard library, makes no HTTP/Django/database calls,
and prints aggregates only (no raw records, headers or identifiers).

## 8. Local verification actually executed

- Synthetic self-test: **PASS**, 7/7 checks.
- Full offline run against the private evidence root: **PASS**, 22/22 source-file SHA-256 +
  byte-size checks and 27/27 derived-metric/checkpoint checks; exit code 0.
- No CI claim is made. CI state, if any, must be read live from the PR; a local PASS is not a
  GitHub Actions PASS.
- `claude-roast` (required by repo `CLAUDE.md`) is not installed in this environment, so no
  prompt score was fabricated.

## 9. Handoff

Prepared by Hermes as evidence packaging only. Not approved, merged or closed by this agent;
#97 was not edited. ChatGPT Web remains the sole final reviewer/merger. Required decision
from review: accept D1–D7 as documentation corrections to the originating run, and decide
whether M1–M5 must be supplied before #97 can move.
