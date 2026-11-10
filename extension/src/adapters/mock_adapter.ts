import { DetectedMetadata, SiteAdapter } from '../types.js';

/**
 * Mock streaming site adapter for offline testing and verification.
 * Target domain: mock-streaming.local
 *
 * Demonstrates:
 * - Detecting title, content type (movie or tv episode)
 * - Extracting season and episode info
 * - Detecting playback progress (video element currentTime / duration)
 * - Safe handling of missing or partial elements
 */
export class MockStreamingAdapter implements SiteAdapter {
  id = 'mock-streaming-adapter';
  name = 'Mock Streaming Service';
  domains = ['mock-streaming.local'];

  canHandle(url: URL): boolean {
    return this.domains.includes(url.hostname);
  }

  extract(document: Document, url: URL): DetectedMetadata | null {
    if (!this.canHandle(url)) {
      return null;
    }

    const titleEl = document.querySelector('[data-testid="media-title"], .media-title, h1');
    const rawTitle = titleEl?.textContent?.trim();
    if (!rawTitle) {
      return null;
    }

    // Check content type
    const typeMeta = document.querySelector('meta[name="media-type"]')?.getAttribute('content');
    let contentType: 'movie' | 'tv' | 'unknown' = 'unknown';
    if (typeMeta === 'movie' || url.pathname.includes('/movie/')) {
      contentType = 'movie';
    } else if (typeMeta === 'tv' || typeMeta === 'episode' || url.pathname.includes('/tv/') || url.pathname.includes('/episode/')) {
      contentType = 'tv';
    }

    // Extract year
    const yearText = document.querySelector('[data-testid="media-year"], .media-year')?.textContent;
    let year: number | null = null;
    if (yearText) {
      const match = yearText.match(/\b(19\d{2}|20\d{2})\b/);
      if (match) {
        year = parseInt(match[1], 10);
      }
    }

    // Extract episode info if TV
    let season: number | null = null;
    let episode: number | null = null;
    let episodeTitle: string | null = null;

    const seasonEpisodeEl = document.querySelector('[data-testid="season-episode"], .season-episode');
    if (seasonEpisodeEl?.textContent) {
      const seMatch = seasonEpisodeEl.textContent.match(/S(\d+)\s*:\s*E(\d+)/i) ||
                      seasonEpisodeEl.textContent.match(/Season\s+(\d+)\s+Episode\s+(\d+)/i);
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

    // Extract playback progress from HTML5 video element
    let playbackProgress = null;
    const video = document.querySelector('video') as HTMLMediaElement | null;
    if (video && typeof video.currentTime === 'number' && typeof video.duration === 'number' && video.duration > 0) {
      const duration = Math.round(video.duration);
      const currentTime = Math.round(video.currentTime);
      const progressPercent = Math.min(100, Math.max(0, Math.round((currentTime / duration) * 100)));
      playbackProgress = {
        currentTime,
        duration,
        progressPercent,
      };
    }

    return {
      title: rawTitle,
      contentType,
      year,
      season,
      episode,
      episodeTitle,
      playbackProgress,
      sourceUrl: url.toString(),
      siteAdapterId: this.id,
      detectedAt: new Date().toISOString(),
    };
  }
}
