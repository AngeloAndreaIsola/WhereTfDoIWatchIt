# Parallel development guide (agent-independent)

**Status:** Reference only · 2026-11-15. This is engineering guidance, **not** a task queue, scheduler, authorization, or instruction to start background agents. It applies to human and AI-assisted development with Codex, OpenCode, or other tools.

For provenance, the [historical WT parallel proposal](./WT_PARALLEL_REFACTOR_PROPOSAL.md) and [historical scope patch](./WT_PARALLEL_SCOPE_PATCH.json) describe the 2026-11-09 planning exercise. The 24 old scope overrides are **not reusable task assignments**. Reevaluate the current codebase and write new task definitions before implementing anything.

## 1. Start from observable current state

Before a work package begins, identify:

- The exact current baseline commit, open PRs, conflicting branches, and active workspaces.
- Which behavior is already implemented, which is genuinely missing, and what acceptance tests exist.
- What files must be read, which may be written, and which are owned by another active change.
- External effects: migrations, databases, background jobs, credentials, provider calls, quotas, deployment, and state changes.

Do not infer missing behavior from a branch name or a diff showing deletions. Do not equate "tests added" with "tests passed." Treat missing or outdated documentation as **unknown**, not as permission to rebuild a feature.

## 2. Model dependencies and ownership

Each new task description should contain:

| Field | Required detail |
| --- | --- |
| Goal | One independently testable behavior or research outcome |
| Baseline | Exact branch and commit, plus relevant upstream PR dependencies |
| Inputs | Specific source files / APIs that may be read |
| Exclusive writes | Named files and narrow directories (avoid broad ownership of `backend/` or `frontend/`) |
| Shared boundaries | Views, models, migrations, UI filter state, auth and provider access |
| Dependencies | Other tasks whose outputs are required |
| Acceptance | Executable tests, typechecks, build results, or evidence for documentation-only work |
| Prohibited effects | Other agents' worktrees, data writes, metered calls, deployments, or anything not explicitly allowed |
| Integration | Who owns shared-file consolidation and which PR is merged first |

A task is ready for implementation only if prerequisites are satisfied **and** it has a nonconflicting write scope. A dependency-complete task is not automatically safe to run concurrently.

## 3. Schedule by file conflicts, not by static waves

Construct a directed dependency graph, then check candidate tasks for overlapping write sets:

- Exact file vs file, file vs parent directory, and overlapping directory globs.
- Shared generated artifacts and fixtures.
- Cross-cutting changes to migrations, schemas, models, common views, authentication, provider adapters and release configuration.
- Two tasks that both need to alter the same API contract even when their initial file lists differ.

Run truly independent packages in parallel. Serialize competing changes, or split into a **core module PR** followed by an **integration PR** owned by one agent. Do not call a partial standalone module a completed end-to-end feature.

**Historical example (not current task assignments):** WT-074 and WT-075 both proposed edits to `backend/tracker/services/streaming_api.py`; WT-059/WT-070 and WT-060/WT-082 had broad test-directory overlaps. An exclusive file/path plan would have avoided simultaneous edits.

## 4. Isolate and preserve work

- Use a distinct branch and independent clone or safely created worktree for each implementation package; never modify an agent's active workspace.
- Capture baseline HEAD and status; inspect existing modifications and stashes **read only**.
- Never use destructive `reset`, `clean`, force-removal, or stash application against someone else's checkout.
- Keep separate commits and PRs for separate features. When PR B depends on PR A, target B at A's head, merge A first, then retarget and revalidate B.
- Preserve unique uncommitted files and historical evidence before considering any cleanup.
- For uncertain branch histories, compare unique commits against the common ancestor and check *behavioral* equivalence, not only filenames.

## 5. Treat migrations and external effects as exclusive

Schema migrations, shared production models, authorization mechanisms, and release configuration need one integration owner at a time. A task that creates a migration must be checked against every other unmerged migration branch.

Never run paid/metered provider requests, scraping experiments, production refreshes, deployment, destructive scripts, or real-data writes just to validate a feature. Obtain explicit task-specific authorization and use local fixtures for routine validation.

A proposal documenting such actions is not evidence that they were approved or performed.

## 6. Verify before proposing a merge

At minimum:

1. Review the exact diff against its intended base for unrelated changes and missing file updates.
2. Run focused regression tests exercising positive, negative, unknown-data, permissions, and interaction cases.
3. Run the relevant full CI suite, lint/typechecks/builds as appropriate; distinguish project environment failures from code defects.
4. Test behavior across seams (backend ↔ frontend, provider ↔ territory, source ↔ identity, filter ↔ pagination).
5. Re-check dependencies and CI after changing the PR base or merging its prerequisite.
6. Report commands actually executed, results, baseline/head SHAs, caveats, and unresolved decisions.

Green CI is necessary for most implementation PRs, but it cannot prove tests were configured to cover new subprojects. Add targeted CI when introducing a new subproject, such as a browser extension.

## 7. Integration and cleanup

Prefer small, reviewable PRs with an explicit merge order. Resolve conflicting behavior by choosing one owner for the shared surface. After merging, verify the new master HEAD, run or inspect post-merge CI, and only then consider cleaning up branches/worktrees.

Worktree cleanup and branch deletion are separate activities. Clean and merged does **not** mean inactive; verify process ownership, untracked and ignored files, unique commits, and stashes before removal.

## 8. Reviving the archived WT plan in the future

If an old WT item becomes useful again, start a **new** task from the latest code and product requirements:

1. Read the [24-task reconciliation](./WT_PARALLEL_REFACTOR_PROPOSAL.md) as historical evidence, not a current backlog.
2. Locate its original detailed task and acceptance criteria, or write new ones if unavailable.
3. Re-check implementation against master and closed/merged PRs.
4. Define fresh dependencies, narrow file ownership, tests, and a merge plan.
5. Execute only after a separate user decision to proceed.

Do not import the JSON scope patch into a scheduler, restore old approvals, or treat any historical task status as a live grant. Nothing in this guide initiates recurring work or automation.
