# #97: Offline Watchmode-to-application offer comparison

This is a **read-only evidence preparation tool**. It does not use a network
client, connect to PostgreSQL, access user preferences, or refresh providers.
Its outputs are **not availability accuracy scores**. The source Watchmode
data consists of advertised offers, not entitlement or playback evidence.

## Inputs and provenance

- The merged 15-title NL cohort:
  `docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv`.
- The preserved v1 Watchmode JSON and separate v2 Coherence correction JSON
  produced by the authorized November 22 pilot. Do not replace v1 in place.
- `application_snapshot.csv` and `comparison.csv` from the **same** current-
  master, read-only `audit_availability_accuracy --offline` execution against
  an **authorized disposable clone**. The source clone may require migrations
  to match code; never run them on the serving DB for this task.
- An optional, reviewed `provider_map.csv` with
  `watchmode_name,watchmode_type,app_provider_slug`. A mapping is valid only
  for that exact Watchmode offer class (`sub`, `rent`, or `buy`). Store and
  channel/add-on offerings must not be inferred from a similarly named
  subscription service. Otherwise only unambiguous exact provider display
  names resolve automatically.

The saved evidence has these reviewed SHA-256 values:

| Artifact | SHA-256 |
| --- | --- |
| NL cohort | `abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b` |
| Original Watchmode v1 | `2b30ab7d78a20a308cafcbda1c495e37d30b4ead4d3bd012affc175e96cea204` |
| Coherence correction v2 | `e815d65784795b944bbca7abe3d7cfd6b06ae6fc395a45750e372edfddd31f42` |

## Example usage

Run from the repository root, with paths to the **already saved** local pilot
files and ignored audit outputs. Do not paste credentials into commands:

```sh
python3 scripts/reconcile_watchmode_offline.py \
  --cohort docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv \
  --watchmode-v1 /path/to/wtdw97-pilot-raw-evidence-20261122.json \
  --watchmode-correction /path/to/wtdw97-pilot-raw-evidence-20261122-v2-correction.json \
  --app-snapshot /path/to/wtdw97-fresh-audit-application_snapshot.csv \
  --app-comparison /path/to/wtdw97-fresh-audit-comparison.csv \
  --snapshot-label tracker_97_audit_20261122 \
  --audit-code-sha cfe7399ea977a2ce272624ab44e770dcaf4e2327 \
  --expected-cohort-sha abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b \
  --expected-v1-sha 2b30ab7d78a20a308cafcbda1c495e37d30b4ead4d3bd012affc175e96cea204 \
  --expected-correction-sha e815d65784795b944bbca7abe3d7cfd6b06ae6fc395a45750e372edfddd31f42 \
  --output /path/to/ignored-evidence/reconciliation.csv \
  --summary /path/to/ignored-evidence/reconciliation-summary.json
```

This writes a single row per saved external Watchmode offer (166 rows for
the reviewed input), with exact title/type/territory, source ID, offer class,
and a conservative candidate overlap state. It preserves the original
evidence through input digests and strips URL query strings from exported
provider links. The corrected Coherence lookup contributes **zero NL rows**;
that means absence of an aggregator listing, not unavailability.

## Interpretation

- `STALE_OR_UNVERIFIED_OVERLAP`: an independently sourced local row exists,
  but its own fresh verification cannot be established from the audit export.
  The snapshot's `availability_confidence` is reconciled across sources and
  may reflect Watchmode itself, so it **must not** certify an independent row.
- `SAME_SOURCE_ECHO`: the only matching local rows came from Watchmode. This
  is circular and must never be treated as independent confirmation.
- `NO_APP_OFFER`: no matching positive canonical offer for this provider/class.
  This is not a false-negative verdict.
- `UNMAPPED_PROVIDER_OR_CLASS`: the provider or class lacks an unambiguous,
  reviewed mapping; no guessed service equivalence is made.
- `IDENTITY_UNVERIFIED`: a separate final-decision gate; the external title/
  year is inconsistent, the local Movie is missing, or the merged audit's
  independent canonical title/year check did not pass. The candidate state is
  retained for triage but cannot support an agreement claim.

The existing audit produces `NOT_COMPARABLE` for legacy rows lacking trusted
`Movie.canonical_title` or year. Do not backfill those fields from this
secondary aggregator or infer identity from the historical display title.

The Office (US, 2005) is intentionally flagged for review when the saved
Watchmode title is merely “The Office”; no undocumented title alias is added.
The Platform / Il buco alias in the main audit remains narrowly scoped to
TMDb movie 619264 (2019).

## Tests and gates

```sh
python3 -m unittest discover -s scripts -p 'test_reconcile_watchmode_offline.py' -v
```

Before using the results for #97, independently review provider mappings,
freshness, source independence and the current canonical snapshot's identity
status. Do not compute a streaming-accuracy percentage or unblock #103 from
these counts. Never perform Watchmode/JustWatch/other API calls in this step.
