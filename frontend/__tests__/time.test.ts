import { describe, it, expect, afterEach, vi } from "vitest";
import { formatRelativeTime } from "@/lib/time";

describe("formatRelativeTime", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("says a source has never run when there is no timestamp", () => {
    expect(formatRelativeTime(null)).toBe("Never run");
    expect(formatRelativeTime(undefined)).toBe("Never run");
    expect(formatRelativeTime("")).toBe("Never run");
  });

  it("says never run for an unparseable timestamp instead of showing Invalid Date", () => {
    expect(formatRelativeTime("not-a-date")).toBe("Never run");
  });

  it("uses plain wording for a run that just happened", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T12:00:00Z"));
    expect(formatRelativeTime("2026-10-03T11:59:40Z")).toBe("just now");
  });

  it("abbreviates minutes, hours and days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T12:00:00Z"));
    expect(formatRelativeTime("2026-10-03T11:30:00Z")).toBe("30m ago");
    expect(formatRelativeTime("2026-10-03T07:00:00Z")).toBe("5h ago");
    expect(formatRelativeTime("2026-10-01T12:00:00Z")).toBe("2d ago");
  });

  it("falls back to a readable date beyond a month", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T12:00:00Z"));
    const label = formatRelativeTime("2026-07-01T12:00:00Z");
    expect(label).not.toMatch(/ago/);
    expect(label).toContain("2026");
  });

  it("never renders a negative age for a timestamp in the future", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T12:00:00Z"));
    expect(formatRelativeTime("2026-10-04T12:00:00Z")).toContain("2026");
  });
});
