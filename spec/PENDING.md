# Spec pending — JustWatch Country Offers

- Function: `fe1b97b6-4d60-4371-ab9c-5a52f4f2d973` (`POST https://anything.notte.cc/mcp`, MCP streamable HTTP).
- Brief: `JW-CANARY-v1`. End marker `END BRIEF:JW-CANARY-v1` is present. Lines seen: **47 of 52 declared** (5 lines absent from the delivered brief).
- Date: 2026-11-24 (CET).

## Schema resolution attempted

| Route | Result |
|---|---|
| R1 `tools/list` | Complete. Tools are `search`, `spec`, `build`, `run`, `publish`. The only tool taking `function_id` is `spec`, which is marketplace-scoped and explicitly excluded by the brief. No `get_function`, `functions/get` or `describe` tool exists. Authenticated `tools/list` succeeded; tool-list schema SHA-256 `0e688edd1bd330f98bb4d89146c6497f5d0abb62a95654c5dcf09c64b9115705` matches the prior run (no version event). |
| R2 repo | Checked by walking the repository (excluding `.git`, `node_modules`, `.venv`, `.next`, `__pycache__`): zero matches for `fe1b97b6*`, no `spec/` directory, no `spec/fe1b97b6.yaml`, no `PENDING.md`. |
| R3 | **Reached.** Stop and ask the owner to export the schema from the Anything dashboard. |
| R4 | Not used. Requires both R1/R2 failure **and** owner authorization for a validation probe; that authorization has not been given. |

## Gap to close

Owner must export, from the Anything dashboard for this personal function:

1. exact variable names, types, defaults and required/optional flags;
2. full response shape, including whether records are inline or an async handle (`run_id`/`task_id` plus status);
3. poll method and terminal states if the response is async;
4. endpoint/method and one sanitized request/response example.

Place the export at `spec/fe1b97b6.yaml`. On first resolution, record `derived_from: R3-owner-export` and hash the file into the manifest; any later difference is a version event.

## Budgets and live state

- Validation probes used: **0 / 3**.
- Collection calls used: **0 / 6** (including calibration and Dune).
- Live JustWatch requests: **0**. No `run` call was made, no variable names guessed, no market batch sent, no offer synthesized.

## Residual approval references (noted, not treated as a gate)

- `docs/operations/issue97_justwatch_collection_preflight.md:22` — "A verified disposable target for a live run and separate execution approval are pending."
- The same file's line 3 policy update says issue #97 no longer requires a separate written upstream permission artifact, but explicitly says this is **not** approval to send requests and that runtime permission checks remain unchanged. Per the brief, these references are noted and do not gate this executor.

## Next action

Owner exports the schema to `spec/fe1b97b6.yaml`; then resume at step 2 of the brief, hash the spec, and only then begin the single-title `NL` calibration with checkpoints under `runs/jw-canary-2026-11-24/`.
