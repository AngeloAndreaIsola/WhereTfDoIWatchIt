# WT task queue: conflict-safe parallelization proposal (2026-11-09)

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
