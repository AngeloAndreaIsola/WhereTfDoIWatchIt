/**
 * Ambiguous-review payload stash.
 *
 * The review page used to receive every ambiguous item through
 * `/watchlist/ambiguous?items=<json>`. Each item carries its TMDb candidates,
 * so a real import (tens of ambiguous rows) produces a query string far beyond
 * the server's 16 kB header limit — the navigation then fails with HTTP 431.
 *
 * Items are therefore handed to the review page through sessionStorage
 * (same-tab, session-scoped). The `?items=` query parameter still works and is
 * tried first, so old links keep functioning.
 */
const STORAGE_KEY = "where-tf-do-i-watch-it.ambiguousReview.items";

export function stashAmbiguousItems<T>(items: T[]): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}

export function readStashedAmbiguousItems<T>(): T[] | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : null;
  } catch {
    return null;
  }
}
