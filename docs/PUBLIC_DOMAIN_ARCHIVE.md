# Public-domain archive rights policy

The collector stores catalogue metadata and original archive links. An archive upload,
collection, age, or bare Public Domain Mark does not establish country availability.

- Recognized CC0, CC BY and CC BY-SA license URLs supply a worldwide grant only
  without contradictory or territorial reservations. CC BY/BY-SA remain classified
  as open licenses; they are not relabeled public domain. Links lead to the archive
  landing page and its license/attribution information.
- A Public Domain Mark needs an affirmative territorial statement. US determinations
  authorize only US offers; foreign copyright caveats never extend that scope.
- Negated, disputed, unverified, restrictive, or contradictory declarations produce
  unknown scope and no positive offer. NC/ND licenses remain restricted. Unknown
  and restricted items retain catalogue metadata for review.
- Structured `ProviderContent.metadata.rights_provenance` preserves the archive URL,
  original rights/license URL, original statement, status, territorial assessment,
  jurisdiction, assessment notes, and observation time. There is no schema migration.
- A newly observed rights downgrade explicitly withdraws this collector's earlier
  offer evidence through `ProviderOfferWithdrawal`. Independent sources' evidence
  remains intact. This is distinct from absence in an incomplete catalogue, which
  never causes removal. A later authorized observation can restore the same offer.

Tests use saved metadata fixtures and the normal PostgreSQL migrations, with no live
archive requests or process-wide migration patches. The default collector capability
is US; direct country-specific evaluation does not imply worldwide catalogue coverage.

As with other catalogue sources, persistence requires an explicitly configured,
enabled `Provider(slug="internet_archive")` and enabled US country. Registering the
source does not enable or run it; no production collector is activated by this change.
