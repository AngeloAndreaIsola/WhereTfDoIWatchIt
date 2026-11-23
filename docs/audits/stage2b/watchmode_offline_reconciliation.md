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
- `application_snapshot.csv` and `comparison.csv` from the **same**
  read-only `audit_availability_accuracy --offline` execution against
  an **authorized disposable clone**. The source clone may require migrations
  to match code; never run them on the serving DB for this task.
- The optional `identity_manifest_20261122.csv` records the canonical title,
  year, UTC observation time and raw response SHA-256 for each of the 15
  official TMDb direct-ID lookups. Pass it as `--identity-manifest` to both
  commands when auditing Option B. From `backend/`, the audit flag takes
  `../docs/audits/stage2b/identity_manifest_20261122.csv`.
- Pass the preserved TMDb response directory as `--manifest-evidence-dir` to
  the comparator to verify every manifest digest against its `movie_<id>.json`
  or `series_<id>.json` file. An unreadable, missing, or mismatched response
  aborts the run before it writes either output. The summary records whether
  this check ran in `manifest_evidence_verified`. Runs without the directory
  rely on the manifest's prior provenance review.
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
| Approved TMDb bundle CSV | `877b22b59cf866926f4fb173275a03d318625b636fa94e0d1cfa22972f35df42` |
| Versioned TMDb identity manifest | `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925` |

The versioned manifest uses each response's returned ID, English title and
release or first-air year. All 15 response digests were verified against the
approved bundle. This product uses the TMDB API but is not endorsed or certified
by TMDB.

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
  --identity-manifest docs/audits/stage2b/identity_manifest_20261122.csv \
  --manifest-evidence-dir /path/to/wtdw97-tmdb-identity-evidence-20261122 \
  --snapshot-label tracker_97_audit_20261122 \
  --audit-code-sha "<commit-used-to-produce-the-application-audit>" \
  --expected-cohort-sha abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b \
  --expected-v1-sha 2b30ab7d78a20a308cafcbda1c495e37d30b4ead4d3bd012affc175e96cea204 \
  --expected-correction-sha e815d65784795b944bbca7abe3d7cfd6b06ae6fc395a45750e372edfddd31f42 \
  --expected-identity-manifest-sha ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925 \
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
- `IDENTITY_UNVERIFIED` means the Watchmode identity or independent canonical
  identity did not pass. The candidate state remains visible for triage.

For an absent local row or a row with an empty `Movie.canonical_title`, the
manifest can supply a matching typed ID, title and year. `identity_source`
records `tmdb_manifest` in that case. `app_movie_found` still reports local
presence, and the application audit skips synthetic profile evaluation when
the Movie is absent. The comparator requires the audit export to record
`identity_source=tmdb_manifest` before applying the fallback. Older exports
without this value need a fresh audit with the reviewed manifest to use the
fallback. Conflicting and quarantined rows remain blocked.

Both tools report `YEAR_MISMATCH` for Coherence (220289), preserving the cohort's
2013 beside the TMDb release year 2014 (`2014-04-06`). Both also report
`TITLE_ALIAS_REVIEW` for The Office (2316), preserving the cohort's “The Office
US” beside the TMDb title “The Office”. Both cases remain blocked for review,
and the reconciliation summary carries the flags even when a title has zero
advertised offers. The documented The Platform / Il buco alias remains scoped
to TMDb movie 619264 (2019).

The direct lookup is separate from the saved Watchmode offer observation.
Watchmode's title metadata may itself come from TMDb, which limits what the
two observations establish about independent identity. Availability evidence
continues to come from the offer inputs.

## Tests and gates

```sh
python3 -m unittest discover -s scripts -p 'test_reconcile_watchmode_offline.py' -v
```

Before using the results for #97, independently review provider mappings,
freshness, source independence and the current canonical snapshot's identity
status. Do not compute a streaming-accuracy percentage or unblock #103 from
these counts. Never perform Watchmode/JustWatch/other API calls in this step.
