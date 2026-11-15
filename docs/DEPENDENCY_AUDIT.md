# Frontend Dependency Audit & Security Hardening

**Audit Date:** August 21, 2026
**Target Application:** `frontend/` (Next.js App)
**Branch:** `chore/npm-audit-hardening`

---

## 1. Audit Summary

| Metric | Details |
| :--- | :--- |
| **Total Audited Packages** | 539 (21 Prod, 510 Dev, 63 Optional, 11 Peer) |
| **Total Vulnerabilities** | 5 |
| **Severity Breakdown** | **High:** 5, **Moderate:** 0, **Low:** 0, **Critical:** 0 |
| **Production vs Dev Scope** | **Production (4 High):** `next` framework & bundled `postcss`<br>**Development (1 High):** `eslint-config-next` / `@next/eslint-plugin-next` (`glob`) |

---

## 2. Fixes Applied

| Package | Previous Version | Updated Version | Type | Justification / Safety Rationale |
| :--- | :--- | :--- | :--- | :--- |
| `@vitejs/plugin-react` | `^6.0.5` | `^6.1.0` | Dev | Safe minor version bump within major v6. Maintains full compatibility with React 18, Vite 8, and Vitest test runner. No application source code changes required. All tests pass (`npm test`, `npm run build`, `npm run lint`). |

---

## 3. Deferred Vulnerabilities & Breaking Major Upgrades

The following high-severity vulnerabilities require semver-major upgrades or source-code modifications. Per project policy, major version upgrades and fixes requiring application source changes are deferred to dedicated major migration tasks to avoid destabilization and conflicts with ongoing feature work.

### 3.1 Next.js Framework Core (`next` 14.2.35)

* **Advisories:**
  * GHSA-c4j6-fc7j-m34r (SSRF in WebSocket upgrades)
  * GHSA-36qx-fr4f-26g5 (Middleware/Proxy bypass in Pages Router)
  * GHSA-m99w-x7hq-7vfj (DoS in App Router Server Actions)
  * GHSA-89xv-2m56-2m9x (SSRF in Server Actions)
  * GHSA-p9j2-gv94-2wf4 (SSRF in rewrites)
  * GHSA-955p-x3mx-jcvp (Unauthenticated disclosure of internal Server Function endpoints)
* **Severity:** High
* **Dependency Scope:** Production
* **Required Fix:** Upgrade `next` to `16.3.2` (or Next.js 15+).
* **Deferral Rationale:** Upgrading Next.js from v14 to v15/16 drags a major upgrade of React from `v18` to `v19`, changes App Router and Server Component async APIs (`params`, `searchParams`, `headers()`, `cookies()`), and requires refactoring application source code. Application source changes are strictly prohibited in this task.
* **Risk Assessment:** The application is currently deployed as a single-user trusted tool behind an internal network boundary without public untrusted Server Action endpoints or multi-tenant cache sharing. The operational security risk in the current environment is low.
* **Recommended Upgrade Path:** Schedule a dedicated major framework migration task (`feat/next15-react19-migration`) to upgrade Next.js and React together, refactoring App Router components and running full regression testing.

### 3.2 PostCSS CSS Parser (`postcss` <=8.5.22)

* **Advisories:**
  * GHSA-qx2v-qp2m-jg93 (XSS via Unescaped `</style>` output)
  * GHSA-6g55-p6wh-862q (Arbitrary file read via `sourceMappingURL`)
  * GHSA-fxqj-rqcc-2cmp (Incomplete fix for source map loading)
  * GHSA-r28c-9q8g-f849 (Path traversal in previous source map auto-loading)
* **Severity:** High
* **Dependency Scope:** Production / Build
* **Required Fix:** Upgrade `next` to `16.3.2` (which bundles `postcss@8.5.26+`).
* **Deferral Rationale:** Direct devDependency `postcss` in `package.json` is already resolving to `8.5.26`. However, Next.js 14 pins its internal bundled `postcss` instance to `8.4.31` (`node_modules/next/node_modules/postcss`). Upgrading this nested instance requires upgrading `next`.
* **Risk Assessment:** PostCSS is executed server-side at build time to compile local Tailwind CSS files. It does not process untrusted user-supplied CSS inputs at runtime.
* **Recommended Upgrade Path:** Will be automatically resolved when Next.js is upgraded to version 15/16.

### 3.3 Glob CLI in ESLint Plugin (`glob` 10.2.0 - 10.4.5 / `eslint-config-next`)

* **Advisories:** GHSA-5j98-mcp5-4vw2 (Command injection via `-c/--cmd` with `shell:true` in `glob` CLI)
* **Severity:** High
* **Dependency Scope:** Development (`eslint-config-next` -> `@next/eslint-plugin-next` -> `glob`)
* **Required Fix:** Upgrade `eslint-config-next` to `16.3.2` and `eslint` to `v9`.
* **Deferral Rationale:** Running `npm audit fix --force` upgrades `eslint-config-next` to v15/v16 and ESLint to v9, which introduces ESLint flat config (`eslint.config.mjs`) requirements, breaking the existing `.eslintrc.json` linting setup and failing `npm run lint`.
* **Risk Assessment:** `glob` is a development-only dependency used during code linting. The vulnerability requires invoking the `glob` command-line interface with malicious `-c/--cmd` arguments, which is not performed by any npm scripts or CI workflows in this repository.
* **Recommended Upgrade Path:** Upgrade ESLint and `eslint-config-next` alongside the Next.js 15 framework upgrade task.

---

## 4. Re-Audit Cadence & Guidelines

To maintain dependency health and security posture:

1. **Monthly Audit Runs:** Run `npm audit` at the start of each development cycle/month.
2. **CI Pipeline Integration:** Execute `npm audit --audit-level=high` in CI to catch new high/critical vulnerabilities early.
3. **Patch/Minor Bumps:** Apply non-breaking patch and minor updates regularly via `npm update` provided build, lint, and test suites pass (`npm run lint && npm run build && npm test`).
4. **Major Version Migrations:** Treat major framework upgrades (e.g. Next.js, React, ESLint) as distinct migration tasks with dedicated test coverage and source refactoring.
