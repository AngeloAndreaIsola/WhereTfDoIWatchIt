import { describe, it, expect } from 'vitest';
import { PermissionManager, PermissionsAPI } from '../src/permissions.js';
import fs from 'node:fs';
import path from 'node:path';

describe('Host Permission Management and Boundaries', () => {
  it('enforces manifest V3 does NOT declare <all_urls> or broad permissions', () => {
    const manifestPath = path.resolve(__dirname, '../manifest.json');
    const raw = fs.readFileSync(manifestPath, 'utf-8');
    const manifest = JSON.parse(raw);

    // Verify minimal permissions
    expect(manifest.permissions).not.toContain('<all_urls>');
    expect(manifest.permissions).not.toContain('*://*/*');
    expect(manifest.permissions).toEqual(['storage', 'activeTab', 'scripting']);

    // Verify optional host permissions are targeted
    expect(manifest.optional_host_permissions).toBeDefined();
    expect(manifest.optional_host_permissions).not.toContain('<all_urls>');
    expect(manifest.optional_host_permissions).not.toContain('*://*/*');
  });

  it('checks permission status correctly', async () => {
    const granted = new Set<string>();
    const mockApi: PermissionsAPI = {
      contains: async (perms) => perms.origins?.every((o) => granted.has(o)) ?? false,
      request: async (perms) => {
        perms.origins?.forEach((o) => granted.add(o));
        return true;
      },
      remove: async (perms) => {
        perms.origins?.forEach((o) => granted.delete(o));
        return true;
      },
    };

    const manager = new PermissionManager(mockApi);
    const url = 'https://mock-streaming.localhost/watch/123';

    // Initially not granted
    const statusBefore = await manager.checkHostPermission(url);
    expect(statusBefore.hasPermission).toBe(false);
    expect(statusBefore.domain).toBe('mock-streaming.localhost');

    // Request grant
    const grantResult = await manager.requestHostPermission(url);
    expect(grantResult).toBe(true);

    const statusAfter = await manager.checkHostPermission(url);
    expect(statusAfter.hasPermission).toBe(true);

    // Revocation
    const revokeResult = await manager.revokeHostPermission(url);
    expect(revokeResult).toBe(true);

    const statusRevoked = await manager.checkHostPermission(url);
    expect(statusRevoked.hasPermission).toBe(false);
  });

  it('handles user denial of permission gracefully', async () => {
    const mockApi: PermissionsAPI = {
      contains: async () => false,
      request: async () => false, // User clicks "Deny"
      remove: async () => true,
    };

    const manager = new PermissionManager(mockApi);
    const denied = await manager.requestHostPermission('https://mock-streaming.localhost/watch/123');
    expect(denied).toBe(false);

    const status = await manager.checkHostPermission('https://mock-streaming.localhost/watch/123');
    expect(status.hasPermission).toBe(false);
  });

  it('requires persistent explicit opt-in even when activeTab grants transient browser access', async () => {
    const browserGranted = new Set<string>();
    const values = new Map<string, string>();
    const storage = {
      get: async (key: string) => values.get(key) ?? null,
      set: async (key: string, value: string) => { values.set(key, value); },
      remove: async (key: string) => { values.delete(key); },
    };
    const api: PermissionsAPI = {
      contains: async (permissions) => permissions.origins?.every((origin) => browserGranted.has(origin)) ?? false,
      request: async (permissions) => { permissions.origins?.forEach((origin) => browserGranted.add(origin)); return true; },
      remove: async (permissions) => { permissions.origins?.forEach((origin) => browserGranted.delete(origin)); return true; },
    };
    const url = 'http://mock-streaming.localhost:4173/movie/inception';
    const manager = new PermissionManager(api, storage);

    browserGranted.add('http://mock-streaming.localhost/*');
    expect((await manager.checkHostPermission(url)).hasPermission).toBe(false);

    await manager.requestHostPermission(url);
    const reopenedPopupManager = new PermissionManager(api, storage);
    expect((await reopenedPopupManager.checkHostPermission(url)).hasPermission).toBe(true);
    await reopenedPopupManager.revokeHostPermission(url);
    expect((await new PermissionManager(api, storage).checkHostPermission(url)).hasPermission).toBe(false);
  });
});
