# Catalogue provider permission and feasibility review

Review/access date: **2026-11-18**. Base: `new-sources` at
`a8a0d1670e3d9f6c26f0a849e028e3e70852347a`.
Intended use: bounded automated catalogue metadata collection, durable native
identities and checkpoints, exact canonical reconciliation and metadata storage;
no playback, media downloading, availability inference or offer writes.

**No reviewed provider has an established permission grant for this application's
intended use. Authorized live discovery/TMDb request budget remains zero.**
Tubi is the strongest technical candidate for the offline bridge; PBS's supported
API programs are a separate promising route for obtaining explicit permission.
Neither recommendation is a permission grant or authorization to contact anyone.

## Evidence rules and summary

Only official publisher documentation/terms and retained project collector
captures are used as evidence. Online research accessed terms/help/documentation
or search indexes, never catalogue/robots/sitemap/API/media endpoints. No mirror,
geographical bypass, login, key request, provider outreach or live reachability
test was performed. Search results incidentally containing title pages were not
opened or used as authorization evidence.

The four permission categories mean:

- **Explicitly permitted:** an applicable grant affirmatively covers this
  application, endpoints, storage/reuse, territory and automation. **None found.**
- **Permission required:** publisher documentation requires an access approval or
  licence, with no applicable application grant established here.
- **Insufficient evidence:** the relevant terms/grant cannot be established from
  retrieved primary evidence. This is a live-execution block, not permission.
- **Explicitly restricted:** primary terms expressly restrict the proposed class
  of automation absent authorization. No exception is inferred.

| Provider | Permission verdict for this application | Existing adapter fit | Relative complexity/maintenance risk |
|---|---|---|---|
| Tubi | **Insufficient evidence**; terms retrieval is territorially blocked | Best movie identity: numeric ID, explicit type, production year, optional IMDb ID; existing retained fixtures and cursor | Low for this offline bridge; medium/high for live transport, SSR changes, geo access and permission |
| RaiPlay | **Insufficient evidence** | Stable UUID; dedicated finite HTTP budget and checkpoint, but programme layout/year are weak canonical evidence | Medium/high: multi-stage sitemap/programme/video walk, incomplete release evidence |
| PBS | **Permission required** for supported API programs; **insufficient evidence** for current keyless GraphQL adapter | Show identity/cursor available; supported APIs differ from current client | High: API access approval, auth, contract change and show/episode date semantics |
| CBC Gem | **Insufficient evidence** | Native show slug and per-category checkpoint; no trusted TMDb IDs | Medium/high: N+1 walk, missing years, API contract and permission uncertainty |
| Pluto TV | **Explicitly restricted absent authorization** | Native IDs, movie dates and resumable grid offsets | High: POST GraphQL, egress-selected market, bot behavior, undocumented third-party contract |
| BBC iPlayer | **Permission required**; retained publisher terms expressly restrict metadata extraction | Stable PIDs and letter/entity checkpoint | High: licensing, truncated A–Z, title-type ambiguity, page/slice changes |
| ABC iview | **Permission required**; written permission needed for retained/reproduced content | Series slugs and cursor; insufficient canonical release years | Medium/high: N+1 walk, API churn, episode/publication dates and permission |

All adapter limits below are **local implementation bounds**, not publisher
permission or rate allowances. No provider-issued quota/cooldown covering the
existing adapter and this application was established. A website being public,
free, browsable without login or described by a sitemap is not a grant.

## RaiPlay

**Official interface/open data:** The collector's retained captures establish
`www.raiplay.it` sitemap, programme HTML and video JSON/HTML interfaces used by
the public website. An official third-party catalogue API, supported automation
contract or open dataset licence covering this use was **not established**.
These endpoints were not queried during this review.

**Primary evidence, accessed 2026-11-18:**

- [Rai account FAQ](https://www.raiplay.it/account/Faq-Crea-e-gestisci-il-tuo-Account-cb8a1d73-76d6-411b-acd9-8394531f9b64.html),
  registration section: “accetta i termini e condizioni di utilizzo dei servizi
  del sito”. This establishes that terms apply; it is not automation permission.
- [Official Rai service FAQ](https://www.rai.it/centroassistenza/domande-frequenti/raiplay/che-cos-e-raiplay/RaiPlay-e-gratuita-dove-posso-vederla)
  describes RaiPlay as a free streaming platform. It supplies no catalogue
  automation, republication or database-storage grant in the retrieved evidence.

**Terms/licensing/permission:** Insufficient evidence; no application-specific
grant, metadata licence or documented third-party catalogue scope retained.
Free viewing does not establish reuse rights.

**Auth/geo:** Existing collector uses anonymous catalogue pages. Account and
territorial rights for viewing are separate; no account or alternative egress
was used. Current endpoint authentication/reachability was not tested.

**Limits/checkpoints:** Current `rai_play.py` requires a configured finite live
HTTP budget and charges redirects. Defaults include 400 programme entries and
0.1s delay, not a publisher-approved envelope. Its v1 programme identity anchor
is resumable. Several sitemap documents and programme/video hops may precede
one usable record, so five attempts may exhaust before a complete item.

**Catalogue-only/reuse:** `fetch()` returns value objects without persistence;
the bridge can ignore offers. Native `ContentItem-UUID` can fit existing
discovery/mapping models. Programme single/multi layout and publication dates
cannot manufacture movie/series release evidence. A reviewed parser binding
and reliable production year/type evidence are needed before association.

## Tubi — selected offline concrete collector

**Official interface/open data:** Retained fixtures show the publisher's sitemap
index/title sitemaps and SSR `window.__data` title state. No supported licensed
third-party catalogue API or applicable open dataset licence was established.
No sitemap/title endpoint was requested.

**Primary evidence, accessed 2026-11-18:**

- [Official terms URL](https://tubitv.com/static/terms) returned a territorial
  availability page via `https://gdpr.tubi.tv/` through the research service.
  Exact returned evidence: “Unfortunately, we're not currently available in
  your area.” The terms body was unavailable. No alternate route was attempted.
- [Official help: free/legal service](https://tubitv.com/help-center/About-Tubi/articles/4409975158043)
  describes a free, advertising-supported service. That does not establish
  permission for automated metadata extraction or storage.

**Terms/licensing/permission:** **Insufficient evidence.** Secondary excerpts
claiming anti-automation clauses were deliberately not promoted to primary
evidence. No inferred search-engine exception, account right or applicable
WTDW grant. Runtime permission remains unverified and blocks the new bridge.

**Auth/geo:** Captured catalogue client is keyless. Current terms retrieval
demonstrates a territorial research-access limitation; the existing fixture
research also documents EU interstitials. No VPN, proxy, authenticated viewer
session, cookie reuse or geographic bypass was used. Current catalogue access
and any authorised territory remain unverified.

**Limits/checkpoints:** `tubi.py` normally caps 500 title entries plus all selected
sitemap overhead and defaults to 0.1s delay. Its legacy client has retries and
implicit Requests redirects, so those settings are **not** a safe live HTTP
budget. A v1 per-sitemap last-URL/cycle checkpoint is available. The rehearsal
injects the hardened HTTP gate instead: one title page, five total attempts
including robots and a redirect, no retries, shared two-second simulated spacing.

**Catalogue-only/reuse:** The unchanged collector returns `DiscoveredContent`
and offer suggestions as values; it does not write offers. The bridge consumes
only content and its cursor. Numeric native IDs are normalized by the existing
parser. Movies carry explicit type and production year; IMDb IDs are preserved
as untrusted source evidence, never treated as a TMDb verification grant.
Series release year is deliberately absent because source years may represent
later seasons; such records defer without title-only association. No fabricated
TMDb ID or manually promoted media type is injected into discovered records.

**Risk:** SSR syntax/shape, ID padding, shell/interstitial responses, sitemap
overhead, and moving windows. Live eligibility still requires explicit rights
and production transport/checkpoint orchestration beyond this fixture-only
requester. The application bridge itself has no network option.

## PBS

**Official API/open data:** Supported Metadata Bank and Media Manager APIs are
documented. These are different interfaces from the existing collector's
`content.services.pbs.org/graphql/` endpoint and web-platform headers. API
publication is not a grant to the keyless web-app GraphQL interface.

**Primary evidence, accessed 2026-11-18:**

- [PBS requesting access](https://support.pbs.org/support/solutions/articles/9000162335-requesting-access),
  authorization profiles: “Each access request must align to one and only one
  access profile.” The page lists four roles and an application process.
- [PBS official release notes](https://support.pbs.org/support/discussions/topics/9000062562)
  document consumer-key/secret authorization at the token endpoint and bearer
  authentication at Metadata Bank REST endpoints. No token endpoint was called.
- [Media Manager changelog documentation](https://docs.pbs.org/space/CDA/3050246/Changelog%2BEndpoint),
  indexed primary documentation retrieved in this review and the prior audit:
  consumer access controls apply when following resource links. The page body
  was not readable on direct retrieval; this is indexed documentation evidence,
  not a tested access grant.

**Terms/licensing/permission:** **Permission required** for an approved supported
API consumer; no WTDW access approval, credential scope or downstream metadata
licence established. Current keyless GraphQL use has insufficient permission
evidence. The Metadata Bank role program must not be conflated with Media Manager.

**Auth/geo/limits:** Current adapter is keyless and reports US scope from its
captured contract. Official API programs require their own credentials/scopes;
no approval or geo entitlement was inferred. No current rate limit established
for the existing GraphQL collector. Numeric quotas published for another PBS
API must not be transferred. Current local bounds: 100 `allShows` pages,
50-show detail chunks and 500-show default batch. Batch-size one still rereads
the index; it is not a two-request cap.

**Catalogue-only/reuse/risk:** Fetch is persistence-free and returns show-level
content plus offers. V1 last-completed-show-slug checkpoint exists. Existing
models can store shows, but episode premiere dates cannot supply a series
premiere year. A supported-API implementation would need approved auth,
schema/identifier mapping, stronger release evidence and a new reviewed binding.

## CBC Gem

**Official interface/open data:** Retained client captures identify anonymous
`services.radio-canada.ca/ott/catalog/v2/gem` category/show metadata and
`gem.cbc.ca` viewing URLs. The collector uses `device=web`. No supported licensed
third-party catalogue API or open dataset grant for this use was established.

**Primary evidence access record, 2026-11-18:**
[CBC terms URL](https://www.cbc.ca/aboutcbc/discover/termsofuse.html) was
unavailable to the research tool (non-retryable retrieval failure; domain-restricted
search also reported robots blocking). **No primary clause was retrieved.**
This is a tooling access result, not quoted publisher permission or a finding
that the source itself permits/denies this use. Secondary legal filings and
unrelated CBC-branded websites were excluded. No mirror/proxy retry was made.

**Permission:** **Insufficient evidence**; retain live block until applicable
terms and explicit scope can be reviewed.

**Auth/geo/limits:** Captured catalogue calls are keyless; protected media
probing requires claims tokens and is not part of the collector. Captured
standard/member/premium rights concern viewing. CA is the adapter's catalogue
scope, not a proven current access entitlement. No publisher-approved quotas
or cooldown established. Local bounds: three categories, 500 pages/category,
400-detail batch; one invocation may issue many requests.

**Catalogue-only/reuse/risk:** Persistence-free fetch, v1 per-category identity
anchor; native slug/title/type/year where observable can fit existing models.
No trusted TMDb ID; missing years, show/episode aggregation, N+1 detail traffic
and changing APIs increase risk. A reviewed binding and canonical evidence
rules would be required; no source registration was added by this prototype.

## Pluto TV

**Official interface/open data:** The existing adapter uses POST GraphQL via
`pluto.tv/api/tn/hubs/graphql/`, an observed first-party website BFF, not an
established third-party supported API or licensed open dataset.

**Primary evidence, accessed 2026-11-18:**
[Publisher terms, Acceptable Use](https://legal.paramount.com/plutotv/terms-of-use):
“Engage in unauthorized spidering, ‘scraping,’ data mining or harvesting of
Content”. The surrounding clause also restricts other unauthorized automated
gathering. The [US terms URL](https://legal.paramount.com/us/en/plutotv/terms-of-use?r=pluto.tv)
identifies Pluto Inc. and an effective date of 2024-11-07. Applicable territory
and any specific contractual exception must be established separately.

**Permission:** **Explicitly restricted absent authorization.** Successful
requests, a non-browser User-Agent or arbitrary-query support are not permission.
No applicable authorization was established.

**Auth/geo/limits:** Captured endpoint is keyless; market is chosen by egress
and the returned `collection.region`, not a configurable country override.
No geo/bot avoidance attempted. No provider-issued quota/cooldown established;
local cap is 100 grid pages per hub, 24 rows/page plus hub requests.

**Catalogue-only/reuse/risk:** Persistence-free fetch can supply content only;
v1 per-hub offset/anchor and native IDs are useful. Movie dates can help identity;
series dates require caution. Current `DiscoveryHTTP` supports GET only, so the
POST BFF cannot be plugged in by weakening that guard. Approved protocol support,
market-scoped checkpoints and permission would require separately reviewed work.

## BBC iPlayer

**Official interface/open data:** Captured A–Z and embedded programme-page state
are website interfaces, not an established licensed third-party catalogue API.
No applicable open catalogue dataset grant established.

**Primary evidence, accessed 2026-11-18:** Publisher-hosted
[2022 English terms, section 15](https://downloads.bbc.co.uk/usingthebbc/bbc_terms_of_use_19September2022english.pdf):
“You’re not allowed to pluck metadata from our content or RSS feeds.” The business
section requires a metadata licence. This is historical primary evidence,
**not** verification that the current UK terms are identical. The current UK
terms URL was unavailable to restricted research; no alternate site was used.

**Permission:** **Permission required**, with an express extraction restriction
in retained terms and no application licence established. The BBC News RSS
exception is not a licence for iPlayer catalogue extraction.

**Auth/geo/limits:** Captured catalogue HTML is keyless; viewing metadata exposes
sign-in/TV-licence requirements and UK scope. Those flags do not grant catalogue
automation rights. No publisher-issued request quota established. Local bounds:
60 entities/window, 60 pages/slice and 0.1s delay; A–Z truncation prevents a
complete-snapshot claim.

**Catalogue-only/reuse/risk:** V1 letter/last-PID checkpoint and stable PIDs fit
native discovery. Fetch is persistence-free. Some standalone programmes are
not films; episode/brand dates and parser-default movie typing need a reviewed
identity contract. Licensing and website shape make this a higher-risk choice.

## ABC iview

**Official interface/open data:** Existing captures describe `/api/series` and
`/api/series/{slug}` on `iview.abc.net.au`. No third-party supported access
contract or open metadata licence covering this use established.

**Primary evidence, accessed 2026-11-18:**

- [iview terms, Use of ABC Content](https://help.abc.net.au/hc/en-us/articles/8632119753231-ABC-iview-Terms-of-Use),
  updated 2025-01-21, reserve content rights and restrict storage/reproduction
  “without obtaining the ABC's prior written permission”. Content includes
  text and images, so a catalogue metadata exception must be demonstrated.
- [Official account FAQ](https://help.abc.net.au/hc/en-us/articles/8551304941583-Why-do-I-need-to-log-in-to-watch-programs-on-ABC-iview),
  updated 2026-09-01: “You'll be able to browse the ABC iview website without
  logging in.” This does not authorize automated extraction/storage.

**Permission:** **Permission required**; no applicable written permission or
metadata exemption established. Viewing is personal/non-commercial under the
retrieved terms; the project must not infer an automated catalogue grant.

**Auth/geo/limits:** Captured API is keyless; official help distinguishes browsing
from account-required on-demand viewing and device exceptions. AU viewing scope
does not grant off-territory automation. No publisher quota established. Local
bounds: full index + 250 details/window, 3,000-series hard cap and 0.18s delay.

**Catalogue-only/reuse/risk:** V1 last-series-slug checkpoint exists; fetch is
persistence-free. Series/house identities fit models, but episode publication
timestamps and title-derived numbering cannot verify a series release year.
Identity evidence and N+1 request control need more work before safe association.

## Next provider and future canary requirements

**Recommend Tubi for technical preparation, with permission pending.** Compared
with the other captured collectors, its movie type/production year/native ID
give the existing exact verifier useful independent matching inputs. The
prototype does not guess series years or turn IMDb IDs into trusted TMDb IDs.
PBS remains the clearest supported-API permission-review lead, but adopting its
approved API would change the current adapter and evidence contract.

Before any live canary, retain an explicit applicable publisher grant/licence,
scope for catalogue automation/storage/reuse, exact host/path/method, territory,
auth requirements, source-issued rate/cooldown rules, evidence date and expiry.
Only then may a reviewed code change populate the permission registry. No CLI,
JSON setting, public-access fact or test fixture may self-approve a source.

Technical work still required for a Tubi live collector: a production guarded
requester covering sitemap/index/detail requests, explicit listing-page and
HTTP ceilings, no library retries/redirect bypass, robots policy, pinned DNS,
cooldown persistence and checkpoint-safe fetch/import orchestration. The fixture
requester is deliberately under `tracker/tests/`; it is not a production crawler.
The new reconciliation entry point uses cache evidence only and has no network
option. No new scheduler/management command is exposed.

Conditional canary envelope after those reviews: one movie page, **at most five
total source HTTP attempts including robots, sitemap metadata, redirects and
auth**, no retries, at least two seconds or the stricter publisher interval,
zero TMDb requests, schedules disabled, an exact expected cursor and one-shot
operator authorization. Real sitemap overhead may exhaust five attempts before
a title; stop without checkpoint advancement rather than enlarging the budget.
Expected writes must be limited to approved discovery/checkpoint rows. Canonical
reconciliation/apply and any deployment remain separate authorizations; offers
and existing associations must be unchanged. Preserve backup and before/after
row/schema/sequence digests. Interruptions resume at the last committed cursor;
never reset live checkpoints or delete live rows as an automatic rollback.

**Contractually eligible now: no. Technically eligible for this offline rehearsal:
yes. Technically eligible for a production live canary now: no.**
