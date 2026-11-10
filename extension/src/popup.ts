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
