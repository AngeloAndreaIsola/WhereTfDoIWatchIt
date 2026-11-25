# Issue #97 JustWatch collection preflight


**LIVE_COLLECTION_BLOCKED.** The 15-title Netherlands plan and synthetic replay are ready for code review. Fresh collection and independent assessment remain pending. [Issue #97](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/97) · [PR #86](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/86)

## 1. Authorization and integration

Reviewed base `a50305e097fbeab356c79de6bdcfb2f90428949c`. PR #86 merged at `bd3da37934f388e389194526f0f5a99a956d9de3`. The owner confirms permission in #97. The current checkout and PR #86 provide the boundaries of the reduced diagnostic and identify the evidence gates. [PR #86 runbook](stage2g_bounded_availability_experiment.md) · [Archived Stage 2S/T readiness](../audits/stage2s/readiness_report.md)

| Item | Reviewed record |
| --- | --- |
| Current written permission reference | **UNKNOWN**. The owner confirmation is in #97. A current reference covering this endpoint, operations, scopes and ceiling is pending. |
| Permission window | **UNKNOWN**. Validity start and end are pending. The runner also requires evidence checked within one hour. |
| HTTP destination | `POST https://apis.justwatch.com/graphql` in the fixed diagnostic. |
| Fixed diagnostic operations and identity | `GetSearchTitles` for NL movies Inception (2010), TMDb 27205, and Dune (2021), TMDb 438631. No live authorizations were issued in this work. |
| Ordinary series adapter | Search can lead to a second call. Seasons use `GetTitleNode` with the seasons document and the matched show's node ID. Expanded search and season scope are pending written review. |
| Application attempt ceilings | Three per title, including up to two eligible transient retries, and six total in the fixed diagnostic. The JustWatch source and search operation each have the same six-attempt ceiling. The expanded cohort has zero allocated live attempts. |
| Permission ceiling and retry allowance | **UNKNOWN**. The ceilings above are enforced software limits. The written provider ceiling, retry allowance and total low-level quota are pending. |
| Quota class, balance and rate policy | **UNKNOWN**. A current reviewed policy reference and quota classification are pending, with remaining request balance if metered. |
| Pacing, concurrency and burst | **UNKNOWN** for source approval. The fixed runner serializes under shared locks and now requires reviewed minimum pacing and the existing evidence for a burst allowance of at least six. |

The fixed v1 manifest digest remains `bfd245d7ba4916f6dd6aaea9dcc77c89c2995c8cae6e26ccc3fe03c468680209`. Transport rejects non-exact documents, variables, scopes and endpoints. Application limits stack with the reviewed permission and rate gates. The attempt ledger reserves each transmitted POST before sending, records denial, redirect and 429 as spent outcomes, and requires reconciliation for interrupted or incomplete scopes. The runner checks evidence expiry before reserving another attempt. In the disposable PostgreSQL preflight without synthetic authorization evidence, both fixed scopes were blocked by absent current permission and quota/rate-policy evidence with zero reservations, overlapping queue items and unfinished runs.

## 2. Collection preparation

The command joins the corrected cohort and reviewed identity CSV by typed TMDb ID. Scope output retains the canonical and pilot labels. Coherence is movie 220289 with a 2013 pilot label and 2014 direct-ID manifest year. The Office is US series 2316 (2005), with canonical title `The Office`. The synthetic matcher accepts the manifest year and the existing typed title aliases. A disagreement in the year of any future JustWatch result remains unresolved pending identity review. [Corrected NL cohort](../audits/stage2b/nl_pilot_cohort_20261122_v2.csv) · [Identity manifest](../audits/stage2b/identity_manifest_20261122.csv)

| Input | SHA-256 |
| --- | --- |
| Cohort CSV | `abcbb06161a5afc5896b507d4f1b1bf1c4fb9831d72973ed2be47487eb17d40b` |
| Identity manifest CSV | `ae78a757272aecbb9824d9ef3cea4d8f125bd5e150cc6abafcc96e50e2737925` |

The offline command has no live transport or database writes. The fixed diagnostic searches one page with `bestOnly` enabled. Inventory completeness and production title or season response shapes require fresh evidence. It produced 15 pending scopes, including five separate pending season operations. Fixture replay used the JustWatch client's GraphQL parsing and required a single typed ID, the manifest year and a reviewed title match. Missing or ambiguous listings remain unknown. Output keeps mapped offers, mapping gaps and season rows separate, and identifies the replay time and synthetic provenance.

| Synthetic scope | Fixture calls | Title offers | Season offers | Unresolved mappings |
| --- | ---: | ---: | ---: | ---: |
| Inception NL | 1 search | 6 | 0 | 2 |
| Breaking Bad NL | 1 search, 1 season lookup | 1 | 1 | 0 |

The synthetic replay was run at 09:37:39 UTC on November 24, 2026. The six synthetic movie classes were base subscription, add-on, rent, buy, free and ad-supported. A separate disposable application dataset returned current Prime Video subscription evidence and last-known stale Netflix subscription evidence on the normal movie availability route. Replay twice left both timestamps and lifecycle flags intact. These were synthetic application rows.

### Presence and offer variants

The offline replay's `title_offers` and `season_offers` count presence by
`(media_type, tmdb_id, country, season_number, provider, offer_class)`.
A presence row retains the confirmed JustWatch node ID and a `variants` array.
Identical source variants within that presence scope collapse before export.
The exported variants contain provider, country, canonical offer class,
quality, amount and currency. Variants that differ on source-only fields can
therefore have identical public fields. Use presence rows for coverage and
the projected variants for price and rendition comparisons. Unknown prices
remain visible.

The Notte canary archive contains offer-level evidence. Its original key
grouped 56 observations into 34 keys by dropping title identity. Adding title
and JustWatch ID preserved all 56 archive rows. The replay's presence count
serves a different comparison.

Application storage retains one offer per provider, territory, typed TMDb ID
and season/episode slots, offer class and provider-native title ID, and one
price row per offer and source. The additive `OfferPrice.variants` column
preserves the distinct JustWatch variants returned together in the latest
observation of that offer. The tracker read payload projects the same public
fields as the replay, plus an HTTP(S) display link with query and fragment
removed. The representative prefers an
open availability window and a validated price, sorting by currency code
and then amount, with a stable variant tie break. Existing price and offer
IDs carry forward. The migration starts older rows with empty variant lists
and leaves their prices intact.

The fixture replay requires the reviewed title and year as well as a typed
ID anchor. A corroborating edge on the same JustWatch node can supply
additional variants. Conflicting typed claims on that node or competing
native IDs leave the scope unresolved. Production also supports an
unambiguous exact-title fallback when the source omits its TMDb ID, with the
year applied when supplied.
These checks apply to offers returned by the configured source query.

The live ledger for this dispatch contains zero attempts, reservations, HTTP outcomes, retries or stops. No JustWatch, Watchmode, Streaming Availability or first-party provider collection ran. Fresh coverage is **0/15**. All 15 scopes remain pending.

## 3. Independent verification

The preserved panel contains 13 NL first-party advertised offers. Its original timestamps remain in the accepted observations. Comparison eligibility requires a contemporaneous fresh JustWatch observation, plus exact typed title, market, service and offer-class matching in a reviewed window. No pair met that standard in this dispatch because fresh collection was blocked. JustWatch-derived app offers are `SAME_SOURCE_ECHO`. Entitlement and playback are unverified. [Accepted observations](../audits/stage2b/issue97-accepted-observations.csv) · [Rejected observations](../audits/stage2b/issue97-rejected-observations.csv)

## 4. Reconciliation

There are zero eligible fresh independent comparisons. Agreement, conflict and needs-recheck rates are undefined. The 15 titles remain unknown for the fresh collection, and missing listings supply no negative denominator. This branch reproduced no new provider accuracy defect. The earlier BBC mapping follow-up remains tracked independently. [Historical verification](../audits/stage2b/issue97-final-verification-report.md) · [Issue #190](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/issues/190)

## 5. Handoff

| Phase | Verdict |
| --- | --- |
| 1. Authorization and integration | Offline review complete. Written scope and operating limits pending. |
| 2. Collection | Fifteen identities and synthetic replay verified. `LIVE_COLLECTION_BLOCKED`. |
| 3. Independent verification | Blocked on fresh observations and a reviewed comparison window. |
| 4. Reconciliation | Blocked on an eligible independent sample. |
| 5. Handoff | Offline preparation ready for ChatGPT review. Issue acceptance remains blocked. |

### Reproduction

Run from the repository root. The test environment used Docker Engine 29.9.3, Compose 5.7.1, Python 3.12.16 and disposable `postgres:16-alpine` with tmpfs storage. PostgreSQL image digest was `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`. The repository runner verifies the test DB identity. The standalone offline replay ran without network access.

```sh
docker build -t wtdw-issue97-offline backend
docker run --rm --network none -e DJANGO_DEBUG=1 \
  --mount "type=bind,source=$PWD/docs/audits/stage2b,target=/reviewed,readonly" \
  --entrypoint python wtdw-issue97-offline manage.py issue97_collection_preflight \
  --cohort /reviewed/nl_pilot_cohort_20261122_v2.csv \
  --identities /reviewed/identity_manifest_20261122.csv \
  --synthetic-fixtures availability/tests/fixtures/issue97_synthetic_justwatch.json
```

For the test runs, this temporary `scripts/sitecustomize.py` was mounted by the declared Compose test configuration. It allowed only the disposable database service name for Python DNS and denied Python socket connections. It returned a synthetic DNS miss for the absent internal frontend service. PostgreSQL uses libpq for the database connection.

```python
import atexit
import socket

_attempts = 0

def _deny(*args, **kwargs):
    global _attempts
    _attempts += 1
    raise AssertionError("Python network blocked in disposable test container")

socket.socket.connect = _deny
socket.socket.connect_ex = _deny
_resolve = socket.getaddrinfo

def _db_dns(host, *args, **kwargs):
    if host == "db-test":
        return _resolve(host, *args, **kwargs)
    if host == "frontend":
        raise socket.gaierror(socket.EAI_NONAME, "Frontend absent in isolated test profile")
    return _deny()

socket.getaddrinfo = _db_dns
atexit.register(lambda: print(f"PYTHON_NETWORK_GUARD blocked_attempts={_attempts}", flush=True))
```

```sh
./scripts/test-backend-isolated.sh availability.tests.test_experiment_runner \
  availability.tests.test_issue97_collection availability.tests.test_justwatch_country_propagation \
  availability.tests.test_justwatch_integrity availability.tests.test_justwatch_available_to \
  availability.tests.test_justwatch_rakuten_mapping availability.tests.test_audit_availability_accuracy -v 1
./scripts/test-backend-isolated.sh -v 1
python3 -m unittest discover -s scripts -p 'test_issue97_verification.py'
git diff --check
```

The focused baseline was run from a temporary export of the reviewed base with the same socket guard. The test labels were `availability.tests.test_experiment_runner`, `availability.tests.test_justwatch_country_propagation`, `availability.tests.test_justwatch_integrity`, `availability.tests.test_justwatch_available_to` and `availability.tests.test_justwatch_rakuten_mapping` with `-v 1` on the same declared test script.

| Run | Result | Exit | Python network guard |
| --- | --- | ---: | --- |
| Reviewed base focused PostgreSQL | 91 passed | 0 | 0 blocked attempts |
| Candidate focused PostgreSQL | 129 passed | 0 | 0 blocked attempts |
| Candidate full PostgreSQL | 3,752 passed | 0 | 0 blocked attempts |
| Historical evidence script regressions | 8 passed | 0 | Offline fixtures |
| Cohort preflight and synthetic replay | 15 pending scopes and 2 successful fixture replays | 0 | Docker network disabled |
| Whitespace verification | Clean | 0 | Local |

Earlier nonzero runs are retained here for review:

- The first test invocation exited 1 before running tests because its temporary guard denied the database service DNS lookup.
- An intermediate 91-test run exited 1 with one failure and one error while the permission evidence fixtures and mocked readiness still used the old schema.
- The first 129-test focused run exited 1 with two failures because the fixture transport omitted the JSON response content type.
- The first 3,752-test full run exited 1 with two candidate-admin test failures because the guard denied lookup of the absent internal `frontend` service. The rerun injected the normal unresolved-host result locally. Both full runs kept Python sockets blocked.
- The first standalone command exited 1 at Django settings initialization. The command above enables debug settings for this network-disabled container.

Hosted CI is `NOT PROVEN` in this report. Live response shape, completeness, independence, operating limits and production accuracy remain unverified.
