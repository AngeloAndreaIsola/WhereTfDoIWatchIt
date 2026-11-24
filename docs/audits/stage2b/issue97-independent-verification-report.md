# Issue #97 — Independent Availability Verification (Stage C)

Seven historical first-party offer candidates for Inception and Dune in NL
come from three provider-page captures on November 23, 2026. The JustWatch
canary observed offers on November 24. The reviewed reference window contains
zero temporally eligible comparisons, and zero comparisons were independently
adjudicated. Issue #97 remains open and #103 remains on hold.

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

The [sanitized Stage A package](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/tree/365d709750ae016e912d33d83a29d749dce38069/docs/audits/issue97/jw-canary-2026-11-24-evidence)
on draft PR #197 pins aggregate results, checkpoints, hashes and byte counts.
Raw call results, offer rows and full function exports are held off-repo.
Verify authorized local inputs against the
[restricted custody inventory](../issue97/evidence-custody-verification.md)
on cleaned master and the Stage A manifest. The unpublished function is
referenced as `stage-a-notte-function`. The corrected package verifies 22/22
inventory entries and 47/47 derived and metadata checks with the retained
supporting inputs. Three declared hashes and the deployed artifact hash
remain unverifiable.

The canary attribution to TMDb movie 27205 (Inception, 2010) and movie
438631 (Dune, 2021) is externally inferred by title, release year and media
type using the frozen identity manifest. The canary has no direct TMDb field.
[Identity manifest](identity_manifest_20261122.csv)

## First-party candidates and comparison window

The full November 23 panel contains 13 accepted NL advertised offers and
eight rejected observations. Seven accepted rows are in scope here. The
rejections establish no title-level negatives. The accepted records identify
advertised offer classes and observed transaction prices. [Historical verification](issue97-final-verification-report.md)

| Capture alias | Typed film identity | First-party capture time (UTC) | Historical candidates |
|---|---|---|---|
| C-1 | Inception, movie 27205 (2010) | 2026-11-23 15:32:01.945280 | Netflix subscription (1) |
| C-2 | Inception, movie 27205 (2010) | 2026-11-23 15:12:31.629 | Base Prime subscription, Prime rent (€2.99), Prime buy (€4.99), HBO Max via Prime add-on (4) |
| C-3 | Dune, movie 438631 (2021) | 2026-11-23 15:14:34.135 | Rakuten TV rent (€2.99) and buy (€9.99) (2) |

The reviewed first-party reference hour is November 23, 15:00–16:00 UTC
(end exclusive). Successful canary batches were observed November 24 at
15:05 UTC for Dune and 15:36 UTC for the Inception retry. The reviewed record
defines no tolerance spanning these dates. The November 22 clone and the
stale Watchmode pilot remain historical context. JustWatch-derived
application rows are same-source echoes. [Stage A attempt accounting](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/365d709750ae016e912d33d83a29d749dce38069/docs/audits/issue97/jw-canary-2026-11-24-evidence/sanitized/attempt_accounting.json)

Historical matching requires typed title and year, independently established
NL territory, service, offer class and observed transaction price where
applicable. Base subscriptions and channel add-ons have separate offer
classes. Playback and entitlement require separate observations, and a
missing listing establishes no unavailability. Draft PR #200 records the
historical row-level replay from authorized local evidence.

## Provisional matrix schema

[The CSV](issue97-reconciliation-matrix-template.csv) has 20 columns and
seven data rows, one per advertised pair. The canary fields are empty
because Stage A's sanitized aggregates do not contain row-level matching
fields. The primary key is `pair_id`. Empty fields indicate unscored prices
or unset comparison data as documented below.

| Column | Type and meaning |
|---|---|
| `pair_id` | Unique string identifying a typed NL title/service/class pair |
| `tmdb_id` | Positive integer ID from the frozen first-party identity manifest |
| `media_type` | `movie`, part of the TMDb identity |
| `year` | Integer canonical release year |
| `country` | ISO alpha-2 market code, `NL` |
| `provider_slug` | Canonical service slug, `netflix`, `prime`, `max_amazon` or `rakuten_tv` |
| `offer_class` | `subscription`, `addon`, `rent` or `buy` |
| `provider_title_ref` | Safe title-page alias tied to the capture; `prime-inception` is shared across the four C-2 rows |
| `first_party_offered` | `yes` for an observed advertised offer |
| `first_party_capture_id` | Underlying capture alias `C-1`, `C-2` or `C-3` |
| `first_party_observed_utc` | ISO 8601 capture timestamp in UTC |
| `first_party_price` | Decimal EUR for rent/buy, empty for unscored prices |
| `temporal_eligible` | `no` for the November 23 versus November 24 comparison |
| `canary_reported` | Optional `yes` or `no` for a row-level canary finding, empty here |
| `canary_label` | Optional source label from matched canary evidence, empty here |
| `canary_source` | Optional canary source name, empty here |
| `canary_observed_utc` | Optional ISO 8601 observation time in UTC, empty here |
| `stale_watchmode_match` | `yes` or `no` for an exact historical typed pair with a stored Watchmode overlap |
| `verdict` | Empty pending independently eligible evidence |
| `notes` | Text describing the first-party offer observation |

Run from the repository root. Expected result: 20 columns, seven data rows,
two typed identities, three captures and zero UUID-shaped tokens in the
matrix and report.

```bash
python3 -c '
import csv, re, sys
from pathlib import Path

matrix = Path(sys.argv[1])
with matrix.open(newline="", encoding="utf-8") as source:
    header, *rows = csv.reader(source, strict=True)
assert len(header) == len(set(header)) == 20
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

## Supported counts

| Measure | Result |
|---|---|
| Historical first-party offer candidates | 7 |
| Underlying provider-page captures | 3 |
| Temporally eligible comparisons | 0 |
| Independently adjudicated comparisons | 0 |
| Confirmed negative, entitlement and playback observations | 0 each in scope |
| Agreement, conflict, false positives and false negatives | **UNDEFINED** |
| Agreement rate, accuracy, precision, recall and F1 | **UNDEFINED** |

Fresh, authorized first-party evidence within a reviewed comparison window
is required for independent adjudication.
