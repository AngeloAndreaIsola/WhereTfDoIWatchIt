import { HandshakeClient } from './handshake.js';
import { PermissionManager } from './permissions.js';
import { AdapterRegistry } from './adapters/index.js';
import { DetectedMetadata } from './types.js';

export interface PopupState {
  currentUrl: string | null;
  hasHostPermission: boolean;
  isAdapterEnabled: boolean;
  isHandshakeActive: boolean;
  detectedMetadata: DetectedMetadata | null;
  statusMessage: string | null;
}

export class PopupController {
  private handshakeClient: HandshakeClient;
  private permissionManager: PermissionManager;
  private adapterRegistry: AdapterRegistry;
  private state: PopupState = {
    currentUrl: null,
    hasHostPermission: false,
    isAdapterEnabled: false,
    isHandshakeActive: false,
    detectedMetadata: null,
    statusMessage: null,
  };

  constructor(options: {
    handshakeClient: HandshakeClient;
    permissionManager: PermissionManager;
    adapterRegistry: AdapterRegistry;
  }) {
    this.handshakeClient = options.handshakeClient;
    this.permissionManager = options.permissionManager;
    this.adapterRegistry = options.adapterRegistry;
  }

  getState(): PopupState {
    return { ...this.state };
  }

  async init(currentUrl: string): Promise<void> {
    this.state.currentUrl = currentUrl;
    const perm = await this.permissionManager.checkHostPermission(currentUrl);
    this.state.hasHostPermission = perm.hasPermission;

    const token = await this.handshakeClient.getStoredToken();
    this.state.isHandshakeActive = !!token;

    try {
      const parsedUrl = new URL(currentUrl);
      const adapter = this.adapterRegistry.getAllAdapters().find((a) => a.canHandle(parsedUrl));
      if (adapter) {
        this.state.isAdapterEnabled = this.adapterRegistry.isAdapterEnabled(adapter.id);
      }
    } catch {
      // invalid URL
    }
  }

  async requestOptIn(currentUrl: string): Promise<boolean> {
    const granted = await this.permissionManager.requestHostPermission(currentUrl);
    this.state.hasHostPermission = granted;

    if (granted) {
      try {
        const parsedUrl = new URL(currentUrl);
        const adapter = this.adapterRegistry.getAllAdapters().find((a) => a.canHandle(parsedUrl));
        if (adapter) {
          this.adapterRegistry.enableAdapter(adapter.id);
          this.state.isAdapterEnabled = true;
        }
      } catch {
        // no-op
      }
      this.state.statusMessage = 'Host permission granted for this site.';
    } else {
      this.state.statusMessage = 'Host permission was denied by the user.';
    }
    return granted;
  }

  async revokeOptIn(currentUrl: string): Promise<boolean> {
    const removed = await this.permissionManager.revokeHostPermission(currentUrl);
    if (removed) {
      this.state.hasHostPermission = false;
      try {
        const parsedUrl = new URL(currentUrl);
        const adapter = this.adapterRegistry.getAllAdapters().find((a) => a.canHandle(parsedUrl));
        if (adapter) {
          this.adapterRegistry.disableAdapter(adapter.id);
          this.state.isAdapterEnabled = false;
        }
      } catch {
        // no-op
      }
      this.state.statusMessage = 'Host permission revoked for this site.';
    }
    return removed;
  }

  async connectApp(): Promise<boolean> {
    try {
      await this.handshakeClient.establishHandshake(['title:send']);
      this.state.isHandshakeActive = true;
      this.state.statusMessage = 'Handshake established with local application.';
      return true;
    } catch (err: any) {
      this.state.statusMessage = `Handshake failed: ${err.message || err}`;
      return false;
    }
  }

  async detectAndInspect(doc: Document, url: URL): Promise<DetectedMetadata | null> {
    if (!this.state.hasHostPermission) {
      this.state.statusMessage = 'Permission not granted for this domain.';
      return null;
    }

    const adapter = this.adapterRegistry.getAdapterForUrl(url);
    if (!adapter) {
      this.state.statusMessage = 'No active adapter available for this domain.';
      return null;
    }

    const metadata = adapter.extract(doc, url);
    this.state.detectedMetadata = metadata;
    if (!metadata) {
      this.state.statusMessage = 'No title detected on current page.';
    } else {
      this.state.statusMessage = `Detected: ${metadata.title}`;
    }
    return metadata;
  }

  async sendDetectedTitle(): Promise<{ success: boolean; message: string }> {
    if (!this.state.detectedMetadata) {
      return { success: false, message: 'No metadata detected to send.' };
    }
    return await this.handshakeClient.sendMetadata(this.state.detectedMetadata);
  }
}

/**
 * UI Renderer and bootstrap wiring.
 */
export function updatePopupUI(doc: Document, controller: PopupController): void {
  const state = controller.getState();

  const permBadge = doc.getElementById('permission-badge');
  const handshakeBadge = doc.getElementById('handshake-badge');
  const btnPerm = doc.getElementById('btn-request-permission') as HTMLButtonElement | null;
  const btnDetect = doc.getElementById('btn-detect-title') as HTMLButtonElement | null;
  const btnSend = doc.getElementById('btn-send-app') as HTMLButtonElement | null;
  const metaContainer = doc.getElementById('metadata-container');
  const metaTitle = doc.getElementById('meta-title');
  const metaDetails = doc.getElementById('meta-details');

  if (permBadge) {
    if (state.hasHostPermission) {
      permBadge.textContent = 'Granted';
      permBadge.className = 'badge badge-ok';
    } else {
      permBadge.textContent = 'Not Granted';
      permBadge.className = 'badge badge-no';
    }
  }

  if (handshakeBadge) {
    if (state.isHandshakeActive) {
      handshakeBadge.textContent = 'Connected';
      handshakeBadge.className = 'badge badge-ok';
    } else {
      handshakeBadge.textContent = 'Disconnected';
      handshakeBadge.className = 'badge badge-no';
    }
  }

  if (btnPerm) {
    btnPerm.textContent = state.hasHostPermission
      ? 'Revoke Permission For This Site'
      : 'Grant Permission For This Site';
  }

  if (btnDetect) {
    btnDetect.disabled = !state.hasHostPermission;
  }

  if (btnSend) {
    btnSend.disabled = !state.detectedMetadata || !state.isHandshakeActive;
  }

  if (metaContainer && metaTitle && metaDetails) {
    if (state.detectedMetadata) {
      metaContainer.style.display = 'block';
      metaTitle.textContent = state.detectedMetadata.title;
      const parts = [
        state.detectedMetadata.contentType.toUpperCase(),
        state.detectedMetadata.year ? `(${state.detectedMetadata.year})` : null,
        state.detectedMetadata.season && state.detectedMetadata.episode
          ? `S${state.detectedMetadata.season}:E${state.detectedMetadata.episode}`
          : null,
        state.detectedMetadata.playbackProgress
          ? `${state.detectedMetadata.playbackProgress.progressPercent}%`
          : null,
      ].filter(Boolean);
      metaDetails.textContent = parts.join(' • ');
    } else {
      metaContainer.style.display = 'none';
    }
  }
}

/**
 * Bootstrap popup application inside browser environment.
 */
export async function bootstrapPopup(
  doc: Document = document,
  controller?: PopupController
): Promise<PopupController> {
  let activeController = controller;

  if (!activeController) {
    const handshakeClient = new HandshakeClient({
      localAppOrigin: 'http://localhost:3001',
    });
    const permissionManager = new PermissionManager();
    const adapterRegistry = new AdapterRegistry();

    activeController = new PopupController({
      handshakeClient,
      permissionManager,
      adapterRegistry,
    });
  }

  // Determine current active URL
  let currentUrl = 'https://mock-streaming.local/';
  if (typeof chrome !== 'undefined' && chrome.tabs?.query) {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs[0]?.url) {
      currentUrl = tabs[0].url;
    }
  }

  await activeController.init(currentUrl);
  updatePopupUI(doc, activeController);

  // Wire buttons
  const btnPerm = doc.getElementById('btn-request-permission');
  const btnDetect = doc.getElementById('btn-detect-title');
  const btnSend = doc.getElementById('btn-send-app');

  btnPerm?.addEventListener('click', async () => {
    const state = activeController!.getState();
    if (state.hasHostPermission) {
      await activeController!.revokeOptIn(currentUrl);
    } else {
      await activeController!.requestOptIn(currentUrl);
    }
    updatePopupUI(doc, activeController!);
  });

  btnDetect?.addEventListener('click', async () => {
    try {
      const parsed = new URL(currentUrl);
      // In browser runtime, extraction can query active tab or inspect current DOM
      await activeController!.detectAndInspect(doc, parsed);
    } catch {
      // no-op
    }
    updatePopupUI(doc, activeController!);
  });

  btnSend?.addEventListener('click', async () => {
    await activeController!.sendDetectedTitle();
    updatePopupUI(doc, activeController!);
  });

  return activeController;
}

// Auto-run bootstrap when script runs in extension popup window
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      bootstrapPopup(document).catch(() => {});
    });
  } else {
    bootstrapPopup(document).catch(() => {});
  }
}
