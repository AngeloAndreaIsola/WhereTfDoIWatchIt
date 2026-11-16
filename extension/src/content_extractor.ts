import { MockStreamingAdapter } from './adapters/mock_adapter.js';
import { DetectedMetadata, SiteAdapter } from './types.js';

/**
 * Pure extraction runner executed within an active streaming tab context.
 * Receives the target document and URL, finds an appropriate adapter,
 * and extracts serializable DetectedMetadata.
 */
export function extractMetadataFromTabDocument(
  doc: Document,
  urlStr: string,
  adapters: SiteAdapter[] = [new MockStreamingAdapter()]
): DetectedMetadata | null {
  try {
    const url = new URL(urlStr);
    for (const adapter of adapters) {
      if (adapter.canHandle(url)) {
        return adapter.extract(doc, url);
      }
    }
  } catch {
    return null;
  }
  return null;
}
