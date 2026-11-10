import { HostPermissionStatus } from './types.js';

export interface PermissionsAPI {
  contains(permissions: chrome.permissions.Permissions): Promise<boolean>;
  request(permissions: chrome.permissions.Permissions): Promise<boolean>;
  remove(permissions: chrome.permissions.Permissions): Promise<boolean>;
}

/**
 * Manage optional host permissions for streaming sites.
 * Ensures no ambient or automatic all-sites permissions are taken.
 */
export class PermissionManager {
  private api: PermissionsAPI;

  constructor(api?: PermissionsAPI) {
    if (api) {
      this.api = api;
    } else if (typeof chrome !== 'undefined' && chrome.permissions) {
      this.api = {
        contains: (perms) => chrome.permissions.contains(perms),
        request: (perms) => chrome.permissions.request(perms),
        remove: (perms) => chrome.permissions.remove(perms),
      };
    } else {
      // In-memory fallback / mock for offline testing
      const grantedOrigins = new Set<string>();
      this.api = {
        contains: async (perms) => {
          if (!perms.origins) return true;
          return perms.origins.every((origin) => grantedOrigins.has(origin));
        },
        request: async (perms) => {
          if (!perms.origins) return true;
          perms.origins.forEach((origin) => grantedOrigins.add(origin));
          return true;
        },
        remove: async (perms) => {
          if (!perms.origins) return true;
          perms.origins.forEach((origin) => grantedOrigins.delete(origin));
          return true;
        },
      };
    }
  }

  /**
   * Check if host permission has been granted for an origin pattern or URL.
   */
  async checkHostPermission(url: string): Promise<HostPermissionStatus> {
    try {
      const parsedUrl = new URL(url);
      const originPattern = `${parsedUrl.protocol}//${parsedUrl.hostname}/*`;
      const hasPermission = await this.api.contains({
        origins: [originPattern],
      });
      return {
        hasPermission,
        domain: parsedUrl.hostname,
      };
    } catch {
      return {
        hasPermission: false,
        domain: 'invalid',
      };
    }
  }

  /**
   * Explicitly request host permission for a site after user interaction.
   */
  async requestHostPermission(url: string): Promise<boolean> {
    try {
      const parsedUrl = new URL(url);
      const originPattern = `${parsedUrl.protocol}//${parsedUrl.hostname}/*`;
      return await this.api.request({
        origins: [originPattern],
      });
    } catch {
      return false;
    }
  }

  /**
   * Explicitly revoke host permission for a site.
   */
  async revokeHostPermission(url: string): Promise<boolean> {
    try {
      const parsedUrl = new URL(url);
      const originPattern = `${parsedUrl.protocol}//${parsedUrl.hostname}/*`;
      return await this.api.remove({
        origins: [originPattern],
      });
    } catch {
      return false;
    }
  }
}
