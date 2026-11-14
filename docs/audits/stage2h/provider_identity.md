# Stage 2H — Provider identity review

## Dune (2021), TMDb 438631, Mediaset Infinity, IT

**Identity result: unresolved. Do not pin a Mediaset provider GUID.**

The Stage 2E plan describes an existing Mediaset Infinity subscription
observation for Dune in Italy, and Stage 2D's aggregate source report records
Mediaset runs and offers. Those reports do not preserve the exact provider
content GUID, a GUID-to-TMDb mapping row, or provider evidence tying a GUID to
the 2021 release. They are historical aggregate evidence, not an identity
proof.

The Stage 2D/2E source notes say Mediaset search cards expose title and movie
type but no release year. A card titled “Dune” can therefore be the 1984 film
or the 2021 film. The exact direct title URL was not preserved either. The
Stage 2G example manifest correctly leaves `verified_provider_id` null and
blocks the Mediaset scope until an independently reviewed GUID is pinned.

The only reachable database during this audit was a separate Homebrew
PostgreSQL database, not `wheretfdoiwatchit_pgdata`. It had no Movie, Offer,
ContentMapping, ProviderContentCandidate, ExternalProviderMapping, or legacy
scraped mapping rows for TMDb 438631 or 27205. This non-target result cannot
resolve the target database's historical mapping.

## JustWatch title identity

The proposed Stage 2G JustWatch adapter requires a movie result with the exact
requested TMDb ID, or falls back only when the result has no conflicting ID
and exact normalized title/year identifies a single candidate. Ambiguous or
unresolved candidates return no result. This guards Dune 1984/2021 ambiguity
in the source adapter; it does not supply independent primary-provider proof
or make historical stale offers fresh.

## Evidence needed to resolve Mediaset identity

Use a separately authorized read-only query against the confirmed target
database to retrieve the exact Mediaset `ProviderContent`/`ContentMapping`
candidate and its mapping method, country, title and any release metadata.
Then corroborate the same GUID against an independently captured official
Mediaset page or other reviewed primary evidence that distinguishes the 2021
film. If that evidence cannot establish the year, keep the scope blocked.

No GUID was added to the manifest, no source was activated, and no provider
request was made.
