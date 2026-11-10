import { describe, it, expect, beforeEach } from 'vitest';
import { MockStreamingAdapter } from '../src/adapters/mock_adapter.js';
import { AdapterRegistry } from '../src/adapters/index.js';
import {
  MOCK_MOVIE_PAGE,
  MOCK_SERIES_EPISODE_PAGE,
  MOCK_EMPTY_PAGE,
  MOCK_MALFORMED_PAGE,
} from './fixtures/mock_pages.js';

describe('Title and Metadata Detection with SiteAdapter', () => {
  let adapter: MockStreamingAdapter;
  let registry: AdapterRegistry;

  beforeEach(() => {
    adapter = new MockStreamingAdapter();
    registry = new AdapterRegistry();
  });

  it('adapter ignores non-supported domains', () => {
    const url = new URL('https://other-site.com/watch/123');
    expect(adapter.canHandle(url)).toBe(false);

    const doc = new DOMParser().parseFromString(MOCK_MOVIE_PAGE, 'text/html');
    expect(adapter.extract(doc, url)).toBeNull();
  });

  it('detects movie title, year, and playback progress on mock streaming page', () => {
    const url = new URL('https://mock-streaming.local/movie/inception-2010');
    expect(adapter.canHandle(url)).toBe(true);

    const doc = new DOMParser().parseFromString(MOCK_MOVIE_PAGE, 'text/html');
    const video = doc.querySelector('video')!;
    Object.defineProperty(video, 'currentTime', { value: 3600, writable: true });
    Object.defineProperty(video, 'duration', { value: 8880, writable: true });

    const metadata = adapter.extract(doc, url);
    expect(metadata).not.toBeNull();
    expect(metadata?.title).toBe('Inception');
    expect(metadata?.contentType).toBe('movie');
    expect(metadata?.year).toBe(2010);
    expect(metadata?.season).toBeNull();
    expect(metadata?.episode).toBeNull();
    expect(metadata?.playbackProgress).toEqual({
      currentTime: 3600,
      duration: 8880,
      progressPercent: 41,
    });
    expect(metadata?.sourceUrl).toBe('https://mock-streaming.local/movie/inception-2010');
  });

  it('detects tv episode metadata (season, episode, episode title)', () => {
    const url = new URL('https://mock-streaming.local/tv/breaking-bad/s05e14');
    const doc = new DOMParser().parseFromString(MOCK_SERIES_EPISODE_PAGE, 'text/html');
    const video = doc.querySelector('video')!;
    Object.defineProperty(video, 'currentTime', { value: 1200, writable: true });
    Object.defineProperty(video, 'duration', { value: 2820, writable: true });

    const metadata = adapter.extract(doc, url);
    expect(metadata).not.toBeNull();
    expect(metadata?.title).toBe('Breaking Bad');
    expect(metadata?.contentType).toBe('tv');
    expect(metadata?.year).toBe(2013);
    expect(metadata?.season).toBe(5);
    expect(metadata?.episode).toBe(14);
    expect(metadata?.episodeTitle).toBe('Ozymandias');
    expect(metadata?.playbackProgress).toEqual({
      currentTime: 1200,
      duration: 2820,
      progressPercent: 43,
    });
  });

  it('handles empty / non-watch page gracefully without errors or phantom titles', () => {
    const url = new URL('https://mock-streaming.local/catalog');
    const doc = new DOMParser().parseFromString(MOCK_EMPTY_PAGE, 'text/html');
    const metadata = adapter.extract(doc, url);
    expect(metadata).toBeNull();
  });

  it('handles malformed / whitespace-only titles safely', () => {
    const url = new URL('https://mock-streaming.local/watch/error');
    const doc = new DOMParser().parseFromString(MOCK_MALFORMED_PAGE, 'text/html');
    const metadata = adapter.extract(doc, url);
    expect(metadata).toBeNull();
  });

  it('registry disables adapters by default and requires explicit opt-in', () => {
    const url = new URL('https://mock-streaming.local/movie/inception');
    // Initially not enabled
    expect(registry.getAdapterForUrl(url)).toBeNull();

    // Explicitly enable
    registry.enableAdapter('mock-streaming-adapter');
    expect(registry.getAdapterForUrl(url)).not.toBeNull();

    // Revoke / disable
    registry.disableAdapter('mock-streaming-adapter');
    expect(registry.getAdapterForUrl(url)).toBeNull();
  });
});
