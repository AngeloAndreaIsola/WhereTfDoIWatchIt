# Issue #97 — R2b Notte canary evidence package (`JW-CANARY-v1b`, 2026-11-24) — Stage A update

**Verdict: Stage A complete. `READY_FOR_CHATGPT_REVIEW`.** This PR updates the original
evidence package in place; it is still a draft, and #97 remains open and untouched. No
provider calls, database access, deployment, merge or runtime-permission change occurred.
Metrics class stays `MEASURED_AGAINST_UNVERIFIED_SOURCE` — never `PROVEN`. All 56 offers
carry `SAME_SOURCE_ECHO` and `UNLICENSED`.

Base: `origin/master` `c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7`. Original failed batch and
all checkpoints remain preserved by hash and metadata; raw unlicensed payloads remain
outside the PR.

## 1. Acceptance items — measured status

| # | Item | Status | Measured result |
|---|---|---|---|
| 1 | D1 — provider totals | **RESOLVED** | `sanitized/provider_summary.json` makes the 56-offer table primary; the 34-row literal-dedup view is retained and explicitly relabelled. |
| 2 | D2 — unreproducible hashes | **PARTIALLY_RESOLVED** | `show_json_sha256` VERIFIED against original retained bytes (exact SHA-256, 5,586 bytes). The other three are explicitly `UNVERIFIABLE` with provenance and reason; no value was invented. |
| 3 | D4 — attempt namespaces | **RESOLVED** | `sanitized/attempt_accounting.json` names four distinct namespaces; no counter was changed or fabricated. |
| 4 | HTTP accounting | **PASS** | 3/6 collection calls and 1/3 probes are measured; internal JustWatch attempts are `ESTIMATE_NOT_MEASURED`, never reported as usage. |
| 5 | D6 — identity provenance | **RESOLVED_AS_EXTERNAL_ATTRIBUTION** | No TMDb field in canary records; hash-matched TMDb identity evidence exists in a committed manifest and retained payloads; the join to canary rows is inferred by title/year/type. |
| 6 | Re-verify offline | **PASS** | verifier, file hashes, synthetic fixture and sanitization scan all re-run; see §8. |
| 7 | PR #197 description updated | **PASS** | disposition recorded for every original finding. |
| 8 | Scope boundaries | **PASS** | no provider calls, no DB/deploy/merge/issue edit; runtime permission requirements unchanged. |

## 2. Reproduced counts (unchanged, all 56 represented)

27 (Dune) + 29 (Inception retry) = **56** raw records / **56** offers; **0** rejects; **0**
duplicates on the extended key; **22** collapses on the literal brief key (56 → 34); **56**
`nl`→`NL` country normalizations; **0** near-duplicate price groups; **2** `['UNKNOWN']`
audio and **2** `['UNKNOWN']` subtitle lists; all rows `NL`, `movie`, `available=true`,
labelled `SAME_SOURCE_ECHO` + `UNLICENSED`. Monetization mix: buy 20, rent 20,
subscription 16, free 0. `offers.json` equals the normalized raw call results as a
multiset (country uppercased, 3 flags appended, labels added, scalar language sentinel
wrapped as a list).

### Corrected provider summary — all 56 offers (D1)

| Provider | All 56 (primary) | Literal-dedup 34 (secondary) |
|---|---:|---:|
| Amazon Video | 12 | 6 |
| Apple TV Store | 12 | 6 |
| Rakuten TV | 10 | 6 |
| HBO Max | 6 | 3 |
| HBO Max Amazon Channel | 6 | 3 |
| Pathé Thuis | 4 | 4 |
| Amazon Prime Video | 2 | 2 |
| Netflix | 2 | 2 |
| meJane | 2 | 2 |
| **Total** | **56** | **34** |

Both columns sum correctly and are reproducible from `offers.json`. The 56-row column is
the canary total; the 34-row column counts unique country/provider/type/quality/price/
currency tuples after discarding title identity. The old `summary.md` table quoted the
34-row numbers under 56-row headline totals — that is the D1 defect; this table corrects
the presentation without altering any record.

## 3. D2 — hash investigation and disposition

Methods used: raw SHA-256 of every retained evidence file; four canonical JSON
serializations of every JSON object in those files; raw/canonical variants of the scratch
preflight artifacts (`tools.json`, `tools.raw.txt`, function-show files); full hash scan of
**11,631** scratch/cache files under 5 MB.

| Hash | Declared value | Status | Provenance / reason |
|---|---|---|---|
| `show_json_sha256` | `123aa9000f175070e1037fcba01f87a83062b7aca5e32ca1410402bb41828d89` | **VERIFIED** | Original pre-redeploy show bytes at `~/.hermes/cache/scratch/anything-mcp-preflight/notte_function_show.json` (5,586 bytes) hash exactly. Raw file not published (presigned URL); redacted derivative `sanitized/function_show_original.json` included. |
| `params_hash` | `d4c2880edbbd00d738229e737171998fdaa9a264ff7fc1a3e1b5dad2faf9ebf4` | **UNVERIFIABLE** | Declared at `runs/.../manifest.json:47`. No matching bytes/serialization found, including all call/probe variables and parameter permutations. Exact serialized input object not retained. |
| `previous_spec_sha256` | `05449a1c3f238bc09a08afee22db3c11ef2ebf8886b5cad19bcdecd32f136bd0` | **UNVERIFIABLE** | Declared at `spec/manifest.json:4` and `runs/.../manifest.json:49`. Pre-R3 spec bytes not retained; PENDING.md documents only the owner-exported current spec. |
| `tool_list_schema_sha256` | `0e688edd1bd330f98bb4d89146c6497f5d0abb62a95654c5dcf09c64b9115705` | **UNVERIFIABLE** | Declared at `spec/manifest.json:12`, `PENDING.md:11`, and scratch `preflight.json`/`resume_report.json`. A tools/list artifact exists but no raw or canonical serialization of it matches. Exact hashed payload not retained. |

The other declared hashes — spec YAML `faf8b4ba…`, source code `b512a7be…` (both copies),
both manifest files — remain **VERIFIED** against retained bytes. No UNVERIFIABLE value was
"corrected": the declared value is preserved with its qualification.

## 4. Attempt accounting (D4 resolved)

| Namespace | Count | Source | Status |
|---|---:|---|---|
| Notte validation probe invocations | **1 / 3** | `probe-01.json` — invalid `reference_market=nl`, failed at parameter validation | MEASURED |
| Notte collection invocations | **3 / 6** | `call-01` failed import, `call-02` Dune closed, `call-03` Inception retry closed | MEASURED |
| Per-title attempt index | Inception 1,2; Dune 1 | `batch-01.attempt=1`, `batch-03.attempt=2`, `batch-02.attempt=1` | MEASURED |
| Global collection call index | Inception 1,3; Dune 2 | `coverage.json.attempt_history` + batch numbers | MEASURED |
| Internal JustWatch HTTP attempts | **unknown** | no per-request ledger retained | **NOT MEASURED** |

`coverage.json`'s `attempt: 1, 3` are global call indices; `batch-03.json`'s `attempt: 2`
is the per-title retry index. They are not contradictory once named — that is the D4
resolution. No counter was rewritten in the raw evidence.

**HTTP vs invocation:** the measured budgets apply to Notte function invocations only.
Static reading of `spec/fe1b97b6.py` shows ≥4 underlying HTTP attempts per successful
invocation (US search page, US discovery page, NL market page, GraphQL offers POST), so two
successes imply **≥8**, but this is explicitly an **`ESTIMATE_NOT_MEASURED`** static lower
bound, not observed usage. The owner's 25-low-level-attempt proposal in #97 is a different
unit and is not comparable to 3/6 or 1/3. The probe and the import-error call made zero by
code position; retry-loop and embedded-fallback additions are unledgered.

## 5. Identity provenance (D6 resolved as external attribution)

- Searched all 22 retained evidence files: **no** `27205`, `438631` or `tmdb_id` field in
  `runs/` or `spec/`. The only occurrence in retained evidence is the operator brief line
  `docs/source-permission/decision-2026-11-24.md:26`.
- Canary records carry `title`, JustWatch `jw_id`, `object_type`, `release_year`, `country`.
  One distinct value per batch: Inception / `tm92641` / movie / 2010; Dune / `tm305113` /
  movie / 2021.
- External identity evidence is retained and hash-matched: committed
  `docs/audits/stage2b/identity_manifest_20261122.csv` rows for TMDb 27205 (movie,
  Inception, 2010, `tmdb_direct_id_details`, hash `4d4a57db…`) and 438631 (movie, Dune,
  2021, `tmdb_direct_id_details`, hash `03fd27dc…`); the corresponding retained TMDb JSON
  payloads hash exactly to those values.
- **Classification:** title/year/media-type are VERIFIED by external hash-matched evidence
  and are consistent with the canary fields. The link from those TMDb ids to the canary
  JustWatch ids is **INFERRED / EXTERNALLY_ATTACHED** by title+year+type because the canary
  records contain no direct key. No production database cross-check was performed.

## 6. Findings disposition (original audit)

| ID | Disposition | Detail |
|---|---|---|
| D1 | **RESOLVED** | §2 provider table uses all 56; 34-row view kept with its own label. |
| D2 | **PARTIALLY_RESOLVED** | §3: one hash VERIFIED, three `UNVERIFIABLE` with provenance/reason; no fabricated corrections. |
| D3 | **DOCUMENTED** | Show lists six versions; event names two; intermediate `v20261124_150411` not expanded — no further event evidence retained. |
| D4 | **RESOLVED** | §4 namespaces named; counters unchanged. |
| D5 | **DOCUMENTED_OPEN** | No JustWatch HTTP ledger; ≥8 stays an estimate, not measured usage. |
| D6 | **RESOLVED_AS_EXTERNAL_ATTRIBUTION** | §5: external hash-matched identity, inferred join. |
| D7 | **DOCUMENTED** | Language-sentinel wrapping asserted and tested by verifier; originating run manifest not edited. |
| D8 | **DOCUMENTED** | Version-event detection timestamp follows the retry; chronology unchanged. |

## 7. Remaining blockers

1. `params_hash`, `previous_spec_sha256`, `tool_list_schema_sha256` source bytes/serialization (`UNVERIFIABLE`).
2. Per-request JustWatch HTTP ledger (D5).
3. A direct canary-record key joining a JustWatch id to a TMDb id (D6); external evidence exists but the join is inferred.
4. Deployed function source artifact hash (only a presigned URL existed; redacted).
5. Independent first-party comparator / entitlement / playback observation — no accuracy rate is computable.

## 8. Sanitization and verification

**Included:** this report; `manifest.json` (22-file hash/byte inventory, qualified hashes,
findings disposition); `verify_evidence.py`; synthetic fixture; byte-identical function
spec/source copies; redacted function-show derivatives; `provider_summary.json`,
`attempt_accounting.json`, `identity_provenance.json`; `SHA256SUMS`.

**Excluded:** raw offers JSON/CSV, raw call results, raw headers/presigned URLs, per-record
validation rows, raw TMDb payloads. Regex scan covers presigned-URL signatures, bearer
tokens, GitHub/OpenAI/AWS key shapes; run/session UUIDs appear only as hash prefixes; the
function id is public and non-credential.

Verification commands (stdlib only; no HTTP/DB/Django):

```bash
BASE=c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7
git fetch origin && git rev-parse origin/master
python3 docs/audits/issue97/jw-canary-2026-11-24-evidence/verify_evidence.py --self-test
python3 docs/audits/issue97/jw-canary-2026-11-24-evidence/verify_evidence.py \
  --manifest docs/audits/issue97/jw-canary-2026-11-24-evidence/manifest.json \
  --evidence-root /path/to/private/evidence/root
cd docs/audits/issue97/jw-canary-2026-11-24-evidence && shasum -a 256 -c SHA256SUMS
# Independent hash confirmation for the VERIFIED show digest:
shasum -a 256 ~/.hermes/cache/scratch/anything-mcp-preflight/notte_function_show.json
```

**Executed results:** synthetic self-test PASS (7 dedup checks + sanitization scan, 10 files
scanned); offline verification PASS on 22/22 file hashes and 41/41 checks (27 derived
metrics, 3 checkpoint checks, 11 artifact/hash-qualification checks); `SHA256SUMS` all OK;
sanitization scan PASS with zero high-risk hits.
No CI claim is made — a local PASS is not a GitHub Actions PASS. Repo `CLAUDE.md` requires
`claude-roast`, which is not installed here; no score was fabricated.

## 9. Handoff

Hermes prepared evidence only. ChatGPT Web remains the sole final reviewer/merger; PR #197
stays in draft. Not approved, merged, deployed or closed by this agent.
