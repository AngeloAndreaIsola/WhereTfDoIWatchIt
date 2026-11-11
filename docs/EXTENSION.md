# WhereTfDoIWatchIt Browser Extension Foundation (WT-049)

## Overview

The WhereTfDoIWatchIt Companion is a minimal cross-browser extension built using Manifest V3 (TypeScript). It enables manual, explicit opt-in detection of video title metadata on streaming sites and securely handshakes with a local instance of WhereTfDoIWatchIt.

## Security & Privacy Invariants

### 1. Minimal Permissions & No All-Sites Access
- **No `<all_urls>` or `*://*/*`**: The extension manifest does not request broad permissions or access to all URLs.
- **`optional_host_permissions`**: Host permissions are declared under `optional_host_permissions` and must be explicitly approved by the user per domain via `chrome.permissions.request`.
- **Zero background history access**: The extension does not declare the `history` permission and cannot read browser history or web navigation events.

### 2. Cookie & Credential Protection
- **No Cookie Capture**: All network communications between the extension and the local application use `credentials: 'omit'`.
- The extension does not read cookies, session headers, credentials, or DRM parameters from visited sites.
- The extension does not inspect network requests or intercept video stream payloads.

### 3. Authenticated & Scoped Local App Handshake
- **Origin-Guarded**: Extension strictly communicates with verified localhost origins (e.g. `http://localhost:3000`, `http://localhost:3001`, `http://localhost:8000`).
- **Fail-Closed Cryptography**: Nonce generation requires the Web Cryptography API (`crypto.getRandomValues`); pseudo-random generators such as `Math.random` are forbidden.
- **CSRF & Nonce Protection**: Handshake involves a two-step handshake:
  1. `POST /api/extension/handshake/init` returns a server challenge nonce and a scoped `X-CSRF-Token`.
  2. `POST /api/extension/handshake/exchange` signs and exchanges client/server nonces with the CSRF token for a scoped `CapabilityToken`.
- **Scoped Capabilities**: Tokens are restricted to granular actions (e.g., `'title:send'`) and cannot perform unauthorized administrative operations.
- **Revocability**: Tokens can be revoked at any time by the user locally or via `POST /api/extension/handshake/revoke`. Expired tokens are purged atomically from storage without recursive lookups.

### 4. Backend Dependency Notice
- The client-side handshake protocol targets the `/api/extension/handshake/*` and `/api/extension/titles/ingest` routes.
- Full end-to-end integration requires the companion backend implementation task providing token issuance, secret signing, and CSRF challenge validation.

## Architecture & Code Boundaries

```
extension/
├── manifest.json              # Manifest V3 declaration (points to dist/src/background.js)
├── package.json               # Isolated build & test scripts
├── tsconfig.json              # TypeScript configuration
├── vitest.config.ts           # Vitest configuration (happy-dom environment)
├── popup.html                 # Action popup markup with active JS script inclusion
├── dist/                      # Emitted JavaScript runtime files for browser loading
├── src/
│   ├── types.ts               # Core domain models (DetectedMetadata, CapabilityToken, etc.)
│   ├── permissions.ts         # Optional host permission manager
│   ├── handshake.ts           # Scoped local app handshake client & storage abstraction
│   ├── popup.ts               # Action popup controller & DOM bootstrap wiring
│   ├── background.ts          # Service worker lifecycle
│   └── adapters/
│       ├── index.ts           # AdapterRegistry (disabled by default)
│       └── mock_adapter.ts    # Mock streaming site adapter
└── tests/
    ├── fixtures/
    │   └── mock_pages.ts      # Offline HTML fixtures
    ├── handshake.test.ts      # Local handshake, recursion prevention, & anti-CSRF security tests
    ├── permissions.test.ts    # Host permissions denial and manifest guard tests
    ├── popup.test.ts          # Popup opt-in workflow & DOM button wiring integration tests
    └── title_detection.test.ts# Pure DOM title, season, episode, and progress tests
```

## Site Adapter Interface

Streaming site adapters implement the `SiteAdapter` interface:

```typescript
export interface SiteAdapter {
  id: string;
  name: string;
  domains: string[];
  canHandle(url: URL): boolean;
  extract(document: Document, url: URL): DetectedMetadata | null;
}
```

### Metadata Model

```typescript
export interface DetectedMetadata {
  title: string;
  contentType: 'movie' | 'tv' | 'unknown';
  year?: number | null;
  season?: number | null;
  episode?: number | null;
  episodeTitle?: string | null;
  playbackProgress?: {
    currentTime: number;
    duration: number;
    progressPercent: number;
  } | null;
  sourceUrl: string;
  siteAdapterId: string;
  detectedAt: string;
}
```

### Invariants for Adapters
- Adapters are **disabled by default**.
- Adapters must never invent positive results for missing/unknown data (e.g. absent year or episode returns `null` or explicit `'unknown'`).
- Adapters must be testable with static offline HTML without requiring live network access or accounts.

## Building and Loading Extension in Browsers

1. Build JavaScript artifacts:
   ```bash
   cd extension && npm run build
   ```
2. In Google Chrome / Chromium / Brave:
   - Navigate to `chrome://extensions/`
   - Enable "Developer mode" (top right toggle)
   - Click "Load unpacked" and select the `extension/` directory.
3. In Mozilla Firefox:
   - Navigate to `about:debugging#/runtime/this-firefox`
   - Click "Load Temporary Add-on..." and select `extension/manifest.json`.

## Running Tests

From repo root:
```bash
cd extension && npm test
cd extension && npm run typecheck
```
