import { SiteAdapter } from '../types.js';
import { MockStreamingAdapter } from './mock_adapter.js';

/**
 * Registry of available site adapters.
 * All streaming-site adapters are disabled by default until user opt-in.
 */
export class AdapterRegistry {
  private adapters = new Map<string, SiteAdapter>();
  private enabledAdapterIds = new Set<string>();

  constructor() {
    this.register(new MockStreamingAdapter());
  }

  register(adapter: SiteAdapter): void {
    this.adapters.set(adapter.id, adapter);
  }

  enableAdapter(adapterId: string): void {
    if (this.adapters.has(adapterId)) {
      this.enabledAdapterIds.add(adapterId);
    }
  }

  disableAdapter(adapterId: string): void {
    this.enabledAdapterIds.delete(adapterId);
  }

  isAdapterEnabled(adapterId: string): boolean {
    return this.enabledAdapterIds.has(adapterId);
  }

  getAdapterForUrl(url: URL): SiteAdapter | null {
    for (const adapter of this.adapters.values()) {
      if (this.enabledAdapterIds.has(adapter.id) && adapter.canHandle(url)) {
        return adapter;
      }
    }
    return null;
  }

  getAllAdapters(): SiteAdapter[] {
    return Array.from(this.adapters.values());
  }
}
