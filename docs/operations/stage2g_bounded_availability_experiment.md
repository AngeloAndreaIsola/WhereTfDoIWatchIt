# Stage 2G bounded availability runner

The fixed example manifest is [stage2e_manifest.example.json](../experiments/stage2e_manifest.example.json). It is inert: adding or changing this file does not activate a source, queue work, start a worker, authorize a run, or contact a provider. The runner requires an exact match to its compiled-in manifest.

## Fixed execution scope

| TMDb | Movie | Country | Source | Operation | Endpoint | Per-title attempts |
|---:|---|---|---|---|---|---:|
| 27205 | Inception (2010) | NL | `justwatch` | `GetSearchTitles` | `https://apis.justwatch.com/graphql` | 3 |
| 438631 | Dune (2021) | NL | `justwatch` | `GetSearchTitles` | `https://apis.justwatch.com/graphql` | 3 |

The global attempt ceiling is **6 low-level HTTP requests**. JustWatch has a six-attempt source ceiling, with a maximum of three attempts for each title-country scope. `GetSearchTitles` is the only permitted operation. Mediaset Infinity, Watchmode, Streaming Availability, independent provider pages, `legacy_scraper`, all other sources, and all other operations have a zero ceiling and are denied. No provider-evidence transport is implemented.

## Identity and persistence boundary

The runner creates only a title-targeted JustWatch client for the two fixed movie-country pairs. It does not select from `RefreshRequest`, drain a queue, call the registry default, or discover more sources. The GraphQL document and variables are checked before transport, including exact title, movie type, NL country, page size, and one-page offset. The JustWatch adapter checks reported TMDb identity and movie namespace against the request. A conflicting or ambiguous result remains unresolved and yields no offers; when identity IDs are absent, its existing exact title/year fallback requires one distinct match. A missing or unresolved result stays non-authoritative and is never interpreted as provider-confirmed unavailability.

The scoped result accepts only matching TMDb identity, movie type, NL country, and the manifest's subscription offer class. It drops provider-content catalogue rows, withdrawal instructions, and authoritative-snapshot claims. A missing aggregator result is not proof that a provider has no offer. This experiment tests JustWatch refresh and canonical offer persistence only; it does not independently establish provider availability. Mediaset and YouTube are never called, and the integrated identity policy in `availability.services.identity_policy` remains unchanged.

## Reservation, retry, and recovery guarantees

The injected transport is the only HTTP path. It uses POST to the exact HTTPS endpoint, disables environment proxy inheritance and redirects, and mounts an HTTP adapter with zero implicit retries. Every low-level request receives a durable PostgreSQL reservation before `requests.post`. HTTP responses, including failures, receive a durable outcome. A timeout, reset, or interruption after reservation leaves that attempt spent and ambiguous; the run stops and requires reconciliation. A response outcome alone does not mean persistence completed.

HTTP **429 is fail-fast**: its reservation is spent, a failure outcome with status 429 is recorded, the affected scope stops immediately, and no retry is made. The error is not converted into an empty search. HTTP 408, 425 and 5xx retain the JustWatch client's bounded retry behavior, with each transmitted retry requiring a new reservation and all retries sharing the same per-title, source, and global ceilings. A timeout or connection reset has an ambiguous upstream outcome and stops the experiment scope after its first reserved attempt; it is not retried. Permanent 4xx responses and GraphQL errors are not retried. The client does not currently interpret `Retry-After`; 429 is deliberately excluded from experiment retries.

The runner uses one stable experiment identity per actual PostgreSQL cluster/database. Reissued or restarted processes cannot reset the counter. Completed scopes are skipped. Any reserved scope without a completion marker blocks restart, whether the process stopped before a response, after a response, or during application persistence. PostgreSQL triggers reject updates and deletes to experiment state, attempt reservations, outcomes, and completion markers. The implementation does not claim exactly-once upstream execution; it guarantees durable pre-send accounting and conservative handling of uncertain outcomes.

The runner takes the shared A7 title-drain advisory lock and the JustWatch catalogue advisory lock. The read-only preflight reports queue overlap, both lock states, unfinished refresh runs, persisted source state/configuration, exact Movie and country rows, canonical offers/freshness, quota evidence, reservations, and the six-attempt destination policy. It makes SELECT queries only and redacts secret-like configuration values.

## Authorization and commands

All commands below run from `backend/`. `dry-run` is the default and makes no HTTP request or application write. Live execution requires a separate HMAC authorization bound to the revised manifest digest, actual database identity, exact six-attempt cap, and expiry. The authorization ID remains stable for this experiment/database so an older authorization cannot reset its durable counter; the old manifest digest cannot authorize this revised manifest. Configure `AVAILABILITY_EXPERIMENT_AUTH_KEY` with at least 32 characters only during a separately approved authorization step. This implementation task does not create a token.

The runner requires three independent pieces of JustWatch evidence through `AVAILABILITY_EXPERIMENT_QUOTA_EVIDENCE`: (1) express permission to automate the exact GraphQL endpoint for both fixed scopes and at least six requests, (2) current quota classification, and (3) current rate-policy evidence. The public [JustWatch Terms of Use](https://support.justwatch.com/article/just-watchs-terms-of-use) prohibit data-mining, robots, scraping, or similar extraction unless expressly authorized. Public accessibility of the endpoint is not permission. A human must inspect the referenced written permission; the runner checks only the evidence fields and exact scope. Permission, quota classification, and rate-policy evidence must each have a `checked_at` timestamp within one hour. For `status: "available"`, `remaining` must be an integer of at least six. A `rate_policy_evidence` record must include a reviewed reference and `max_burst` of at least six; an unknown/undocumented burst allowance does not pass. `status: "unmetered_public"` records a current operator-checked no-billed-quota classification; it does **not** assert unlimited usage or absence of operational rate limits. Do not fabricate or prefill evidence. The official [Content Partner API](https://apis.justwatch.com/docs/api/) is separately documented and requires a concluded contract and partner token; it does not authorize use of this unofficial GraphQL endpoint.

Required field shape only; the values below are placeholders, not evidence:

```json
{
  "justwatch": {
    "status": "available or unmetered_public",
    "checked_at": "<current UTC timestamp>",
    "remaining": "<integer >= 6 when status is available>",
    "access_permission": {
      "status": "authorized",
      "checked_at": "<current UTC timestamp>",
      "evidence_reference": "<reviewed written permission reference>",
      "endpoint": "https://apis.justwatch.com/graphql",
      "maximum_requests": "<integer >= 6 explicitly covered by permission>",
      "scope_ids": ["dune-nl-justwatch", "inception-nl-justwatch"]
    },
    "rate_policy_evidence": {
      "checked_at": "<current UTC timestamp>",
      "evidence_reference": "<reviewed current source reference>",
      "max_burst": "<integer >= 6 supported by the evidence>"
    }
  }
}
```

The JSON shape above is illustrative only; none of its placeholder values are evidence. The preflight blocks when express permission is absent, stale, covers another endpoint, does not cover both exact scopes, or allows fewer than six requests. Permission must be reviewed before considering the rate/quota gate satisfied.

Read-only commands:

```sh
python manage.py availability_experiment_preflight
python manage.py availability_experiment dry-run
```

After code review, genuine current rate-policy/quota evidence, clean operational/database gates, and separate explicit authorization, the authorization command requires the exact digest, database identity, cap, expiry, and affirmative live-execution flag. It writes a mode-0600 token file; it does not itself make provider requests or application writes.

```sh
python manage.py authorize_availability_experiment \
  --confirm-live-execution \
  --confirm-manifest-digest <sha256-from-review> \
  --confirm-database-identity <identity-from-preflight> \
  --maximum-attempts 6 \
  --expires-at "<future-ISO-8601-with-timezone>" \
  --output /secure/path/stage2s.authorization
python manage.py availability_experiment execute \
  --authorization-file /secure/path/stage2s.authorization
```

The engineering change does not set quota evidence, create an authorization token, or invoke `execute`.

## Remaining execution gates

- Obtain written JustWatch permission for the exact unofficial GraphQL endpoint, both fixed scopes, and at least six requests; obtain genuine current quota/rate-policy evidence adequate for that ceiling. A public endpoint with no billed API quota does not imply unlimited request capacity.
- Run the read-only preflight against the actual target database and exact revised manifest. Confirm the cluster/database identity, migrations, empty or reconciled durable ledger, matching enabled source and Movie/Country rows, no pending exact-scope requests, no unfinished runs, no relevant advisory locks, and no ambiguous reservations.
- Confirm ordinary worker/cron ownership and recheck queue/lock state immediately before any future authorized run; PostgreSQL cannot attest to processes outside its view.
- Do not claim application/provider accuracy from this experiment. Independent official-provider evidence requires a separate task and authorization.

Never point tests at the `wheretfdoiwatchit_pgdata` volume. Use mocks or an isolated disposable PostgreSQL instance.
