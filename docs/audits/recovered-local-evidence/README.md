# Recovered local audit evidence

This directory preserves audit records that were present only in local
worktrees or as untracked files. The copied source documents are unchanged;
this index records their origin and limits.

## Provenance

| Material | Source location | Source revision | Notes |
|---|---|---|---|
| Stage 2B audit corrections | `cosmic-sailor/docs/audits/stage2b/` | `af04d479edd66a0f240e7e5bd942ac2157915720` (`stage2b-live-availability-audit`) | Includes research notes, reliability findings, discrepancies, evidence, and the live sample. Measurements are historical. |
| Stage 2D accuracy evidence | `mighty-lagoon/docs/audits/stage2d/` | `00d9dc843c558dc2000f0bb4fba055671c5d012f` (detached worktree) | Includes database coverage, comparisons, application snapshots, and accuracy findings. Measurements are historical. |
| Stage 2Q identity remediation | Main repository `docs/audits/stage2q/` | `bd3da37934f388e389194526f0f5a99a956d9de3` (`master`; source files were untracked) | Preserved as found; this recovery did not execute remediation. |
| Stage 2R readiness reassessment | Main repository `docs/audits/stage2r/` | `bd3da37934f388e389194526f0f5a99a956d9de3` (`master`; source files were untracked) | Preserved as found. The JustWatch experiment was not run. |
| New-provider research | `new-sources/docs/sources/` | `043004b771dd9f76404855594998d3a35f12009a` (`new-sources`; research files were untracked) | Historical findings only; see the current review in the PR description before enabling any source. |

The snapshots and measurements above describe the environments and dates of
their original audits. They are not claims about current provider availability,
current production accuracy, or current experiment readiness. Re-running a
metered JustWatch experiment was explicitly out of scope and was not done.

No original worktree, branch, or stash was changed to prepare this copy.
