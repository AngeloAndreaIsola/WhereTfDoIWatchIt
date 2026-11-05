# Mapping Correction Impact Planner

A read-only planning and audit tool that computes the blast radius of correcting a `ContentMapping` before any mutation is planned or executed.

## Overview

When correcting a provider catalogue entry's mapping to a proposed TMDb ID and content type, downstream canonical availability records (`Offer`, `OfferEvidence`, `OfferPrice`, and reconciliation states) can be impacted. Because `Offer.identity` embeds `tmdb_id` and `content_type`, modifying a mapping without remediating associated records can lead to desynchronized offer identities and reconciliation `mapping_doubt` flags.

The **Mapping Correction Impact Planner** evaluates the impact with:
- **Strict read-only safety**: No database rows are modified, added, or deleted. Spies and checksums assert that zero write operations occur.
- **Zero external network dependencies**: No live TMDb, RapidAPI, or external API calls are made.
- **Deterministic precondition fingerprinting**: Generates a canonical `sha256:` fingerprint of the database state relevant to the scope. Any material change changes the fingerprint.
- **Inspectable JSON manifest**: Emits a `mapping_correction_impact/manifest-v1` manifest carrying the inputs, database identity, precondition fingerprint, canonical digest, and detailed findings.

---

## Inputs

1. **Provider**: Provider slug (e.g. `bbc_iplayer`, `tubi`, `cbc_gem`).
2. **Provider-content ID**: Provider-native ID (e.g. `b00jd68z`, `608226`).
3. **Proposed TMDb ID**: Target TMDb ID integer (e.g. `2996`).
4. **Proposed Content Type**: `movie` or `series`.

---

## Report Sections

The generated report contains:

1. **Existing Mapping**:
   - Primary key, provider, provider-content ID.
   - Current target TMDb ID and content type.
   - Match method (`title_year`, `manual`, `exact`, etc.) and confidence score.
   - Indication if the mapping is already `MANUAL`.
   - Full column values dump.

2. **Provider-Native Identity**:
   - Provider slug and provider-content ID.
   - Count and details of `ProviderContent` rows associated with the ID.
   - Stored title, release date, slots (season/episode), provider URL, and metadata.

3. **Former & Proposed TMDb Targets**:
   - Former TMDb target metadata (from local `Movie` model if available).
   - Proposed TMDb target metadata (from local `Movie` model if available).

4. **Associated Records**:
   - **Offers**: All canonical `Offer` records tied to `(provider, provider_content_id)`.
   - **OfferEvidence**: All `OfferEvidence` observations matching associated offer identities or `(provider_slug, provider_content_id)`.
   - **OfferPrice**: All `OfferPrice` rows linked to the offer identities.

5. **Corroborating Observations from Other Sources**:
   - Observations for proposed TMDb target across other content IDs for this provider.
   - Observations for proposed TMDb target from other providers and sources.
   - Evidence records and mappings for proposed TMDb target.
   - Global and provider-level corroboration flags.

6. **Manual Mapping Conflicts**:
   - Flags if the target mapping itself is already marked `MANUAL`.
   - Competing manual (or non-manual) mappings claiming the proposed TMDb target for the same provider.

7. **Potentially Invalid Availability**:
   - Active offers lost on former TMDb target.
   - Stale offer identities (where `Offer.identity` diverges from projected remapped identity).
   - Risk of reconciliation `mapping_doubt` ("mapping points to tmdb ...").

8. **Deterministic Precondition Fingerprint & Canonical Digest**:
   - `precondition_fingerprint`: Canonical SHA-256 over non-volatile database columns for the target scope.
   - `digest`: Canonical SHA-256 over the manifest (excluding volatile fields `generated_at` and `digest`).

---

## Usage

### Management Command

Human-readable terminal summary:
```bash
python manage.py plan_mapping_correction \
    --provider bbc_iplayer \
    --provider-content-id b00jd68z \
    --tmdb-id 2996 \
    --content-type series
```

Output as JSON manifest to stdout:
```bash
python manage.py plan_mapping_correction \
    --provider bbc_iplayer \
    --provider-content-id b00jd68z \
    --tmdb-id 2996 \
    --content-type series \
    --json
```

Write JSON manifest artifact to file:
```bash
python manage.py plan_mapping_correction \
    --provider bbc_iplayer \
    --provider-content-id b00jd68z \
    --tmdb-id 2996 \
    --content-type series \
    --write-manifest ./remediation-manifest.json
```

### Python Service API

```python
from availability.services.mapping_correction_impact import MappingCorrectionImpactPlanner

planner = MappingCorrectionImpactPlanner(
    provider="bbc_iplayer",
    provider_content_id="b00jd68z",
    proposed_tmdb_id=2996,
    proposed_content_type="series",
)
manifest = planner.plan()
print(manifest["digest"])
print(manifest["precondition_fingerprint"])
```
