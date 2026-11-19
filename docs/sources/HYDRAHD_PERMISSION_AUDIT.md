# HydraHD provenance and automated-discovery permission audit

Review date: **2026-11-18**. Evidence scope: retained repository documents,
the previous canary's read-only observations, and deployed code. **No external
HTTP, DNS investigation, site visit, legal-status search or mirror probe was
performed.** This is an evidence assessment, not a determination of ownership
or legal rights.

**Verdict: UNVERIFIED — automated discovery remains blocked.**

| Question | Retained evidence | Assessment and confidence |
|---|---|---|
| Current domain | `CATALOGUE_DISCOVERY.md`, HydraHD section, records `.gd` listings and a self-assertion by `hydrahd.info` that `.gd` is primary. Live provider settings still name `hydrahd.ru`. | Association is historically self-asserted, not independently verified. High confidence that current verified ownership/provenance is missing. |
| Ownership/operator | No retained operator identity, authenticated operator statement tying these hosts together, independently corroborated ownership record or equivalent primary evidence. | **Not established.** A domain string, working HTML or another site's claim is insufficient. |
| Automated catalogue permission | Historical discoveries are labeled `PERMITTED`, but the collector sets that label itself. No retained explicit permission/license covering automated movie/series catalogue requests. | **Not established.** Labels and historical success cannot supply a grant. |
| Robots access | Documentation records an earlier robots observation permitting ordinary reference use. The retained summary does not independently timestamp each robots response or establish a current automation grant. | Historical robots policy only; current content unverified. Robots access and permission are separate. |
| Technical reachability | Historical `.gd` listing requests succeeded. Earlier sitemap investigation returned 403 and was not retried. | Historical reachability only; no current reachability claim or permission inference. |
| Restrictions | No persisted HydraHD cooldown/restriction was present at canary preflight. Historical sitemap 403 and earlier `.ru`/`.ws` access problems are documented. | Absence of a persisted restriction is not proof that access is permitted. Do not retry restricted endpoints or change hosts to evade a block. |
| Request limits | Earlier runs used one-second spacing; the proposed canary specifies two seconds, one page, five attempts, no retries. No retained operator rate policy is established. | Earlier operating settings and the user's ceiling are not source-issued limits. Applicable source limits remain missing. |
| Redirects/alternate hosts | Older scraper adapters list `.gd` and `.ws`; the historical discovery service pins `.gd`. | No approved alternate/mirror. Hardened discovery rejects cross-host redirects, even if the alternate appears in an older adapter. Same-host redirects must retain the exact resource (query included, trailing slash normalization only), pass policy and public-address checks, and fit the redirect/request budgets. |

The primary retained research is [Catalogue discovery operations](CATALOGUE_DISCOVERY.md),
especially its HydraHD and AnimeWorld sections. Historical troubleshooting in
[Scraper fix report](../SCRAPER_FIX_REPORT.md) describes previous domain/access
issues; it is not a present authorization. The preceding
[live canary report](../audits/live_discovery_reconciliation_canary_20261118.md)
records the actual provider-domain discrepancy and zero-request stop. Historical
observations precede this review; the canary baseline/checkpoints were captured
on 2026-11-17/18. Where an individual permission/robots observation has no retained
capture timestamp, its exact observation date is **unknown**.

To change the verdict, retain primary evidence identifying the responsible
operator and linking the specific hostname to it, an explicit grant covering
this automated catalogue use and endpoints, applicable access/rate/redirect
constraints, the evidence's source/capture date and review expiry. Resolve the
`.ru` provider setting versus `.gd` collector discrepancy through reviewed
evidence and a separately scoped settings change. A robots response, certificate,
public DNS record or one successful request alone would not settle permission.
No permission can be enabled by provider JSON or a CLI bypass switch. A reviewed
code change must populate the permission registry with scope, primary evidence,
review/expiry dates and limits. Explicit operator denial still wins.

## Safer official-source investigation

| Existing integration | Retained approval/evidence | Can it replace this full live canary now? |
|---|---|---|
| TMDb API | [SPEC](../SPEC.md) already identifies the keyed official metadata API as the title/identity authority. The existing gateway supports bounded search/details and caches. | Best existing route for a separately authorized **metadata verification** smoke test, after confirming account scope/current API terms. It does not discover provider catalogue identities and cannot prove the full discovery-to-reconciliation path. No request was made. |
| Streaming Availability API | SPEC already identifies the licensed keyed availability integration. | Title-targeted; not a catalogue discovery source, and its normal path may create offers. It cannot silently replace a zero-offer provider-discovery canary. |
| AnimeWorld official RSS | Retained changelog research calls the feed official; previous feed requests succeeded. The same documentation records anti-scraping terms for the site/archive. | Official publication does not establish an automation exception to those terms. No current reviewed exception/grant is retained. Its discovery permission stays unverified too; no archive or RSS requests were made. |
| Internet Archive / Library of Congress catalogue collector | [Archive rights policy](../PUBLIC_DOMAIN_ARCHIVE.md) reviews per-item licensing and territorial scope; source registration alone does not enable collection. | Item rights do not establish automated API access approval. No complete retained current automation approval/rate evidence or existing adapter into this reconciliation selector was established. Not promoted to an approved live replacement. |

The current incremental reconciliation selector supports HydraHD and scoped
AnimeWorld observations. Official API or archive output must not be relabeled
as HydraHD records to make a test pass. No already-approved official catalogue
adapter covering this exact end-to-end path was established from retained
evidence. Keep the complete pipeline fixture-driven until a suitably approved
source and adapter are separately reviewed. There is **no justified live crawl
procedure under the present evidence**.
