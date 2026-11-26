# Stage E offline adjudication and typed application replay

The frozen record still contains 56 JustWatch source-reported offers and seven historical first-party candidate matches. The earliest possible original JustWatch event for each match is more than ten minutes after its first-party capture. There are zero temporally eligible comparisons, zero confirmed negatives and no defined accuracy rate. Six more panel rows have historical first-party evidence alone. Two free-class controls have no assigned outcome. Entitlement and playback are untested. [Historical protocol](stage-e-evidence-protocol.md) · [Independent review of the previous implementation](stage-de-independent-readiness-review.md)

## Inputs to the adjudicator

Pass a separately reviewed `ReviewedPair` for a frozen candidate and one sealed `Observation` per side. The panel supplies typed scope, canonical title/year, edition, NL market, service/class, channel parent and the approved UTC slot. Each role has its own native ID, reviewed title/alias, package ID, issuer, digest of the effective document locator and reviewed manifest digest. Its capture receipt pins the contributing payload digest and original observation bounds. A replay changing the original timestamp or substituting a different document fails the receipt check.

The input boundary is a reviewed extraction. The reviewer establishes each binding, the document kind, the title-specific action, negative semantics, comparable price conditions and completeness before comparison. Each explicit negative needs a second review. To score a disagreement, the sealed pair also needs a second-review disposition confirming the particular FP, FN or price conflict for the named index. Pending availability disputes contribute to the recheck count and pending price disputes contribute to the price recheck count. Fresh review records, authorized slots and a collection entry point are outstanding. The frozen historical pointers support document identification only. [Source review and custody prerequisites](stage-e-source-review.md)

| Output | Admission and counting |
| --- | --- |
| First-party advertised | Only a successful scoped title document and reviewed current class signal produce a binary verdict. A disputed signal requires recheck. |
| Named index accuracy | Each eligible pair contributes to one of TP, FP, FN, TN when both assertions are binary. Source, app and comparator share a maximum ten-minute expanded span inside one half-open 30-minute UTC slot. The app adds the local read to the original contributing capture. Matching origins or payloads indicate an echo. Unresolved positive, unresolved negative, abstention and two unknowns remain distinguishable. |
| Price | A positive rent/buy pair needs complete unique variant sets and exact per-variant EUR minor units, quality, basis, promotion and access conditions. Different comparable amounts are a price conflict. A partial set yields unknown. |
| Display retrieval | Count displayed pairs over displayed plus missed pairs for one surface. Exclude hidden classes, stale history and unscorable reads. A verified typed 404 can yield a retrieval miss. On the report surface, validate typed identity, provider label, market, class, source lineage and original time. The source-reported bucket can satisfy report retrieval while the application's current assertion remains unknown. |

For source-report retrieval, the typed adapter checks the source issuer, document and payload against the native comparator. An echo can show that the app displayed the sealed source report. It contributes zero to the independent display numerator and denominator. A title-level display requires a title-level row or an exact season/episode mapping separately reviewed for that series and sealed pair. The adapter reports display fidelity to the source in `projection` and the independently verified result in `state`.

With a current, independently confirmed offer, a fresh index abstention can accompany a verified display miss. An empty typed 200 and the typed detail route's supported 404 each contribute one eligible miss. A provider 404 cannot establish an advertised offer. The index retains its abstention and contributes no binary accuracy cell. The projection stays unscored without a sealed positive index assertion, and a displayed row still requires that assertion and its trace.

Pass the expected frozen sequence of candidate IDs and scopes to `summarize` independently of the submitted decisions. The audit loads all 15 scopes from the pinned candidate matrix and typed identity inventory. The summary reports planned, submitted and matched counts, missing IDs, unexpected IDs and scope mismatches. Conditional precision and recall use binary outcomes among the matching rows. Without a manifest, completeness is unknown and only conditional rates are available. Full-panel precision and recall require complete coverage and established denominators. A zero denominator yields `UNDEFINED`.

## Reproduction

From the repository root, run the following with the existing local rehearsal images. The shell harness verifies disposable database identity, uses a private Docker network with external routing disabled, mounts the backend read-only and uses a tmpfs PostgreSQL cluster.

```sh
python3 -BE scripts/audit_issue97_stage_e_adjudication.py
python3 -BE -m unittest scripts.test_audit_issue97_stage_e
PYTHONPATH=backend python3 -B -m unittest availability.tests.test_issue97_stage_e_adjudication
sh scripts/rehearse_issue97_stage_e.sh \
  availability.tests.test_issue97_stage_e_adjudication \
  availability.tests.test_issue97_stage_e_app_replay \
  availability.tests.test_issue97_stage_e_rehearsal \
  availability.tests.test_experiment_runner.GuardedTransportTests \
  availability.tests.test_experiment_runner.PostgreSQLReservationTests \
  availability.tests.test_experiment_runner.PostgreSQLPreflightTests.test_genuine_evidence_absence_leaves_read_only_disposable_preflight_blocked \
  availability.tests.test_justwatch_reviewed_transport availability.tests.test_issue97_collection \
  tracker.tests.test_issue97_availability_contract
```

The standalone audit checks the existing provenance joins, the typed identity manifest and the retained observation ranges for both historical index titles. Its 2030 synthetic smoke exercises all 15 panel rows, withholds full-panel metrics when 14 rows are omitted, and rejects an older original timestamp on every row. The five historical-binding and authorization regressions and 27 adjudicator tests pass.

The focused suite ran **94 tests** on PostgreSQL 16.15, including seven typed application replays and all 27 adjudicator tests. The backend rehearsal image ID was `sha256:b0fcbfe183cb9074a38fde01a487c3e0240b6a28f9cfa3c4b74f5e8e0dd487f4`. The replay parsed a synthetic JustWatch response through mocked HTTP, persisted its Netflix row and title-check provenance in the disposable database, and read the normal movie and series detail routes with Django's in-process HTTP client. Separate Prime fixtures exercised the current and purchase surfaces.

| Local read | Observed result |
| --- | --- |
| Movie 27205, NL | HTTP 200. The Netflix source report retained the JustWatch source and original fixture timestamp. Prime subscription appeared under current offers. The configured fixture store user saw rent and buy under purchase offers. |
| Movie 27205, NL, independently positive with an abstaining index | After removing the saved Netflix row, HTTP 200 returned an empty report list. The regression on `cce03f5a839b80cb4859bf739e9b8a38f3266c5b` produced retrieval **0/0 (UNDEFINED)**. The repair scores a miss, **0/1**. |
| Untracked movie 27205, NL, independently positive with an abstaining index | HTTP 404 returned the typed movie's untracked response. The repair scores a miss, **0/1**, while the series with the same numerical ID still returns HTTP 200. |
| Series 27205, NL and movie 27205, BE | HTTP 200. Neither source-reported surface included the NL movie observation. |
| Untracked movie 999999, NL | HTTP 404. |
| Same movie, eight days later | HTTP 200. Current and source-reported lists were empty. Historical rows preserved their original last-seen time in PostgreSQL and the response. |

Each read emits its route, status and response SHA-256 for that run. Surrogate database IDs can change the response digest across runs. Mixing one displayed row with one or both miss types yields retrieval **1/2** or **1/3** and conditional index recall **1/1**. Full-panel recall remains undefined when an independently positive row abstains or a frozen row is missing. Fixture results establish application behavior only. The comparative historical baseline remains as reported above.

Independent combined-tree review and successful hosted checks remain open. Live work also requires the source and custody decisions, exact-scope owner authorization, runtime and quota receipts, and a reviewed integrated runner/preflight. Issue #97 remains open and #103 stays on hold. [Authorization prerequisites](stage-e-authorization-request.md)
