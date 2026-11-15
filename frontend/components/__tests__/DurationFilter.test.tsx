import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  DurationFilter,
  formatDurationMinutes,
  getDurationSummary,
} from "@/components/DurationFilter";
import { FilterBar, DEFAULT_FILTER_STATE, FilterState } from "@/components/FilterBar";
import { CurrentLocationProvider } from "@/components/CurrentLocationProvider";
import { ViewerPreferencesProvider } from "@/components/ViewerPreferencesProvider";

describe("formatDurationMinutes", () => {
  it("formats minutes into readable format", () => {
    expect(formatDurationMinutes(25)).toBe("25m");
    expect(formatDurationMinutes(60)).toBe("1h");
    expect(formatDurationMinutes(105)).toBe("1h 45m");
    expect(formatDurationMinutes(180)).toBe("3h");
    expect(formatDurationMinutes(null)).toBe("");
    expect(formatDurationMinutes(undefined)).toBe("");
    expect(formatDurationMinutes(-5)).toBe("");
  });
});

describe("getDurationSummary", () => {
  it("recognizes presets and custom summaries", () => {
    expect(getDurationSummary("", "")).toBe("Any duration");
    expect(getDurationSummary("", "30")).toBe("Under 30 min");
    expect(getDurationSummary("31", "60")).toBe("30–60 min");
    expect(getDurationSummary("61", "120")).toBe("1–2 hours");
    expect(getDurationSummary("121", "180")).toBe("2–3 hours");
    expect(getDurationSummary("181", "")).toBe("Over 3 hours");
    expect(getDurationSummary("45", "90")).toBe("45m – 1h 30m");
    expect(getDurationSummary("45", "")).toBe("≥ 45m");
    expect(getDurationSummary("", "90")).toBe("≤ 1h 30m");
  });
});

describe("DurationFilter Component", () => {
  it("renders closed initially and toggles on click", () => {
    const onChange = vi.fn();
    render(
      <DurationFilter durationMin="" durationMax="" onChange={onChange} />
    );

    const toggleBtn = screen.getByTestId("duration-filter-toggle");
    expect(toggleBtn).toBeDefined();
    expect(toggleBtn.textContent).toContain("Any duration");

    // Open dropdown
    fireEvent.click(toggleBtn);
    expect(screen.getByTestId("duration-filter-dropdown")).toBeDefined();

    // Select preset Under 30 min
    const under30Btn = screen.getByText("Under 30 min");
    fireEvent.click(under30Btn);
    expect(onChange).toHaveBeenCalledWith("", "30");
  });

  it("handles custom duration min and max input changes", () => {
    const onChange = vi.fn();
    render(
      <DurationFilter durationMin="30" durationMax="60" onChange={onChange} />
    );

    const toggleBtn = screen.getByTestId("duration-filter-toggle");
    fireEvent.click(toggleBtn);

    const minInput = screen.getByPlaceholderText("Min");
    const maxInput = screen.getByPlaceholderText("Max");

    fireEvent.change(minInput, { target: { value: "45" } });
    expect(onChange).toHaveBeenCalledWith("45", "60");

    fireEvent.change(maxInput, { target: { value: "90" } });
    expect(onChange).toHaveBeenCalledWith("30", "90");
  });

  it("resets filter when reset clicked in dropdown", () => {
    const onChange = vi.fn();
    render(
      <DurationFilter durationMin="61" durationMax="120" onChange={onChange} />
    );

    const toggleBtn = screen.getByTestId("duration-filter-toggle");
    fireEvent.click(toggleBtn);

    const resetBtn = screen.getByText("Reset");
    fireEvent.click(resetBtn);
    expect(onChange).toHaveBeenCalledWith("", "");
  });
});

describe("FilterBar with Duration and Clear All", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (url === "/api/genres/") {
          return { ok: true, status: 200, json: async () => ({ results: [] }) };
        }
        if (url === "/api/providers/") {
          return { ok: true, status: 200, json: async () => [] };
        }
        if (url === "/api/preferences/") {
          return { ok: true, status: 200, json: async () => ({ current_country: "US" }) };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      })
    );
  });

  const renderWithProviders = (filters: FilterState, onChange = vi.fn()) => {
    return render(
      <CurrentLocationProvider>
        <ViewerPreferencesProvider>
          <FilterBar filters={filters} onChange={onChange} />
        </ViewerPreferencesProvider>
      </CurrentLocationProvider>
    );
  };

  it("shows Clear all filters when active filters exist and resets on click", async () => {
    const onChange = vi.fn();
    const activeFilters: FilterState = {
      ...DEFAULT_FILTER_STATE,
      duration_min: "61",
      duration_max: "120",
      genre: "28",
    };

    renderWithProviders(activeFilters, onChange);

    const clearBtn = await screen.findByTestId("clear-all-filters-btn");
    expect(clearBtn).toBeDefined();

    fireEvent.click(clearBtn);
    expect(onChange).toHaveBeenCalledWith(DEFAULT_FILTER_STATE);
  });

  it("hides Clear all filters when default empty state", () => {
    renderWithProviders(DEFAULT_FILTER_STATE);
    expect(screen.queryByTestId("clear-all-filters-btn")).toBeNull();
  });
});
