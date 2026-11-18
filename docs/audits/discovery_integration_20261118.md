# Discovery safeguard integration and permitted-source assessment — 2026-11-18

Integration code: `7b51710811a16628a86aea8e18e9a48314581076`.
Previous target/deployed application: `09684bae89cf20908ddb4a382452b0efb4a6045e`.
The final documentation commit is reported separately at delivery.

**No catalogue source qualifies for a live canary on the evidence established here.**
The integration does not grant permission, deploy the application, enable schedules,
or authorize any live discovery or reconciliation.

## Integration and review

The remote and local `new-sources` heads agreed at `09684ba`; `7b51710` has that
commit as its direct parent. The clean target worktree was fast-forwarded without
rewriting history. Backup branch:
`backup/new-sources-before-discovery-hardening-20261118` → `09684ba`.

The target checkout is
`/Users/angeloandreaisola/orca/workspaces/WhereTfDoIWatchIt/incremental-new-sources-integration`.
The supplied working directory is a different checkout on the hardening branch.
Its three untracked audit documents were left untouched. Changes in the master,
integration-safety and reconciliation-parity worktrees were also left untouched.
No worktree, volume or backup was deleted.

Read-only inspection of every running Docker container found no mounts of the
target checkout. A dev frontend mounts the hardening checkout's `frontend/`;
the integration changes no frontend files. The deployed backend has an immutable
image ID, read-only root filesystem, no source mounts and an independent frozen
release bundle outside all Git checkouts. Updating Git cannot replace its image
or change its code. Other running workers belong to the separate
`wheretfdoiwatchit` Compose project and have no checkout mounts.

Review of all executable changes and test fixtures confirmed:

- Every guarded HTTP dispatch reserves one request attempt, including redirects,
  retries, robots and metadata; transport failures consume the reserved attempt.
  DNS/policy rejection occurs before dispatch. Operator/provider caps take the
  lower value. A separate page-attempt ceiling spans partitions.
- Redirects are explicit and finite. Exact approved host, HTTPS/443, path scope,
  credentials, resource/query invariance and destination IP checks precede the
  destination request. Resource identity permits trailing-slash normalization.
- All DNS answers must be public; one validated address is used directly for the
  connection, with the approved hostname retained for TLS/SNI/Host verification.
  Library retries, proxies, cookies and session credentials cannot add requests.
- One monotonic clock spaces the run's attempts. Robots delay/rate can tighten
  it. Provider advisory locking serializes runs for that provider. Persisted
  cooldowns are checked across modes before run/lease writes; tested listing
  429 handling retains Retry-After and a five-minute fallback.
- Permission is a reviewed, expiring code registry, independent of adapter
  existence, reachability and robots. Unknown/denied/expired permissions and
  disabled providers fail before scraper creation, DNS and state creation.
  There are **no verified grants** in the runtime registry.
- The reconciliation parity implementation, matching thresholds, both migration
  trees (including tracker `0057`/`0058`), deployment tooling, source isolation
  and Compose files are byte-identical to the previous target. Scheduler global,
  provider and individual mode switches remain disabled by default.

Scope limitation: these controls protect `discover_unofficial_catalogue` and its
scheduler entry. They do not retrofit the separate availability collectors or
legacy commands. A future approved source must not use those paths to bypass the
new permission gate. No new source grant or adapter bridge was added.

## Validation and evidence

Authoritative results are recorded in the adjacent JSON summary. Tests use a
copied source tree from integrated `new-sources`, the existing Python image, an
internal-only Docker network and a Python DNS/socket guard allowing only the
disposable PostgreSQL server. No packages or images were fetched.

The live database was dumped read-only and restored, without running migrations,
into `incremental_hardening` in `wtdw-discovery-integration-db` (network alias
`wtdw-discovery-hardening-db`). The clone has no live database network attached.
The test runner has no real external API keys. Complete-suite tests use their
own disposable Django test database; rehearsals use the restored clone.

Required checks: focused discovery/reconciliation/gateway selection (185 tests),
complete backend suite (3,423 tests), deployment safeguards (21 tests),
`makemigrations --check --dry-run`, `migrate --check`, `manage.py check`, base/dev
overlay/test/release Compose `config --quiet`, and `git diff --check`.

An initial full-suite run had one harness error: the Compose fixture expected
beside `manage.py` was missing from the copied tree. It was supplied in the
isolated copy, then the complete suite and both rehearsals were rerun serially.
The dev Compose file is an overlay and was validated together with the base;
validating it alone correctly fails for lack of a frontend build/image.
Denied unmocked HTTP paths in the suite cannot resolve external DNS or connect
externally; fixture dispatch counts are not live requests.

Offline discovery rehearsal covers one-page resume, natural eligibility,
verified/repeated preview, ambiguity, contradictory details, existing mapping
conflict, moving-listing duplicate replay, checkpoint retention and rollback.
It checks all 60 tables and restores/verifies 57 sequences. The existing
incremental rehearsal also exercises its retained clone-only apply, replay,
changed-evidence, budget, interruption, lock and provider-isolation scenarios;
all 50 application table digests must match after rollback. No live
reconciliation or apply is run by either rehearsal.

Private evidence, live dump and full logs remain in the original hardening
checkout under `.catalogue-staging/integration-hardening-20261118/`.
Private rows, environments, credentials and the database dump are not committed.
Disposable test containers are stopped after validation and retained.

## Permission and domain assessment

Domain evidence here distinguishes established project captures and official
publisher identity from fresh DNS/ownership verification. No catalogue, sitemap,
robots, media or API endpoint was queried. Only indexed research and official
terms/documentation pages were consulted. No mirror or unverified source host
was probed. Current catalogue reachability and current DNS answers remain
unverified for **every** candidate.

| Candidate | Domain evidence and catalogue interface | Automation permission and authentication |
|---|---|---|
| RaiPlay | Official `www.raiplay.it`; provider setting is `raiplay.it`. Existing captured sitemap/programme/video JSON/HTML contracts. No documented, licensed third-party catalogue API established. | **Unverified.** No grant covering WTDW collection/storage. Existing client is keyless; viewing may require a Rai account. The official [account FAQ](https://www.raiplay.it/account/Faq-Crea-e-gestisci-il-tuo-Account-cb8a1d73-76d6-411b-acd9-8394531f9b64.html) establishes account behavior, not automation permission. |
| Pluto TV | Official `pluto.tv`; adapter POSTs to the site's GraphQL BFF `/api/tn/hubs/graphql/`. This is an observed web-app interface, not an established third-party licensed API. | **Blocked absent authorization.** [Publisher terms](https://legal.paramount.com/us/en/plutotv/terms-of-use?r=pluto.tv) restrict unauthorized automated gathering; other market terms also require prior permission. Existing client is keyless and market depends on egress. User-agent success is not permission. Applicable territory/grant must be resolved. |
| Tubi | Official `tubitv.com`, confirmed by its [terms page](https://tubitv.com/static/terms). Adapter uses sitemap and SSR title state. No documented third-party catalogue API grant established. | **Unverified; blocked.** The official terms body was not readable through the research tool, so no clause or permission is inferred from it. Secondary anti-automation excerpts were not used as a primary grant/denial finding. Existing catalogue client is keyless; some content has login/geo constraints. |
| PBS | Official `pbs.org`; captured adapter endpoint is `content.services.pbs.org/graphql/`, with web-platform headers and no key. Official [Media Manager documentation](https://docs.pbs.org/space/CDA/3050246/Changelog%2BEndpoint) documents a different consumer-scoped API and resource access checks. | **Unverified for this adapter/application.** API publication does not grant access to the current keyless GraphQL route. The separate Metadata Bank has [access profiles](https://support.pbs.org/support/solutions/articles/9000162335-requesting-access); neither an application grant nor relevant credentials were established. PBS is a useful future permission-review lead, not an authorized canary. |
| CBC Gem | Established project captures identify `gem.cbc.ca` and catalogue host `services.radio-canada.ca/ott/catalog/v2/gem`. The API host differs from the provider setting. Public consumer catalogue route, not an established licensed third-party API. | **Unverified.** Official terms retrieval was blocked by the research service; no alternative host/proxy was used. Client uses keyless `device=web` catalogue calls. Media probing requires claims tokens and is outside the adapter. Member/premium rights affect playback and do not authorize catalogue automation. |
| BBC iPlayer | Official `www.bbc.co.uk`; provider setting includes `bbc.co.uk/iplayer`. Captured A–Z and embedded page-state interfaces; no applicable official third-party catalogue API established. | **Blocked without applicable permission/licence.** Publisher-hosted [2022 terms, section 15](https://downloads.bbc.co.uk/usingthebbc/bbc_terms_of_use_19September2022english.pdf) restrict metadata extraction and require a business metadata licence. This is retained historical primary evidence; current UK terms were inaccessible to research, so current scope needs review. Catalogue client is keyless; viewing requires sign-in/licence and UK access. |
| ABC iview | Official `iview.abc.net.au`; captured `/api/series` and `/api/series/{slug}` contracts. An exposed web catalogue is not a third-party grant. | **Unverified; blocked pending written scope.** [iview terms](https://help.abc.net.au/hc/en-us/articles/8632119753231-ABC-iview-Terms-of-Use) reserve rights and restrict retaining/reproducing content without permission. No catalogue metadata exception/grant was established. Existing client is keyless; viewing account/territorial requirements must be considered separately. |

These are operational permission verdicts for this project, not a legal opinion.
None has verified operator-issued request limits or a cooldown agreement for
this use. Robots, a public feed, sitemap publication, an undocumented endpoint,
technical success and existing `Provider.is_enabled=True` do not fill that gap.

## Adapter, identity, checkpoint and write-risk assessment

Code evidence is the corresponding module under `backend/availability/sources/`
and `availability/services/refresh.py`. Older `CATALOGUE_CHECKPOINT_AUDIT.md`
reports missing checkpoints for several sources; current implementations have
since added them, so the old audit's FAIL entries are not current code findings.

| Candidate | Current local bounds (not publisher-approved limits) | Implemented checkpoint and identity/association risks |
|---|---|---|
| RaiPlay | Required finite HTTP budget on live paths; redirects charged; default 0.1s delay, up to 400 programmes. This is a separate transport, without the newly integrated DNS/permission gate. | v1 programme traversal anchor. Stable `ContentItem-UUID`; original release year often absent, publication dates must not substitute. Layout alone is weak movie/series evidence. |
| Pluto TV | Up to 100 grid pages per hub, 24 rows/page, plus collection requests. No shared per-HTTP budget/cooldown established for this adapter. | v1 per-hub offset + identity anchor, market scoped. Native movie IDs/show slugs, title/date; no TMDb IDs; market drift and show dates need caution. |
| Tubi | Up to 500 title entries plus sitemap overhead; 0.1s default delay. Entry cap is not an HTTP-attempt cap. | v1 per-sitemap last URL + cycle. Normalized numeric native ID, title/year/type and occasional IMDb ID provide comparatively strong identity, subject to geo/shell/episode handling. |
| PBS | Up to 100 allShows pages; 50-show detail chunks; 500-show default window. No enforced per-HTTP attempt budget established. | v1 last completed show slug. Show/asset identities are distinct; series records only; episode premiere dates must not become series release years. Full index reread means batch-size 1 is not a two-request ceiling. |
| CBC Gem | Three categories, up to 500 pages/category; 400-detail default window. No enforced shared per-HTTP attempt budget established. | v1 per-category last card identity. Stable show slug; title/year/type where supplied, no TMDb IDs. Episode/show aggregation and missing years can prevent safe matching. |
| BBC iPlayer | 60 entities/batch, 60 pages/slice, 0.1s default delay; index/detail overhead. No enforced shared per-HTTP attempt budget established. | v1 letter + last PID. Stable PID, but single programmes can be non-film material; brand/episode release dates need caution. A–Z truncation prevents completeness claims. |
| ABC iview | One full index plus up to 250 series details; hard 3,000-series cap, 0.18s delay. No enforced shared per-HTTP attempt budget established. | v1 last series slug. Series/house IDs, no trusted external identity; episode publication dates and title-parsed numbering are inadequate canonical-year proof. |

The baseline live snapshot has **zero `availability_synccursor` rows**, so none
of these candidates has a persisted availability checkpoint to resume there.
Their implemented checkpoint formats do not imply a populated live checkpoint.
The existing five unofficial discovery-state rows, including HydraHD historical
movies **132** and TV **77**, are a separate checkpoint system.

For all seven sources:

1. The adapters return `DiscoveredContent`/`ProviderOffer` through the availability
   refresh service; they do not naturally create the incremental worker's
   `DiscoveredCatalogueRecord` inputs. That worker currently accepts only
   HydraHD and AnimeWorld. No candidate can currently exercise this entire
   pipeline unchanged, and no provenance/type/year flags may be forged to do so.
2. Existing refresh commands can persist offers and mappings. Non-authoritative
   results prevent absence-based deactivation, but do **not** prevent offer
   inserts/upserts. Therefore no existing live refresh command is approved as
   an offer-preserving canary. A discovery-only staging bridge, permission gate,
   guarded transport and exact metadata validation would need separate work.
3. A fetch-only invocation on an isolated clone could avoid live writes, but it
   would still require verified permission, enforceable HTTP controls and
   explicit live-request authorization. None was executed.

## Next canary and exact offline alternative

**Recommended live source: none. Live request budget: zero.**
Do not deploy `7b51710` or run a legacy/availability collector as a substitute.
PBS's documented consumer APIs are a future permission-review lead; no access
request, email or message was sent. RaiPlay and Tubi are useful retained-fixture
identity examples, but neither has a verified grant for a live test.

The exact offline alternative, on a restored clone with the checked network and
socket guard, is:

```sh
docker exec wtdw-discovery-integration-runner python \
  /app/scripts/validate_discovery_hardening.py \
  --database incremental_hardening --host wtdw-discovery-hardening-db
```

It uses fixture-only permission; it is never added to the runtime registry.
Each discovery invocation has **one page, five simulated HTTP attempts maximum,
zero retries, at least two simulated seconds between attempts**. The one-page
scenario uses robots + listing (2/5), then replay uses another 2/5. Each of five
independent reconciliation previews has an eight-attempt fixture ceiling; actual
counts are 2, 2, 1, 2, 2. All source and TMDb responses are fixtures.

Expected clone changes before rollback: one discovery plus discovery state;
movie checkpoint 132→133, TV remains 77; replay inserts no duplicate and retains
133. The new row is naturally eligible, pending classification; previews write
no tables. All 4,769 associations, 326 baseline deferred records and zero offers
are preserved. On completion, transaction rollback and clone-only sequence
restoration must recover all 60 table hashes and 57 sequences. If the process
fails, retain logs and recreate a **new disposable clone** from the retained
dump; never repair/reset a live checkpoint or delete existing recovery assets.

Future authorization must name the source, exact verified host/endpoint/method,
permission evidence and expiry, credentials/market scope, publisher limits and
cooldown, the reviewed discovery-only adapter/registry SHA, and a pinned release.
First separately authorize any implementation and deployment; then authorize a
specific one-shot live request budget and allowed database effects. A proposed
starting envelope is one page, at most five total HTTP attempts including robots,
redirects and any authentication, zero retries, at least two seconds or the
stricter publisher rule, no TMDb requests and no reconciliation. It is only a
conditional design envelope, **not an executable or approved live procedure**;
the exact command must wait for a qualifying source and implemented adapter.
Capture full before/after row/schema/sequence hashes, permitted changed-row IDs,
and checkpoint evidence. All offers, existing associations, deferred records,
other providers and scheduler settings must match. Any failure stops the run;
resume only from its last committed checkpoint after cooldown and renewed
authorization. No blanket database rollback or live row deletion is authorized.

The next useful authorization is **offline source-bridge/permission-review work
only**, choosing a provider and supplying its applicable grant. Deployment and
live requests remain separate decisions.

## Live preservation

Before/after preservation details and digests are in the JSON summary. Required
invariants are: identical backend container and image
`sha256:5ac32c0066a33187345092c3119241df5af8c68f4937f9667100e9a1a5c1592a`,
revision `09684ba`, healthy, zero restarts, no source mounts; tracker `0058` and
availability `0016`; all public-table row hashes and sequences unchanged; 5,095
discoveries, 4,769 canonical associations/proofs/provider identities, 326 deferred
outcomes, 3,300 legacy mappings, checkpoints 132/77, zero offers and no
reconciliation-run/refresh-job rows. Provider settings remain unchanged.
`UNOFFICIAL_CATALOGUE_DISCOVERY_SCHEDULES` is empty, no target discovery worker
is running, and historical/incremental/reconciliation scheduler modes remain
disabled. No live migration, scheduler activation, backend replacement,
canonical-threshold change or reconciliation was performed.
