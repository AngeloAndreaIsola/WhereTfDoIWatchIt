import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PopupController } from '../src/popup.js';
import { HandshakeClient, MemoryStorageAdapter } from '../src/handshake.js';
import { PermissionManager, PermissionsAPI } from '../src/permissions.js';
import { AdapterRegistry } from '../src/adapters/index.js';
import { MOCK_MOVIE_PAGE } from './fixtures/mock_pages.js';

describe('Popup Opt-In and Controller Workflow', () => {
  let storage: MemoryStorageAdapter;
  let grantedOrigins: Set<string>;
  let mockApi: PermissionsAPI;
  let permissionManager: PermissionManager;
  let handshakeClient: HandshakeClient;
  let adapterRegistry: AdapterRegistry;
  let controller: PopupController;

  beforeEach(() => {
    storage = new MemoryStorageAdapter();
    grantedOrigins = new Set<string>();

    mockApi = {
      contains: async (perms) => perms.origins?.every((o) => grantedOrigins.has(o)) ?? false,
      request: async (perms) => {
        perms.origins?.forEach((o) => grantedOrigins.add(o));
        return true;
      },
      remove: async (perms) => {
        perms.origins?.forEach((o) => grantedOrigins.delete(o));
        return true;
      },
    };

    permissionManager = new PermissionManager(mockApi);
    handshakeClient = new HandshakeClient({
      localAppOrigin: 'http://localhost:3001',
      storage,
    });
    adapterRegistry = new AdapterRegistry();

    controller = new PopupController({
      handshakeClient,
      permissionManager,
      adapterRegistry,
    });
  });

  it('initializes popup state and reflects permission absence', async () => {
    await controller.init('https://mock-streaming.local/movie/inception');
    const state = controller.getState();
    expect(state.currentUrl).toBe('https://mock-streaming.local/movie/inception');
    expect(state.hasHostPermission).toBe(false);
    expect(state.isAdapterEnabled).toBe(false);
    expect(state.detectedMetadata).toBeNull();
  });

  it('requests host opt-in permission and enables adapter upon user approval', async () => {
    const url = 'https://mock-streaming.local/movie/inception';
    await controller.init(url);

    const granted = await controller.requestOptIn(url);
    expect(granted).toBe(true);

    const state = controller.getState();
    expect(state.hasHostPermission).toBe(true);
    expect(state.isAdapterEnabled).toBe(true);
    expect(state.statusMessage).toContain('Host permission granted');
  });

  it('revokes opt-in permission and disables adapter cleanly', async () => {
    const url = 'https://mock-streaming.local/movie/inception';
    await controller.init(url);
    await controller.requestOptIn(url);

    const revoked = await controller.revokeOptIn(url);
    expect(revoked).toBe(true);

    const state = controller.getState();
    expect(state.hasHostPermission).toBe(false);
    expect(state.isAdapterEnabled).toBe(false);
    expect(state.statusMessage).toContain('Host permission revoked');
  });

  it('extracts metadata only when host permission is granted', async () => {
    const url = new URL('https://mock-streaming.local/movie/inception');
    const doc = new DOMParser().parseFromString(MOCK_MOVIE_PAGE, 'text/html');

    // Without permission -> blocked
    await controller.init(url.toString());
    const resultBlocked = await controller.detectAndInspect(doc, url);
    expect(resultBlocked).toBeNull();
    expect(controller.getState().statusMessage).toContain('Permission not granted');

    // Grant permission
    await controller.requestOptIn(url.toString());
    const resultAllowed = await controller.detectAndInspect(doc, url);
    expect(resultAllowed).not.toBeNull();
    expect(resultAllowed?.title).toBe('Inception');
  });
});
