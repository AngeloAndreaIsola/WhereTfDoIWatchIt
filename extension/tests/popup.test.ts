import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PopupController, bootstrapPopup, updatePopupUI } from '../src/popup.js';
import { HandshakeClient, MemoryStorageAdapter } from '../src/handshake.js';
import { PermissionManager, PermissionsAPI } from '../src/permissions.js';
import { AdapterRegistry } from '../src/adapters/index.js';
import { MOCK_MOVIE_PAGE, MOCK_SERIES_EPISODE_PAGE } from './fixtures/mock_pages.js';
import fs from 'node:fs';
import path from 'node:path';

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
    const url = 'https://mock-streaming.local/movie/inception';
    const siteDoc = new DOMParser().parseFromString(MOCK_MOVIE_PAGE, 'text/html');

    // Without permission -> blocked
    await controller.init(url);
    const resultBlocked = await controller.detectActiveTabTitle(siteDoc);
    expect(resultBlocked).toBeNull();
    expect(controller.getState().statusMessage).toContain('Permission not granted');

    // Grant permission
    await controller.requestOptIn(url);
    const resultAllowed = await controller.detectActiveTabTitle(siteDoc);
    expect(resultAllowed).not.toBeNull();
    expect(resultAllowed?.title).toBe('Inception');
  });

  describe('DOM Integration and UI Wiring with Distinct Popup vs Active Page DOM', () => {
    let popupDoc: Document;

    beforeEach(() => {
      const htmlPath = path.resolve(__dirname, '../popup.html');
      let htmlContent = fs.readFileSync(htmlPath, 'utf-8');
      // Strip script tag during DOM test so happy-dom doesn't try to fetch non-existent server script
      htmlContent = htmlContent.replace(/<script[\s\S]*?<\/script>/gi, '');
      popupDoc = new DOMParser().parseFromString(htmlContent, 'text/html');
    });

    it('renders initial state with disabled detection button and disconnected badges', async () => {
      await bootstrapPopup(popupDoc, controller);

      const permBadge = popupDoc.getElementById('permission-badge')!;
      const handshakeBadge = popupDoc.getElementById('handshake-badge')!;
      const btnDetect = popupDoc.getElementById('btn-detect-title') as HTMLButtonElement;
      const btnSend = popupDoc.getElementById('btn-send-app') as HTMLButtonElement;

      expect(permBadge.textContent).toBe('Not Granted');
      expect(handshakeBadge.textContent).toBe('Disconnected');
      expect(btnDetect.disabled).toBe(true);
      expect(btnSend.disabled).toBe(true);
    });

    it('toggles permission button click and enables detection', async () => {
      await bootstrapPopup(popupDoc, controller);

      const btnPerm = popupDoc.getElementById('btn-request-permission') as HTMLButtonElement;
      const permBadge = popupDoc.getElementById('permission-badge')!;
      const btnDetect = popupDoc.getElementById('btn-detect-title') as HTMLButtonElement;

      // Click grant permission
      btnPerm.click();
      await new Promise((r) => setTimeout(r, 10));

      expect(permBadge.textContent).toBe('Granted');
      expect(btnDetect.disabled).toBe(false);
      expect(btnPerm.textContent).toBe('Revoke Permission For This Site');

      // Click again to revoke
      btnPerm.click();
      await new Promise((r) => setTimeout(r, 10));

      expect(permBadge.textContent).toBe('Not Granted');
      expect(btnDetect.disabled).toBe(true);
      expect(btnPerm.textContent).toBe('Grant Permission For This Site');
    });

    it('extracts metadata from active streaming tab document (NOT popup DOM) and renders details', async () => {
      // Mock active streaming page document (completely separate from popupDoc)
      const activeStreamingDoc = new DOMParser().parseFromString(MOCK_SERIES_EPISODE_PAGE, 'text/html');

      // Bootstrap popup with active streaming document passed as active tab fallback
      await bootstrapPopup(popupDoc, controller, activeStreamingDoc);

      // Grant permission
      const btnPerm = popupDoc.getElementById('btn-request-permission') as HTMLButtonElement;
      btnPerm.click();
      await new Promise((r) => setTimeout(r, 10));

      // Trigger detection
      const btnDetect = popupDoc.getElementById('btn-detect-title') as HTMLButtonElement;
      btnDetect.click();
      await new Promise((r) => setTimeout(r, 10));

      const metaContainer = popupDoc.getElementById('metadata-container')!;
      const metaTitle = popupDoc.getElementById('meta-title')!;
      const metaDetails = popupDoc.getElementById('meta-details')!;

      // Verified extracted from activeStreamingDoc, not popupDoc
      expect(metaContainer.style.display).toBe('block');
      expect(metaTitle.textContent).toBe('Breaking Bad');
      expect(metaDetails.textContent).toContain('TV');
      expect(metaDetails.textContent).toContain('S5:E14');
    });

    it('supports tab script executor delegation for in-browser tab isolation', async () => {
      const mockScriptExecutor = vi.fn(async (tabId: number, urlStr: string) => {
        return {
          title: 'Remote Streaming Title',
          contentType: 'movie' as const,
          year: 2024,
          sourceUrl: urlStr,
          siteAdapterId: 'mock-streaming-adapter',
          detectedAt: new Date().toISOString(),
        };
      });

      const delegatedController = new PopupController({
        handshakeClient,
        permissionManager,
        adapterRegistry,
        scriptExecutor: mockScriptExecutor,
      });

      await bootstrapPopup(popupDoc, delegatedController);

      // Grant permission
      const btnPerm = popupDoc.getElementById('btn-request-permission') as HTMLButtonElement;
      btnPerm.click();
      await new Promise((r) => setTimeout(r, 10));

      // Set activeTabId on state to simulate real browser active tab
      await delegatedController.init('https://mock-streaming.local/watch/remote', 42);

      const btnDetect = popupDoc.getElementById('btn-detect-title') as HTMLButtonElement;
      btnDetect.click();
      await new Promise((r) => setTimeout(r, 10));

      expect(mockScriptExecutor).toHaveBeenCalledWith(42, 'https://mock-streaming.local/watch/remote');
      const metaTitle = popupDoc.getElementById('meta-title')!;
      expect(metaTitle.textContent).toBe('Remote Streaming Title');
    });
  });
});
