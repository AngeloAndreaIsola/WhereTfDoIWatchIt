# Bounded discovery request controls

Discovery is fail closed. The checked-in permission registry currently has no
verified unofficial automation grants. In particular, HydraHD and AnimeWorld
return `BLOCKED` before scraper creation, DNS, run/lease creation or HTTP. The
underlying old scraper adapters are not a permission bypass. This gate covers
`discover_unofficial_catalogue` and its scheduler entry. Legacy execution and
matching additionally fail closed through the shared scraper policy; see
[merge-readiness boundaries](MERGE_READINESS.md). Parser registration cannot
authorize legacy HTTP or TMDb calls.

The collector owns one `DiscoveryHTTP` transport per provider run. Every actual
GET reserves an attempt before its socket-producing `_send` seam, including
robots, listing, explicit redirect and retry attempts. A reviewed provider cap
and the operator request budget both apply (the lower wins). Automatic pool
retries and redirects are disabled. Session GET/request paths route through the
gate; direct prepared sends and non-GET methods are rejected. Metadata has a
separate reviewed endpoint scope and shares the same budget/clock.

The page ceiling counts **page attempts**, including a page that fails during
robots, HTTP or parsing. It is global across selected partitions, enforced before
calling the page adapter, and bounded by the reviewed provider page cap. Retries
remain inside the same page attempt. Checkpoints retain the established commit
and resume mechanism. Redirects cannot change the active resource/query or
move to another page; direct second-resource requests in a page also fail.

Only HTTPS on port 443, the exact approved hostname and reviewed paths are
accepted. URL credentials, controls, fragments, alternative schemes/ports and
unapproved hosts are rejected before destination dispatch. Every DNS answer
must be globally routable. The TLS connection pins one validated address,
preserves the approved hostname for SNI/certificate verification, and does not
resolve that hostname again during connection. Mixed public/private results
fail closed. No environment proxies, cookies, auth or sensitive request headers
are forwarded. Only User-Agent and Accept are carried to the approved host.
The redirect hop limit is finite (0–5; default 3).

One injectable monotonic clock spaces every attempt in the run: at least two
seconds, the operator delay, the provider minimum, and any stricter observed
robots crawl-delay/request-rate interval. Retries and redirects cannot skip
spacing. Existing bounded backoff/Retry-After behavior remains; long waits
become persisted provider cooldowns rather than an inline worker sleep. A 429
without a usable Retry-After receives a five-minute cooldown. All discovery
modes check persisted provider cooldowns before run bookkeeping or HTTP.

Reports contain the effective attempt/page limits, total actual attempts,
page attempts, and per-attempt kind, start time, redirect hop, retry flag and
HTTP status when received. Robots/listing/metadata counts partition the total;
redirect/retry counts are overlapping tags and must not be added to that total.
Failed dispatches still consume attempts. Diagnostics use stable reason codes,
numeric Retry-After values and exception class names; raw credential-bearing
URLs, redirect Location values, headers and exception text are not logged.

CLI defaults are one page, two-second minimum spacing and zero retries. Service
callers can request a finite larger page window (service default 10); scheduler
configurations have an independent `max_pages` setting defaulting to 1 and
`max_redirects` defaulting to 3. All existing scheduler switches remain disabled.

After separate permission review, deployment approval and live-run authorization,
the bounded command shape would be:

```sh
# EXAMPLE ONLY: current permission preflight must still block HydraHD.
python manage.py discover_unofficial_catalogue \
  --provider hydrahd --mode historical --partition movies \
  --max-pages 1 --request-budget 5 --max-redirects 3 --delay 2 --retry-limit 0
```

No checkpoint reset or manual advancement is needed. The present evidence
does **not** justify running this command against a source. See the
[permission audit](HYDRAHD_PERMISSION_AUDIT.md).

Offline integration reproduction on an exact restored clone, with external
egress blocked at both network and socket levels:

```sh
python scripts/validate_discovery_hardening.py \
  --database incremental_hardening --host wtdw-discovery-hardening-db
```

The script checks the clone name/host and completed baseline, uses an explicit
fixture-only permission and deterministic HTTP/clock, simulates discovery,
eligibility and reconciliation preview/conflicts, verifies every dry-run is
read only, then rolls back scenario rows and restores clone sequence values.
Fixture permission is not included in the runtime approval registry.

## Bounded Tubi catalogue requester

`tracker.services.catalogue_only_requester.collect_tubi_catalogue_page` is
an internal entry point. It requires the same enabled provider and reviewed,
unexpired grant as the catalogue-only importer, including after each wait and
before each redirect dispatch. The checked-in registry has no Tubi grant.
There is no command, worker or scheduler binding for this entry point.

A future approved canary must name a single title sitemap that is present
in the index and within the reviewed URL scope. Each invocation reads robots,
the index, that sitemap, and one title page, with at most five HTTP attempts
including redirects. All attempts share a minimum two-second gap. Robots
can tighten the gap or block individual URLs, including redirect targets.
The requester holds the existing provider lock through the final cooldown.
Transport or parse errors, a denied redirect, and an unobservable title
stop the run before it can advance the checkpoint. Every invocation uses
zero retries and returns attempt accounting with successful observations.

Use the exact returned cursor and the previously committed cursor with
`import_catalogue_batch`. The returned cursor is scoped to the selected
sitemap. Import validates the grant again and commits the observations and
cursor together. The result contains title records only. Use only the
existing cache-only `reconcile_catalogue_records` to resolve those recorded
IDs. The requester does not publish playback offers or mark a source
catalogue complete. The offline regression uses retained detail pages and
synthetic sitemap/robots transport, with no provider or TMDb requests.
