import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(async () => {
  cleanup();
  vi.restoreAllMocks();
  try {
    const cacheModule = await import("./lib/navigationCache");
    cacheModule.invalidateCatalogCache();
    cacheModule.invalidateWatchlistCache();
  } catch {
    // navigation cache may not be loaded by every test file
  }
});
