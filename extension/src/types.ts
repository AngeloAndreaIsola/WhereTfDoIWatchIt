/**
 * Extension type contracts for WT-049.
 */

export type ContentType = 'movie' | 'tv' | 'unknown';

export interface PlaybackProgress {
  currentTime: number;
  duration: number;
  progressPercent: number;
}

export interface DetectedMetadata {
  title: string;
  contentType: ContentType;
  year?: number | null;
  season?: number | null;
  episode?: number | null;
  episodeTitle?: string | null;
  playbackProgress?: PlaybackProgress | null;
  sourceUrl: string;
  siteAdapterId: string;
  detectedAt: string;
}

export interface SiteAdapter {
  id: string;
  name: string;
  domains: string[];
  canHandle(url: URL): boolean;
  extract(document: Document, url: URL): DetectedMetadata | null;
}

export type CapabilityScope = 'title:send' | 'watchlist:read' | 'metadata:resolve';

export interface CapabilityToken {
  token: string;
  issuedAt: number;
  expiresAt: number;
  scopes: CapabilityScope[];
  origin: string;
}

export interface HandshakeInitRequest {
  clientNonce: string;
}

export interface HandshakeInitResponse {
  challengeId: string;
  serverNonce: string;
  authorizationUrl: string;
}

export interface HandshakeExchangeRequest {
  challengeId: string;
  clientNonce: string;
  serverNonce: string;
  requestedScopes: CapabilityScope[];
}

export interface HandshakeExchangeResponse {
  capabilityToken: CapabilityToken;
  state: 'granted' | 'denied';
}

export interface HostPermissionStatus {
  hasPermission: boolean;
  domain: string;
}
