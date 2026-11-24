# Manual verification sheet — 8 priority NL pairs (#97)

**Purpose.** Supply *fresh, independent, advertised-availability* observations for the
selected Netherlands pairs so the application's stale canonical offers can be compared
against a source that is not the application's own data. This is **advertised
availability only** — a public listing does not prove subscription entitlement,
territory access after login, or successful playback.

**You do not need Netflix and must not buy anything.** If a page asks for an account,
a subscription, a payment method or a region check you cannot legitimately pass, stop,
record `inaccessible`, and leave the claim **unverified**. Do not use a VPN, borrowed
account, paywall bypass or automated clicking.

## The eight targets

These are *targets*, not assumed positive offers. "Canonical identity" is the exact
typed identity the comparator requires; never join by title alone.

| # | Title (year, type) | Canonical TMDb id | Provider slug | Offer class | Direct official URL | What would count as advertised evidence |
|---|---|---|---|---|---|---|
| 1 | Breaking Bad (2008, series) | 1396 | `netflix` | `subscription` | https://www.netflix.com/nl/title/70143836 | Title-specific Netflix NL page advertising the series on a plan |
| 2 | Inception (2010, movie) | 27205 | `netflix` | `subscription` | https://www.netflix.com/nl/title/70131314 | Anonymous NL page identifying the film and a plan/ads flag |
| 3 | Inception (2010, movie) | 27205 | `prime` | `subscription` | https://www.primevideo.com/detail/0FFJSDLK6HUWC5T8XJUQODMFJ3 | Film GTI, NL territory, base-Prime action distinct from a channel |
| 4 | Stranger Things (2016, series) | 66732 | `netflix` | `subscription` | https://www.netflix.com/nl/title/80057281 | Title-specific Netflix NL page and plan flag |
| 5 | Dark (2017, series) | 70523 | `netflix` | `subscription` | https://www.netflix.com/nl/title/80100172 | Title-specific Netflix NL page and plan flag |
| 6 | The Platform (2019, movie) | 619264 | `netflix` | `subscription` | https://www.netflix.com/nl/title/81128579 | Original title *El hoyo*, exact TMDb homepage match |
| 7 | Dune (2021, movie) | 438631 | `rakuten_tv` | `rent` | https://www.rakuten.tv/nl/movies/dune-2021 | NL storefront, film identity, named rental action and price |
| 8 | The Holdovers (2023, movie) | 840430 | `rakuten_tv` | `rent` | https://www.rakuten.tv/nl/movies/the-holdovers | NL storefront, film identity, named rental action and price |

Country for all rows: `NL`. Observation kind for all rows: `advertised`.

## What to capture per row

1. Open the direct URL once. Note the UTC instant you observed it, with explicit
   `+00:00` offset (e.g. `date -u +%Y-%m-%dT%H:%M:%S+00:00`).
2. Save the page or a screenshot to a private local folder. Do not include cookies,
   tokens, query strings, account names or personal data. Strip query strings from any
   URL you write down.
3. Classify what you saw: `clear` (title-specific listing and offer class visible),
   `ambiguous` (trailer, search result, login wall, wrong edition, client shell),
   or `inaccessible` (login/subscription/geo denial).
4. If no listing is visible, record that as **unknown/inaccessible**. A missing page
   is **not** evidence that the offer is unavailable.

## Fill the CSV (one row per observation)

Header (exact, no extra columns):

```
observation_id,tmdb_id,media_type,country,provider_slug,offer_type,observed_at_utc,observation_kind,source_name,source_permission,evidence_ref,evidence_sha256,url_sanitized,notes
```

- `offer_type`: `subscription`, `addon`, `free`, `free_with_ads`, `rent`, `buy`.
- `observation_kind`: `advertised`, `entitled`, `playback`
  (only `entitled`/`playback` could support an access claim; none is requested here).
- `source_permission`: e.g. `anonymous-public-page` or `owner-personal-view`.
- `evidence_ref`: path to the saved artifact (or a private absolute path the verifier
  can read locally).
- `evidence_sha256`: leave **blank** to have the ingestion tool compute it
  automatically, or paste a digest to have it verified.
- `url_sanitized`: the URL above with no query string.

Example row (illustrative, not an observation):

```
OBS-NL-01,1396,series,NL,netflix,subscription,2026-11-24T10:15:00+00:00,advertised,netflix-public-page,anonymous-public-page,/private/evidence/netflix_bbb_nl.html,,https://www.netflix.com/nl/title/70143836,clear title-specific listing
```

## Run the offline ingestion tool

Run from the repository root with the corrected NL cohort and the reviewed TMDb
identity manifest. Both CSVs are required.

```sh
python3 scripts/ingest_owner_observations.py \
  --observations /private/path/owner_observations.csv \
  --cohort docs/audits/stage2b/nl_pilot_cohort_20261122_v2.csv \
  --identity-manifest docs/audits/stage2b/identity_manifest_20261122.csv \
  --output-dir /private/path/issue97-owner-ingest
```

The tool validates both input CSVs and exits on malformed identity data. Each
observation must match a cohort entry by TMDb ID, media type, and country. The
manifest must agree with the cohort title and release or first-air year, using
the reviewed aliases for the exact typed ID and year. Coherence (movie 220289)
is rejected as `IDENTITY_BLOCKED` with `YEAR_MISMATCH` because the cohort has
2013 and TMDb has 2014. The 13 previously accepted identities and the reviewed
alias for *The Office US* remain eligible. [Identity decisions](issue97-identity-decisions.md)

The command runs offline, hashes each observation artifact, and writes
`accepted.csv`, `rejected.csv`, and `summary.json`. It also rejects duplicate IDs,
naive timestamps, invalid offer classes, and URLs carrying query strings or
credentials. To exclude same-source echoes, pass
`--app-snapshot <dir>/application_snapshot.csv`.

## What this evidence can and cannot establish

- It can confirm that a provider **advertised** an offer in NL at the recorded time.
- It cannot establish your entitlement, post-login regional access, or playback.
- It cannot produce an accuracy rate until there are fresh *independent* comparable
  pairs and a defensible negative control. Absence of a listing is not a negative.

Related: [final report](issue97-final-verification-report.md) ·
[accepted historical observations](issue97-accepted-observations.csv) ·
[identity decisions](issue97-identity-decisions.md).
