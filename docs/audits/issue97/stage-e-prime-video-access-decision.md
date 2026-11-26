# Stage E Batch A source decision for Prime Video

**E-C2 / E02–E05 and E08 · Inception (2010), NL movie 27205 · BLOCKED.** Source access, extraction, retention and publication are unsubstantiated. Owner approval is pending. The historical rows bind one detail document to five separate decisions. [Source review](stage-e-source-review.md) · [Batch A plan](stage-e-batch-a-plan.md)

| Decision | Proposed scope and evidence required |
| --- | --- |
| Access | One anonymous HTTPS GET on port 443 to `www.primevideo.com`. The only proposed path is the exact `/detail/` film path from the E02 pointer in the frozen matrix. Resolve the historical `public_url` in the accepted-observation ledger offline. Pin the full literal path, detail ID and film GTI privately and reconcile them to the four historical pointers for E-C2. No prefix or wildcard admission, query, redirect or retry. Expect a public film detail HTML document with its embedded same-film fields. The source reviewer must substantiate automated access under legitimate anonymous NL context and record the permitted limit, pacing and cooldown. |
| Extraction | Request separate approval to parse the detail ID, film GTI, media type, title and year or reviewed edition binding, effective and recorded territory, and only the same film's benefits, class actions and transactional price conditions listed below. Read those fields from the sealed response offline. Exclude recommendations and unrelated GTIs, viewer/session data, playback metadata and links. |
| Retention | Request custody of the one raw HTML response with its embedded film fields and only the necessary review metadata through review or until **2026-12-09 14:00 UTC**, whichever comes first, subject to any shorter source limit. Record whether storage of embedded price and channel data is permissible. If custody cannot support review of a required field, keep the affected rows pending or omit them before issuance. |
| Derived publication | Request separate approval to publish scoped availability decisions, reason codes, class-level totals and sanitized provenance. Ask separately about price agreement counts, mismatch direction and exact EUR-cent amounts. Publish each price output only if expressly allowed. Omit GTIs, detail IDs, raw offer labels and embedded data, exact locators and headers from Git and GitHub. |

| Candidate | Evidence to review on the same film GTI |
| --- | --- |
| E02 `prime/subscription` | A title-specific base Prime benefit with its qualifying action. A general trial or channel-only benefit leaves base Prime unknown. |
| E03 `prime/rent`, E04 `prime/buy` | Distinct full-film rent and buy actions. For each, establish EUR cents, edition, quality, price basis, promotion and access conditions before any price comparison. Incomplete price evidence leaves price unknown even if the offer class is established. |
| E05 `max_amazon/addon` | Same-film HBO Max channel benefit under parent `prime`. Review the package and parent separately from base Prime, store actions and standalone Max. |
| E08 `prime/free` | Explicit current full-film viewing at zero payment without membership or ads. The control has no historical outcome. A preview, trial or missing action leaves it unknown. |

Require the recorded and effective territories to agree on NL. Validate Inception (2010), its typed film ID and edition before accepting class signals. An explicit current negative must cover that class, service and relevant variants and requires a second reviewer. Keep catalogue presence, advertised class, entitlement and playback as separate fields. Entitlement and playback remain `untested`. [Typed identity decisions](../stage2b/issue97-identity-decisions.md) · [Evidence protocol](stage-e-evidence-protocol.md)

The proposed Inception slot is **2026-12-02 [14:00, 14:30) UTC**. Record UTC request bounds, monotonic elapsed time, synchronization and uncertainty for the acquisition host, and original assertion time and freshness evidence. The private receipt binds E-C2 to one sealed body digest and byte count, exact locator digest, reviewed film binding, territory decision and custody reference. Point each class decision back to that receipt. [Batch A plan](stage-e-batch-a-plan.md)

## Private decision to complete

- Source reviewer records a separate allow, deny or unresolved decision for access, extraction, retention and derived publication. Attach dated terms or permission, version/effective date, territory, approved fields and price outputs, pacing, review date, reuse restrictions and expiry.
- Custodian specifies restricted storage, named readers, earliest applicable deletion deadline, deletion owner and permitted surviving metadata. Check sanitized class and price outputs individually before release.
- Owner lists included and omitted rows, signs the exact manifest and allowlist digests with the slot and expiry, and attaches the reviewed preflight and clock receipts. Current execution allowance is zero.
