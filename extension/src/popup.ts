import { HandshakeClient } from './handshake.js';
import { PermissionManager } from './permissions.js';
import { AdapterRegistry } from './adapters/index.js';
import { DetectedMetadata } from './types.js';
import { extractMetadataFromTabDocument } from './content_extractor.js';

export interface PopupState {
  currentUrl: string | null;
  activeTabId: number | null;
  hasHostPermission: boolean;
  isAdapterEnabled: boolean;
  isHandshakeActive: boolean;
  detectedMetadata: DetectedMetadata | null;
  statusMessage: string | null;
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

  async init(currentUrl: string, activeTabId: number | null = null): Promise<void> {
    this.state.currentUrl = currentUrl;
    this.state.activeTabId = activeTabId;
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

    if (!this.state.hasHostPermission) {
      this.state.statusMessage = 'Permission not granted for this domain.';
      return null;
    }

    let parsedUrl: URL;
    try {
      parsedUrl = new URL(this.state.currentUrl);
    } catch {
      this.state.statusMessage = 'Invalid active tab URL.';
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
  controller?: PopupController,
  activeTabDocumentFallback?: Document
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

  // Determine current active URL & tab ID
  let currentUrl = 'https://mock-streaming.local/';
  let activeTabId: number | null = null;
  if (typeof chrome !== 'undefined' && chrome.tabs?.query) {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs[0]?.url) {
      currentUrl = tabs[0].url;
      activeTabId = tabs[0].id ?? null;
    }
  }

  await activeController.init(currentUrl, activeTabId);
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
    // Explicit on-demand extraction targeting the active tab (never inspecting popup DOM)
    await activeController!.detectActiveTabTitle(activeTabDocumentFallback);
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
