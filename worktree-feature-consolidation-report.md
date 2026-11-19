# WhereTfDoIWatchIt — Worktree Feature Consolidation Report

Date: 2026-11-15  
Repository: `/Users/angeloandreaisola/Documents/GitHub/WhereTfDoIWatchIt`  
Remote baseline: `origin/master` at `47d78dd6137f17fd8082feb38af6e918b230081a` (PR #92 merge)

## Outcome

The local canonical checkout was fast-forwarded from `dbcacee6a79faecc00e6194db60e097a772b99e1` to `origin/master` with `git merge --ff-only`. Its tracked files were clean, the merge-base check confirmed a safe fast-forward, and none of the incoming paths overlapped the pre-existing untracked `.worktrees/`, `docs/audits/stage2q/`, or `docs/audits/stage2r/` content.

Initial inventory: **27** registered worktrees (including canonical), **87** local branches, **95** remote-tracking branches, and **5** stashes. Final inventory: **8** worktrees (including canonical), **87** local branches, **95** remote-tracking branches, and the same **5** stash SHAs. **19** redundant worktrees were removed using ordinary `git worktree remove`; one clean temporary audit worktree was also removed. No branch or stash was deleted, no reset/rebase/squash/force operation was used, no recovery PR was created, and no PR was merged by this task.

## Existing reports reviewed

Reviewed the prior `cleanup-report.md`, `final-audit-cleanup-report.md`, `final-recovery-report.md`, `feature-recovery-opportunities-2026-11-15.md`, the detached worktree's `WORKTREE_CLEANUP_REPORT.md`, the preferences/stash audit, and the Stage 2E/2Q/2R readiness reports. Their earlier baselines and counts were stale; this report uses the freshly fetched `origin/master` SHA above. The feature-opportunity report's conclusion is confirmed: PRs #87–#92 cover watched/catalogue recovery, RaiPlay logging is already in master, PR #74 remains incomplete, and unsupported source prototypes should remain disabled.

## Individual worktree assessments

| Worktree (original path) | Features found | Recovery result | Master coverage | Removed/retained | Reason |
|---|---|---|---|---|---|
| Canonical `WhereTfDoIWatchIt` — `master`, initial `dbcacee6a7` | Application baseline; untracked nested worktrees and local Stage 2Q/2R reports | Fast-forwarded to `47d78dd6137f17fd8082feb38af6e918b230081a`; untracked files preserved | Current master | Retained | Canonical checkout; pre-existing untracked content remains intact |
| `/private/tmp/watched-pr92-recovery` — `recovery/watched-actor-duration-filters`, `ab0bfa2147` | Watched actor AND and duration filters | PR #92 already contains the implementation and tests | Fully integrated by merged PR #92 | Removed | Clean, inactive, unlocked; source branch preserved |
| `.codex/worktrees/release54-b2-final/WhereTfDoIWatchIt` — `codex/release54-b2-final`, `eb6afe21e4` | Release 5.4 availability/freshness hardening | PR #69 and later master supersede this snapshot | Integrated | Removed | Clean, inactive, unlocked; branch preserved |
| `.../opencode/.../cosmic-sailor` — `stage2b-live-availability-audit`, `af04d479ed` | Stage 2B evidence and five modified audit files | No application feature; retain all local evidence edits | Base evidence archived; edits remain unique local evidence | Retained | Dirty tracked reports; preserve pending provenance review |
| `.../opencode/.../curious-meadow` — `phase3-3i-raiplay-logging`, `1f9de30b2a` | RaiPlay checkpoint INFO logging, tests, operations guidance | No recovery needed; test file is byte-identical in master | Fully integrated | Removed | Clean and inactive; unique commit's behavior is present; branch preserved |
| `.../opencode/.../eager-harbor` — detached, `dbcacee6a7` | Snapshot of prior canonical master | None needed | Ancestor of current master | Removed | Clean, inactive snapshot; no unique files; detached commit remains reachable |
| `.../opencode/.../gentle-garden` — detached, `7cec749f40` | Historical `WORKTREE_CLEANUP_REPORT.md` | Read and superseded by this report; original retained | Report information reviewed; no application feature | Retained | Unique untracked historical report preserved |
| `.../opencode/.../gentle-rocket` — detached, `7cec749f40` | Clean old-master snapshot | None needed | Ancestor of current master | Removed | Clean, inactive; no unique files |
| `.../opencode/.../mighty-lagoon` — detached, `00d9dc843c` | Eight Stage 2D evidence files, including CSV snapshots | Existing archive contains most files exactly; environment inventory differs | Historical reports archived; differing local revision retained | Retained | Untracked evidence includes a non-identical report; preserve provenance |
| `.../opencode/.../playful-falcon` — `docs/archive-phase3-wave2-reconciliation`, `305c1a101a` | Phase 3/Wave 2 reconciliation archive | Already represented by merged audit archive | Integrated | Removed | Clean, inactive, branch tip contained in current master |
| `.../opencode/.../proud-rocket` — detached, `01d69c02df` | Clean historical snapshot | None needed | Its committed tree is superseded by current master | Removed | Clean, inactive; no unique files |
| `.../opencode/.../shiny-wizard` — `stage2b-docs-finalize`, `9941e0e489` | Stage 2B documentation finalization | Already integrated | Integrated | Removed | Clean, inactive; branch preserved |
| `.../opencode/.../witty-cactus` — detached, `bd3da37934` | Two untracked pip output files (`=3.12`, `=3.14`) | No feature code; contents inspected as dependency-install output | No feature coverage needed | Retained | Untracked files are not application source; preserved pending owner cleanup |
| `.../opencode/.../witty-pixel` — detached, `dbcacee6a7` | Clean prior-master snapshot | None needed | Ancestor of current master | Removed | Clean, inactive; no unique files |
| `WhereTfDoIWatchIt-cv` — `agent/gemini/resume-review`, `c178f79a97` | Three series display/navigation/dependency commits | Equivalent series page/card behavior is present in master and merged series PRs | Functionally integrated/superseded | Removed | Orca showed inactive with no terminal; clean; branch and commits preserved |
| `WhereTfDoIWatchIt-provider-recon` — `agent/codex/provider-reconciliation`, `c178f79a97` | Same three series display/navigation commits as `cv` | Equivalent behavior present in master | Functionally integrated/superseded | Removed | Orca showed inactive with no terminal; clean; branch and commits preserved |
| `WhereTfDoIWatchIt-recovery-watched` — `recovery/phase0c-watched-section`, `5c7795cbe3` | Original Watched listing endpoint/page | Merged PR #51; PR #92 later completed filtering/history | Fully integrated | Removed | Clean, inactive, unlocked; branch preserved |
| `WhereTfDoIWatchIt-stage2s` — `codex/stage2s-reduced-justwatch`, `fa8a7eb193` | Reduced bounded JustWatch diagnostic | Merged PR #86; execution remains gated by Stage 2R requirements | Code integrated; live experiment not authorized/performed | Removed | Clean, inactive; branch preserved |
| `WhereTfDoIWatchIt-watched` — `feature/watched-section`, `72420d71d7` | Watched page/filter edits; 8 tracked edits and 3 untracked tests | Actor/duration behavior is in PR #92; remaining edits/tests overlap #87/#89/#92. Differing local tests retained | Feature behavior integrated; extra local test variants not merged | Retained | Dirty worktree; preserve test material pending focused comparison |
| `.worktrees/jules-audit` — detached, `114fafc4fe` | Historical audit snapshot | No unique implementation identified | Superseded by current master and preserved audit docs | Removed | Clean, inactive, unlocked; no unique files |
| `.worktrees/new-sources` — `new-sources`, `043004b771` | Bilibili TV, OK.ru, StreamingCommunity parsers and research | Kept as research/prototype; no adapter enabled or copied into production code | No supported availability claim | Retained | One tracked registry edit plus six untracked source/report files; availability evidence/access blockers remain |
| `.worktrees/phase4-integration-rehearsal` — `phase4-integration-rehearsal`, `a0c0895f76` | Candidate discovery, mapping impact planner, RaiPlay targeted evidence and fixtures | Feature commits verified as integrated or superseded in master | Covered by merged Wave 2/Phase 4 PRs | Removed | Clean, inactive, unlocked; branch and merge history preserved |
| `orca/workspaces/.orca-preparing/773-...` — detached, `22acd2cc57` | Orca worktree-creation preparation snapshot | No change | Not applicable | Retained | Explicit Git lock `orca-create-preparation:v1:773:...`; untouched |
| `orca/.../Remove-Broken-Standalone-HydraHD-Script` — `phase3-dead-code`, `a26f3819dc` | Removal of obsolete standalone Hydra test script | Script is already absent in master; current scraper tests cover supported behavior | Superseded | Removed | Clean, inactive, unlocked; unique commit and branch preserved |
| `orca/.../Retire-BBCScraper` — `phase3-bbc-retirement`, `ade4bec709` | Retire legacy BBC scraper fallback | Master uses canonical BBC source and tests reject legacy fallback | Superseded | Removed | Clean, inactive, unlocked; unique commit and branch preserved |
| `orca/.../stage2n-youtube-identity` — `codex/stage2n-youtube-identity`, `7457efe10a` | YouTube identity policy and experiment runner safeguards | Merged PR #85 and later master | Integrated | Removed | Clean, inactive, unlocked; ignored files were generated build/cache artifacts; branch preserved |
| `orca/.../wt-audit-readonly` — `AngeloAndreaIsola/wt-audit-readonly`, `043004b771` | Clean read-only snapshot matching the new-sources baseline | No unique code or data | Superseded/duplicate snapshot | Removed | Clean, inactive, unlocked; branch preserved |

## Feature recovery and blockers

- **Watched section:** PR #92 is merged in the verified master. The original dirty watched worktree remains because it has additional non-identical local tests and tracked changes; no duplicate implementation was applied.
- **RaiPlay:** logging improvements and checkpoint tests are present in master; the local test blob matched exactly. No recovery PR is warranted.
- **Series filtering:** current `unified_catalog` handles movie/series/tv selection. The series stash's backend behavior is integrated; its broad test replacement was not applied. All five stash references remain unchanged.
- **Bilibili TV / OK.ru:** prototypes mark availability false and lack authoritative title identity, country, and offer evidence. They remain disabled and retained for research.
- **StreamingCommunity:** automated fetch is blocked by Cloudflare/access restrictions; only a manual HTML parser exists. No bypass or live request was attempted.
- **Browser extension PR #74:** retained as an incomplete open PR. Client foundation exists; backend handshake/token/ingest endpoints and end-to-end confirmation flow are missing. No partial merge or new PR was made.
- **Availability experiment:** Stage 2S code is integrated. Stage 2R still requires a revised/verified readiness state, current quota evidence, and separate authorization before any provider request; none occurred.
- **Stage 2D evidence:** preserved in `docs/audits/recovered-local-evidence/stage2d/`; exact copies were confirmed for most untracked files. The distinct environment inventory in `mighty-lagoon` remains untouched.

## Stashes (stable SHA; no apply/pop/drop)

| SHA | Prior purpose | Result |
|---|---|---|
| `59062ab04fec7ebec8a79aaa01d9e49968ea2f04` | Actor filters / local test changes | Behavior superseded by #87/#89/#92; stash preserved |
| `282946115aa4da769a91af216bdc9987c5da4553` | Series content-type filtering | Backend behavior integrated; stash preserved |
| `a37fb657547dd75fe31574c7f89fb6c1ef0041fa` | Language preferences relocation | Broad UI change is superseded/incomplete per audit; stash preserved |
| `f626daa7c5ff553e89ec11d4ab1466341514414b` | Catalog tabs/provider categories | Superseded by unified catalogue/provider filter work; stash preserved |
| `f95036538842a945c6c6097ac3cd1ca1f78a180e` | Graphify metadata | No product feature; stash preserved |

## Cleanup and verification

- Successfully removed **19 of the original 27** registered worktrees; final count is **8**, including canonical.
- A clean temporary audit worktree based on `origin/master` was created for comparison and removed with ordinary `git worktree remove`.
- All removed worktrees were individually checked for status, branch/HEAD, relevant tracked/untracked content, ignored files, PR/master coverage, Orca activity, and locks. Removed ignored content consisted of generated caches/build outputs/staticfiles; no `.env` or source-only ignored implementation was identified.
- No active terminal was found in the removed Orca workspaces. The locked preparation tree was retained. `cosmic-sailor` was already dirty at initial inspection and was not removed. Its status was not changed by this task.
- `origin/master` remains `47d78dd6137f17fd8082feb38af6e918b230081a`; local `master` now points to that same SHA.
- Local branch count remains 87 and remote-tracking branch count remains 95. All branch refs, including branches formerly checked out by removed worktrees, remain in place.
- Stash count remains 5; all five stable SHAs above remain present.
- No provider or metered requests, deployments, database writes, source activation, tests, branch deletion, stash mutation, or PR creation/merge occurred.

## Remaining worktrees and exact blockers

1. Canonical checkout — active recovery session and source of the requested report.
2. `cosmic-sailor` — five tracked Stage 2B evidence edits; retain until their provenance and intended disposition are reviewed.
3. `gentle-garden` — unique untracked historical cleanup report; retain as source record.
4. `mighty-lagoon` — untracked Stage 2D evidence; one environment inventory revision differs from the archived copy.
5. `witty-cactus` — untracked dependency-install output files; preserve pending owner cleanup.
6. `WhereTfDoIWatchIt-watched` — dirty changes and three differing untracked tests; behavior overlaps merged work, but preserve until local test deltas are reviewed.
7. `.worktrees/new-sources` — blocked provider prototypes and research; preserve without enabling sources.
8. Orca preparation worktree — explicitly locked; cannot be removed while the lock remains.

## Recommended next actions

1. Review the residual test-only deltas in `WhereTfDoIWatchIt-watched`; preserve any added regression coverage in a focused PR if useful.
2. Compare the Stage 2B/2D evidence revisions with archived provenance before consolidating or retiring those source worktrees.
3. Keep PR #74 blocked on backend handshake/ingest design and security review; do not merge its partial client foundation alone.
4. Reassess Stage 2R prerequisites before any provider activity; keep unsupported adapters disabled.
5. Revisit the remaining five non-canonical worktrees after the dirty/untracked evidence is either integrated, archived, or explicitly retired and the Orca lock is released.

## Branch reference snapshot

Local and remote-tracking branch names and object IDs after consolidation. The only local branch movement was the authorized fast-forward of `master`; branch refs for removed worktrees remain.

### Local branches

- `AngeloAndreaIsola/Retire-BBCScraper` — `5dcd6cf0c4daff70551cd2e0748003b17ac4814e`
- `AngeloAndreaIsola/wt-audit-readonly` — `043004b771dd9f76404855594998d3a35f12009a`
- `agent/codex/provider-reconciliation` — `c178f79a97165301809c62fc4533e5cced3ff9a5`
- `agent/gemini/resume-review` — `c178f79a97165301809c62fc4533e5cced3ff9a5`
- `b1-regression-tests` — `b7ef1b761b336e031e468c0383fe4b0ad836d9a4`
- `b2-1-freshness` — `9b635b611b2305b1e8940304e663a165a93384d5`
- `b2-2-explicit-refresh` — `0333d613bef5d5088deb863a33d0c746c5795041`
- `b2-3a-mapping-evidence` — `0dad646618a964734bb70c09044c0f81463c7940`
- `b2-3b-negative-evidence` — `ed77288ff74bc16fa1be1687d905feb9cc57acc6`
- `b2-4-offline-counterfactual` — `668c9d050517698d61b94f64eed51c0574df75a9`
- `codex/integration-prs71-73` — `0d45c5ac38bfe5da259fda009bb0544a67fdb151`
- `codex/release54-b2-final` — `eb6afe21e4ad7e018fbda15aca3224f0d294d46f`
- `codex/review-fix-pr71` — `b5fa2c367c06160488bce06dd74883ee95bd94e7`
- `codex/review-fix-pr72` — `e081164c12cb05123abcfef1b1390656e57071ef`
- `codex/review-fix-pr73` — `ee77167e7be5fe78df22bbad7a6a3213e5656aa5`
- `codex/stage2-availability-reliability` — `ced6e92b887520fe407621bdecad66ce073497b9`
- `codex/stage2c-accuracy-audit` — `17ac3987fcf472bf05ab7794519d91ef79446160`
- `codex/stage2c-accuracy-audit-baseline` — `f8a7dca7c9b29cffb1902eb67edf3573bae484d4`
- `codex/stage2c-availability-accuracy` — `17ac3987fcf472bf05ab7794519d91ef79446160`
- `codex/stage2e-freshness-recovery` — `01d69c02df6bedbbc3d15669477e2085d1c5071e`
- `codex/stage2f-a6` — `6570d49a2aeb3e30f80fcef2971281ea8566d312`
- `codex/stage2f-a7` — `da24f27092bb22063a68f082597bba0069b8ec8c`
- `codex/stage2f-final-integration` — `1963f21cdad85fa9d19e1b1616ea7770e5fcad39`
- `codex/stage2f-integration` — `39c0016a360d7d8d78f853a76959c50d49ae00ee`
- `codex/stage2g-safe-runner` — `f4f33a2b8c0c975dd7e174de9685e6b273d40234`
- `codex/stage2h-preflight` — `8381943b426c50ce3b8ccce2c10fe0f6743019c2`
- `codex/stage2h-preflight-docs` — `96f0806d66ba38dd4629b0aed1723461eaf07252`
- `codex/stage2m-mediaset` — `f50e3c6403d97aa8e5829757718a8f3962c9707f`
- `codex/stage2n-youtube-identity` — `7457efe10aaee611542601d7fa1e25ca693813aa`
- `codex/stage2s-reduced-justwatch` — `fa8a7eb1930561e6493c3a66dc706dab2158bbc0`
- `docs/archive-phase3-wave2-reconciliation` — `305c1a101a8a15f46d327a02803de4d7c9f88f96`
- `docs/letterboxd-import-guide-17263394451853639388` — `19408c4f8e6b3c0296e2c4602f23991a8714aef9`
- `docs/prime-api-contracts` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `feat-phase3-cron-audit-15708720946622184744` — `196eb02fa9c98b1735c46288eaea5039800b6987`
- `feat/add-free-streaming-providers-16420454610534920493` — `d83f2bff570e5d817811bfcacc44b303f1125e7d`
- `feat/budgeted-candidate-discovery-13583236362894172550` — `3a9693a5f44814c256ba8021ba9878514d026e22`
- `feat/catalog-quality-filter-url-state-4470949067363357068` — `19e47331bc70301a4f88e1dc1a256ab30dfeddaa`
- `feat/custom-boolean-language-rules-5232926195011109247` — `ea61efd72f074b9bb56ab701c10c81afea008d02`
- `feat/free-providers-major-level-17585199686131502064` — `cf8224c3e1731fbc5104b05326fb3583e15e6e7d`
- `feat/free-streaming-providers-5835872393983394663` — `b98059e19fecb11b30e2738c66dfef4dc4d8a9aa`
- `feat/language-info-management-501365045898889741` — `877daaa97b5233ee19a4a662d7212779c3bf6490`
- `feat/mapping-correction-impact-planner-1924939449884960228` — `12d467a4fd4ea963b905e83f924463253fa49f82`
- `feat/myanimelist-watchlist-import-9702231326422358649` — `cf5071a1de7660bed9ec1507ac42160055cb8689`
- `feat/phase4-watchlist-report-6527270736229954389` — `91b8a26a620152ca4cb22a2692157740469d4e8b`
- `feat/populate-catalog-series-2080809075555544640` — `dcb817ff8bd62c938e89add306981f21292a3075`
- `feat/prime-frontend-components` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `feat/prime-mvp-integration` — `c8367d0ad70675a6096816ead65db648f0d6b1f0`
- `feat/prime-preferences-service` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `feat/provider-system-17883285716514410038` — `8e132ddbb14581ebf526d03f9cad40509b8323f9`
- `feat/quota-aware-watchlist-refresh-11916490932068358894` — `db10e28045ca4118647322f3486f5eb354aa119f`
- `feat/raiplay-targeted-evidence-7928594183525293879` — `aca08914d0d11d188b04ddc1dee569b4f787c622`
- `feat/unified-catalog-provider-filter-8237583686476792170` — `ea31502432274ae58c3c4d0cd499cef3658714e7`
- `feat/universal-scraper-engine-903318669926468912` — `0703ee194e935ce96917226ca9668bcfc5f936c5`
- `feat/update-subscriptions-provider-display-15715424988401923445` — `0a3cfcb1aac8b9fa01db162fda3074448d3ec8a2`
- `feat/v2-viewer-language-intersection-15681527597786524200` — `38e2d7ebfc09e8b49240a5f31ecfea5f1a469262`
- `feat/watchable-catalog-and-filters-15248320015060973307` — `bf0f12f90ec6e9b4192feb90eda072d4e98bacae`
- `feat/watchlist-grid-watch-flow-7059692553676946454` — `d543328fcd957cbcd627f13fadfb0480cf0d0aad`
- `feat/watchlist-report-analytics-16586436093253514506` — `e6666fc8f9a72006fd4d5ff05e5e6895ca6f513b`
- `feature/architecture-fix` — `b10bc3a8d38f338807a8a131739d45cbc52828b0`
- `feature/audit-mapping` — `7affa8b49b0abdc5d6a7b3a4d3e2aeb6cd117b5f`
- `feature/improved-watchlist-import` — `2a07fddc8f3cc4913831314d29f955bd3ece6cd0`
- `feature/scrapres-improvedment` — `6fc79f4de89f8d6e0902028ce194900700eca622`
- `feature/sereis-integration` — `8620ee0a2834264ea25d7fb68531ba0aa6d43de4`
- `feature/series-integration-bis` — `3f1255559b779fb61b4d3a6087b55460654187f1`
- `feature/watched-section` — `72420d71d7739f8cc847499dc96c1029a32e8f52`
- `fix/audit-mapping-correctness` — `4f5016a7c3fd10e5302adb71ce222d3bc7cbb9d4`
- `fix/audit-provider-mapping-semantics` — `258585aa4e19497173c6484ba01c53e12b808426`
- `fix/stage2g-test-db-teardown` — `41c7efd730165c82f749a5b153c245e44739c11f`
- `integration/release54-hardening` — `aae0fc190b228d0843e35b99e6fe555445edd84d`
- `integration/wave2-validation` — `2fa128a048fcc780f7c257a11ca47876e889b914`
- `master` — `47d78dd6137f17fd8082feb38af6e918b230081a`
- `new-sources` — `043004b771dd9f76404855594998d3a35f12009a`
- `phase3-3i-raiplay-logging` — `1f9de30b2acd821913e150e7b8e32847c0bf23d4`
- `phase3-bbc-retirement` — `ade4bec709fd19ab1a8bd5f2b780323e8ae55626`
- `phase3-dead-code` — `a26f3819dc34a0ab4fce77b5c249d3c854899422`
- `phase3-nhk-feasibility` — `5dcd6cf0c4daff70551cd2e0748003b17ac4814e`
- `phase3-raiplay-audit` — `5dcd6cf0c4daff70551cd2e0748003b17ac4814e`
- `phase4-integration-rehearsal` — `a0c0895f7646742f93fa1a8a560e706792d3e38e`
- `phase4-step-fixtures` — `3c16c2c5f650a6fde344764b2a361c338e698f5b`
- `pr-44` — `ae2f931c766fa6efbb9b8263eab462635a53942f`
- `pr50` — `87f2ec2d3946f24e4d0ed539ace3391cd7802ceb`
- `pr52` — `d5c35d30a53353a64a695fb1b499f1b1109e595a`
- `pr53` — `afdfdad1a80f25560a4da27acecaaf53a0e315ed`
- `recovery/phase0c-watched-section` — `5c7795cbe34d26feaa61db16968af4434dc70b12`
- `recovery/watched-actor-duration-filters` — `ab0bfa214792d9217972e53685bc4ed8724d956d`
- `stage2b-docs-finalize` — `9941e0e48931c4022f3e42684ca9c259648f2a5f`
- `stage2b-live-availability-audit` — `af04d479edd66a0f240e7e5bd942ac2157915720`

### Remote-tracking branches

- `origin` — `47d78dd6137f17fd8082feb38af6e918b230081a`
- `origin/b1-regression-tests` — `b7ef1b761b336e031e468c0383fe4b0ad836d9a4`
- `origin/b2-1-freshness` — `9b635b611b2305b1e8940304e663a165a93384d5`
- `origin/b2-2-explicit-refresh` — `0333d613bef5d5088deb863a33d0c746c5795041`
- `origin/b2-3a-mapping-evidence` — `0dad646618a964734bb70c09044c0f81463c7940`
- `origin/b2-3b-negative-evidence` — `ed77288ff74bc16fa1be1687d905feb9cc57acc6`
- `origin/b2-4-offline-counterfactual` — `668c9d050517698d61b94f64eed51c0574df75a9`
- `origin/chore/npm-audit-hardening-12573672010307186858` — `a6929dd6cd5784efdf6af345aab28d8cd06b9053`
- `origin/chore/production-deployment-hardening-5895881941730741326` — `383463e8fc542b142fef101887a081231d0d0ffc`
- `origin/ci/github-actions-2650780226528452421` — `a5b3b7396fbf70fdc12e17f1449273ae70a00985`
- `origin/codex/release54-b2-final` — `eb6afe21e4ad7e018fbda15aca3224f0d294d46f`
- `origin/codex/stage2-availability-reliability` — `ced6e92b887520fe407621bdecad66ce073497b9`
- `origin/codex/stage2c-accuracy-audit-baseline` — `f8a7dca7c9b29cffb1902eb67edf3573bae484d4`
- `origin/codex/stage2e-freshness-recovery` — `01d69c02df6bedbbc3d15669477e2085d1c5071e`
- `origin/codex/stage2f-a6` — `1963f21cdad85fa9d19e1b1616ea7770e5fcad39`
- `origin/codex/stage2f-a7` — `da24f27092bb22063a68f082597bba0069b8ec8c`
- `origin/codex/stage2g-safe-runner` — `f4f33a2b8c0c975dd7e174de9685e6b273d40234`
- `origin/codex/stage2h-preflight` — `8381943b426c50ce3b8ccce2c10fe0f6743019c2`
- `origin/codex/stage2h-preflight-docs` — `96f0806d66ba38dd4629b0aed1723461eaf07252`
- `origin/codex/stage2m-mediaset` — `f50e3c6403d97aa8e5829757718a8f3962c9707f`
- `origin/codex/stage2n-youtube-identity` — `7457efe10aaee611542601d7fa1e25ca693813aa`
- `origin/codex/stage2s-reduced-justwatch` — `fa8a7eb1930561e6493c3a66dc706dab2158bbc0`
- `origin/docs/api-contracts-18393345990345875221` — `cf536e4e49971beb05aa84a9f09e9f729f67fd00`
- `origin/docs/archive-phase3-wave2-reconciliation` — `305c1a101a8a15f46d327a02803de4d7c9f88f96`
- `origin/docs/letterboxd-import-guide-17263394451853639388` — `19408c4f8e6b3c0296e2c4602f23991a8714aef9`
- `origin/docs/prime-api-contracts` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `origin/feat-phase3-cron-audit-15708720946622184744` — `196eb02fa9c98b1735c46288eaea5039800b6987`
- `origin/feat-watchlist-resolve-endpoint-7609453922498756206` — `d605c5bba52ad55f09fb4e7d4abbe1f332ea8d50`
- `origin/feat/add-free-streaming-providers-16420454610534920493` — `d83f2bff570e5d817811bfcacc44b303f1125e7d`
- `origin/feat/ambiguous-match-posters-12732467758765676546` — `24abe5fec37ad0ff83aed94c65c38b69bf471dfd`
- `origin/feat/budgeted-candidate-discovery-13583236362894172550` — `3a9693a5f44814c256ba8021ba9878514d026e22`
- `origin/feat/catalog-quality-filter-url-state-4470949067363357068` — `19e47331bc70301a4f88e1dc1a256ab30dfeddaa`
- `origin/feat/custom-boolean-language-rules-5232926195011109247` — `ea61efd72f074b9bb56ab701c10c81afea008d02`
- `origin/feat/free-providers-major-level-17585199686131502064` — `cf8224c3e1731fbc5104b05326fb3583e15e6e7d`
- `origin/feat/free-streaming-providers-5835872393983394663` — `b98059e19fecb11b30e2738c66dfef4dc4d8a9aa`
- `origin/feat/frontend-navigation-shell-6198519620786389843` — `f807ea93a7991eb3c98ba978f57ee0aca1c1d0d3`
- `origin/feat/homepage-search-2694128714989925710` — `e348c2b6b6261190053570896869507d6037042f`
- `origin/feat/language-info-management-501365045898889741` — `877daaa97b5233ee19a4a662d7212779c3bf6490`
- `origin/feat/manual-language-entry-ux-5684161884104217673` — `afc86b014f62459c21fd354a8796a06f82ef84f2`
- `origin/feat/mapping-correction-impact-planner-1924939449884960228` — `12d467a4fd4ea963b905e83f924463253fa49f82`
- `origin/feat/myanimelist-watchlist-import-9702231326422358649` — `cf5071a1de7660bed9ec1507ac42160055cb8689`
- `origin/feat/phase4-watchlist-report-6527270736229954389` — `91b8a26a620152ca4cb22a2692157740469d4e8b`
- `origin/feat/phase4-watchlist-ui-6980021061695045519` — `878f538eca10b01f426a72ea2378869323f96148`
- `origin/feat/phase5-preferences-ui-12516016953003929532` — `ff6a7e933072553ef313e38cb866e4a3b10c3a97`
- `origin/feat/phase5-shared-states-16596538083320479515` — `173995c453d8b1ba8c5a587e45b3a9a681f4ae26`
- `origin/feat/populate-catalog-series-2080809075555544640` — `dcb817ff8bd62c938e89add306981f21292a3075`
- `origin/feat/prime-frontend-components` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `origin/feat/prime-mvp-integration` — `c8367d0ad70675a6096816ead65db648f0d6b1f0`
- `origin/feat/prime-preferences-service` — `ee4a3c1bbd750b3819d8609520e55ed50b1b5c52`
- `origin/feat/provider-system-17883285716514410038` — `8e132ddbb14581ebf526d03f9cad40509b8323f9`
- `origin/feat/quota-aware-watchlist-refresh-11916490932068358894` — `db10e28045ca4118647322f3486f5eb354aa119f`
- `origin/feat/raiplay-targeted-evidence-7928594183525293879` — `aca08914d0d11d188b04ddc1dee569b4f787c622`
- `origin/feat/standardize-canonical-languages-12597047426914375054` — `6fd32e2c8ed81fe29bef5b282fc6004b0b0a6c47`
- `origin/feat/subscription-management-12614326780448238933` — `172b283b61792bff0a912df909e5e33f556fc29d`
- `origin/feat/targeted-offer-recovery-execution-16248261256924213681` — `afdfdad1a80f25560a4da27acecaaf53a0e315ed`
- `origin/feat/tmdb-candidate-review-ui-15263331623384702473` — `d5c35d30a53353a64a695fb1b499f1b1109e595a`
- `origin/feat/unified-catalog-provider-filter-8237583686476792170` — `ea31502432274ae58c3c4d0cd499cef3658714e7`
- `origin/feat/universal-scraper-engine-903318669926468912` — `0703ee194e935ce96917226ca9668bcfc5f936c5`
- `origin/feat/update-subscriptions-provider-display-15715424988401923445` — `0a3cfcb1aac8b9fa01db162fda3074448d3ec8a2`
- `origin/feat/v2-viewer-language-intersection-15681527597786524200` — `38e2d7ebfc09e8b49240a5f31ecfea5f1a469262`
- `origin/feat/verified-tmdb-mapping-acceptance-8140360374282775162` — `87f2ec2d3946f24e4d0ed539ace3391cd7802ceb`
- `origin/feat/watchable-catalog-and-filters-15248320015060973307` — `bf0f12f90ec6e9b4192feb90eda072d4e98bacae`
- `origin/feat/watchlist-grid-watch-flow-7059692553676946454` — `d543328fcd957cbcd627f13fadfb0480cf0d0aad`
- `origin/feat/watchlist-report-analytics-16586436093253514506` — `e6666fc8f9a72006fd4d5ff05e5e6895ca6f513b`
- `origin/feat/wt-006-i18n-skeleton-18205009091830428319` — `b5fa2c367c06160488bce06dd74883ee95bd94e7`
- `origin/feat/wt-040-public-domain-archive-15514489871917568465` — `e081164c12cb05123abcfef1b1390656e57071ef`
- `origin/feat/wt-049-extension-foundation-3441011916249609102` — `2a19e73a86a6fe794cfd3def3396fff70951de0a`
- `origin/feat/wt013-user-configurable-source-ranking-4915492890240648143` — `ee77167e7be5fe78df22bbad7a6a3213e5656aa5`
- `origin/feature/architecture-fix` — `b10bc3a8d38f338807a8a131739d45cbc52828b0`
- `origin/feature/improved-watchlist-import` — `2a07fddc8f3cc4913831314d29f955bd3ece6cd0`
- `origin/feature/scrapres-improvedment` — `6fc79f4de89f8d6e0902028ce194900700eca622`
- `origin/feature/scrapres-improvedment-16709454313608472348` — `a1eb1305bceb954b4cf0c75316a19a90ab1d46a6`
- `origin/feature/sereis-integration` — `8620ee0a2834264ea25d7fb68531ba0aa6d43de4`
- `origin/feature/series-integration-bis` — `3f1255559b779fb61b4d3a6087b55460654187f1`
- `origin/fix/release54-entitlement-exact-7701925700072331769` — `cb265161b05202b3c0ebffa5c3506dc45c8d6b7c`
- `origin/fix/release54-metered-gate-873010178121159132` — `9cb7be4e1aa443e68c56b7a89bcfa279ee0182d7`
- `origin/fix/release54-stale-offer-semantics` — `66cf1c0a05be55f81586767ade27651786e1c7c8`
- `origin/fix/stage2g-test-db-teardown` — `41c7efd730165c82f749a5b153c245e44739c11f`
- `origin/integration/release54-hardening` — `d2ade4476f3a94d84189cd94ea7c90781fb18aae`
- `origin/jules-15303779976092915187-eecb2b5f` — `480a7ea0feabc22e7f60064dd10402e4e3a41243`
- `origin/master` — `47d78dd6137f17fd8082feb38af6e918b230081a`
- `origin/phase4-step-fixtures` — `3c16c2c5f650a6fde344764b2a361c338e698f5b`
- `origin/planning/wt-parallel-task-refactor-20261109` — `f2bf4a2d67b1801d0b95419efc2bf9e6ce9efe35`
- `origin/pr-92-head` — `e6ee3280827df2f55da06296e761de39e8065f25`
- `origin/recovery/audit-evidence` — `90ec295c82570dfd45c05dcc08f3d697e05a363c`
- `origin/recovery/audit-mapping-validation` — `871b793ad111e74ade0b7a2a78b612e2e94d191e`
- `origin/recovery/catalog-filters` — `71829f8c92af8331c94cfdeedc6fc45d83cd1d18`
- `origin/recovery/phase0c-watched-section` — `5c7795cbe34d26feaa61db16968af4434dc70b12`
- `origin/recovery/watched-actor-duration-filters` — `ab0bfa214792d9217972e53685bc4ed8724d956d`
- `origin/recovery/watched-filter` — `aaec5319a3dd61a2633ccfeab1f675fad92de20c`
- `origin/stage2b-docs-finalize` — `9941e0e48931c4022f3e42684ca9c259648f2a5f`
- `origin/stage2b-live-availability-audit` — `af04d479edd66a0f240e7e5bd942ac2157915720`
- `origin/test/frontend-error-states-7012620144486360031` — `b12cbc095ae0e8f677dcc053004eb6f7c68560b5`
- `origin/test/letterboxd-edge-cases-14359891364838173769` — `290b298b731b11caea6564e9cb74bf0394cb542e`
- `origin/test/release54-justwatch-regression-9532900614687410405` — `8c40c38903974aca88a2cfe1a0896aba820f2c45`
