# Stage 2 minimal fix plan

All findings below are on the verified `4332732977ac414b051c0e2308e142af9c5803af` baseline. Audit documentation is complete before code edits. Changes remain in the isolated Stage 2 worktree; no merge/deploy is authorized.

| ID | Severity | Minimal change | Regression test | Dependencies / risk |
|---|---|---|---|---|
| A1 | P0 | Distinguish `unknown` territory from explicit global/source-independent evidence. Only legacy source contract or a declared source capability may map a blank market to anywhere. Unknown-country offer cannot enter local/VPN accessible lists. | Entitled aggregator offer with empty country is not `available_here`; explicit globally scoped legacy scraper remains behavior-compatible; known NL and configured VPN countries retain expected verdict. | Audit every producer of blank country and update global-source contract first. May alter legacy display semantics; no schema migration expected. |
| A2 | P1 | Make `reserve_watchmode_call` honor `budget_is_reserved("watchmode")`, as Streaming Availability does. Keep reservation inside HTTP retry loop so additional attempts debit separately. | Coordinator first attempt debits once; direct client call debits once; each retry debits one; local refusal sends zero HTTP. | Narrow change in rate limiter plus focused client/coordinator tests. |
| A3 | P1 | Make empty-result negative evidence depend on matching scope and meaningful completed outcome, not any historical DONE on the title. At minimum aggregate per country/source, require current successful no-offer evidence, and let later failure avoid asserting absence. | Old US/source-A DONE + fresh NL/source-B failure → NL unknown; NL successful complete authoritative no-offer → unavailable; non-authoritative omission remains unknown/does not clear positive. | Need choose canonical coverage contract for sources. Keep same resolver for surfaces. Avoid new migrations unless request scope evidence cannot be derived. |
| A4 | P2 | Feed detail endpoint the same queue-derived refresh state as watchlist, preferably from a shared service for title+country/source scope. | API test compares watchlist/detail for pending, checked-negative, failed, never-checked. | Can be addressed alongside A3; response contract only. |
| A5 | P2 | Seed canonical `internet_archive` provider (disabled/free category as appropriate) and ensure source row/checkpoint setup is available. Add migration/data-seed preserving existing provider rows. | Fixture-backed fresh migrated DB sync persists provider content/cursor without pre-test `Provider.get_or_create`; repeat scan resumes instead of page one. | Confirm provider category/access type policy; migration should use `get_or_create`, reverse safely. |
| A6 | P2 | Resolve freshness configuration by source row and pass it into title and catalogue scheduler checks, matching read path. | Config 1h with 2h-old observation schedules; config 48h with 2h-old observation skips; default policy unchanged. | Avoid N+1 source reads; snapshot configuration once per batch. |
| A7 | P2 | Use the same advisory drain lock in all callers or remove the unlocked duplicate execution path. | PostgreSQL two-connection race test for queue worker vs watchlist refresh; assert one source invocation. | Ensure lock lifetime covers only drain and is acquired consistently. |
| U1 | Unverified | Validate Watchmode success-envelope shape against allowed current API contract before converting missing arrays to empty. | HTTP 200 error/schema payload raises source error and does not write negative evidence. | First establish upstream-supported shapes with primary docs or sanitized captured fixture; do not infer from aggregator disagreement. |

## Implemented in this audit

- **A1 (P0):** missing territory is unknown for non-scraped offers, including language-unknown and rent/buy cases; explicitly global scraped offers retain legacy behavior. Backend and frontend expose an unknown-territory label. Regressions failed before the fix and pass after it.
- **A2 (P1):** Watchmode coordinator reservation is not charged again by the client for the initial attempt; direct calls and retries still reserve per outbound attempt. Nested contexts cannot restore consumed credits. Regressions failed before the fix and pass after it.
- **A3 (P1):** queue `CHECKED` state no longer becomes definitive global unavailability when offers are empty. The title-source absence classifier remains responsible for a real negative. Cross-country prior success plus current failure regression passes.
- **A4 (P2):** detail source state is passed into the shared resolver, matching watchlist report pending/check status.

Focused PostgreSQL regressions were run after recording the baseline failures. A5/A6/A7 remain follow-up work; no code edits have been made for them.

## Independent review and integration

An independent read-only review of changed code/tests found no blocking issues. Validation completed: 3,011 backend tests; 246 frontend tests; lint, TypeScript, production build, migration check, and diff check passed. Prepare a focused PR for review; do not merge without integration CI. PR #74 is out of scope.
