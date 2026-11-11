import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  HandshakeClient,
  MemoryStorageAdapter,
  isValidLocalAppOrigin,
} from '../src/handshake.js';
import { DetectedMetadata } from '../src/types.js';

describe('Extension to Local App Handshake & Security', () => {
  let storage: MemoryStorageAdapter;

  beforeEach(() => {
    storage = new MemoryStorageAdapter();
  });

  describe('Origin Validation', () => {
    it('allows valid local app origins on allowed ports', () => {
      expect(isValidLocalAppOrigin('http://localhost:3000')).toBe(true);
      expect(isValidLocalAppOrigin('http://localhost:3001')).toBe(true);
      expect(isValidLocalAppOrigin('http://localhost:8000')).toBe(true);
      expect(isValidLocalAppOrigin('http://127.0.0.1:3000')).toBe(true);
    });

    it('rejects foreign or unauthorized domains and non-standard ports', () => {
      expect(isValidLocalAppOrigin('https://evil.com')).toBe(false);
      expect(isValidLocalAppOrigin('http://localhost:9999')).toBe(false);
      expect(isValidLocalAppOrigin('http://attacker-controlled.local')).toBe(false);
      expect(isValidLocalAppOrigin('ftp://localhost:3000')).toBe(false);
    });

    it('throws error when initializing HandshakeClient with invalid origin', () => {
      expect(() => {
        new HandshakeClient({
          localAppOrigin: 'https://malicious-site.com',
          storage,
        });
      }).toThrow(/Unauthorized or invalid local app origin/);
    });
  });

  describe('Handshake Protocol & Token Issuance', () => {
    it('completes two-step handshake with challenge-response nonces and CSRF token', async () => {
      const recordedRequests: { url: string; init?: RequestInit }[] = [];

      const mockFetch: typeof fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlStr = input.toString();
        recordedRequests.push({ url: urlStr, init });

        if (urlStr.endsWith('/api/extension/handshake/init')) {
          expect(init?.credentials).toBe('omit');
          const body = JSON.parse(init?.body as string);
          expect(body.clientNonce).toBeDefined();

          return new Response(
            JSON.stringify({
              serverNonce: 'srv-nonce-98765',
              csrfToken: 'csrf-secret-abcde',
              allowedOrigins: ['http://localhost:3001'],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }

        if (urlStr.endsWith('/api/extension/handshake/exchange')) {
          expect(init?.credentials).toBe('omit');
          const headers = init?.headers as Record<string, string>;
          expect(headers['X-CSRF-Token']).toBe('csrf-secret-abcde');

          const body = JSON.parse(init?.body as string);
          expect(body.serverNonce).toBe('srv-nonce-98765');
          expect(body.csrfToken).toBe('csrf-secret-abcde');
          expect(body.requestedScopes).toEqual(['title:send']);

          return new Response(
            JSON.stringify({
              state: 'granted',
              capabilityToken: {
                token: 'cap-tok-12345678',
                issuedAt: Date.now(),
                expiresAt: Date.now() + 3600_000,
                scopes: ['title:send'],
                origin: 'http://localhost:3001',
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }

        return new Response('Not Found', { status: 404 });
      });

      const client = new HandshakeClient({
        localAppOrigin: 'http://localhost:3001',
        storage,
        fetchFn: mockFetch,
      });

      const token = await client.establishHandshake(['title:send']);
      expect(token.token).toBe('cap-tok-12345678');
      expect(token.scopes).toContain('title:send');

      // Verify token persisted in storage
      const stored = await client.getStoredToken();
      expect(stored?.token).toBe('cap-tok-12345678');
    });

    it('fails closed when cryptographic random generator is unavailable', async () => {
      const originalCrypto = globalThis.crypto;
      // Temporarily remove getRandomValues
      Object.defineProperty(globalThis, 'crypto', {
        value: undefined,
        configurable: true,
      });

      try {
        const client = new HandshakeClient({
          localAppOrigin: 'http://localhost:3001',
          storage,
        });

        await expect(client.establishHandshake(['title:send'])).rejects.toThrow(
          /Cryptographically secure random number generator is unavailable/
        );
      } finally {
        Object.defineProperty(globalThis, 'crypto', {
          value: originalCrypto,
          configurable: true,
        });
      }
    });

    it('handles expired tokens safely without recursion or hang', async () => {
      // Put expired token into storage
      const getSpy = vi.spyOn(storage, 'get');
      const removeSpy = vi.spyOn(storage, 'remove');

      await storage.set(
        'wheretfdoiwatchit_capability_token',
        JSON.stringify({
          token: 'expired-token',
          issuedAt: Date.now() - 100_000,
          expiresAt: Date.now() - 10_000, // Expired
          scopes: ['title:send'],
          origin: 'http://localhost:3001',
        })
      );

      const client = new HandshakeClient({
        localAppOrigin: 'http://localhost:3001',
        storage,
      });

      // Call getStoredToken
      const token = await client.getStoredToken();
      expect(token).toBeNull();

      // Ensure storage.get was called exactly once and remove called exactly once (no recursion)
      expect(getSpy).toHaveBeenCalledTimes(1);
      expect(removeSpy).toHaveBeenCalledTimes(1);

      // Verify storage is indeed empty now
      expect(await storage.get('wheretfdoiwatchit_capability_token')).toBeNull();
    });

    it('revokes token atomically and notifies server with bounded calls', async () => {
      const getSpy = vi.spyOn(storage, 'get');
      const removeSpy = vi.spyOn(storage, 'remove');

      const mockFetch: typeof fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlStr = input.toString();
        if (urlStr.endsWith('/api/extension/handshake/revoke')) {
          expect(init?.credentials).toBe('omit');
          const headers = init?.headers as Record<string, string>;
          expect(headers['Authorization']).toBe('Bearer active-token');
          return new Response(JSON.stringify({ revoked: true }), { status: 200 });
        }
        return new Response('Not found', { status: 404 });
      });

      await storage.set(
        'wheretfdoiwatchit_capability_token',
        JSON.stringify({
          token: 'active-token',
          issuedAt: Date.now(),
          expiresAt: Date.now() + 10000,
          scopes: ['title:send'],
          origin: 'http://localhost:3001',
        })
      );

      const client = new HandshakeClient({
        localAppOrigin: 'http://localhost:3001',
        storage,
        fetchFn: mockFetch,
      });

      await client.revokeToken();
      expect(await client.getStoredToken()).toBeNull();
      expect(mockFetch).toHaveBeenCalledTimes(1);

      // Verify bounded calls: remove called during revoke, no infinite loops
      expect(removeSpy).toHaveBeenCalledTimes(1);
    });

    it('sends detected metadata to local app with authorization header and credentials omitted', async () => {
      await storage.set(
        'wheretfdoiwatchit_capability_token',
        JSON.stringify({
          token: 'valid-token-xyz',
          issuedAt: Date.now(),
          expiresAt: Date.now() + 60_000,
          scopes: ['title:send'],
          origin: 'http://localhost:3001',
        })
      );

      let payloadSent: any = null;
      let authHeader: string | undefined = undefined;
      let credentialsSetting: RequestCredentials | undefined = undefined;

      const mockFetch: typeof fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlStr = input.toString();
        if (urlStr.endsWith('/api/extension/titles/ingest')) {
          credentialsSetting = init?.credentials;
          authHeader = (init?.headers as Record<string, string>)?.['Authorization'];
          payloadSent = JSON.parse(init?.body as string);
          return new Response(JSON.stringify({ success: true, message: 'Ingested' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response('Not found', { status: 404 });
      });

      const client = new HandshakeClient({
        localAppOrigin: 'http://localhost:3001',
        storage,
        fetchFn: mockFetch,
      });

      const metadata: DetectedMetadata = {
        title: 'Interstellar',
        contentType: 'movie',
        year: 2014,
        sourceUrl: 'https://mock-streaming.local/movie/interstellar',
        siteAdapterId: 'mock-streaming-adapter',
        detectedAt: new Date().toISOString(),
      };

      const result = await client.sendMetadata(metadata);
      expect(result.success).toBe(true);
      expect(authHeader).toBe('Bearer valid-token-xyz');
      expect(credentialsSetting).toBe('omit');
      expect(payloadSent.title).toBe('Interstellar');
      expect(payloadSent.year).toBe(2014);
    });

    it('rejects sending metadata when token lacks title:send scope', async () => {
      await storage.set(
        'wheretfdoiwatchit_capability_token',
        JSON.stringify({
          token: 'readonly-token',
          issuedAt: Date.now(),
          expiresAt: Date.now() + 60_000,
          scopes: ['watchlist:read'],
          origin: 'http://localhost:3001',
        })
      );

      const client = new HandshakeClient({
        localAppOrigin: 'http://localhost:3001',
        storage,
      });

      const metadata: DetectedMetadata = {
        title: 'Test',
        contentType: 'movie',
        sourceUrl: 'https://mock-streaming.local/test',
        siteAdapterId: 'mock',
        detectedAt: new Date().toISOString(),
      };

      await expect(client.sendMetadata(metadata)).rejects.toThrow(/Active token lacks "title:send" scope/);
    });
  });
});
