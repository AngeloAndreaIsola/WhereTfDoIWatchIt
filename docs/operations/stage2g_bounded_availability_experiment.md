# Stage 2G bounded availability runner

The example manifest is [stage2e_manifest.example.json](../experiments/stage2e_manifest.example.json). It is inert: adding it does not activate a source, queue request, worker, cron entry, or provider call.

## Fixed execution scope

| TMDb | Media | Country | Source | Operation | Endpoint | Per-scope attempts | Expected offer |
|---:|---|---|---|---|---|---:|---|
| 438631 | movie | IT | `mediaset_infinity` | `title_refresh` | `https://frontier.api-graph.mediaset.it/` | 2 | subscription |
| 27205 | movie | NL | `justwatch` | `title_refresh` | `https://apis.justwatch.com/graphql` | 3 | subscription |
| 438631 | movie | NL | `justwatch` | `title_refresh` | `https://apis.justwatch.com/graphql` | 3 | subscription |

The application subtotal is eight attempts: six JustWatch and two Mediaset Infinity. Streaming Availability, Watchmode, and `legacy_scraper` are explicitly denied. The durable global ceiling is eleven. Three slots are reserved in policy for independent provider-page evidence, but no such page is currently authorized because Stage 2E did not preserve exact destination URLs. The evidence operation is therefore unavailable until a separately reviewed manifest/schema change adds exact destinations and page limits.

## Enforcement and recovery

The runner constructs only the JustWatch and Mediaset title clients; it never selects work from `RefreshRequest`, invokes a queue drain, calls the registry default, or discovers additional sources. JustWatch's GraphQL document and variables are checked exactly, including title, movie type, country, page size, and one-page offset. Mediaset's operation document, title, movie variant, and endpoint are also checked. Responses cannot expand the manifest identity: only matching movie/country/subscription offers are persisted. Provider catalogue content and withdrawal instructions are discarded for this experiment.

The injected transport is the lowest common HTTP call seam used by these two clients. It forces POST to the exact HTTPS destination, disables redirects, and reserves a PostgreSQL attempt before calling `requests.post`. Each response status and each raised transport error spends its reservation. Timeout/reset failures are treated as ambiguous and stop retries. JustWatch retries explicit retryable HTTP outcomes only; every retry needs a new reservation. PostgreSQL triggers reject updates and deletes to experiment state, attempt reservations, outcomes, and completion markers.

Reservations use one stable experiment identity per actual PostgreSQL cluster/database. A new process or re-issued authorization uses the same counter. Completed scopes are skipped. Any reserved scope without a completion marker blocks restart for manual reconciliation, whether the process stopped before a response, after a response, or during application persistence. A success outcome does not mean persistence completed. The implementation does not claim exactly-once upstream execution: it guarantees durable pre-send attempt accounting and treats uncertain outcomes as spent.

The runner takes the shared A7 title-drain advisory lock and the existing per-source catalogue advisory lock. This excludes ordinary title drains and same-source catalogue refreshes during execution. Preflight reports current queue overlap, both lock states, unfinished refresh runs, persisted source state/configuration, exact Movie and country scope, canonical offers/freshness, quota evidence, reservations, and fixed destination policy. It is read-only and prints secrets/URLs in source configuration as redacted values.

## Authorization and commands

All commands below run from `backend/`. `dry-run` is the default. It cannot issue HTTP or write application data. Live execution requires a separate HMAC authorization token bound to the manifest digest, actual database identity, exact 11-attempt cap, and expiry. Configure `AVAILABILITY_EXPERIMENT_AUTH_KEY` with at least 32 characters before authorization. The runner also requires fresh explicit quota evidence through `AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE`; missing evidence never becomes available by inference from historical SyncRuns.

The quota evidence is a short-lived operator attestation, at most one hour old. Use `status: "available"` with an integer `remaining` value at least as large as the source cap when the provider exposes a quota. For these two no-billed-quota public clients, `status: "unmetered_public"` records the explicit source classification; it does not assert absence of operational rate limits.

The environment value is JSON with one record for each selected source, for example (replace both timestamps with the current UTC time):

```sh
export AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE='{"justwatch":{"status":"unmetered_public","checked_at":"<current-UTC-ISO-8601>"},"mediaset_infinity":{"status":"unmetered_public","checked_at":"<current-UTC-ISO-8601>"}}'
```

```sh
python manage.py availability_experiment_preflight
python manage.py availability_experiment dry-run
```

When a later operator has independently reviewed current quota/worker state and intends to authorize the exact manifest, the authorization command requires the shown digest, database identity, cap, expiry, and explicit live-execution flag. The command writes a mode-0600 token file and does not make provider requests or application writes.

```sh
python manage.py authorize_availability_experiment \
  --confirm-live-execution \
  --confirm-manifest-digest <sha256-from-review> \
  --confirm-database-identity <identity-from-preflight> \
  --maximum-attempts 11 \
  --expires-at "<future-ISO-8601-with-timezone>" \
  --output /secure/path/stage2e.authorization
python manage.py availability_experiment execute \
  --authorization-file /secure/path/stage2e.authorization
```

The engineering task did not create an authorization token, set quota evidence, or invoke `execute`.

## Known execution blockers

- The fixed manifest only contains title-refresh scopes; provider-page evidence collection is not implemented until exact primary-provider destinations are reviewed.
- The execution gate requires current operator-supplied quota evidence, a PostgreSQL connection with access to `pg_control_system()`, a matching persisted enabled source and Movie/Country rows, no exact-scope queue overlap, no unfinished refresh run, and both advisory locks available.
- The run applies to canonical availability offer persistence only. It does not materialize Movies, run a catalogue walk, or make a negative-availability claim from missing offers.
- Test runs may use mocked `requests.post` or disposable PostgreSQL databases. Never point them at the `wheretfdoiwatchit_pgdata` volume.
