# Issue #97 independent review of the four repaired heads

**Combined candidate — REQUEST_CHANGES.** The four repairs resolve the previously reported cases in their assigned scope. One additional Stage E case on #216 drops a verified display miss when the index abstains. The combined backend suite passed 3,900 tests. A separate one-test regression against the same application tree failed. Hold Stage D Batch 1 and Stage E Batch A. ChatGPT Web owns final review and merges.

All four open drafts target master at `0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea`. GitHub pull metadata and the advertised pull refs matched the following heads. I checked each Actions run’s head SHA, attempt and job conclusions with `gh run view RUN --json headSha,attempt,conclusion,jobs` and read its test output with `gh run view RUN --log`. All four runs passed backend, frontend and extension on attempt 1, with 403 frontend and 34 extension tests per run.

| PR | Reviewed head | Hosted CI run | Backend tests | Offline review verdict |
| --- | --- | --- | ---: | --- |
| [#213](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/213) | `2876a2ee3a4161d2231fd078be37ed8672315583` | [45715371446](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45715371446) | 3863 | **READY_FOR_CHATGPT_REVIEW** |
| [#214](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/214) | `deed4b1a15d7d841102627cb272a354e4dbda857` | [45715298084](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45715298084) | 3871 | **READY_FOR_CHATGPT_REVIEW** |
| [#216](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/216) | `cce03f5a839b80cb4859bf739e9b8a38f3266c5b` | [45711023878](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45711023878) | 3892 | **REQUEST_CHANGES** |
| [#217](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/217) | `abe035c59b39e627690ef94a3dfe64458d1e689f` | [45715149887](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45715149887) | 3863 | **READY_FOR_CHATGPT_REVIEW** |

## Remaining finding on #216

**High · Independent display retrieval omits an eligible miss.** The protocol counts independently positive pairs with a completed, correctly scoped display read. A fresh index response with no class assertion can abstain. A verified empty app response for that title is still a display miss. With one such pair the expected display result is 0/1. The adapter returns 0/0 (`UNDEFINED`). In a larger panel this can inflate measured retrieval by dropping misses. Actual historical accuracy remains undefined. [Evidence protocol, display and denominator rules](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea/docs/audits/issue97/stage-e-evidence-protocol.md#adjudication-and-denominators) · [Adapter on the reviewed head](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/cce03f5a839b80cb4859bf739e9b8a38f3266c5b/backend/availability/services/issue97_stage_e_adjudication.py#L458-L480)

Minimal offline reproduction using `TypedApplicationReplay` from `availability.tests.test_issue97_stage_e_app_replay`:

1. In the existing synthetic NL movie 27205 setup, get `pair, index, native = self.reviewed_report()`. Delete the fixture’s single Netflix source-report row with `self.original.delete()`. Call `self.read("movie")`. The normal typed route returns HTTP 200 with an empty `source_reported_offers` list. Scoring this body with the original index returns `miss`.
2. Load the same public synthetic search fixture and set its matched title node’s `offers` to `[]`. Run `replay_scope` on the same typed scope. It returns `status="unknown"` with zero title offers. Pin the SHA-256 of the new fixture bytes (sorted-key compact JSON) into both the index receipt and observation. Set the index signal to the empty string. Keep its valid binding and original times, independent native positive and local read.
3. `adjudicate(pair, index, native)` returns eligible, index unknown, advertised yes, outcome abstention. `self.score_report(pair, index, native, response)` returns independent state `unresolved` and projection `unscored`. `display_rate([result])` gives 0/0. `self.assertEqual(result.state, "miss")` fails. This ran on disposable PostgreSQL with provider transport patched to raise on use.

**Requested correction.** In `source_report_retrieval`, classify absence from the verified typed display even when a valid, fresh and independent source abstains. Preserve the separate assertion and projection verdicts, independent identity and time checks, and exclusions for echoes, malformed reads and out-of-scope rows. Add a typed HTTP regression for the empty 200 and the supported typed 404. Rerun the four-head verification and hosted checks on any updated candidate before integration.

## Reproduction of the earlier findings

| Area | Earlier revision and reproduction | Repaired result |
| --- | --- | --- |
| #213 hosted evidence | On `b2e954562cbd797e052e706a974a982fdb6a07be`, the report listed 10/11/10 extension/backend/frontend steps, six runner tests and no warnings. GitHub PR edit history also recorded 13 missing typed rows plus a receipt on a retained target. | Master attempt 1 had three failed jobs with zero steps and a billing/spending annotation. Master attempt 2 and original #213 each had 11/12/10 successful steps, 25 Stage D runner tests and frontend hook/image warnings. The explicit migrate step applied 101 migrations. The repair limits the billing evidence to the observed runs, identifies both revisions, and describes 12 missing identities plus one receipt on a fresh target. The repaired head has its own passing run above. [Master rerun](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/attempts/2) · [Original #213](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45647912441) |
| #214 signing instructions | The gate at `6f68bb78d8e0b14239463a54f99ccd85104dc13e` and old PR description both gave `movie-1396-nl` as scope two. Running the new report assertion against the old report failed on the media type. | Manifest, corrected report, current PR body and both independent disposable preflights agree on **`movie-238-nl` followed by `series-1396-nl`**. The standalone guard passed. |
| #216 conflicts | At `83be9530216a9688a9d766454c52d4a9d488ce93`, replace only the fixture index signal with `explicit_class_negative`. Both review flags are false. The old outcome was FN, false-negative rate 1/1. A comparable unreviewed price mismatch also counted as a conflict. | The same negative gives `needs_recheck`, denominator zero. A reviewed negative with the wrong named index also stays excluded. With its matching FN disposition it counts 1/1. A price mismatch keeps TP, with price denominator zero pending review and one after price-conflict review. FP is covered by the opposite-cell regression. |
| #216 independence and granularity | On the same old head, set the reviewed index and observation document reference equal to the native document while retaining different digest bytes. The old adapter counted a matching source-report row 1/1. In an independent fixture, setting season 1 and episode 2 on the sole movie row also counted 1/1. | The shared document produces `same_source_echo`, projection `displayed`, independent denominator zero. The child row produces `unresolved`, projection `unscored`, denominator zero. Combined tests exercise persisted child rows, identical payloads, presence and absence, and an exact reviewed series mapping. |
| #216 panel completeness | Submitting one TP to old `summarize` returned planned 1, full-panel recall 1.0. | Without a manifest, planned count and completeness are unknown and full-panel recall is undefined. Against the independently loaded 15 frozen scopes, one TP retains conditional 1/1 and reports 14 missing, both full-panel rates undefined. All 15 synthetic positives yield 15/15, and substituting historical original times excludes all 15. Duplicate, extra and scope-mismatched submissions have regression coverage. |
| #217 source authorization | The old packet at `af3720ffacdd8dfd3372d325d1e21873426528c0` claimed readiness for owner authorization and listed E01–E07. The repaired table-to-CSV assertion failed on the old packet. #217 postdates the findings in #218. | All nine Batch A IDs, capture scopes, classes and pending statuses match the frozen CSV. E08 Prime free and E09 Rakuten free-with-ads remain unknown. The 33 source-specific and nine cross-source entries distinguish preparation from live authorization. Zero of three source decisions are complete. |

The prior review used a wider candidate. On the historical #207 head, the local merge-tree result against this master adds only its report, and GitHub still reported a dirty merge state. The historical #210 readiness headline coexists with a read-only preflight showing 13 blockers and zero accounting on a fresh database. On the old #215 head, an in-memory comparison reproduced two distinct historical identifier matches in its report. Its cleanliness branch checked HEAD ancestry and remote-branch counts. These historical findings have no assigned replacement for #207 or #215 in the four-head candidate. Publication here includes only aggregate results. [Prior independent review at #218](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/20a4fb80d1aee8b3db406cb7868716e4359bb275/docs/audits/issue97/independent-final-review-f.md)

## Combined tree and verification commands

From the pinned master above, create a temporary index. For each of #213, #214, #216 and #217 in that order, apply its full base-to-head diff. All four have the same merge base and disjoint changed paths. `git write-tree` returned **`dd3ba2701511a7de2511788672aaaf0034c41011`**. This is the local combined code and documentation tested below. The publication branch carries this review on master. Hosted checks in the table belong to the individual heads. No hosted combined-tree run was established.

```sh
set -eu
base=0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea
scratch=$(mktemp -d)
export GIT_INDEX_FILE="$scratch/index"
git read-tree "$base"
for head in 2876a2ee3a4161d2231fd078be37ed8672315583 deed4b1a15d7d841102627cb272a354e4dbda857 cce03f5a839b80cb4859bf739e9b8a38f3266c5b abe035c59b39e627690ef94a3dfe64458d1e689f; do
    test "$(git merge-base "$base" "$head")" = "$base"
    git diff --binary "$base" "$head" > "$scratch/head.patch"
    git apply --cached "$scratch/head.patch"
done
candidate=$(git write-tree)
test "$candidate" = dd3ba2701511a7de2511788672aaaf0034c41011
git diff --check "$base" "$candidate"
mkdir "$scratch/tree"
git archive "$candidate" | tar -xf - -C "$scratch/tree"
export GIT_DIR=$(git rev-parse --absolute-git-dir)
export GIT_WORK_TREE="$scratch/tree"
cd "$GIT_WORK_TREE"
PYTHONPATH=backend python3 -B scripts/test_issue97_stage_d.py -v
python3 -BE -m unittest -v scripts.test_audit_issue97_stage_e
python3 -BE scripts/audit_issue97_stage_e.py
python3 -BE scripts/audit_issue97_stage_e_adjudication.py
PYTHONPATH=backend python3 -B -m unittest availability.tests.test_issue97_stage_e_adjudication
```

The combined diff has eleven paths. Relative to repository root:

| PR | Changed files |
| --- | --- |
| #213 | `docs/audits/issue97/ci-actions-verification.md` |
| #214 | `backend/availability/tests/test_issue97_stage_d_runner.py`, `docs/audits/issue97/stage-d-offline-verification.md`, `scripts/test_issue97_stage_d.py` |
| #216 | `backend/availability/services/issue97_stage_e_adjudication.py`, `backend/availability/tests/test_issue97_stage_e_adjudication.py`, `backend/availability/tests/test_issue97_stage_e_app_replay.py`, `docs/audits/issue97/stage-e-adjudication-replay.md`, `scripts/audit_issue97_stage_e_adjudication.py` |
| #217 | `docs/audits/issue97/stage-e-source-authorization-readiness.md`, `scripts/test_audit_issue97_stage_e.py` |

| Combined check | Command and observed result |
| --- | --- |
| Schema and preflight | Two fresh PostgreSQL 16.15 tmpfs clusters, Docker network `none`, zero published ports, code mounted read-only. Ran `manage.py makemigrations --check --dry-run`, then `manage.py migrate availability 0018` and `0019` in opposite orders, `migrate --noinput`, `migrate --check` and catalog queries. Both converged with 103 migration records, 0020 applied, non-null JSONB offer variants with no database default, enabled immutability triggers on both ledger tables and unique event (attempt, kind). Read-only `manage.py issue97_stage_d --batch 1` returned blocked, 12 missing typed identities and one missing current NL search receipt, zero attempts, handoffs, transmissions and checkpoints. Budget, attempt and event tables remained empty. [Isolated image pins and setup](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/blob/0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea/docs/audits/issue97/stage-d-runtime-authorization-verification.md#local-commands-and-results) |
| Backend | `python manage.py test tracker availability -v 1 --noinput` on the combined export. **3,900 passed in 187.596 seconds**. The additional one-test typed-read probe above failed independently. |
| Standalone | Stage D 6 passed, 1 optional restricted-input check skipped. Stage E historical regressions 5/5, adjudicator 24/24. Both standalone audits passed. The historical audit reads pinned Git objects, hence the explicit Git directory for the exported tree. |
| Frontend | `npm run lint`, `NEXT_TELEMETRY_DISABLED=1 npm run build`, `npm test` succeeded, 403/403 tests in 47 files. Lint and build reported the existing hook dependency and raw image warnings. |
| Extension | `npm run typecheck`, `npm test`, `npm run build`, `npm run validate:package` succeeded, 34/34 tests in four files. |
| Local client isolation | Node 22.23.1. The offline npm cache lacked packages. Installed lockfile dependencies from the npm registry with `npm ci --ignore-scripts --no-audit --no-fund`, then ran the application checks in a sandbox allowing only loopback outbound connections. Frontend, extension and workflow contents equal the master trees. |

**Frozen baseline and live decision.** The Stage E candidate CSV has SHA-256 `ac1d37ecf5713e76ef7a146b2f9847e2312050890e769d2fa570ab3e46a4891d`. Its 15 rows cover nine A and six B candidates across eight historical documents. Seven historical pairs fall outside the allowed window, six lack a historical index pair and two controls have no assigned outcome. The baseline remains 56 source-reported offers, seven historical candidates, zero temporally eligible independent comparisons, zero confirmed negatives and accuracy **UNDEFINED**. Entitlement and playback remain untested.

Stage D still needs the intended retained target and identities, current runtime receipt and exact-operation permission, quota/prior-spend and rate/exclusivity evidence, restricted custody, a clean preflight and separate batch approval. Stage E needs source-specific access, extraction, retention and publication decisions, the reviewed allowlist, integrated preflight, bounded slot, runtime authority and separate owner approval. Issue #97 remains open and #103 remains on hold.

Review conducted November 25–26, 2026 (Europe/Amsterdam). Review route Codex / OpenAI `gpt-6.2-astra`, XHIGH, `COST_UNKNOWN`. This review made **zero live provider or first-party requests**, issued zero operational authorizations and ran zero live batches. GitHub and the npm registry were accessed for review, publication and dependency installation. All database writes and migrations used disposable local test clusters. No deploy, permission change, merge, closure, history rewrite or CI waiver occurred.
