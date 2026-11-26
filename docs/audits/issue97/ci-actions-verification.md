# GitHub Actions CI verification for issue #97

- **Date** 2026-11-25
- **PR** [#213](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/pull/213) (`ci/97-actions-recheck` into `master`)
- **Observed verdict** CI passed in the two hosted runs below.
- **Repair route** Codex / OpenAI `gpt-6.2-sol`, high reasoning, `COST_UNKNOWN`.

## Hosted CI evidence

All three jobs in the first attempt of the master run failed before executing any steps. GitHub annotated the failure as an account payment or spending-limit block. Runners were allocated and all steps completed successfully on the master rerun and the original PR head. This establishes that the block did not prevent those runs from executing. [Master attempt 1](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/attempts/1)

| Run | Event | Head SHA | Result |
| --- | --- | --- | --- |
| [45638972373, attempt 2](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/attempts/2) | Push rerun on `master` | `0b45d6e7d98bb0bef57a7a75fb7830b59ed0daea` | Success |
| [45647912441, attempt 1](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45647912441) | `pull_request` on the original #213 head | `b2e954562cbd797e052e706a974a982fdb6a07be` | Success |

The repository was public when checked and the CI workflow was active. Step counts below include setup and cleanup steps recorded by GitHub Actions.

| Job | Successful steps per run | Master job and duration | Original #213 job and duration | Result per run |
| --- | ---: | --- | --- | --- |
| Browser Extension Build, Typecheck & Tests | 11 / 11 | [139317987827](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/job/139317987827), 15s | [139323824897](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45647912441/job/139323824897), 12s | Typecheck, build and Manifest V3 package validation passed. Vitest passed 34 tests in 4 files. |
| Backend Tests | 12 / 12 | [139317988498](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/job/139317988498), 3m54s | [139323825501](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45647912441/job/139323825501), 3m29s | Django detected no model changes, applied 101 migrations in the explicit migrate step on the PostgreSQL 16 service, and passed 3,863 tests. |
| Frontend Lint, Build & Test | 10 / 10 | [139317988810](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45638972373/job/139317988810), 1m23s | [139323825411](https://github.com/AngeloAndreaIsola/WhereTfDoIWatchIt/actions/runs/45647912441/job/139323825411), 2m14s | Lint and build succeeded with warnings. Vitest passed 403 tests in 47 files. |

Both frontend logs show the same warnings in `npm run lint` and in the build's lint phase.

- `frontend/components/LeavingSoonSection.tsx:172:6` has an unnecessary `normalizedCountry` dependency on a `useMemo` hook (`react-hooks/exhaustive-deps`).
- `frontend/components/UnofficialCatalogItem.tsx:99:11` uses a raw `<img>` element (`@next/next/no-img-element`).

The frontend test step also logged React `act(...)` warnings. Both runs logged a GitHub runner warning that the Node.js 20-targeted checkout and setup actions were being forced onto Node.js 24.

### Commands from the workflow

| Working directory | Commands in execution order |
| --- | --- |
| `extension` | `npm ci`, `npm run typecheck`, `npm test`, `npm run build`, `npm run validate:package` |
| `backend` | `pip install -r requirements.txt`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate`, `python manage.py test tracker availability -v 2 --noinput` |
| `frontend` | `npm ci`, `npm run lint`, `NEXT_TELEMETRY_DISABLED=1 npm run build`, `npm test` |

Both verbose backend logs record 2 passing tests in `availability.tests.test_issue97_stage_d_preflight`, 25 in `availability.tests.test_issue97_stage_d_runner`, and 11 in `availability.tests.test_issue97_stage_e_rehearsal`. The six-test standalone Stage D manifest check below is a separate suite.

## Local verification

The focused Django checks used a disposable PostgreSQL 16.15 container on an internal Docker network with mocked provider transport. From `backend`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --noinput`, and `python manage.py migrate --check` succeeded. The command below passed all 38 focused tests (2 Stage D preflight, 25 Stage D runner, 11 Stage E rehearsal).

```sh
python manage.py test --noinput -v 2 \
  availability.tests.test_issue97_stage_d_preflight \
  availability.tests.test_issue97_stage_d_runner \
  availability.tests.test_issue97_stage_e_rehearsal
```

On that newly migrated database, the default `issue97_stage_d` management command ran inside a PostgreSQL read-only transaction with the disposable target attested. The report returned `readiness=blocked` because 12 typed title identities and one current runtime receipt restricted to NL search were missing. It emitted no authorization scope, all accounting counters stayed at zero, no scopes were checkpointed, and the patched HTTP send was never called. [Stage D runtime authorization verification](stage-d-runtime-authorization-verification.md)

The standalone commands ran from the repository root.

| Command | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 scripts/test_issue97_stage_d.py` | 6 discovered, 5 passed, 1 skipped because retained raw TMDb responses were excluded. |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_audit_issue97_stage_e.py` | 4 passed. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/audit_issue97_stage_e.py` | CSV, provenance and scope checks passed. The audit found 15 candidates (A=9, B=6), 13 accepted observations, 7 Stage C pairs, 2 controls and 7 ineligible or unadjudicated candidates. All 15 candidates mapped to 8 historical native documents (A=3, B=5). There were 0 fresh adjudicated pairs, so accuracy metrics remain undefined. |

The repair accessed GitHub for CI evidence and publication. It made zero live JustWatch or first-party provider requests. Stage D Batch 1 and Stage E Batch A remain on hold. ChatGPT Web owns final review and merge decisions.
