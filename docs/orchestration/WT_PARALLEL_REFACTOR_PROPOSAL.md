# WT parallel task-scope proposal — archived reference

**Status:** `ARCHIVED_REFERENCE_ONLY` as of 2026-11-15. **Not executable.** Jules is no longer available for this project; the historical task queue is **not** being reconciled to a live dispatcher, authorized, resumed, or executed. This is a snapshot of an old planning exercise, not the current product backlog.

- Original proposal: 2026-11-09; source task manifest generated 2026-11-07 (`WhereTfDoIWatchIt_Jules_Tasks_v2.json`).
- Reconciliation reference: remote `master` at `7cec749f40ec1bfad51ac6d3c54a5b80485fc5ec` (2026-11-15), repository tracked-file tree, selected implementation files, and merged PR evidence.
- Historical task scopes: [`WT_PARALLEL_SCOPE_PATCH.json`](./WT_PARALLEL_SCOPE_PATCH.json), preserved as **non-executable archival data**. `dispatch_authorized=false`; no scope overrides are applied.
- Reusable, agent-independent guidance: [`PARALLEL_DEVELOPMENT_GUIDE.md`](./PARALLEL_DEVELOPMENT_GUIDE.md).
- This review **did not** inspect the complete original task markdown or a live Hermes/Jules DB and did not execute acceptance tests; a matching path or PR does not prove all original acceptance criteria were met.

## Reconciliation of all 24 historical scope overrides

Classification refers only to the *proposal's original work scope*, not to a live task state:

- **Implemented:** strong evidence that the referenced implementation was merged, though no new acceptance tests were run here.
- **Partially implemented:** related functionality is in master, but the proposed artifact or acceptance equivalence is unverified.
- **Still relevant (candidate):** concept appears suitable for future reconsideration, **not** an approved task or automatic backlog item.
- **Superseded:** independently demonstrated replacement; do not infer this merely because files are missing. None established by this limited review.
- **Unknown:** not enough evidence to call the task completed, needed, missing, or superseded. Absence of a proposed file is *not* proof that functionality is absent.

| Historical task | Classification | Evidence and disposition |
| --- | --- | --- |
| WT-005 | Unknown | Owner-model design decision document absent; original acceptance criteria not available. |
| WT-006 | Implemented | Merged [PR #71](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/71); EN/IT/JA locale modules and messages are in master. Historical scope only. |
| WT-010 | Partially implemented | Catalog, watchlist and watched pages have URL-state handling; proposed `frontend/lib/url_state.ts` is absent. Shared-helper requirement unverified. |
| WT-012 | Unknown | Proposed `AlphabetStrip.tsx` absent; no task-specific PR found. Feature need not established. |
| WT-022 | Unknown | Ratings-rights research document absent; no task-specific PR found. |
| WT-028 | Unknown | Google-lists feasibility document absent; no task-specific PR found. |
| WT-031 | Unknown | Prime-import research document absent; no task-specific PR found. |
| WT-036 | Still relevant (candidate) | Proposed `tag_search.py` and dedicated test absent. Potential future tag-search work; requires a new spec and migration review, not an old grant. |
| WT-039 | Unknown | Semantic-retrieval research document absent; no task-specific PR found. |
| WT-040 | Implemented | Merged [PR #72](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/72); public-domain source, catalogue service and tests are in master. Historical scope only. |
| WT-042 | Unknown | Specialist-sources research document absent; no task-specific PR found. |
| WT-043 | Unknown | Worker-architecture research document absent; broader existing worker implementation not assessed. |
| WT-054 | Unknown | Reel-recognition research document absent; no task-specific PR found. |
| WT-056 | Unknown | Plex/NAS-boundary research document absent; no task-specific PR found. |
| WT-058 | Unknown | App-direction design document absent; prior design decisions not verified. |
| WT-059 | Unknown | Two proposed WT-059-specific regression files absent; equivalent generic test coverage not verified. |
| WT-060 | Partially implemented | `availability_metrics.py`, `/availability-metrics` page and `test_availability_metrics.py` exist in master. Original WT-060 task acceptance/coverage not verified. |
| WT-067 | Partially implemented | `unified_catalogue.py`, `catalogue_query.py` and unified view exist; proposed `test_catalogue_coverage_wt067.py` absent. Scope/acceptance not verified. |
| WT-069 | Unknown | Unofficial-URL-policy research document absent; no task-specific PR found. |
| WT-070 | Unknown | Two proposed aggregator-specific WT-070 test files absent; equivalent coverage not verified. |
| WT-071 | Unknown | Proposed WT-071 broadcaster-normalization test file absent; equivalent coverage not verified. |
| WT-072 | Unknown | Subtitle-rights research document absent; no task-specific PR found. |
| WT-074 | Still relevant (candidate) | Proposed `streaming_changes.py`, tests and fixtures absent. Reevaluate as an independent offline parser only if still useful. |
| WT-082 | Unknown | Proposed seed command, WT-082 fixtures, regression test and documentation absent; future need not verified. |

**Counts:** 2 implemented; 3 partially implemented; 2 still-relevant candidates; 0 demonstrated superseded; 17 unknown. **No task is scheduled or authorized.**

This is a *documentation-only*, file/PR-level reconciliation. GitHub searches for the exact WT IDs found task-specific PRs for WT-006 and WT-040; other related changes may exist under different names. The original task manifest/acceptance criteria must be reviewed before using any classification as a completion decision.

## Reuse boundary

The transferable lessons are dependency-aware ordering, exact write-scope ownership, file/directory conflict detection, isolated workspaces, exclusive migrations, separately reviewed integration steps, and tests on the final integrated branch. See the guide linked above.

The historical account limits, live queue reservations, session fences, approvals, authorizations, and dispatch sequence **below** are retained only as context explaining the old design. They are **not instructions** for Codex, OpenCode, Hermes, or any future agent.

---

## Original 2026-11-09 proposal (historical record; do not execute)

**Status:** Draft, offline task refactor. No queue writes, dispatch, authorization, provider API calls, or merges.

## Audit

The saved portable queue (2026-11-07) has 85 WT tasks: 70 designated worker tasks, 15 manual/review. Dependency references are complete, and the DAG is acyclic. WT-001 is the sole root. When its trusted release baseline is actually evidenced, 36 further tasks are dependency-ready (23 workers and 13 manual/review); dependency-ready is NOT dispatch-ready.

The original parallel wave proposal is not independently executable: WT-059/WT-070 overlap in broad test directories, WT-060/WT-082 can also overlap in tests, and WT-074/WT-075 both write `backend/tracker/services/streaming_api.py`. Named locks alone do not catch these path collisions.

## Refactor contracts

Keep every original dependency, gate, task ID, approval and historical state. Propose **exact new-write scopes** for these tasks, after reconciling each with its current prompt/acceptance criteria:

- Design/research only: WT-005, WT-022, WT-028, WT-031, WT-039, WT-042, WT-043, WT-054, WT-056, WT-058, WT-069, WT-072. Write only `docs/design/WT-<id>*.md` or `docs/research/WT-<id>*.md` as appropriate; inspect application code read-only. Separate any implementation into follow-up work.
- Dedicated regression/test paths: WT-059, WT-060, WT-070, WT-071. Use individually named `test_*_wtNNN.py` files instead of entire test directories. If production code fixes are required, submit a distinct integration task, not an unauthorized source edit.
- Dedicated offline fixture seed and documentation: WT-082. Own its `seed_dev_data.py`, `test_seed_dev_data.py`, `tests/fixtures/wt082/`, and `docs/WT-082-OFFLINE-FIXTURES.md` only.
- Isolated offline changes parser: WT-074. New `streaming_changes.py`, `test_streaming_changes.py`, `tests/fixtures/wt074/` only; make `streaming_api.py` read-only until a separate reviewed integration task. WT-075 keeps responsibility for its original streaming API changes.
- Narrow reusable UI/services: WT-006, WT-010, WT-012, WT-036, WT-040, WT-067. Replace unnecessarily broad `frontend/lib/`, `frontend/components/`, test, or collection-service directory ownership with named new modules/helpers/tests. If this excludes required central file modifications, track them as integration debt.

Maintain global `migration` exclusivity and shared model/view/auth/release locks. Never make a partial isolated implementation count as a completed end-to-end task without its integration follow-up.

## Queue application contract

1. Fetch **live** Hermes durable queue/session/grant/reservation/lock state and Git/PR status, and reconcile its current task prompts with the saved v2 portable manifest. Current live account capacity cannot be inferred from saved six-account limits or a reported eight slots.
2. Reconcile WT-014 old integration, WT-060 feedback session, WT-082 reviewer/activation. Never duplicate any active/UNKNOWN attempt; preserve the J06 fence.
3. Validate the approved release base, provider capability and quotas, all task gates, and per-account preview snapshots. Do not reuse stale or expired grant hashes.
4. Rewrite scope metadata AND actual worker prompt for pending unassigned tasks only; use atomic/optimistic versioned DB updates and preserve history. Any altered snapshot needs fresh preview and human approval.
5. Schedule by ready dependency DAG plus exact path/directory/named lock conflict detection, not static wave number. Isolate worktrees/branches/PRs per task; serialize shared-file integration and migrations.
6. Validate tests for lock/path collision rejection, DAG, account authorization, duplicate suppression, quota gate, interrupted recovery, migration exclusivity, scope-of-diff, and no auto-merge or paid API tests.

**This document is not a dispatch instruction.** LIVE_DISPATCH_FROZEN and all human approval gates remain in force.
