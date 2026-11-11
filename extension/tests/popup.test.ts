import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PopupController, bootstrapPopup, updatePopupUI } from '../src/popup.js';
import { HandshakeClient, MemoryStorageAdapter } from '../src/handshake.js';
import { PermissionManager, PermissionsAPI } from '../src/permissions.js';
import { AdapterRegistry } from '../src/adapters/index.js';
import { MOCK_MOVIE_PAGE } from './fixtures/mock_pages.js';
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

  describe('DOM Integration and UI Wiring', () => {
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

    it('displays detected metadata card in DOM when detected', async () => {
      await bootstrapPopup(popupDoc, controller);

      // Grant permission
      const btnPerm = popupDoc.getElementById('btn-request-permission') as HTMLButtonElement;
      btnPerm.click();
      await new Promise((r) => setTimeout(r, 10));

      // Append mock movie elements to document for detection
      const container = popupDoc.createElement('div');
      container.innerHTML = `
        <h1 data-testid="media-title">The Matrix</h1>
        <span data-testid="media-year">1999</span>
      `;
      popupDoc.body.appendChild(container);

      // Trigger detection
      const btnDetect = popupDoc.getElementById('btn-detect-title') as HTMLButtonElement;
      btnDetect.click();
      await new Promise((r) => setTimeout(r, 10));

      const metaContainer = popupDoc.getElementById('metadata-container')!;
      const metaTitle = popupDoc.getElementById('meta-title')!;
      expect(metaContainer.style.display).toBe('block');
      expect(metaTitle.textContent).toBe('The Matrix');
    });
  });
});
