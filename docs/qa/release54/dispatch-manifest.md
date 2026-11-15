# PR #54 Batch A Dispatch Manifest (generated 2026-11-05, read-only)

## Verified SHAs
- PR #54 head: 45bfe85bb85cd375bdd290afe43c724c75e2f0e1 (feature/scrapres-improvedment → master), OPEN, MERGEABLE
- CI: Backend Tests SUCCESS, Frontend Lint/Build/Test SUCCESS (run 42025092520)
- master HEAD: 043004b771dd9f76404855594998d3a35f12009a
- Local branch: feature/scrapres-improvedment (clean tracked; untracked docs/.worktrees preserved)

## Tasks (Batch A) — all PRs target feature/scrapres-improvedment, NOT master

| ID | Owner | Agent/Account | Branch (suggested) | Base SHA | Owned files | Dependencies | Tests |
|---|---|---|---|---|---|---|---|
| J1 | Jules | acct TBD (avoid acct2 used for PR53) | fix/release54-entitlement-exact | 45bfe85 | backend/tracker/services/access.py + tests | none | viaplay vs viaplay_sports, Prime base vs channels/addon/rental, alias, unknown, free, no-subs |
| J2 | Jules | acct TBD | fix/release54-metered-gate | 45bfe85 | backend/tracker/services/source_policy.py + scheduler/worker config + tests/docs | none (activation gated by H1) | truth table skip/consult, Watchmode finite quota, env parity, counterfactual |
| J3 | Jules | acct TBD | test/release54-justwatch-regression | 45bfe85 | JustWatch tests + narrowly necessary importer changes only | none (avoid access.py/source_policy/reconciliation) | unmapped skip, no auto-subscribe, ambiguity fail-closed, partial pagination |
| D1 | Devin | Free tier | fix/release54-stale-offer-semantics | 45bfe85 | reconciliation/read models/API + frontend status labels | must NOT touch access.py/source_policy.py | stale-only last-known, fresh+stale, unknown, expiry, conflict, language |

## Conflict control
- J1 sole owner of access.py; J2 sole owner of source_policy.py; D1 sole owner of reconciliation/status presentation; J3 tests-first, pauses if core runtime files needed.

## Blockers
- Jules: SSL fixed via SSL_CERT_FILE=/etc/ssl/cert.pem in .env (all 6 slots healthy, no active sessions).
- Devin: Free tier; verify repo binding + limits before dispatch.
- Jules read-only deadline Nov 11 2026 — dispatch promptly.
- No live remote session started; awaiting explicit approval.

## Ready vs blocked
- Ready (read-only/local): H0 preflight done; H1/H2 after integration.
- Ready to dispatch pending approval: J1, J2, J3, D1.
- Deferred: J4, J5, D3 (post-merge).
- PR #43 and PR #51 kept separate (not included).

## Dispatch Results (Batch A)

### Jules
- J1 account 1: session 7701925700072331769 — https://jules.google.com/session/7701925700072331769 — base feature/scrapres-improvedment — auto-PR enabled
- J2 account 2: session 873010178121159132 — https://jules.google.com/session/873010178121159132 — base feature/scrapres-improvedment — auto-PR enabled
- J3 account 3: session 9532900614687410405 — https://jules.google.com/session/9532900614687410405 — base feature/scrapres-improvedment — auto-PR enabled
- Starting commit for all: 45bfe85bb85cd375bdd290afe43c724c75e2f0e1
- Target PR base (verified at submit): feature/scrapres-improvedment (Jules default for configured repo source)

### Devin D1
- STATUS: NOT LAUNCHED — blocker: Free-tier cloud session availability/repo binding and credit limits not verified; avoid unexpected charges. D1 prompt saved at /Users/angeloandreaisola/.hermes/cache/scratch/jules-prompts/D1.txt. Exact command (run only after approval/verification): devin --cloud --prompt-file .../D1.txt (from repo dir). No substitute paid worker used.

### H1 (accuracy audit)
- STATUS: COMPLETE — report at docs/qa/release54/h1-accuracy-audit.md
- Inspected SHA: 45bfe85bb85cd375bdd290afe43c724c75e2f0e1
- P0 blockers: entitlement fuzzy matching (access.py _match); missing-language-metadata treated as definite mismatch (score.py).
- Findings for J1/J2/J3/D1 recorded in report sections 4-7.
