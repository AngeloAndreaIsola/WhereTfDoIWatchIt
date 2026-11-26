# Stage E Batch A source decision for Rakuten TV

**E-C3 / E06, E07 and E09 · Dune (2021), NL movie 438631 · BLOCKED.** Source access, extraction, retention and publication are unsubstantiated. Owner approval is pending. The historical rows bind one NL movie document to three separate decisions. [Source review](stage-e-source-review.md) · [Batch A plan](stage-e-batch-a-plan.md)

| Decision | Proposed scope and evidence required |
| --- | --- |
| Access | One anonymous HTTPS GET on port 443 to `www.rakuten.tv`. The only proposed path is the exact `/nl/movies/` Dune (2021) path from the E06 pointer in the frozen matrix. Resolve the historical `public_url` in the accepted-observation ledger offline. Pin the full literal path and native movie identifier privately and verify E07 shares the document. No prefix or wildcard admission, query, redirect or retry. Expect a Netherlands movie-detail HTML document with title-level card and offer fields. The source reviewer must substantiate automated access under legitimate NL context and record the permitted limit, pacing and cooldown. |
| Extraction | Request separate approval to parse only the title card's native ID, canonical title or reviewed alias, director and release year, corroborating NL storefront context, and the same movie's rent, buy and ad-funded actions with transactional price qualifiers. Read embedded fields offline from the sealed document. Exclude recommendations and unrelated catalogue data. |
| Retention | Request custody of the one raw movie document with title actions and the minimum review metadata through review or until **2026-12-09 15:00 UTC**, whichever comes first, subject to any shorter source limit. The source reviewer must record whether the body and embedded price/action fields may be retained. If the evidence review cannot be completed within the permitted conditions, leave affected rows pending or omit them before issuance. |
| Derived publication | Request separate approval for candidate and class decisions, reason codes, class-level totals and sanitized provenance. Ask separately about price agreement counts, mismatch direction and exact EUR-cent amounts. Exclude page copy, movie-card payloads, native identifiers, exact locators and raw headers from Git and GitHub. |

| Candidate | Evidence to review on the same movie card |
| --- | --- |
| E06 `rakuten_tv/rent`, E07 `rakuten_tv/buy` | Separate full-film rental and purchase actions. For each, establish EUR cents, equivalent edition, quality, price basis, promotion and access terms before scoring price agreement. |
| E09 `rakuten_tv/free_with_ads` | Explicit current ad-funded full-film viewing at zero payment without a paid subscription. The control has no historical outcome. An ad-funded trailer, missing button or generic storefront promotion leaves it unknown. |

Validate the native movie card against the 2021 Denis Villeneuve edition with title, director and year, using a separately reviewed binding if a needed field is absent. Require the canonical NL route plus independent NL storefront evidence in the document. Locale, currency or the slug alone are insufficient. An explicit current negative needs class and variant coverage and a second reviewer. Keep catalogue presence, advertised class, entitlement and playback as separate fields. Entitlement and playback remain `untested`. [Typed identity decisions](../stage2b/issue97-identity-decisions.md) · [Evidence protocol](stage-e-evidence-protocol.md)

The proposed Dune slot is **2026-12-02 [15:00, 15:30) UTC**. Record UTC request bounds, monotonic elapsed time, synchronization and uncertainty for the acquisition host, and original assertion time and freshness evidence. A client shell with no expected movie data stops the batch. Use the private receipt to bind the response digest and byte count, exact locator digest, reviewed movie binding, territory decision and custody reference to all three decisions. [Batch A plan](stage-e-batch-a-plan.md)

## Private decision to complete

- Source reviewer records a separate allow, deny or unresolved decision for access, extraction, retention and derived publication. Attach dated terms or permission, version/effective date, territory, approved fields and price outputs, pacing, review date, reuse restrictions and expiry.
- Custodian specifies restricted storage, named readers, earliest applicable deletion deadline, deletion owner and permitted surviving metadata. Check sanitized class and price outputs individually before release.
- Owner lists included and omitted rows, signs the exact manifest and allowlist digests with the slot and expiry, and attaches the reviewed preflight and clock receipts. Current execution allowance is zero.
