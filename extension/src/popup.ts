import { HandshakeClient } from './handshake.js';
import { PermissionManager } from './permissions.js';
import { AdapterRegistry } from './adapters/index.js';
import { DetectedMetadata } from './types.js';
import { extractMetadataFromTabDocument } from './content_extractor.js';
import { LOCAL_APP_ORIGIN } from './config.js';

export interface PopupState {
  currentUrl: string | null;
  activeTabId: number | null;
  isSupportedSite: boolean;
  hasHostPermission: boolean;
  isAdapterEnabled: boolean;
  isHandshakeActive: boolean;
  detectedMetadata: DetectedMetadata | null;
  statusMessage: string | null;
  hasPendingHandshake: boolean;
}

export type TabScriptExecutor = (
  tabId: number,
  urlStr: string
) => Promise<DetectedMetadata | null>;

export class PopupController {
  private handshakeClient: HandshakeClient;
  private permissionManager: PermissionManager;
  private adapterRegistry: AdapterRegistry;
  private scriptExecutor?: TabScriptExecutor;
  private state: PopupState = {
    currentUrl: null,
    activeTabId: null,
    isSupportedSite: false,
    hasHostPermission: false,
    isAdapterEnabled: false,
    isHandshakeActive: false,
    detectedMetadata: null,
    statusMessage: null,
    hasPendingHandshake: false,
  };

  constructor(options: {
    handshakeClient: HandshakeClient;
    permissionManager: PermissionManager;
    adapterRegistry: AdapterRegistry;
    scriptExecutor?: TabScriptExecutor;
  }) {
    this.handshakeClient = options.handshakeClient;
    this.permissionManager = options.permissionManager;
    this.adapterRegistry = options.adapterRegistry;
    this.scriptExecutor = options.scriptExecutor;
  }

  getState(): PopupState {
    return { ...this.state };
  }

  setStatusMessage(message: string): void {
    this.state.statusMessage = message;
  }

  async init(currentUrl: string | null, activeTabId: number | null = null): Promise<void> {
    this.state.currentUrl = currentUrl;
    this.state.activeTabId = activeTabId;
    this.state.isSupportedSite = false;
    const token = await this.handshakeClient.getStoredToken();
    this.state.isHandshakeActive = !!token;
    this.state.hasPendingHandshake = await this.handshakeClient.hasPendingHandshake();
    if (!currentUrl) {
      this.state.hasHostPermission = false;
      this.state.isAdapterEnabled = false;
      this.state.statusMessage = 'Could not read the active tab URL. Reopen the extension on a supported site.';
      return;
    }
    const perm = await this.permissionManager.checkHostPermission(currentUrl);
    this.state.hasHostPermission = perm.hasPermission;

    try {
      const parsedUrl = new URL(currentUrl);
      const adapter = this.adapterRegistry.getAllAdapters().find((a) => a.canHandle(parsedUrl));
      if (adapter) {
        this.state.isSupportedSite = true;
        if (this.state.hasHostPermission) this.adapterRegistry.enableAdapter(adapter.id);
        else this.adapterRegistry.disableAdapter(adapter.id);
        this.state.isAdapterEnabled = this.adapterRegistry.isAdapterEnabled(adapter.id);
      } else {
        this.state.statusMessage = 'This site is not supported by an installed adapter.';
      }
    } catch {
      // invalid URL
    }
  }

  async requestOptIn(currentUrl: string): Promise<boolean> {
    const parsedUrl = new URL(currentUrl);
    const adapter = this.adapterRegistry.getAllAdapters().find((a) => a.canHandle(parsedUrl));
    if (!adapter) {
      this.state.isSupportedSite = false;
      this.state.statusMessage = 'This site is not supported by an installed adapter.';
      return false;
    }
    this.state.isSupportedSite = true;
    const granted = await this.permissionManager.requestHostPermission(currentUrl);
    this.state.hasHostPermission = granted;

    if (granted) {
      try {
        this.adapterRegistry.enableAdapter(adapter.id);
        this.state.isAdapterEnabled = true;
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
      this.state.detectedMetadata = null;
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
      const localOrigin = this.handshakeClient.getLocalAppOrigin();
      const hasLocalHostPermission = (await this.permissionManager.checkHostPermission(`${localOrigin}/`)).hasPermission;
      if (!hasLocalHostPermission) {
        const granted = await this.permissionManager.requestHostPermission(`${localOrigin}/`);
        if (!granted) {
          this.state.statusMessage = 'Local app connection was denied because localhost access was not granted.';
          return false;
        }
      }

      if (await this.handshakeClient.hasPendingHandshake()) {
        await this.handshakeClient.completeHandshake(['title:send']);
        this.state.hasPendingHandshake = false;
        this.state.isHandshakeActive = true;
        this.state.statusMessage = 'Connected to the local app.';
        return true;
      }

      const authorizationUrl = await this.handshakeClient.beginHandshake();
      this.state.hasPendingHandshake = true;
      if (typeof chrome === 'undefined' || !chrome.tabs?.create) {
        this.state.statusMessage = `Open this local authorization page, approve access, then click Connect again: ${authorizationUrl}`;
        return false;
      }
      await chrome.tabs.create({ url: authorizationUrl });
      this.state.statusMessage = 'Approve access in the local app tab, then reopen the extension and click Finish app connection.';
      return false;
    } catch (err: any) {
      this.state.statusMessage = `Handshake failed: ${err.message || err}`;
      return false;
    }
  }

  async disconnectApp(): Promise<void> {
    await this.handshakeClient.revokeToken();
    await this.permissionManager.revokeHostPermission(`${this.handshakeClient.getLocalAppOrigin()}/`);
    this.state.isHandshakeActive = false;
    this.state.statusMessage = 'Disconnected from the local app.';
  }

  /**
   * Performs explicit on-demand title detection inside the active streaming tab.
   * Guarantees that metadata is extracted from the active streaming document, NOT the popup DOM.
   */
  async detectActiveTabTitle(
    tabDocFallback?: Document
  ): Promise<DetectedMetadata | null> {
    if (!this.state.currentUrl) {
      this.state.statusMessage = 'No active tab URL detected.';
      return null;
    }

    let parsedUrl: URL;
    try {
      parsedUrl = new URL(this.state.currentUrl);
    } catch {
      this.state.statusMessage = 'Invalid active tab URL.';
      return null;
    }
    const supportedAdapter = this.adapterRegistry.getAllAdapters().find((adapter) => adapter.canHandle(parsedUrl));
    this.state.isSupportedSite = !!supportedAdapter;
    if (!supportedAdapter) {
      this.state.hasHostPermission = false;
      this.state.isAdapterEnabled = false;
      this.state.detectedMetadata = null;
      this.state.statusMessage = 'This site is not supported by an installed adapter.';
      return null;
    }

    const permission = await this.permissionManager.checkHostPermission(this.state.currentUrl);
    this.state.hasHostPermission = permission.hasPermission;
    if (!permission.hasPermission) {
      this.state.isAdapterEnabled = false;
      this.state.statusMessage = 'Permission not granted for this domain.';
      return null;
    }

    const adapter = this.adapterRegistry.getAdapterForUrl(parsedUrl);
    if (!adapter) {
      this.state.statusMessage = 'No active adapter available for this domain.';
      return null;
    }

    let metadata: DetectedMetadata | null = null;

    // Execute extraction in active tab context
    if (this.scriptExecutor && this.state.activeTabId != null) {
      metadata = await this.scriptExecutor(this.state.activeTabId, this.state.currentUrl);
    } else if (typeof chrome !== 'undefined' && chrome.scripting && this.state.activeTabId != null) {
      // In-browser execution using chrome.scripting.executeScript
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: this.state.activeTabId },
          func: (urlStr: string) => {
            const titleEl = document.querySelector('[data-testid="media-title"], .media-title, h1');
            const rawTitle = titleEl?.textContent?.trim();
            if (!rawTitle) return null;

            const typeMeta = document.querySelector('meta[name="media-type"]')?.getAttribute('content');
            let contentType: 'movie' | 'tv' | 'unknown' = 'unknown';
            if (typeMeta === 'movie' || urlStr.includes('/movie/')) contentType = 'movie';
            else if (typeMeta === 'tv' || typeMeta === 'episode' || urlStr.includes('/tv/')) contentType = 'tv';

            const yearText = document.querySelector('[data-testid="media-year"], .media-year')?.textContent;
            let year: number | null = null;
            if (yearText) {
              const m = yearText.match(/\b(19\d{2}|20\d{2})\b/);
              if (m) year = parseInt(m[1], 10);
            }

            let season: number | null = null;
            let episode: number | null = null;
            let episodeTitle: string | null = null;
            const seEl = document.querySelector('[data-testid="season-episode"], .season-episode');
            if (seEl?.textContent) {
              const seMatch = seEl.textContent.match(/S(\d+)\s*:\s*E(\d+)/i) ||
                              seEl.textContent.match(/Season\s+(\d+)\s+Episode\s+(\d+)/i);
              if (seMatch) {
                season = parseInt(seMatch[1], 10);
                episode = parseInt(seMatch[2], 10);
                if (contentType === 'unknown') contentType = 'tv';
              }
            }

            const epTitleEl = document.querySelector('[data-testid="episode-title"], .episode-title');
            if (epTitleEl?.textContent?.trim()) {
              episodeTitle = epTitleEl.textContent.trim();
              if (contentType === 'unknown') contentType = 'tv';
            }

            let playbackProgress = null;
            const video = document.querySelector('video') as HTMLMediaElement | null;
            if (video && typeof video.currentTime === 'number' && typeof video.duration === 'number' && video.duration > 0) {
              const duration = Math.round(video.duration);
              const currentTime = Math.round(video.currentTime);
              const progressPercent = Math.min(100, Math.max(0, Math.round((currentTime / duration) * 100)));
              playbackProgress = { currentTime, duration, progressPercent };
            }

            return {
              title: rawTitle,
              contentType,
              year,
              season,
              episode,
              episodeTitle,
              playbackProgress,
              sourceUrl: urlStr,
              siteAdapterId: 'mock-streaming-adapter',
              detectedAt: new Date().toISOString(),
            };
          },
          args: [this.state.currentUrl],
        });

        if (results && results[0] && results[0].result) {
          metadata = results[0].result as DetectedMetadata;
        }
      } catch (err: any) {
        this.state.statusMessage = `Failed executing script in tab: ${err.message || err}`;
        return null;
      }
    } else if (tabDocFallback) {
      // Test environment or fallback: explicit target document passed in
      metadata = extractMetadataFromTabDocument(tabDocFallback, this.state.currentUrl, [adapter]);
    }

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
    try {
      return await this.handshakeClient.sendMetadata(this.state.detectedMetadata);
    } catch (error) {
      this.state.isHandshakeActive = !!(await this.handshakeClient.getStoredToken());
      throw error;
    }
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
  const statusMessage = doc.getElementById('status-message');
  const btnConnect = doc.getElementById('btn-connect-app') as HTMLButtonElement | null;

  if (statusMessage) statusMessage.textContent = state.statusMessage || '';

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
    btnPerm.disabled = !state.isSupportedSite;
    btnPerm.textContent = !state.isSupportedSite
      ? 'Unsupported Site'
      : state.hasHostPermission
        ? 'Revoke Permission For This Site'
        : 'Grant Permission For This Site';
  }

  if (btnDetect) {
    btnDetect.disabled = !state.isSupportedSite || !state.hasHostPermission;
  }

  if (btnSend) {
    btnSend.disabled = !state.detectedMetadata || !state.isHandshakeActive;
  }
  if (btnConnect) {
    btnConnect.textContent = state.hasPendingHandshake ? 'Finish app connection' :
      state.isHandshakeActive ? 'Disconnect local app' : 'Connect to local app';
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
  controller?: PopupController,
  activeTabDocumentFallback?: Document,
  activeTabOverride?: { id: number | null; url: string | null }
): Promise<PopupController> {
  let activeController = controller;

  if (!activeController) {
    const handshakeClient = new HandshakeClient({
      localAppOrigin: LOCAL_APP_ORIGIN,
    });
    const permissionManager = new PermissionManager();
    const adapterRegistry = new AdapterRegistry();

    activeController = new PopupController({
      handshakeClient,
      permissionManager,
      adapterRegistry,
    });
  }

  // Determine current active URL & tab ID
  let currentUrl: string | null = null;
  let activeTabId: number | null = null;
  if (activeTabOverride) {
    currentUrl = activeTabOverride.url;
    activeTabId = activeTabOverride.id;
  } else if (typeof chrome !== 'undefined' && chrome.tabs?.query) {
    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tabs[0]?.url) {
        currentUrl = tabs[0].url;
        activeTabId = tabs[0].id ?? null;
      }
    } catch {
      // Extension pages opened as ordinary tabs may have no active browser window.
      // Keep the popup usable for app connection, but fail closed for site access.
      currentUrl = null;
      activeTabId = null;
    }
  }

  await activeController.init(currentUrl, activeTabId);
  updatePopupUI(doc, activeController);

  // Wire buttons
  const btnPerm = doc.getElementById('btn-request-permission');
  const btnDetect = doc.getElementById('btn-detect-title');
  const btnSend = doc.getElementById('btn-send-app');
  const btnConnect = doc.getElementById('btn-connect-app');

  const currentTabStillActive = async (): Promise<boolean> => {
    if (activeTabId == null || typeof chrome === 'undefined' || !chrome.tabs?.query) return true;
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    return tabs[0]?.id === activeTabId;
  };

  btnPerm?.addEventListener('click', async () => {
    const state = activeController!.getState();
    try {
      if (!currentUrl) {
        activeController!.setStatusMessage('Could not read the active tab URL. Reopen the extension on a supported site.');
      } else if (state.hasHostPermission) {
        await activeController!.revokeOptIn(currentUrl);
      } else {
        await activeController!.requestOptIn(currentUrl);
      }
    } catch (error: any) {
      activeController!.setStatusMessage(`Permission update failed: ${error?.message || error}`);
    }
    updatePopupUI(doc, activeController!);
  });

  btnDetect?.addEventListener('click', async () => {
    // Explicit on-demand extraction targeting the active tab (never inspecting popup DOM)
    try {
      if (!(await currentTabStillActive())) {
        activeController!.setStatusMessage('The active tab changed. Reopen the extension on the intended site.');
      } else {
        await activeController!.detectActiveTabTitle(activeTabDocumentFallback);
      }
    } catch (error: any) {
      activeController!.setStatusMessage(`Title detection failed: ${error?.message || error}`);
    }
    updatePopupUI(doc, activeController!);
  });

  btnSend?.addEventListener('click', async () => {
    try {
      const result = await activeController!.sendDetectedTitle();
      activeController!.setStatusMessage(result.message);
    } catch (error: any) {
      activeController!.setStatusMessage(`Sending metadata failed: ${error?.message || error}`);
    }
    updatePopupUI(doc, activeController!);
  });

  btnConnect?.addEventListener('click', async () => {
    const state = activeController!.getState();
    if (state.isHandshakeActive) {
      await activeController!.disconnectApp();
    } else {
      await activeController!.connectApp();
    }
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
