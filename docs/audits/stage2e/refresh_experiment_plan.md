# Stage 2E — Bounded refresh experiment plan (dry run)

Nothing in this plan has been executed. It targets at most five existing,
Movie-anchored Stage 2C title-country cases with canonical evidence. It avoids
the four unanchored NL offers and does not call Streaming Availability or
Watchmode.

## Proposed cases and budget

| Case | Why selected / provider evidence already stored | App source | Work type | Max external requests | Metered? | Change to expect if confirmed |
|---|---|---|---|---:|---|---|
| Dune (438631), IT | Existing Mediaset Infinity subscription and Max offer; direct provider-specific source available | `mediaset_infinity` | title targeted, movie | 2 (search + detail) | no billed API quota | update/create Mediaset provider content and subscription Offer; freshness becomes current for returned scope |
| Inception (27205), NL | Existing Netflix, Max, and Prime subscription evidence from multiple aggregator/provider mappings | `justwatch` | title targeted, movie | 3 attempts (1 request × 3 default tries) | no billed API quota; operational risk remains | refresh/update returned provider Offers; cannot infer absence from omission |
| Dune (438631), NL | Existing Prime base/add-on/rent/buy rows plus Max; Prime mapping uses title/year evidence, worth reviewing as a potentially ambiguous mapping | `justwatch` | title targeted, movie | 3 attempts (1 request × 3 default tries) | no billed API quota; operational risk remains | refresh returned provider Offers; does not independently validate Prime mapping |

The executable cohort is these three cases: two JustWatch movie lookups and
one Mediaset movie lookup. A movie title lookup avoids the extra per-season
requests incurred for series.

The Stage 2C IT/NL cohort has no demonstrated canonical `free_with_ads` case
from its captured offer rows. YouTube's declared free program excludes IT/NL;
other free/ad-supported catalogues are catalogue-only and a broad walk would
violate the bounded experiment. Free/ad evidence is therefore **not covered**
in this run; add a supported country/title only after a separate scoped case
and source request estimate are available.

### Numeric request and quota guard

- JustWatch title fetches: **6 maximum HTTP attempts total** (two movie
  lookups, three transport attempts each; the current client default is
  `DEFAULT_RETRIES = 3`). Although it has no billed API quota, it is an
  unofficial aggregator; count retries, expect rate limiting/schema drift,
  and treat omission as non-negative evidence.
- Mediaset Infinity: **2 maximum HTTP requests** (one search plus one detail
  for the single matching movie). No billed API quota; still a public service
  with operational limits.
- Streaming Availability: **0 requests**.
- Watchmode: **0 requests**.
- RaiPlay, Prime Video, Pluto TV, Internet Archive: **0 requests**; these
  require catalogue paths or remain blocked by provisioning.
- Separate primary-provider evidence collection: at most **3 page requests**
  (one exact provider title page per selected case; do not follow redirects,
  no background asset fetching, stop on login/geo/error). This is evidence
  capture, not source API refresh.
- Hard total cap if all planned work is later explicitly authorized: **11
  outbound HTTP attempts** (6 JustWatch attempts + 2 Mediaset requests + 3
  provider-page requests). Do not follow redirects; any redirect ends that
  primary-page check as unverified. No retries beyond these limits.

No quota availability was verified. Before any future execution, read the
source configuration and local rate-limit counters, then explicitly pin the
worker to the selected sources so no other title or metered source can enter
the run. The current worker does not provide a per-command `--source` selector
for title-lane requests; an execution wrapper/allowlist or engineering change
is required before submitting anything.

## Preflight, verification, recovery

1. Resolve A7 with a shared drain lock or run only after a reviewed scheduler
   change prevents concurrent watchlist drain. Resolve A6 or snapshot source
   configuration and use a scheduler path whose effective windows are
   understood.
2. Read the queue, selected `Source.enabled`/configuration rows, latest
   successful/attempted run records, and quota counters without exposing
   account data. Confirm there are no duplicate pending requests for the
   sample.
3. Export only aggregate counts and selected sample offer IDs/timestamps to a
   private recovery note. Do not export personal watchlists or subscriptions.
4. Enqueue exactly the selected source/title/country requests with the
   8-attempt application-source cap (6 JustWatch attempts and 2 Mediaset
   requests); do not invoke the broad refresh command or catalogue workers.
5. Read back `RefreshRun`, `SyncRun`, `Offer`, `OfferEvidence`, and mapping
   timestamps. Confirm source/country/provider/class, fresh classification,
   and no unrelated rows changed. Preserve old offers because all selected
   sources are non-authoritative.
6. If partial/failing, stop; retain logs and run IDs; do not repeat. No rollback
   is expected to remove stale or newly observed evidence. Recovery is a
   separately reviewed correction of only the identified sample rows, using
   the existing canonical history/evidence process.

The per-provider evidence is not itself authoritative application evidence.
Keep app observation and independent provider observation in separate records.

## Stage 2C audit rerun

After a later authorized refresh and independent evidence capture, run from
`backend/` with a new isolated output directory:

```sh
RUN_DIR="$(mktemp -d /tmp/wtdw-stage2e-accuracy.XXXXXX)"
python manage.py audit_availability_accuracy \
  --cohort ../docs/audits/stage2c/cohort.csv \
  --external-evidence ../docs/audits/stage2c/external_evidence.csv \
  --output "$RUN_DIR" --offline
```

Keep the original Stage 2C/2D outputs unchanged. Do not edit the external
evidence file in place; create a timestamped copy containing provider name,
country, exact offer class, primary provider URL, retrieval time, and explicit
verification status. Aggregator-only evidence remains insufficient. The audit
must still find fresh, verified application evidence before reporting an
agreement.
