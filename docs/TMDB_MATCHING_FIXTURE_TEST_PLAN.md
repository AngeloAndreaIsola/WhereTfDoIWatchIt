# Synthetic TMDb Matching Fixtures — Expected Outcomes

**Scope:** contract test plan for local/provider content identity, mapping
persistence, provider-access observation, canonical offer persistence, and
mapping correction. All payloads are offline fixtures in
`backend/availability/tests/tmdb_matching_fixtures.py`; no scenario is allowed
to call TMDb or any other live endpoint.

## Vocabulary and outcome layers

The test suite must keep these five layers separate. A positive result at one
layer does **not** imply a positive result at later layers.

| Layer | Meaning | Durable record / observable |
|---|---|---|
| **Candidate discovered** | Title/type/year evidence identifies one TMDb id. | `MatchCandidate` only, prior to persistence. |
| **Candidate review required** | Evidence is ambiguous, rejected earlier, or has unsafe type drift. | No automatic mapping or offer; issue/review queue if implemented. |
| **Mapping accepted** | A `ContentMapping` is stored or an authoritative manual mapping is retained. | `ContentMapping` with target `tmdb_id`. |
| **Provider access observed** | Source observed content as present/watchable at the provider. | `ProviderContent` and a source-level `ProviderOffer`/Offer observation. |
| **Canonical Offer persisted** | A mapped observed offer is written as canonical `Offer` data. | `Offer` (and provenance rows such as `OfferEvidence` / `OfferPrice`). |

### Invariants

1. Discovery is not mapping. A candidate must not create a `ContentMapping`
   until policy accepts it.
2. Mapping is not provider access. A `ContentMapping` may validly exist with no
   `Offer`.
3. Provider access is not canonical persistence. Unmapped provider content may
   be observed but cannot create canonical `Offer.tmdb_id` data.
4. Source evidence is scoped: representative episode facts cannot describe an
   entire series.
5. A non-authoritative or failed lookup never erases earlier rows.
6. IDs in these fixtures are synthetic (`900_xxx`, `syn-*`,
   `ContentItem-syn-*`) unless an existing test explicitly verifies a real
   identity.

## Fixture module

```python
from availability.tests.tmdb_matching_fixtures import (
    ALL_SCENARIOS,
    EXACT_MATCH,
    CORRECTION_EVIDENCE,
)
```

The module exposes `SimpleNamespace` builders shaped like `DiscoveredContent`
and `ProviderOffer`, data dictionaries for ORM setup, and named constants for
synthetic TMDb IDs. It performs no I/O.

---

## Scenario matrix

| # | Fixture label | Candidate | Review | Mapping | Access | Offer | Core expected assertion |
|---:|---|---|---|---|---|---|---|
| 1 | `exact_title_year` | yes | no | yes | conditional | conditional | exact movie title + year gives `0.9`, `title_year` |
| 2 | `translated_vs_original_title` | no | no | no | no | no | translated title is not silently assumed equivalent |
| 3 | `duplicate_titles` | no | yes | no | no | no | distinct IDs with same title+year are ambiguous |
| 4 | `missing_year` | yes | no | yes | conditional | conditional | unique title-only movie candidate gives `0.7` |
| 5 | `conflicting_year` | no | no | no | no | no | known conflicting movie year blocks fallback |
| 6 | `same_movie_tv_title` | yes | no | yes | conditional | conditional | content type prevents movie/series cross-match |
| 7 | `raiplay_single_multi_ambiguity` | no for flip | yes for flip | no for flip | no for flip | no for flip | stable layouts classify; layout flip requires review |
| 8 | `series_unreliable_release_year` | yes | no | yes | conditional | conditional | series may use title-only `0.7` despite year conflict |
| 9 | `no_candidate` | no | no | no | no | no | unknown title remains unmapped |
| 10 | `review_required` | no | yes | no | no | no | unknown-year duplicate title is ambiguous |
| 11 | `manual_mapping_conflict` | ignored | no | existing manual retained | conditional | conditional | automatic result cannot replace MANUAL mapping |
| 12 | `rejected_reappear` | no auto-accept | yes | no promotion | conditional | no new offer | reappearance cannot silently override a rejection |
| 13 | `tmdb_429`, `tmdb_timeout`, `tmdb_cache_expiry` | unavailable / retry | no | no on failures | content only | no | persist discovered content, record error, preserve cache policy |
| 14 | `mapping_without_offer` | n/a | no | yes | no | no | mapping and offer are independent records |
| 15 | `mapping_correction_with_evidence` | manual target | no | corrected | yes after refresh | yes, new target | old evidence/price remain historically attached |
| 16 | `episode_evidence_not_generalizable` | yes | no | yes | yes | yes, minimal series offer | do not copy episode attributes to series offer |

`conditional` means the matching fixture itself only covers identity. A source
integration test may add a valid provider-access observation and then assert
canonical offer persistence separately.

---

## Detailed cases and assertions

### 1. Exact title/year match — `EXACT_MATCH`

**Setup:** one local Movie `The Godfather` (1972, `movie`, `900001`); one
provider entry with the same title, year, and type.

**Expected decisions**

- Candidate discovered: **yes**, TMDb `900001`, confidence `0.9`, method
  `title_year`.
- Candidate review required: **no**.
- Mapping accepted: **yes** once the refresh/mapping layer records it.
- Provider access observed: **not implied** by matching alone.
- Canonical Offer persisted: **only after** a provider access observation exists.

**Core test**

```python
match = match_local_catalog(EXACT_MATCH["provider_content"])
self.assertEqual(match.tmdb_id, TMDB_GODFATHER)
self.assertEqual(match.confidence, 0.9)
```

### 2. Translated vs original title — `TRANSLATED_TITLE`

**Setup:** catalog title `The Godfather`; provider reports `Il Padrino`.

**Expected decisions**

- Candidate discovered: **no** under current exact local title matching.
- Candidate review required: **no** (absence is not ambiguity).
- Mapping accepted / access / Offer: **no** from this identity result.

The fixture includes a variant that adds a local `Il Padrino` row pointing to
the same synthetic TMDb id. That variant is an exact match, not an ambiguous
duplicate: uniqueness is measured by distinct TMDb ids.

### 3. Duplicate titles — `DUPLICATE_TITLES`

**Setup:** two distinct movie IDs share `Solstice` and year 2018.

**Expected decisions**

- Candidate discovered: **no** automatic winner.
- Candidate review required: **yes**; reason
  `ambiguous_multiple_distinct_tmdb_ids`.
- Mapping / access / Offer: **no** automatic persistence.

### 4. Missing year — `MISSING_YEAR`

**Setup:** only one local movie has title `The Godfather`; provider provides no
year.

**Expected decisions**

- Candidate discovered: **yes**, confidence `0.7`.
- Candidate review required: **no**.
- Mapping accepted: **yes** if title-only matches are allowed by the caller.
- Access / Offer: **not implied**.

### 5. Conflicting movie year — `CONFLICTING_YEAR`

**Setup:** catalog has `The Godfather` (1972); provider says 2005.

**Expected decisions**

- Candidate discovered: **no**.
- Candidate review required: **no** by the matcher contract; known mismatch is
  negative evidence, not an ambiguity.
- Mapping / access / Offer: **no** automatic rows for the mismatched identity.

Assert the matcher does **not** regress to its `0.7` title-only movie fallback.

### 6. Same title for movie and TV — `SAME_MOVIE_TV`

**Setup:** `Dark` exists as both a movie and series under different synthetic
IDs. Provider fixtures carry the correct `content_type`.

**Expected decisions**

- Movie candidate discovered: **yes**, only `TMDB_DARK_MOVIE`.
- Series candidate discovered: **yes**, only `TMDB_DARK_SERIES`.
- Review: **no**.
- Mapping: **yes**, per type.
- Access / Offer: source-dependent, not inferred from the candidate.

### 7. RaiPlay `single` / `multi` classification — `RAI_SINGLE_MULTI`

**Stable classifications**

- `layout="single"` → `movie`.
- `layout="multi"` → `series`.

**Ambiguous layout flip**

A given RaiPlay programme native id observed first as `single` and later as
`multi` must not silently change `ProviderContent.content_type`. Expected
outcomes:

- Candidate discovered: **no automatic candidate for the flip**.
- Candidate review required: **yes**, reason
  `content_type_flip_single_to_multi`.
- Mapping / access / Offer: **no automatic mutation** until a human decides the
  identity/type policy.

This complements the RaiPlay source contract: stable `single` pages make
movie-level claims and stable `multi` pages make series-level claims.

### 8. Series unreliable release year — `SERIES_UNRELIABLE_YEAR`

**Setup:** local series `Dark` begins in 2017; provider says 2024 because that
may be a catalog re-add year.

**Expected decisions**

- Candidate discovered: **yes** by title only, confidence `0.7`.
- Review: **no**, if title is unique within series.
- Mapping accepted: **yes**.
- Access / Offer: separate source outcomes.

Do not apply the movie conflicting-year rule to series.

### 9. No candidate — `NO_CANDIDATE`

**Setup:** provider reports `Totally Unknown Film`; local catalog is empty.

- Candidate discovered / review / mapping / Offer: **no**.
- Provider access observed: source may still persist `ProviderContent` as an
  observed catalogue fact.

If a refresh test is used, assert that `ProviderContent` survives unresolved
identity while `ContentMapping` and `Offer` remain absent.

### 10. Review-required candidate — `REVIEW_REQUIRED`

**Setup:** two TMDb IDs share the same movie title, but provider year is
missing.

- Candidate discovered: **no automatic winner**.
- Candidate review required: **yes**.
- Mapping / access / Offer: **no automatic canonical persistence**.

This is intentionally distinct from scenario 3: the ambiguity arises at the
title-only tier rather than the title+year tier.

### 11. Manual mapping conflict — `MANUAL_MAPPING_CONFLICT`

**Setup:** an existing `MANUAL`, confidence `1.0` mapping targets `900001`; a
fresh automatic title/year candidate points at `900002`.

- Candidate discovered: automatic candidate may be computed, but is **not
  authoritative**.
- Candidate review: **no** if manual mapping policy is unambiguous.
- Mapping accepted: **existing manual mapping remains accepted**.
- Access / Offer: follow the retained mapping only.

Assert no ORM update changes target, confidence, or method of the manual row.

### 12. Rejected candidate reappears — `REJECTED_REAPPEAR`

**Setup:** same provider-native id was rejected earlier, represented in the
fixture as confidence `0.0` / rejected metadata. It reappears with the same
otherwise-exact facts.

- Candidate discovered: no automatic re-acceptance.
- Candidate review required: **yes** (`previously_rejected_mapping`).
- Mapping accepted: **no promotion**.
- Provider access: may be observed as `ProviderContent`.
- Canonical Offer: **no new Offer tied to an automatically resurrected mapping**.

If the application lacks durable rejection state today, mark this as an
expected-red/contract test rather than simulating deletion (deletion loses the
information necessary to enforce this rule).

### 13. TMDb failure and cache lifecycle — `TMDB_RATE_LIMIT`, `TMDB_TIMEOUT`, `TMDB_CACHE_EXPIRY`

#### 429 and timeout

- Candidate discovered: unavailable for that attempt.
- Mapping accepted: **no new mapping**.
- Provider access observed: `ProviderContent` may still be persisted if the
  provider source delivered it.
- Canonical Offer: **no mapped canonical offer** without a valid mapping.
- Operational assertion: failure/error count recorded on the relevant run;
  earlier mappings/offers must not be deactivated or erased.

#### Cache expiry

- Expired cache calls for a refresh attempt.
- Failure after refresh attempt may use cache as explicit fallback, but must not
  overwrite freshness metadata as if a fresh TMDb response was observed.

### 14. Mapping exists without an Offer — `MAPPING_NO_OFFER`

**Setup:** a valid mapping exists for a RaiPlay programme, but provider video
was unavailable / no offer was observed.

- Mapping accepted: **yes**.
- Provider access observed: **no**.
- Canonical Offer persisted: **no**.

Assert `ContentMapping.objects.filter(...).exists()` and
`not Offer.objects.filter(...).exists()`. Do not treat an absent Offer as an
invalid mapping.

### 15. Mapping correction, evidence and price provenance — `CORRECTION_EVIDENCE`

**Setup:** canonical mapping changes manually from `900040` to `900041`; former
target owns an Offer with `OfferEvidence` and `OfferPrice`.

Expected outcome:

- Mapping accepted: **new manual target** (`900041`).
- Former OfferEvidence / OfferPrice: **preserved** and remain FK-linked to the
  former Offer; do not rewrite history to make it look as though the prior
  evidence claimed `900041`.
- Former Offer: deactivated/retired according to mapping-remediation policy.
- Fresh provider observation: creates/updates a canonical Offer targeting
  `900041`, with its own new evidence and price provenance.

Assertions should include both FK topology and targets:

```python
self.assertEqual(old_evidence.offer_id, old_offer.id)
self.assertEqual(old_price.offer_id, old_offer.id)
self.assertEqual(old_offer.tmdb_id, TMDB_CORRECTION_OLD)
self.assertFalse(old_offer.currently_available)
self.assertEqual(new_offer.tmdb_id, TMDB_CORRECTION_NEW)
self.assertTrue(new_offer.currently_available)
```

### 16. Representative episode evidence cannot generalize — `EPISODE_EVIDENCE`

**Setup:** a RaiPlay `multi` programme maps to a series. Its representative
linked video happens to be S1E1 and reports duration, quality, subtitles, and
availability dates.

Expected decisions:

- Candidate discovered/mapping accepted: **yes**, against the series title.
- Provider access observed: **yes**, at a series-level claim.
- Canonical Offer persisted: **yes**, but minimal.

The resulting series `Offer` must have:

- `content_type == "series"`
- `season_number is None`, `episode_number is None`
- no episode duration field/claim
- `quality == ""`
- `subtitle_languages is None` (or empty only if source deliberately made a
  show-level claim)
- `available_from is None`, `available_until is None`

Never promote S1E1's facts to every episode or to the whole series.

---

## Recommended test organization

Create future tests as separate unit/integration classes without modifying
source-owned implementation files:

1. **Matcher unit tests**: scenarios 1–6, 8–10 using `Movie` rows and
   `match_local_catalog`.
2. **Identity policy tests**: scenarios 7, 11, 12, 13; expected-red where
   rejection/type-change state is not implemented yet.
3. **Persistence tests**: scenarios 14–16 using `ProviderContent`,
   `ContentMapping`, `Offer`, `OfferEvidence`, and `OfferPrice`.
4. **No network guard**: patch all client/request methods or use fixture
   routers; a test must fail loudly for any unexpected URL.

## Verification commands

After adding test code that consumes these fixtures, run only the relevant
backend tests under the repo's test settings (no live request fixture can be
used):

```bash
cd /Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt/backend
USE_SQLITE=1 DJANGO_SETTINGS_MODULE=config.test_settings \
  python manage.py test availability.tests.test_matching -v 2
```

This task itself adds data and a test plan only; it does not change runtime
logic or claim that the currently unimplemented policy cases pass.
