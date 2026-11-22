# Leaving Soon QA for PR #180

**Verdict: LOCAL_QA_COMPLETE**

**Base commit** `92dec642fe1d9309a492b705dc661a601b40723f`. The results below distinguish the PR head from the local fix applied on top of it. Final PR review belongs to ChatGPT Web.

The original 20 tests passed on the PR head. The additional cases exposed one reproducible read defect. A source could withdraw its offer, yet the response still credited that source with a verified departure because another source kept the canonical row available. The local fix checks current source presence in bulk before crediting a date. A separate question about conflicting dates overwritten during ingestion remains open.

## Disposition of the four original failures

| Original case and classification | Expected versus actual at the PR head | Evidence and final disposition |
|---|---|---|
| **Withdrawn expiry source — real defect** | The test first observed one confirmed departure. After withdrawal it expected zero and received one. The ledger recorded Tubi absent and JustWatch present. JustWatch had supplied no date. | `backend/availability/persistence/repositories.py:448` removes the source assertion while preserving availability from co-reporters. At the base commit, `backend/tracker/views_leaving_soon.py:70` accepted the canonical row's date and source without consulting that ledger. In the local view, line 54 loads ledger entries for dated identities and line 111 checks each supporting source. The withdrawal and source-purge regressions now each require both zero confirmed offers and empty items. See `backend/tracker/tests/test_leaving_soon.py:478` and `:492`. |
| **Two dates written to one canonical identity — design ambiguity, unresolved** | JustWatch wrote day 9, then Tubi wrote day 7 to the same identity. The exploratory test expected zero confirmed dates. The endpoint returned one. The positive control, where JustWatch supplied no date and Tubi supplied day 7, also returned one. | Upsert replaces the source and end date at `backend/availability/persistence/repositories.py:303` and `:310`. The evidence record at `:335` preserves presence. Its fields at `backend/availability/models.py:585` carry no per-source expiry. Both histories therefore present identical window evidence to the reader. The final regression at `backend/tracker/tests/test_leaving_soon.py:464` uses two surviving rows for the same effective offer. Day 7 against day 9 suppresses the departure. Updating them to agree restores one departure, credited only to Tubi. Reconciliation handles these persisted conflicts at `backend/availability/services/reconciliation.py:552`. |
| **Single PBS episode plus parent rows — test-fixture defect in the expected label** | The fixture expected episode 2 to be `last_option`. It received `leaving_provider`. Exactly one departure was confirmed. | The PBS adapter derives series and season windows at `backend/availability/sources/pbs.py:503` and `:534`, and reads the episode's declared date at `:566`. The agreed overlap policy treats accessible parent coverage with an unconfirmed end as a potential alternative. The base view implements that rule at `backend/tracker/views_leaving_soon.py:158`. The final adapter-backed test at `backend/tracker/tests/test_leaving_soon.py:412` checks the exact source, season, episode, label, and count. It makes no claim that a second provider exists. |
| **Anonymous request with no legacy owner setting — test-fixture defect in the expected status** | The exploratory test expected HTTP 403. The route returned HTTP 200 and called the stubbed watchlist reader. | The single-owner contract at base `backend/tracker/views_leaving_soon.py:81` explicitly uses the watchlist field default when the setting is absent. That field currently defaults to 1 at `backend/tracker/models.py:717`. The independent final test removes the setting, expects 200, and checks the exact owner passed to the reader at `backend/tracker/tests/test_leaving_soon.py:531`. A separate routed test at `:538` assigns distinct titles to two synthetic owners and verifies that the authenticated principal gets only its own title even when another ID is supplied. |

The four original failures reproduced in both suites. None was an environment failure.

## Checks against the six invariants

| Invariant | Independent evidence in the final worktree |
|---|---|
| Expiry provenance | PBS emitted three scopes from one asset. Only its episode supplied a confirmed date. A Tubi season date remained excluded even when JustWatch agreed. Missing dates, stale direct evidence paired with fresh presence, a JustWatch-only date, and a persisted window conflict yielded no departures. A fresh matching direct date supplied the positive control. Tests at lines 412, 436, 464 and 501 of `backend/tracker/tests/test_leaving_soon.py`. |
| Typed identities | Both a movie and a series with TMDb ID 801 were on the same watchlist. Real reconciliation returned the movie's single departure and the series' own series, season and episode rows, with their exact providers and labels. Test at line 383. |
| Overlapping alternatives | A later season covered its earlier episode. The same episode number in another season retained `last_option`. An earlier series departure had later overlapping coverage. Adding an accessible undated series offer prevented every series row from claiming `last_option`. Tests at lines 383 and 412. |
| Country and reach | Identical duplicate countries, empty duplicates in either order, comma-separated codes, an unassigned code, and a non-ASCII letter returned 400 with zero SQL statements. With US saved and NL requested, the NL offer required VPN reach. With VPN enabled, each explicit country returned exactly its own offer and locality. Tests at lines 510 and 523. |
| Owner isolation | The authenticated principal, explicitly configured legacy owner, and absent-setting model default were exercised. The view uses the model default for single-owner compatibility. It contains no numeric fallback of its own. Tests at lines 318, 531 and 538. |
| Entitlement | The real classifier and saved subscriptions admitted AVOD, excluded rent and purchase even with a store account, excluded an unknown access mode and an unknown offer type, and required both a channel and its base. Adding the base changed the earlier AVOD departure from `last_option` to `leaving_provider`. Test at line 548. |

## Query counts and side effects

Each measurement covers a routed GET, including response rendering. Fixture writes occurred outside capture. The final scaling fixture used repository upserts, three native seasons per title, and one episode per season on a second provider. All 288 offers appeared in the largest response.

| Watchlist titles | Offers | SELECTs at PR head | SELECTs with local fix |
|---:|---:|---:|---:|
| 0 | 0 | 5 | 5 |
| 1 | 6 | 10 | 11 |
| 12 | 72 | 10 | 11 |
| 48 | 288 | 10 | 11 |

The additional query reads the per-source ledger for all dated identities. The initial scale run used canonical offer fixtures. The final run also populated their ledger rows. Repeating the largest GET at the frozen evaluation time produced identical JSON and the same count. Each malformed-country GET in the dedicated rejection test used zero queries.

The shared request helper at `backend/tracker/tests/test_leaving_soon.py:358` uses Django's `CaptureQueriesContext` on the default connection. Every captured statement began with `SELECT`. The tests observed zero INSERT, UPDATE, DELETE, or schema statements, zero Django on-commit callbacks, and zero calls to source acquisition, targeted/catalogue refresh, refresh enqueue, Requests transport, or Python socket connect. Each boundary has a failing guard and a call-count assertion. Real reconciliation, ORM queries and classification ran inside those guards.

## Harness and logs

All database tests used the repository's disposable harness with a per-invocation Compose project and tmpfs PostgreSQL. The final focused and full runs used projects `wtdwi-test-29642` and `wtdwi-test-29643`. Commands from the worktree root:

```sh
git rev-parse HEAD
git show 92dec64 -- backend/tracker/views_leaving_soon.py backend/tracker/tests/test_leaving_soon.py
./scripts/test-backend-isolated.sh tracker.tests.test_leaving_soon -v 2
./scripts/test-backend-isolated.sh tracker availability -v 2
git diff --check
```

| Run | Passed | Failed |
|---|---:|---:|
| PR head, existing focused tests | 20 | 0 |
| PR head plus initial QA probes, focused | 28 | 4 |
| PR head plus initial QA probes, full | 3,718 | 4 |
| Interim source-presence fix plus purge case, focused | 30 | 3 |
| Final worktree, focused | 33 | 0 |
| Final worktree, full tracker and availability suite | 3,723 | 0 |

Both final commands exited 0. Django reported 3.098 seconds for the focused module and 145.090 seconds for the full suite, with zero errors or skips. An AST comparison verified that all 20 original test methods are identical to their versions at the base commit.

The original logs were copied before the final reruns and checked byte-for-byte. Their local filenames and SHA-256 digests are:

- `.qa-focused-original.log` — `58a6517c7bfe2c426ba46a216e29d4a21da53ca59577295b96f842f62f40b019`
- `.qa-suite-original.log` — `a016463ff290c58f0e4204d186b26c74160e3c9ae231c3190332888deb5cbfd9`

Final stdout and stderr are in `.qa-focused.log` and `.qa-suite.log`. The intermediate focused run is in `.qa-provenance.log`. All five logs are ignored local artifacts.

## Follow-up and coverage limits

The persistence follow-up is to decide whether expiry disagreements must survive successive writes to the same canonical identity. If the product requires historical disagreement detection, ingestion would need to retain per-source end dates and their observation lifecycle. Until then a later direct date can be confirmed after a conflicting earlier value has been overwritten. The view can enforce disagreements between surviving rows and reject a date whose supporting source withdrew or was purged while another source kept the offer present.

The original module's helper stubs both reconciliation and classification at `backend/tracker/tests/test_leaving_soon.py:57`. The new database cases cover those layers together. The PBS case uses the production row builder against an offline asset. Most other fixtures begin at the repository or canonical model. The shared-identity fixture deliberately gives Tubi and JustWatch the same native key and explicitly invokes the repository's withdrawal operation. This demonstrates a supported persistence state. Its frequency in live catalogues is unknown. The separate purge case uses the same source-retirement operation as `backend/tracker/management/commands/purge_orphan_offer_evidence.py:92`.

The authentication test supplies a DRF principal directly. Login mechanics, concurrent ingestion and live payload accuracy remain outside this run. The scale measurement establishes a query bound across these fixture sizes only. Runtime complexity and database plans at larger sizes remain unmeasured.

## Changed files

- `backend/tracker/views_leaving_soon.py` — bulk source-presence check for dated observations, including compatibility with older rows lacking a ledger.
- `backend/tracker/tests/test_leaving_soon.py` — 13 additional routed tests and fixture support for the ledger check. All original test assertions were retained.
- `docs/leaving-soon-task2-qa.md` — local QA results and follow-up.
