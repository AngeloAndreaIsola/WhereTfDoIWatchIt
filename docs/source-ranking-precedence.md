# Source ranking precedence

Offer feeds and access resolution use the same sort key for offers that pass their
respective eligibility checks. The precedence is:

1. Verified availability evidence, then a matching language result. Unknown or stale
   evidence cannot outrank a verified offer because of a source preference.
2. Included/free access before rent or buy when `prefer_entitled` is enabled.
3. `source_priority` in the exact order saved by the user. It controls source category
   order, including local/foreign offers when both are reachable.
4. `prefer_official` and `prefer_local` as tie breakers within the configured source
   category order. They do not override `source_priority`.
5. `provider_priority`, then language rank and quality as tie breakers.

An offer must still satisfy the applicable language, entitlement, country/VPN reach,
and access-evidence rules before it can affect an access verdict. Reordering offers
changes presentation and `best_offer`; it does not change whether the verdict is
`AVAILABLE_HERE` or `AVAILABLE_VPN`. Source category is separate from the legacy
`is_scraped_source` country-independent reach flag, so an unofficial offer with a
country can be ranked as unofficial and still require that country to be reachable.
