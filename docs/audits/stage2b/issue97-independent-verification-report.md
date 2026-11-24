# Issue #97 — Stage A and Stage C evidence reconciliation

The local replay matched all seven historical first-party candidate pairs on typed title attribution, NL territory, service, advertised offer class and observed transaction price where applicable. The pairs came from three provider-page captures on November 23, 2026. The matching JustWatch records were observed on November 24. **Zero pairs qualify for contemporaneous comparison or independent adjudication.** Issue #97 stays open and #103 remains on hold. [Stage A draft PR #197](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/197) · [Stage C draft PR #199](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/199) · [Historical pair matrix](issue97-reconciliation-matrix-template.csv)

## Evidence and custody

The November 24, 2026 canary observed 56 source-reported NL offers: 27 Dune
and 29 Inception retry records. The historical incorrect key omitted title
identity and yielded 34 rows. The collection recorded three measured Notte
invocations out of six allowed (one Inception import error, one Dune success,
one Inception retry success) and one validation probe out of three allowed.
Internal JustWatch HTTP attempts are **UNKNOWN**. Four nominal actions per
successful invocation is a static estimate, `ESTIMATE_NOT_MEASURED`.
The source retained `UNLICENSED`, `SAME_SOURCE_ECHO` and
`MEASURED_AGAINST_UNVERIFIED_SOURCE`. [Stage A handoff on PR #197](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/197#issuecomment-7141961443)

| Evidence | Revision or reference | What it supports |
|---|---|---|
| Sanitized Stage A package | PR #197 at `365d709750ae016e912d33d83a29d749dce38069` | Manifest, source hashes and byte counts, batch checkpoints, offer and attempt aggregates, identity provenance, sanitized function schema and metadata, synthetic fixture and verifier. |
| Retained canary inputs | [Restricted custody inventory](../issue97/evidence-custody-verification.md) on cleaned master | The 20 restricted originals include call results, normalized offer rows and full function exports. The sanitized reference for the unpublished function is `stage-a-notte-function`. |
| First-party observations | November 23 reference hour `[15:00, 16:00) UTC` | Thirteen accepted NL advertised offers across the full panel. Seven are in scope for Inception and Dune. Eight rejections establish no title-level negatives. The November 22 clone and its stale rows are separate evidence. |
| Stage C draft | Parent PR #199 | First-party candidates, a provisional matrix and the reviewed comparison window. |

The sanitized package omits the row-level combination of title, market,
provider, class, price and observation time needed for reconciliation. Raw
offer rows, call results, sensitive links and raw TMDb payloads remain in
restricted custody. The source decision records owner risk acceptance for the
historical run and keeps JustWatch permission open. The matrix records only
historical pair decisions and safe observation references. The replay runs
locally against retained inputs. [Stage A package](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/tree/365d709750ae016e912d33d83a29d749dce38069/docs/audits/issue97/jw-canary-2026-11-24-evidence) · [Source decision](../../source-permission/decision-2026-11-24.md)

## Offline reconciliation procedure

1. On the evidence host, read authorized local inputs listed in the custody inventory into a verifier without printing their contents. Verify the offer file and successful call files against the SHA-256 and byte counts in the custody inventory and Stage A manifest. Confirm that the documented normalization reproduces 56 rows. Keep the failed Inception invocation as a separate checkpoint and retain the inputs and any temporary rows locally.
2. Use `jw_id`, title, media type, release year and market to establish the canary identity, then join the typed TMDb identity from the retained manifest. Stage A attributes Inception to TMDb movie `27205` (2010) and Dune to TMDb movie `438631` (2021) by title, year and media type. The local replay also found that the canary discovery IMDb flag consistently matches the IMDb ID in the corresponding hash-verified TMDb details for both titles. The JW-to-TMDb attribution remains inferred and externally attached. The canary carries no direct TMDb key, and the replay used no serving database. [Identity manifest](identity_manifest_20261122.csv)
3. Require `available=true` and NL on the canary row. Check whether the country is explicit or inferred from a parent filter. Check the first-party capture’s independent NL market basis. Read provider and monetization together and use the saved HTTPS destination locally to check the provider’s title ID. Use the exact service labels and classes below, including the channel distinction. The JustWatch destination is a same-source identity hint. The first-party page and its captured evidence supply the independent observation.

   | Canary service and class | First-party service and class | Title check on the retained canary link |
   |---|---|---|
   | Netflix / subscription | `netflix` / subscription | Netflix title path carries the captured video ID. |
   | Amazon Prime Video / subscription | `prime` / subscription | Prime Video detail GTI matches the first-party film GTI. The capture has a base Prime action. |
   | Amazon Video / rent or buy | `prime` / matching rent or buy | The same GTI has separate rental and purchase actions. |
   | HBO Max Amazon Channel / subscription | `max_amazon` / addon | The same GTI has the separately sold HBO Max channel option. |
   | Rakuten TV / rent or buy | `rakuten_tv` / matching rent or buy | The NL movie path carries the captured 2021 film slug. |

4. Compare currency and decimal amount for each rent or buy candidate. The canary encodes subscription-class prices as zero. Price for the base subscriptions and channel is unscored. Quality and other variants remain distinct in the 56-row inventory. One title-page observation can cover a service/class pair while leaving individual variants unverified. Count that pair once.
5. Record provenance with the pinned revision, offer-file digest, successful batch, original offer `observed_at`, and `source_labels`. For the first-party side retain the observation ID, capture time and manifest reference to the original response digest. Treat any missing or ambiguous identity, scope, class, source or timestamp as unresolved. Check the observation times against the reviewed window before adjudication. A missing listing, a denied redirect, a login wall or a client shell supplies no confirmed negative.

## Historical matches and observation times

The matrix binds each of the seven accepted observation IDs to a match result. C-1 is the Netflix title capture, C-2 the Prime Video film capture, and C-3 the Rakuten NL film capture. The first-party timestamps below come from the frozen accepted observations. The canary ranges are the earliest and latest row-level `observed_at` by successful batch. They describe collection timing. The function resolution, redeploy and call completion times are separate clocks. [Matrix](issue97-reconciliation-matrix-template.csv)

| Capture | Advertised offer pairs | First-party capture UTC | Canary batch and offer observations UTC | Approximate elapsed time |
|---|---|---|---|---|
| C-1 | Inception Netflix subscription | `2026-11-23T15:32:01.945280+00:00` | Batch 03, `2026-11-24T15:36:25.710081Z`–`2026-11-24T15:36:25.711435Z` | 24 h 4 min |
| C-2 | Inception base Prime subscription, Prime rent, Prime buy, HBO Max via Prime add-on | `2026-11-23T15:12:31.629+00:00` | Batch 03, same Inception retry | 24 h 24 min |
| C-3 | Dune Rakuten rent and buy | `2026-11-23T15:14:34.135+00:00` | Batch 02, `2026-11-24T15:05:02.837313Z`–`2026-11-24T15:05:02.838794Z` | 23 h 50 min |

The reviewed record defines the November 23 reference hour and contains no approved temporal tolerance spanning the two dates. All seven historical matches are ineligible. A JustWatch-derived app row is a same-source echo. The stale Watchmode pilot and the cloned app’s display responses cannot adjudicate the November 24 canary. [Historical verification](issue97-final-verification-report.md)

## Historical matrix schema

[The CSV](issue97-reconciliation-matrix-template.csv) has 17 columns and seven
data rows. Its primary key is `pair_id`. Each row binds one frozen
first-party observation to a historical replay disposition.

| Column | Type and meaning |
|---|---|
| `pair_id` | Unique string identifying a typed NL title/service/class pair |
| `tmdb_id` | Positive integer TMDb ID |
| `media_type` | `movie`, part of the TMDb identity |
| `year` | Integer canonical release year |
| `country` | ISO alpha-2 market code, `NL` |
| `provider_slug` | Canonical service slug, `netflix`, `prime`, `max_amazon` or `rakuten_tv` |
| `offer_class` | `subscription`, `addon`, `rent` or `buy` |
| `first_party_observation_id` | Stable identifier of the frozen advertised offer observation |
| `first_party_capture_id` | Underlying capture alias `C-1`, `C-2` or `C-3` |
| `first_party_provider_title_ref` | Safe title-page alias tied to the capture; `prime-inception` is shared across the four C-2 rows |
| `first_party_observed_utc` | ISO 8601 capture timestamp in UTC |
| `first_party_price_eur` | Decimal EUR for rent/buy, empty for unscored prices |
| `identity_attribution` | `inferred_external` for the canary-to-TMDb attribution |
| `historical_pair_match` | `yes` for the historical advertised pair match in the local replay |
| `temporal_eligible` | `no` for the November 23 versus November 24 comparison |
| `independently_adjudicated` | `no` for every row |
| `disposition` | `outside_reviewed_window` for every row |

Run from the repository root. Expected result: 17 columns, seven data rows,
two typed identities, three captures and zero UUID-shaped tokens in the
matrix and report.

```bash
python3 -c '
import csv, re, sys
from pathlib import Path

matrix = Path(sys.argv[1])
with matrix.open(newline="", encoding="utf-8") as source:
    header, *rows = csv.reader(source, strict=True)
assert len(header) == len(set(header)) == 17
assert len(rows) == 7 and all(len(row) == len(header) for row in rows)
assert len({row[0] for row in rows}) == 7
cells = [dict(zip(header, row)) for row in rows]
identities = {(r["tmdb_id"], r["media_type"], r["year"], r["country"]) for r in cells}
assert identities == {("27205", "movie", "2010", "NL"), ("438631", "movie", "2021", "NL")}
assert all(r["temporal_eligible"] == "no" for r in cells)
assert {r["first_party_capture_id"] for r in cells} == {"C-1", "C-2", "C-3"}
uuid = re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")
assert all(not uuid.search(path.read_text(encoding="utf-8")) for path in (
    matrix, matrix.with_name("issue97-independent-verification-report.md")
))
print(f"PASS: {len(header)} columns, {len(rows)} rows, {len(identities)} typed identities, 3 captures, 0 UUID hits")
' docs/audits/stage2b/issue97-reconciliation-matrix-template.csv
```

## Supported counts and remaining evidence

| Metric | Result |
|---|---|
| Observed canary offers | 56 across two title-market cases, all NL |
| Rows under the historical incorrect key | 34 |
| Historical first-party candidates | 7 advertised pairs from 3 captures. |
| Historical replay matches | 7 title/market/service/class pairs with externally inferred canary identity |
| Temporally eligible comparisons | 0. Every candidate falls outside the documented window. |
| Independently adjudicated contemporaneous comparisons | 0. |
| Confirmed negatives, entitlement observations, playback observations | 0 observations in scope for each. Access and playback remain unverified. |
| Agreement, conflict, false positives and false negatives | **UNDEFINED** |
| Agreement rate, accuracy, precision, recall and F1 | **UNDEFINED** |

All 56 rows lack a qualifying contemporaneous comparison under this design. Missing first-party observations include other stores, standalone services, add-ons and offer classes. Fresh evidence needs a reviewed comparison window, exact identity and offer scope, and authorized independent positive and negative observations. Entitlement and playback require their own evidence. A portable offer-row replay requires explicit transfer approval. The retained evidence does not establish production accuracy.

## Verification performed

- Agent A's corrected package at the pinned PR #197 head reports 10/10 package checksums and 7/7 synthetic checks. With all retained supporting inputs it reports 22/22 inventory checks, 47/47 derived and metadata checks, and 3/3 supporting input checks. Without the redeploy download log the replay is **UNVERIFIABLE** (21/22 inventory checks). The historically declared `params_hash`, `previous_spec_sha256` and `tool_list_schema_sha256` and the deployed artifact hash remain unverifiable.
- `restore_issue97_archive.py` verified 109/109 members. On the current checkout, `verify_issue97_evidence.py` stopped at the checklist hash after the checklist changed in #191. Running both commands against the original #189 tree at `e3dd825c4e6f3fd0657a863cd6915387e36a1070` passed and rechecked 16 original files, 26 later responses, 15 TMDb identities, 166 historical pilot rows, 13 advertised pairs and eight rejections. The accepted observation bytes match the current tree.
- The local row join checked 56 canary rows, seven historical pair decisions and both market/clock boundaries. `python3 -m unittest discover -s scripts -p test_issue97_verification.py -v` passed eight tests. Work was offline for provider evidence. Local replay and hosted CI are tracked separately in the PR handoff.
