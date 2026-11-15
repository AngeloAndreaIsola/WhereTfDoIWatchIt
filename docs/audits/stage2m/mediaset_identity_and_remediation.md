# Stage 2M — Mediaset identity and Dune mapping audit

**Code baseline inspected:** `094a9d4de18ba8c6585def3186b71cf72116b1fc`  
**Database:** Docker-backed PostgreSQL `tracker` on `desktop-linux`, checked read-only  
**Provider HTTP:** none  
**Database writes:** none

## Root cause and code correction

Before this change, `MediasetInfinitySource` accepted normalized title equality, then put the requested TMDb ID on each `ProviderOffer` and emitted the same value as `DiscoveredContent.external_ids.tmdb`. Refresh persistence treated that request-copied value as an externally asserted identifier and wrote an `EXTERNAL_ID` mapping. The title search has no release year and cannot distinguish homonymous works, so a search for Dune (2021) could turn Mediaset's Dune (1984) into a canonical 2021 offer.

The adapter now preserves the provider GUID/title/description as unresolved provider-native catalogue data, clears any legacy request-copied `external_ids.tmdb` metadata, and emits no offer TMDb ID. Mediaset title matching cannot create an automatic mapping; only a trusted manual mapping can resolve an offer. Multiple matching GUIDs stop before detail lookup and offer extraction. A missing result remains non-authoritative and does not withdraw an existing offer.

Existing collector-sourced Mediaset `EXTERNAL_ID` mappings are treated as untrusted by refresh, reconciliation, recovery, and mapped-catalogue reads. A Mediaset offer can still be trusted when it was directly supplied by a trusted external-ID source such as JustWatch, or when the provider identity has a manual mapping. This preserves direct source identity and avoids suppressing valid JustWatch observations that do not carry a separate provider-catalogue row.

The Stage 2G scope wrapper now accepts the Mediaset adapter's truthful `tmdb_id=None` result only for the pinned provider GUID and only when a matching manual provider-to-TMDb mapping exists. It then binds the offer to the one manifest TMDb ID. The present manifest still has no verified Mediaset GUID, so the scope remains blocked.

## Database audit

The read-only query found four Mediaset mappings, each with confidence `0.95`, method `external_id`, and source `mediaset_infinity`:

| Mapping | Provider GUID | Saved content | Target | Audit classification |
| --- | --- | --- | --- | --- |
| 1006 | `F307480501000102` | Nice Guys (2016) | TMDb 290250, Nice Guys (2016) | Suspicious provenance; saved title/year agree, identity not independently established by the source |
| 1007 | `F011273901000102` | Insomnia (description says 2002) | TMDb 320, Insomnia (2002) | Suspicious provenance; saved title/year agree, identity not independently established by the source |
| 1008 | `Y311540501000101` | Platoon (description says 1986) | TMDb 792, Platoon (1986) | Suspicious provenance; saved title/year agree, identity not independently established by the source |
| 1009 | `F000989101000104` | Dune (description says USA, 1984; no saved release date) | TMDb 438631, Dune (2021) | **Affected identity conflict; mapping must not be treated as verified** |

No two Mediaset provider GUIDs in the saved sample shared the same normalized movie title. This does not establish that the catalogue has no duplicate editions. The code audit found a second adapter with the same request-copy pattern: YouTube emits `request.tmdb_id` in both its offer and `external_ids.tmdb` after title search. It is a separate follow-up finding; this PR's runtime quarantine is specific to Mediaset. Watchmode first requires an exact corroborating API identity before using the request ID. Streaming Availability calls its show endpoint with the requested TMDb ID; its response identity handling should be reviewed separately, but this is not the title-search path that caused the Mediaset defect. JustWatch extracts TMDb/IMDb IDs from the provider response.

## Read-only impact report: mapping 1009 / offer 4542

The local `plan_mapping_correction` report was generated against the proposed target equal to the existing target (TMDb 438631). Its digest is `sha256:82781972466cbdd670fbb183d74916035f07bb1c634dfed10a52f4b0e1706a14`. Because the proposed target is unchanged, the tool reports no target mismatch; it does **not** validate Dune's identity. It is an impact inventory, not an authorization to remap.

The report and SELECT-only inspection found:

- Mapping 1009 points to ProviderContent 11628 and is not manual; both mapping and provider content came from `mediaset_infinity`.
- Offer 4542 is Mediaset Infinity IT subscription availability, marked currently available, and references GUID `F000989101000104`.
- One saved OfferEvidence row (159) records a positive, non-authoritative source observation. No OfferPrice row is associated.
- No other provider-native Mediaset GUID for this title was saved. There is no same-provider corroborating mapping. Other-provider offers exist, but they do not prove this GUID's identity.
- Source run 7124 is the saved Mediaset observation run. No historical rows were deleted or updated for this audit.

The unredacted generated report remains outside the repository at the operator's private report directory with file mode `0600`. It must not be published because it includes provider URLs and row metadata.

## Proposed remediation, not applied

1. Preserve mapping 1009, ProviderContent 11628, offer 4542, evidence 159, and run 7124 as historical evidence with their original timestamps and provenance.
2. Mark mapping 1009 unverified or quarantine it from identity resolution. Do not change its target to Dune (1984) based only on the saved description.
3. Prevent offer 4542 from resolving as watchable for TMDb 438631 while the provider identity is unresolved. Keep the row for audit and do not delete it.
4. Keep ProviderContent 11628 as provider-native catalogue evidence. Retain its title, description, URL, and GUID; record identity uncertainty separately.
5. If an authoritative Mediaset detail page or other independently verifiable record later identifies the GUID as Dune (1984), prepare a separately reviewed correction manifest. If it confirms Dune (2021), prepare a manual mapping review. If neither is established, leave the GUID quarantined.
6. Treat mappings 1006–1008 as provenance-suspicious, not proven contaminated: retain their rows and offers pending individual review. Reconcile them only through a reviewed impact manifest after independent identity evidence is available.

Use the existing `plan_mapping_correction` impact tooling before any future correction and preserve its before/after manifest. The existing impact report does not itself provide an operation to change a mapping's verification status; any later remediation needs an explicit reviewed implementation/tool plan. No remediation command or migration was run in Stage 2M.

## Verification and remaining requirements

Fixture tests cover Dune (1984) returned for a Dune (2021) request, title collisions across years, unknown years, ambiguous GUIDs, trusted manual mapping, corrupt collector-sourced external-ID mappings, source-specific direct offer evidence, non-authoritative absence, catalogue visibility, recovery rejection, and the Stage 2G manual-map/GUID gate. See the PR for the complete suite results.

The saved GUID remains unverified. A future primary-provider lookup must inspect the exact Mediaset page for `F000989101000104` and independently establish the title/version, year, and Italian offer class. The current experiment remains blocked until that identity is verified, a reviewed manifest/code revision pins the GUID, and a trusted manual mapping ties it to the intended TMDb title. No quota evidence or operator authorization is created by this audit.
