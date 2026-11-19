# Discovery request hardening and source audit — 2026-11-18

**DISCOVERY_HARDENING_READY** — implementation and offline verification only.
**HydraHD live automated discovery remains BLOCKED: permission/provenance unverified.**

Work is isolated on `hardening/discovery-request-controls`, based on the verified
deployed application `09684bae89cf20908ddb4a382452b0efb4a6045e`. No merge, push,
deployment, live crawl, provider-setting change, live migration or reconciliation
apply was performed. The local implementation commit is reported with delivery.

## Root cause and implementation

The old page walker bounded calls to `Session.get`, not actual outbound attempts.
Requests could follow redirects internally through `send` without incrementing
that counter. There was no independent page budget, and sleeping between page
iterations left the first robots-to-listing pair unpaced. HydraHD's `PERMITTED`
label was assigned by the collector rather than established by permission
evidence. Adapter mirror lists and historical success were not sufficient proof.

The new implementation provides:

- `DiscoveryHTTP`, the single sequential egress gate for discovery. Every robots,
  listing, metadata, retry and redirect attempt reserves budget before dispatch.
  Operator and reviewed per-provider caps both apply; the lower wins. There are
  no automatic library retries, environment proxies, cookies or credentials.
- Explicit redirect handling, with an independent finite hop limit and validation
  before destination dispatch. Cross-host redirects, resource/query changes,
  URL credentials, unsafe schemes/ports and forbidden network destinations fail
  closed. DNS answers are validated and the TLS peer address is pinned, retaining
  hostname/SNI/certificate validation. Private, loopback, link-local, reserved,
  multicast, site-local, unspecified and unsafe mapped addresses are rejected.
- One injectable monotonic request clock for every attempt, enforcing at least
  two seconds plus any stricter provider/robots policy. Existing bounded retries
  and Retry-After cooldowns remain; a 429 without a usable header receives a
  five-minute cooldown. Cooldowns are checked across discovery modes before run
  writes or HTTP. Diagnostics exclude raw URLs, Location values and secrets.
- A separate page-attempt ceiling, global across partitions and checked before
  calling the adapter. A failed page consumes a page attempt. Retries cannot
  obtain another page, redirects cannot change the page resource, and checkpoint
  resume uses the existing mechanism. CLI/scheduler defaults are one page;
  service calls retain a finite default of ten, capped by provider policy.
- A reviewed permission registry with scope, evidence, review/expiry dates and
  request/page/rate caps. Unknown, expired or denied permission stops before
  scraper creation, DNS, lease creation or source requests. Operator denial and
  disabled provider settings also stop the run. No CLI/JSON approval bypass was
  added. **The runtime registry contains no verified unofficial automation grant.**

The disabled canary template now includes `max_pages=1`, `max_redirects=3` and a
five-attempt discovery budget that includes robots. No enable switch changed.
No matching/classification threshold, model or migration changed.

See [request-control runbook](../sources/DISCOVERY_REQUEST_CONTROLS.md) for exact
semantics, request-accounting fields and the conditional command shape. This
gate covers the unofficial discovery command/scheduler; unrelated legacy
scraper commands must not be used to bypass it.

## Deterministic regression results

The focused discovery/reconciliation/gateway selection passed **185 tests**.
The authoritative serial complete backend verification passed **3,423 tests**.
There are **26 dedicated request-control regressions**, covering all requested
cases plus DNS pinning, sensitive-header stripping, mixed DNS answers, IPv6
transition/private addresses, metadata accounting, endpoint scoping and direct
session bypass prevention.

| Required case | Dedicated regression coverage |
|---|---|
| One-page ceiling | `test_one_page_global_ceiling_and_checkpoint_resume` |
| Page exhaustion | `test_provider_page_budget_caps_requested_window`, `test_invalid_page_limit_does_not_dispatch_or_write_state` |
| Request exhaustion | `test_request_exhaustion_after_robots_never_fetches_listing`, `test_request_exhaustion_and_failed_requests_consume_budget` |
| Redirect accounting | `test_redirect_accounting_spacing_and_sensitive_headers`, `test_redirect_cannot_bypass_page_or_request_budget` |
| Cross-domain redirect | `test_cross_domain_redirect_is_rejected_before_destination_dns` |
| Redirect loops/limit | `test_redirect_loop_and_finite_limit` |
| Private destinations | `test_private_network_redirects_and_unsafe_urls`, mixed-DNS and IPv6 tests |
| Robots/listing spacing | `test_every_request_kind_shares_spacing_and_budget`, one-page integration test |
| Retry spacing | `test_retry_spacing_and_attempt_accounting` |
| Retry-After/cooldowns | `test_retry_after_persisted_cooldown_blocks_every_mode_before_dispatch`, missing-header fallback test |
| Failed attempts count | `test_failed_page_consumes_page_budget_without_advancing_checkpoint`, transport timeout test |
| Interrupt/resume | `test_interrupt_preserves_checkpoint_and_resumes_same_page` and retained crash/lease regressions |
| Unverified permission | `test_unverified_denied_expired_disabled_or_failed_preflight_never_dispatches` |
| Denied permission | Same test plus `test_disabled_provider_and_operator_denial_cannot_dispatch` |
| Disabled schedules | `test_disabled_schedules_cannot_dispatch` and existing scheduler tests |
| Failed preflight: no dispatch | Permission test asserts neither DNS nor `_send` occurs and no state row is created |

Migration consistency: `makemigrations --check --dry-run` reports no changes;
`migrate --check` passes on the restored clone. Django system checks report no
issues. **21 deployment-controller safeguard tests passed** without Docker
mutations. `git diff --check` passes. No dependencies were installed or fetched.

Two intermediate test runs accidentally overlapped on the disposable Django
test database; both were stopped and excluded. The final complete run was
serial against a newly recreated test database. Neither the live database nor
the restored rehearsal database was targeted by that overlap.

## Offline end-to-end evidence

The live database was dumped read only and restored into the separate
`incremental_hardening` database in `wtdw-discovery-hardening-db`. The test runner
used the already available immutable Python image with an isolated source copy,
an internal-only Docker network, empty real API credentials and an additional
DNS/socket guard allowing only clone PostgreSQL. Fixtures supplied every HTTP
response; **external HTTP attempts were zero**.

`scripts/validate_discovery_hardening.py` verifies its exact PostgreSQL clone
name/host and completed baseline before simulations. Results:

- One historical movie page, **133**, following checkpoint 132, consumed exactly
  **two fixture attempts**: robots at monotonic time 0 and listing at time 2.
- Exactly one new discovery, clone ID **5096**, became naturally eligible as
  `new_discovery`; it remained pending persisted classification. The shared
  classifier correctly inferred **movie** from the movie partition/listing and
  extracted year 2020. TV checkpoint stayed 77; movie checkpoint became 133.
- Reconciliation dry-run selected only that naturally eligible record, verified
  exact title/year/type against fixture search and details for TMDb ID
  **1999998001**, and returned `VERIFIED` / `tmdb_search_and_details`. It consumed
  **2/8** permitted fixture attempts, no retries or cache writes. This is fixture
  evidence, not verification that a real-world title has that identity.
- Repeated preview returned the same proposal and changed no table. Ambiguous
  search used one fixture attempt and returned `AMBIGUOUS`; contradictory details
  used two and returned `CONFLICTING`. A clone-only contradictory mapping fixture
  used two and was rejected as an existing-mapping conflict, without modification.
  This fixture was rolled back; no reconciliation apply was called.
- Replaying the same moving listing at the next checkpoint page detected repeated
  nonterminal content, inserted no duplicate identity and retained checkpoint
  133. No checkpoint was reset, skipped or manually advanced.
- Across the five independent preview scenarios, fixture TMDb accounting was
  **2 + 2 + 1 + 2 + 2 = 9** attempts, each within its independent budget of 8.
  Discovery plus replay used **2 + 2** fixture attempts. These are simulated
  dispatch counts; no actual source/TMDb request occurred.
- Every dry-run preserved all **60** table digests. The overall simulation changed
  only discovery/checkpoint rows before rollback. The **4,769 associations** and
  **zero offers** remained intact. Scenario rollback and clone-only restoration of
  nontransactional sequence values restored all **60 tables / 50 application
  tables and 57 sequences** to the pre-scenario snapshot.

The existing incremental integration rehearsal also passed after adapting its
discovery fixture to the guarded transport: all scenarios passed and all 50
application-table hashes matched after rollback. Its explicit reconciliation
apply simulations were confined to the disposable clone; no live apply occurred.

## Source verdict and next live test

The [HydraHD permission audit](../sources/HYDRAHD_PERMISSION_AUDIT.md) distinguishes
verified permission, robots policy, technical reachability, successful historical
requests and assumptions. **Ownership/provenance, automation permission and
operator-issued limits remain unverified.** The `.ru` provider setting versus
`.gd` collector discrepancy is preserved. No mirror, robots, sitemap, status
page or provider endpoint was probed to resolve it.

Retained evidence identifies TMDb as an already integrated official keyed
metadata authority, making a separately authorized metadata smoke test a safer
candidate. It does not supply provider catalogue discovery and cannot replace
the full canary. Streaming Availability is title-targeted and can create offers.
AnimeWorld feed publication does not establish an exception to documented
anti-scraping terms. Archive item licensing is separate from automated API
permission. No already-approved official catalogue adapter covering this exact
pipeline was established from the retained evidence.

There is **no justified live crawl procedure at present**. A future procedure
requires primary permission/domain evidence, reviewed scoped registry approval,
resolution of provider settings, an independently authorized pinned release and
a separately authorized one-page canary. The conditional command in the runbook
does not confer permission or authorization. Do not enable schedules or use
legacy commands/mirrors to work around the blocked preflight.

## Live preservation and reproduction

Live remained on image
`sha256:5ac32c0066a33187345092c3119241df5af8c68f4937f9667100e9a1a5c1592a`,
application `09684bae89cf20908ddb4a382452b0efb4a6045e`, tracker `0058` and
availability `0016`. Backend health remained healthy with **zero restarts**,
the same container ID and unchanged disabled schedule configuration.

All **60 public-table counts, full-row digests and per-row hashes**, including
all **50 application tables**, matched before/after. All **57 sequences** matched.
There are **no live differences** to explain. The 5,095 discoveries, 4,769
associations/proofs/provider-content identities, 326 deferred outcomes, 3,300
legacy mappings, movie/TV checkpoints **132 / 77**, provider/settings rows and
zero offers remain unchanged.

Primary commands (all test commands used the isolated runner):

```sh
docker exec new-sources-db-1 pg_dump -U tracker -d tracker -Fc > live-readonly.dump
docker exec -i wtdw-discovery-hardening-db pg_restore \
  -U hardening -d incremental_hardening --no-owner --no-privileges < live-readonly.dump
docker exec wtdw-discovery-hardening-runner python manage.py test \
  tracker.tests.test_discovery_controls tracker.tests.test_catalogue_discovery \
  tracker.tests.test_catalogue_identity tracker.tests.test_kisskh_catalogue_discovery \
  tracker.tests.test_catalogue_discovery_scheduler \
  tracker.tests.test_incremental_catalogue_reconciliation tracker.tests.test_reconciliation_parity \
  tracker.tests.test_catalogue_reconciliation tracker.tests.test_canonical_bootstrap \
  availability.tests.test_candidate_gateway --noinput -v 1
docker exec wtdw-discovery-hardening-runner python manage.py test tracker availability --noinput -v 1
docker exec wtdw-discovery-hardening-runner python scripts/validate_discovery_hardening.py \
  --database incremental_hardening --host wtdw-discovery-hardening-db
docker exec -e TMDB_API_KEY=fixture-only wtdw-discovery-hardening-runner \
  python scripts/validate_incremental_catalogue.py \
  --database incremental_hardening --host wtdw-discovery-hardening-db \
  --output /tmp/incremental-compatibility.json
docker exec wtdw-discovery-hardening-runner python manage.py makemigrations --check --dry-run
docker exec wtdw-discovery-hardening-runner python manage.py migrate --check
docker exec wtdw-discovery-hardening-runner python manage.py check
python3 -m unittest discover -s ops/deployment -p 'test_release.py' -v
git diff --check
```

Private evidence is retained under
`.catalogue-staging/discovery-hardening-20261118/`: authoritative serial
`full-verified.log`, focused logs, offline end-to-end and compatibility JSON,
live before/after captures, filtered runtime inspections, deployment/migration
checks, the read-only dump and socket guard. Credentials were not copied from
the live environment. The committed JSON summary contains preservation digests
and verification results without private database rows.
