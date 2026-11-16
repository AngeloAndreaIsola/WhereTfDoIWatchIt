# WhereTfDoIWatchIt browser extension

The Companion is a Manifest V3 extension for explicit, user-initiated title and playback metadata capture. The only implemented site adapter is the local E2E fixture at `mock-streaming.localhost`; Netflix, Prime Video, and other streaming services are not supported.

## Privacy and permissions

- The extension has no `<all_urls>` or wildcard host access. Its only required permissions are `storage`, `activeTab`, and `scripting`.
- Optional host access is requested only when the user opts into the supported site or starts a connection to a local app. Streaming-site consent is recorded separately from transient `activeTab` access, so reopening the popup does not silently grant consent.
- The extension does not read cookies, inspect streaming-site network traffic, access DRM data, or read media streams. It reads supported title fields and the current time and duration from an HTML5 video element only after the user selects **Detect Title**.
- Local app requests use `credentials: 'omit'`. The server requires an allowlisted extension origin, a matching extension request marker, an explicit CSRF-protected Allow action, an unexpired one-use challenge, and a scoped bearer capability.

## Supported local adapter

The `mock-streaming-adapter` recognizes the E2E fixture selectors for title, year, season/episode, and episode title. It can read playback progress from an ordinary HTML5 `<video>` element. A page without the required title field or video fails gracefully. No production streaming-site adapter is implemented.

## Local app connection

The extension connects to a local WhereTfDoIWatchIt server on `http://localhost:8002` (the host port in this repository's Docker Compose setup). The user explicitly starts the connection, approves the challenge in the app, then returns to the extension and finishes the connection. Local app cookies are neither sent nor used for extension authentication.

For a local development server using another approved loopback port, build with `WTDIW_EXTENSION_LOCAL_APP_ORIGIN=http://localhost:<port> npm run build`. The package script accepts only HTTP origins on `localhost` or `127.0.0.1` and ports 3000, 3001, or 8000–8003.

Set `EXTENSION_ALLOWED_ORIGINS` on the Django server to a comma-separated list of allowed extension origins, for example `chrome-extension://<installed-extension-id>`. The server routes are:

- `POST /api/extension/handshake/init` creates a five-minute challenge and returns the same-origin authorization URL.
- `GET/POST /api/extension/authorize/<challenge-id>/` displays an explicit Allow/Deny form protected by Django CSRF middleware.
- `POST /api/extension/handshake/exchange` issues a one-hour `title:send` capability only after explicit approval and nonce validation.
- `POST /api/extension/titles/ingest` validates the bearer token, origin, scope, adapter, source host, and metadata before persisting an `ExtensionPlaybackRecord`.
- `POST /api/extension/handshake/revoke` revokes the capability. Expired and revoked tokens are rejected server-side.

Capability tokens are stored as SHA-256 digests by the server. The current ingestion endpoint stores an extension playback record; it does not resolve the title into TMDb or claim that it has created a normal watched item in the app.

## Build and load in Chromium

```bash
cd extension
npm ci
npm run typecheck
npm test
npm run build
npm run validate:package
```

Load the generated unpacked package from `extension/dist/package` in `chrome://extensions/` with Developer mode enabled. The build copies the Manifest V3 manifest, popup, and emitted scripts into a self-contained directory. Do not load the TypeScript source directory directly.

## Tests

Extension unit tests cover permission persistence, popup flows, extraction, and handshake behavior. Backend tests cover explicit authorization, CSRF, scopes, expiry, revocation, origin validation, and persistence. These tests do not replace real-browser verification. See [PR #74 E2E verification](verification/PR74_EXTENSION_E2E.md) for installed Ego Lite results and limitations.
