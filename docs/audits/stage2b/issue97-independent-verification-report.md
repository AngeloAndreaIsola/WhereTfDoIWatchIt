# Issue #97 — Stage A and Stage C evidence reconciliation

The local replay matched all seven historical first-party candidate pairs on typed title attribution, NL territory, service, advertised offer class and observed transaction price where applicable. The pairs came from three provider-page captures on November 23, 2026. The matching JustWatch records were observed on November 24. **Zero pairs qualify for contemporaneous comparison or independent adjudication.** Issue #97 stays open and #103 remains on hold. [Stage A draft PR #197](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/197) · [Stage C draft PR #199](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/199) · [Historical pair matrix](issue97-reconciliation-matrix-template.csv)

## Evidence and custody

| Evidence | Revision or reference | What it supports |
|---|---|---|
| Stage A package `JW-CANARY-v1b` | PR #197 at `0ee004e3bafcc18dcffb359cb98fcf01dde93ef5`, with package base recorded as `c2ead763bb8e49859e13e56a3cd8e80e6c0ee4d7` | Manifest with source hashes, bytes and batch checkpoints, provider and attempt aggregates, identity notes, redacted function metadata, source/spec copies, synthetic fixture, verifier and package checksums. The 56-offer total includes 27 Dune rows and 29 Inception retry rows. The separate 34-row summary uses a key that omits title identity. |
| Retained canary inputs | `origin/master` at `ff150586319e89b8ce4f8dfde9403a33eceea1d1`, under `runs/jw-canary-2026-11-24/` | Tracked raw call results and normalized row-level JSON/CSV, along with checkpoints, validation and run metadata. Every offer carries `SAME_SOURCE_ECHO` and `UNLICENSED`. |
| First-party observations | November 23 reference hour `[15:00, 16:00) UTC` | Thirteen accepted NL advertised offers across the full panel. Seven are in scope for Inception and Dune. Eight rejections establish no title-level negatives. The November 22 clone and its stale rows are separate evidence. |
| Stage C draft | PR #199 at `29e15ff54d5e34f60d50397f5ecddbc9b5c57f18`, directly based on `ff150586319e89b8ce4f8dfde9403a33eceea1d1` | First-party observations, a provisional matrix and the requirement for a reviewed comparison window. |

The sanitized package is available on PR #197. Its aggregates and identity notes omit the row-level combination of title, market, provider, class, price and observation time needed for reconciliation. It excludes raw offer rows, call responses, sensitive headers and links, and raw TMDb payloads. The repository already tracks some excluded raw inputs. The source decision records owner risk acceptance for the historical run and keeps JustWatch permission open. No publication or transfer approval for these inputs or a row-level derivative is recorded. The matrix records the historical decisions against the existing first-party rows. The rest of the matching procedure runs locally on existing bytes. [Stage A package](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/tree/0ee004e3bafcc18dcffb359cb98fcf01dde93ef5/docs/audits/issue97/jw-canary-2026-11-24-evidence) · [Source decision](../../source-permission/decision-2026-11-24.md)

## Offline reconciliation procedure

1. On the evidence host, read the pinned master inputs into a local verifier without printing their contents. Use `git show <master SHA>:runs/jw-canary-2026-11-24/offers.json` as the verifier input, with equivalent paths for the two successful calls. Verify the offer file and successful call files against the SHA-256 and byte counts in PR #197. Confirm that documented normalization reproduces the 56 rows, keeping the failed Inception batch as a separate checkpoint. Keep raw responses, URL components, identifiers and any temporary rows local. No provider access is needed.
2. Use `jw_id`, title, media type, release year and market to establish the canary identity, then join the typed TMDb identity from the retained manifest. Stage A attributes Inception `tm92641` to TMDb movie `27205` (2010) and Dune `tm305113` to TMDb movie `438631` (2021) by title, year and type. The local replay also found that the canary discovery IMDb flag consistently matches the IMDb ID in the corresponding hash-verified TMDb details for both titles. The JW-to-TMDb attribution remains inferred and externally attached. The canary carries no direct TMDb key, and the replay used no serving database. [Identity manifest](identity_manifest_20261122.csv) · [First-party identity decisions](issue97-identity-decisions.md)
3. Require `available=true` and NL on the canary row. Check whether the country is explicit or inferred from a parent filter. Check the first-party capture’s independent NL market basis. Read provider and monetization together and use the saved HTTPS destination locally to check the provider’s title ID. Use the exact service labels and classes below, including the channel distinction. The JustWatch destination is a same-source identity hint. The first-party page and its captured evidence supply the independent observation. [Accepted observations](issue97-accepted-observations.csv) · [Reviewed service and class decisions](issue97-provider-mapping-decisions.md)

   | Canary service and class | First-party service and class | Title check on the retained canary link |
   |---|---|---|
   | Netflix / subscription | `netflix` / subscription | Netflix title path carries the captured video ID. |
   | Amazon Prime Video / subscription | `prime` / subscription | Prime Video detail GTI matches the first-party film GTI. The capture has a base Prime action. |
   | Amazon Video / rent or buy | `prime` / matching rent or buy | The same GTI has separate rental and purchase actions. |
   | HBO Max Amazon Channel / subscription | `max_amazon` / addon | The same GTI has the separately sold `PV_CHANNEL/wbdmaxnl` option. |
   | Rakuten TV / rent or buy | `rakuten_tv` / matching rent or buy | The NL movie path carries the captured 2021 film slug. |

4. Compare currency and decimal amount for each rent or buy candidate. The canary encodes subscription-class prices as zero. Price for the base subscriptions and channel is unscored. Quality and other variants remain distinct in the 56-row inventory. One title-page observation can cover a service/class pair while leaving individual variants unverified. Count that pair once.
5. Record provenance with the pinned revision, offer-file digest, successful batch, original offer `observed_at`, and `source_labels`. For the first-party side retain the observation ID, capture time and manifest reference to the original response digest. Treat any missing or ambiguous identity, scope, class, source or timestamp as unresolved. Check the observation times against the reviewed window before adjudication. A missing listing, a denied redirect, a login wall or a client shell supplies no confirmed negative.

## Historical matches and observation times

The matrix binds each of the seven accepted observation IDs to a match result. C-1 is the Netflix title capture, C-2 the Prime Video film capture, and C-3 the Rakuten NL film capture. The first-party timestamps below come from the frozen accepted observations. The canary ranges are the earliest and latest row-level `observed_at` by successful batch. They describe collection timing. The function resolution, redeploy and call completion times are separate clocks. [First-party capture manifest](issue97-evidence-manifest.json) · [Matrix](issue97-reconciliation-matrix-template.csv)

| Capture | Advertised offer pairs | First-party capture UTC | Canary batch and offer observations UTC | Approximate elapsed time |
|---|---|---|---|---|
| C-1 | Inception Netflix subscription | `2026-11-23T15:32:01.945280+00:00` | Batch 03, `2026-11-24T15:36:25.710081Z`–`2026-11-24T15:36:25.711435Z` | 24 h 4 min |
| C-2 | Inception base Prime subscription, Prime rent, Prime buy, HBO Max via Prime add-on | `2026-11-23T15:12:31.629+00:00` | Batch 03, same Inception retry | 24 h 24 min |
| C-3 | Dune Rakuten rent and buy | `2026-11-23T15:14:34.135+00:00` | Batch 02, `2026-11-24T15:05:02.837313Z`–`2026-11-24T15:05:02.838794Z` | 23 h 50 min |

The reviewed record defines the November 23 reference hour and contains no approved temporal tolerance spanning the two dates. All seven historical matches are ineligible. A JustWatch-derived app row is a same-source echo. The stale Watchmode pilot and the cloned app’s display responses cannot adjudicate the November 24 canary. [Historical verification](issue97-final-verification-report.md)

## Supported counts and remaining evidence

| Metric | Result |
|---|---|
| Source-reported canary offers read locally | 56 across two title-market cases, all NL. This is the inventory denominator. |
| Historical first-party candidates | 7 advertised pairs from 3 captures. |
| Factual historical matches | 7 pairs. Typed attribution is inferred. The count is at the title/market/service/class level. |
| Temporally eligible comparisons | 0. Every candidate falls outside the documented window. |
| Independently adjudicated contemporaneous comparisons | 0. |
| Confirmed negatives, entitlement observations, playback observations | 0 observations in scope for each. Access and playback remain unverified. |
| Agreements, conflicts, false positives and false negatives | Unclassified. There are no eligible comparisons. No counts are reported. |
| Agreement rate, accuracy, precision, recall and F1 | Undefined. There is no eligible denominator. |

All 56 rows lack a qualifying contemporaneous comparison under this design. Missing first-party observations include other stores, standalone services, add-ons and offer classes. Fresh evidence needs a reviewed comparison window, exact identity and offer scope, and authorized independent positive and negative observations. Entitlement and playback require their own evidence. A portable offer-row replay requires explicit transfer approval. The retained evidence does not establish production accuracy.

## Verification performed

- On pinned PR #197, `shasum -a 256 -c SHA256SUMS` passed 11/11. `verify_evidence.py --self-test` passed seven synthetic checks and the scan of ten files. `verify_evidence.py --evidence-root <master checkout>` passed 41/41 derived and metadata checks and 21/22 source hash/size checks. It exited 1 because the inventory’s `download_redeploy.log` was unavailable. The retained offer and call hashes matched. The original pre-redeploy show hash matched locally. Three declared hashes (`params_hash`, `previous_spec_sha256`, `tool_list_schema_sha256`) stay qualified as unverifiable. The deployed artifact hash and low-level request ledger remain unavailable.
- `restore_issue97_archive.py` verified 109/109 members. On the current checkout, `verify_issue97_evidence.py` stopped at the checklist hash after the checklist changed in #191. Running both commands against the original #189 tree at `e3dd825c4e6f3fd0657a863cd6915387e36a1070` passed and rechecked 16 original files, 26 later responses, 15 TMDb identities, 166 historical pilot rows, 13 advertised pairs and eight rejections. The accepted observation bytes match the current tree.
- The local row join checked 56 canary rows, seven historical pair decisions and both market/clock boundaries. `python3 -m unittest discover -s scripts -p test_issue97_verification.py -v` passed eight tests. Work was offline for provider evidence. Hosted CI results are unverified.
