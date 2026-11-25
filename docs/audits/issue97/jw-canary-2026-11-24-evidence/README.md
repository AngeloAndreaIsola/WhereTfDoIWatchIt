# Issue #97 Stage A evidence

**READY_FOR_REVIEW.** This package records the historical Dutch Notte canary
(`JW-CANARY-v1b`, November 24, 2026). The original collection was prepared
against `c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7`. This review uses the cleaned
master at `d84308be248828bc9bcef07465eb7dd533e1f621`.

## Offers and identity

| Historical result | Count |
|---|---:|
| Observed source-reported offers | **56** |
| Dune (2021) | 27 |
| Inception (2010) retry | 29 |
| Rows under the historical incorrect key | **34** |
| Cross-title collapses under that key | 22 |
| Rejects | 0 |

The 56 offers comprise 20 buy, 20 rent and 16 subscription records. The historical
key used country, provider, monetization type, quality, price and currency.
It omitted title identity and collapsed 22 rows across titles. The title-aware
key retained all 56. The [provider summary](sanitized/provider_summary.json)
preserves both aggregates for reproduction.

Each offer carries `UNLICENSED` and `SAME_SOURCE_ECHO`. The evidence class is
`MEASURED_AGAINST_UNVERIFIED_SOURCE`. Stage A publishes no accuracy metric.
The canary records carry title, release year, media type, country and a
JustWatch title key. The match from TMDb to the canary is **externally inferred
by title, release year and media type**. The retained TMDb payloads match the
digests in the committed identity manifest. The canary has no direct TMDb
join field. [Identity provenance](sanitized/identity_provenance.json)

## Invocation accounting and chronology

| Counter | Scope |
|---|---|
| **3 / 6** measured Notte collection invocations | One Inception import error, one Dune success, one Inception retry success |
| **1 / 3** measured Notte validation probe invocations | Invalid `reference_market=nl` rejected during parameter validation |
| Per-title attempt indices | Inception 1 and 2, Dune 1 |
| Global collection indices | Inception 1 and 3, Dune 2 |
| Internal JustWatch HTTP attempts | **UNKNOWN**. No per-request ledger was retained. |

Static inspection of the restricted export identifies four nominal actions
per successful invocation. That estimate carries `ESTIMATE_NOT_MEASURED`.
Browser traffic, fallback traffic and retries have no independent count.
See the [attempt accounting](sanitized/attempt_accounting.json) for observation
timestamps. The function version event from `v20261124_135310` to
`v20261124_150434` was detected after the retry. The redeploy show lists six
versions, including the intermediate `v20261124_150411`. The version-change
record links the two endpoint versions.

## Custody and hash provenance

The Notte shows describe an unpublished, unshared function. This package
refers to it as `stage-a-notte-function`. The [schema projection](sanitized/function_schema.json)
retains input constraints and output field names. The [custody record](../evidence-custody-verification.md)
locates the full Python and YAML exports and the raw canary artifacts. The
sanitized shows preserve versions, timestamps, byte counts and source digests.
The [manifest](manifest.json) records the original hashes and byte counts for
all replay inputs. The three archived spec filenames use the same function
alias here. The verifier resolves them from the hash-checked private spec
manifest in memory.

| Historically declared digest | Status |
|---|---|
| `show_json_sha256` | **VERIFIED** against the retained pre-redeploy show (5,586 bytes) |
| `params_hash` | **UNVERIFIABLE**. The exact serialized parameters are unavailable. |
| `previous_spec_sha256` | **UNVERIFIABLE**. The pre-R3 spec bytes are unavailable. |
| `tool_list_schema_sha256` | **UNVERIFIABLE**. The exact hashed tools/list serialization is unavailable. |

The redeploy download log matches the declared digest and 206-byte length.
It was found in an ignored path in the primary checkout. The deployed artifact
hash remains **UNVERIFIABLE**. The replay verifies the log file's bytes.

## Offline verification

Run from the repository root. The verifier reads Git blobs and local files
without writing to them. The commands use the paths available on the evidence
host. Supply the equivalent retained input paths on a different machine.

```bash
evidence=docs/audits/issue97/jw-canary-2026-11-24-evidence
custody=~/.hermes/private/issue97-canary-ff15058
scratch=~/.hermes/cache/scratch
python3 -B "$evidence/verify_evidence.py" --self-test
python3 -B "$evidence/verify_evidence.py" \
  --evidence-root "$custody" \
  --download-log ~/Documents/GitHub/WhereTfDoIWatchIt/runs/jw-canary-2026-11-24/download_redeploy.log \
  --original-show "$scratch/anything-mcp-preflight/notte_function_show.json" \
  --tmdb-evidence-root "$scratch/issue97-final-workflow/tmdb-evidence"
(cd "$evidence" && shasum -a 256 -c SHA256SUMS)
python3 -B "$evidence/verify_evidence.py" --evidence-root "$custody" --scan-tree HEAD
```

- Synthetic fixture PASS, 7/7 checks and zero findings across 11 package files.
- Local replay PASS, 22/22 inventory checks including all 20 restricted artifacts,
  the historical source decision and the recovered download log. All 47 derived
  and metadata checks and all three supporting input checks passed. Omitting
  `--download-log` yields 21/22 inventory checks and overall `UNVERIFIABLE`
  (exit 2).
- Package checksums PASS, 10/10. Tracked tree scan PASS, with 20/20 byte comparisons
  against the source commit, 21/21 raw paths ignored, and zero tracked raw paths,
  raw-digest matches or newly introduced restricted identifier or credential
  matches. The scanner reports inherited provider UUID matches by path. Seven
  unchanged archive files carry those matches on both cleaned master and the
  original PR head. Older Git objects are retained.
- GitHub Actions execution is billing-blocked. Local results cover the evidence
  package and tracked tree.

ChatGPT Web owns final review and any merge decision for draft #197. Issue #97
remains open. This evidence supports the historical Stage A review.
