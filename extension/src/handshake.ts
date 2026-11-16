import {
  CapabilityScope,
  CapabilityToken,
  HandshakeExchangeRequest,
  HandshakeExchangeResponse,
  HandshakeInitRequest,
  HandshakeInitResponse,
  DetectedMetadata,
} from './types.js';

export interface StorageAdapter {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

export class MemoryStorageAdapter implements StorageAdapter {
  private data = new Map<string, string>();
  async get(key: string): Promise<string | null> {
    return this.data.get(key) ?? null;
  }
  async set(key: string, value: string): Promise<void> {
    this.data.set(key, value);
  }
  async remove(key: string): Promise<void> {
    this.data.delete(key);
  }
}

export class ChromeStorageAdapter implements StorageAdapter {
  async get(key: string): Promise<string | null> {
    const result = await chrome.storage.local.get(key);
    return (result[key] as string) ?? null;
  }
  async set(key: string, value: string): Promise<void> {
    await chrome.storage.local.set({ [key]: value });
  }
  async remove(key: string): Promise<void> {
    await chrome.storage.local.remove(key);
  }
}

export interface HandshakeConfig {
  localAppOrigin: string; // e.g. "http://localhost:3000" or "http://localhost:3001"
  storage?: StorageAdapter;
  fetchFn?: typeof fetch;
}

const STORAGE_KEY_TOKEN = 'wheretfdoiwatchit_capability_token';
const STORAGE_KEY_PENDING = 'wheretfdoiwatchit_pending_handshake';

interface PendingHandshake {
  challengeId: string;
  clientNonce: string;
  serverNonce: string;
  authorizationUrl: string;
}

/**
 * Validates whether an origin is an authorized local application host.
 */
export function isValidLocalAppOrigin(origin: string): boolean {
  try {
    const url = new URL(origin);
    const isLocalhost = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
    const isAllowedPort = ['3000', '3001', '8000', '8001', '8002', '8003'].includes(url.port);
    return isLocalhost && isAllowedPort && (url.protocol === 'http:' || url.protocol === 'https:');
  } catch {
    return false;
  }
}

/**
 * Extension Handshake Client.
 *
 * Implements a CSRF-protected, scoped, authenticated handshake between
 * the browser extension and the local WhereTfDoIWatchIt application.
 *
 * Guarantees:
 * - Credentials (cookies) are strictly omitted (`credentials: 'omit'`).
 * - No ambient cookie/auth capture from external sites.
 * - Capabilities are scoped (e.g. 'title:send').
 * - Revocable locally and on server.
 * - Nonce generation fails closed if cryptographic entropy is unavailable.
 * - Atomic local token removal without recursive lookup or infinite loops.
 */
export class HandshakeClient {
  private localAppOrigin: string;
  private storage: StorageAdapter;
  private fetchFn: typeof fetch;

  constructor(config: HandshakeConfig) {
    if (!isValidLocalAppOrigin(config.localAppOrigin)) {
      throw new Error(`Unauthorized or invalid local app origin: ${config.localAppOrigin}`);
    }
    this.localAppOrigin = config.localAppOrigin;
    this.storage =
      config.storage ||
      (typeof chrome !== 'undefined' && chrome?.storage?.local
        ? new ChromeStorageAdapter()
        : new MemoryStorageAdapter());
    this.fetchFn = config.fetchFn || fetch.bind(globalThis);
  }

  getLocalAppOrigin(): string {
    return this.localAppOrigin;
  }

  async hasPendingHandshake(): Promise<boolean> {
    return !!(await this.storage.get(STORAGE_KEY_PENDING));
  }

  /**
   * Generates a cryptographically strong client nonce.
   * Fails closed if Web Cryptography API is unavailable.
   */
  private generateNonce(): string {
    const cryptoObj = typeof crypto !== 'undefined' ? crypto : (globalThis as any).crypto;
    if (!cryptoObj || typeof cryptoObj.getRandomValues !== 'function') {
      throw new Error('Cryptographically secure random number generator is unavailable.');
    }
    const buffer = new Uint8Array(16);
    cryptoObj.getRandomValues(buffer);
    return Array.from(buffer)
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }

  /**
   * Perform handshake initialization and capability token exchange.
   */
  async beginHandshake(): Promise<string> {
    const clientNonce = this.generateNonce();

    // Step 1: Initialize handshake and obtain anti-CSRF challenge & server nonce
    const initReq: HandshakeInitRequest = {
      clientNonce,
    };

    const initUrl = `${this.localAppOrigin}/api/extension/handshake/init`;
    const initRes = await this.fetchFn(initUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-WTDIW-Extension': '1',
      },
      credentials: 'omit', // DO NOT send or receive cookies
      body: JSON.stringify(initReq),
    });

    if (!initRes.ok) {
      throw new Error(`Handshake init failed with status: ${initRes.status}`);
    }

    const initData = (await initRes.json()) as HandshakeInitResponse;
    if (!initData.challengeId || !initData.serverNonce || !initData.authorizationUrl) {
      throw new Error('Malformed handshake init response');
    }

    const authorizationUrl = new URL(initData.authorizationUrl);
    if (authorizationUrl.origin !== this.localAppOrigin || !authorizationUrl.pathname.startsWith('/api/extension/authorize/')) {
      throw new Error('The local application returned an invalid authorization URL.');
    }
    await this.storage.set(STORAGE_KEY_PENDING, JSON.stringify({
      challengeId: initData.challengeId,
      clientNonce,
      serverNonce: initData.serverNonce,
      authorizationUrl: authorizationUrl.toString(),
    } satisfies PendingHandshake));
    return authorizationUrl.toString();
  }

  async completeHandshake(
    requestedScopes: CapabilityScope[] = ['title:send']
  ): Promise<CapabilityToken> {
    const pendingRaw = await this.storage.get(STORAGE_KEY_PENDING);
    if (!pendingRaw) throw new Error('No pending app authorization. Start Connect to local app first.');
    let pending: PendingHandshake;
    try {
      pending = JSON.parse(pendingRaw) as PendingHandshake;
    } catch {
      await this.storage.remove(STORAGE_KEY_PENDING);
      throw new Error('Pending app authorization is invalid. Start the connection again.');
    }

    // The server issues a token only after the local user approves the challenge.
    const exchangeReq: HandshakeExchangeRequest = {
      challengeId: pending.challengeId,
      clientNonce: pending.clientNonce,
      serverNonce: pending.serverNonce,
      requestedScopes,
    };

    const exchangeUrl = `${this.localAppOrigin}/api/extension/handshake/exchange`;
    const exchangeRes = await this.fetchFn(exchangeUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-WTDIW-Extension': '1',
      },
      credentials: 'omit', // DO NOT capture or send cookies
      body: JSON.stringify(exchangeReq),
    });

    if (!exchangeRes.ok) {
      throw new Error(`Handshake exchange failed with status: ${exchangeRes.status}`);
    }

    const exchangeData = (await exchangeRes.json()) as HandshakeExchangeResponse;
    if (exchangeData.state !== 'granted' || !exchangeData.capabilityToken) {
      throw new Error('Capability token request rejected by local application');
    }

    // Save token in storage
    await this.storage.set(STORAGE_KEY_TOKEN, JSON.stringify(exchangeData.capabilityToken));
    await this.storage.remove(STORAGE_KEY_PENDING);
    return exchangeData.capabilityToken;
  }

  async establishHandshake(
    requestedScopes: CapabilityScope[] = ['title:send']
  ): Promise<CapabilityToken> {
    await this.beginHandshake();
    return await this.completeHandshake(requestedScopes);
  }

  /**
   * Retrieve active token from storage if not expired.
   * Avoids recursive calls: atomically purges expired tokens without calling revokeToken.
   */
  async getStoredToken(): Promise<CapabilityToken | null> {
    const raw = await this.storage.get(STORAGE_KEY_TOKEN);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as CapabilityToken;
      if (parsed.expiresAt && Date.now() > parsed.expiresAt) {
        // Purge expired token directly from storage
        await this.storage.remove(STORAGE_KEY_TOKEN);
        return null;
      }
      return parsed;
    } catch {
      await this.storage.remove(STORAGE_KEY_TOKEN);
      return null;
    }
  }

  /**
   * Revoke token locally and notify the local application.
   * Atomically reads and deletes token from storage first, avoiding recursion.
   */
  async revokeToken(): Promise<void> {
    const raw = await this.storage.get(STORAGE_KEY_TOKEN);
    await this.storage.remove(STORAGE_KEY_TOKEN);

    if (raw) {
      let tokenValue: string | null = null;
      try {
        const parsed = JSON.parse(raw) as CapabilityToken;
        tokenValue = parsed.token;
      } catch {
        // Token was malformed, already removed from storage
      }

      if (tokenValue) {
        try {
          await this.fetchFn(`${this.localAppOrigin}/api/extension/handshake/revoke`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-WTDIW-Extension': '1',
              Authorization: `Bearer ${tokenValue}`,
            },
            credentials: 'omit',
            body: JSON.stringify({ token: tokenValue }),
          });
        } catch {
          // Local revocation completed even if server is unreachable
        }
      }
    }
  }

  /**
   * Send detected title metadata to the local app using the capability token.
   */
  async sendMetadata(metadata: DetectedMetadata): Promise<{ success: boolean; message: string }> {
    const token = await this.getStoredToken();
    if (!token) {
      throw new Error('No active capability token. Complete handshake first.');
    }
    if (!token.scopes.includes('title:send')) {
      throw new Error('Active token lacks "title:send" scope.');
    }

    const res = await this.fetchFn(`${this.localAppOrigin}/api/extension/titles/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token.token}`,
        'X-WTDIW-Extension': '1',
      },
      credentials: 'omit',
      body: JSON.stringify(metadata),
    });

    if (!res.ok) {
      if (res.status === 401) await this.storage.remove(STORAGE_KEY_TOKEN);
      throw new Error(`Failed to ingest metadata: ${res.status}`);
    }

    return (await res.json()) as { success: boolean; message: string };
  }
}
