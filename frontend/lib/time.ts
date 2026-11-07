/**
 * Human-readable "how long ago" label for the source cards.
 *
 * Cards show when a source last *ran*, and an absolute timestamp in every
 * card is unreadable at a glance — "3h ago" scans, "02/10/2026, 20:47" does
 * not. `null`/unparseable means the collector has never run for this
 * provider, which is a real state (queued-but-not-yet-run, or a source with
 * no run history) and gets its own wording instead of an empty string.
 */
export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "Never run";

  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Never run";

  const diffMs = Date.now() - then;
  if (diffMs < 0) return formatAbsolute(then);

  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";

  const hours = Math.floor(minutes / 60);
  if (hours < 1) return `${minutes}m ago`;

  const days = Math.floor(hours / 24);
  if (days < 1) return `${hours}h ago`;

  const months = Math.floor(days / 30);
  if (months < 1) return `${days}d ago`;

  return formatAbsolute(then);
}

/**
 * "Last seen" label for stale/unverified availability evidence. Unlike
 * {@link formatRelativeTime}, a missing timestamp means "date unknown".
 */
export function formatLastSeen(iso?: string | null): string {
  if (!iso) return "Last seen: date unknown";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "Last seen: date unknown";
  return `Last seen ${formatAbsolute(then)}`;
}

function formatAbsolute(timestamp: number): string {
  return new Date(timestamp).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
