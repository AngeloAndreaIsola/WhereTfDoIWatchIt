import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ProviderGrid, { ProviderItem } from "../ProviderGrid";
import ProviderIcon from "../ProviderIcon";

const mockProviders: ProviderItem[] = [
  {
    id: 1,
    slug: "disney",
    name: "Disney+",
    category: "official",
    is_major: true,
    is_enabled: true,
    icon_name: "disney",
    is_subscribed: false,
  },
  {
    id: 2,
    slug: "hulu",
    name: "Hulu",
    category: "official",
    is_major: true,
    is_enabled: true,
    icon_name: "hulu",
    is_subscribed: true,
  },
];

const refreshableProviders: ProviderItem[] = [
  {
    id: 14,
    slug: "raiplay",
    name: "RaiPlay",
    category: "bvod",
    is_major: false,
    is_enabled: true,
    icon_name: "raiplay",
    is_subscribed: true,
    refresh_kind: "catalogue",
    source_slug: "raiplay",
    last_run: null,
    last_run_error: "",
    available_items: 857,
    refreshing: false,
  },
  {
    id: 46,
    slug: "kisskh",
    name: "KissKH",
    category: "scraper",
    is_major: false,
    is_enabled: true,
    icon_name: "kisskh",
    is_subscribed: false,
    refresh_kind: "scraper",
    source_slug: null,
    last_run: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    last_run_error: "",
    available_items: 61,
    refreshing: false,
  },
];

describe("ProviderGrid Component", () => {
  it("shows pending discovery separately from available titles", () => {
    const provider = {
      ...refreshableProviders[1],
      available_items: 0,
      catalogue_discovery: {
        inventory_count: 12,
        pending_classification_count: 12,
        last_successful_sync: new Date(Date.now() - 60_000).toISOString(),
        last_scanned_pages: { movies: 8, "tv-shows": 3 },
        coverage_status: "PARTIAL",
        error: "HTTP 451",
      },
    };
    render(<ProviderGrid providers={[provider]} onToggle={vi.fn()} />);

    expect(screen.getByText("0 active mappings")).toBeInTheDocument();
    expect(screen.getByText("Pending classification 12 · 12 discovered")).toBeInTheDocument();
    expect(screen.getByText("Checkpoint movies 8 · tv-shows 3")).toBeInTheDocument();
    expect(screen.getByText("Coverage partial")).toBeInTheDocument();
    expect(screen.getByText("HTTP 451")).toBeInTheDocument();
  });

  it("renders major providers grid with active/inactive states", () => {
    render(<ProviderGrid providers={mockProviders} onToggle={vi.fn()} />);

    expect(screen.getByText("Disney+")).toBeInTheDocument();
    expect(screen.getByText("Hulu")).toBeInTheDocument();

    const disneyButton = screen.getByRole("button", { name: /Disney\+/i });
    const huluButton = screen.getByRole("button", { name: /Hulu/i });

    expect(disneyButton).toHaveAttribute("aria-pressed", "false");
    expect(huluButton).toHaveAttribute("aria-pressed", "true");
  });

  it("calls onToggle when provider button is clicked", async () => {
    const handleToggle = vi.fn().mockResolvedValue(undefined);
    render(<ProviderGrid providers={mockProviders} onToggle={handleToggle} />);

    const disneyButton = screen.getByRole("button", { name: /Disney\+/i });
    fireEvent.click(disneyButton);

    await waitFor(() => {
      expect(handleToggle).toHaveBeenCalledWith("disney");
    });
  });

  it("renders custom SVG icons for known provider slugs", () => {
    const { container } = render(<ProviderIcon slug="disney" name="Disney+" />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("shows the batched card meta: available items and last run", () => {
    render(<ProviderGrid providers={refreshableProviders} onToggle={vi.fn()} />);

    expect(screen.getByText("857 available")).toBeInTheDocument();
    expect(screen.getByText("61 active mappings")).toBeInTheDocument();
    // Never ran vs. a relative age from the batched status.
    expect(screen.getByText("Last run Never run")).toBeInTheDocument();
    expect(screen.getByText("Last run 10d ago")).toBeInTheDocument();
  });

  it("refreshes exactly one source and never toggles it", async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const onRefresh = vi.fn().mockResolvedValue(null);
    render(
      <ProviderGrid
        providers={refreshableProviders}
        onToggle={onToggle}
        onRefresh={onRefresh}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh RaiPlay" }));

    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledWith("raiplay");
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("hides the refresh control when no collector is registered", () => {
    render(<ProviderGrid providers={mockProviders} onToggle={vi.fn()} onRefresh={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Refresh Disney+" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Refresh Hulu" })).toBeNull();
    // Exactly the two cards: no hidden controls, cards still activatable.
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("keeps the refresh control disabled while the source is refreshing", () => {
    const onRefresh = vi.fn();
    const inFlight: ProviderItem[] = [
      { ...refreshableProviders[0], refreshing: true },
    ];
    render(
      <ProviderGrid
        providers={inFlight}
        onToggle={vi.fn()}
        onRefresh={onRefresh}
        refreshPending={new Set(["raiplay"])}
      />
    );

    const refreshButton = screen.getByRole("button", { name: "Refresh RaiPlay" }) as HTMLButtonElement;
    expect(refreshButton).toBeDisabled();
    expect(screen.getByText("Refreshing…")).toBeInTheDocument();

    fireEvent.click(refreshButton);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it("surfaces a per-source refresh failure on that card only", () => {
    render(
      <ProviderGrid
        providers={refreshableProviders}
        onToggle={vi.fn()}
        onRefresh={vi.fn()}
        refreshErrors={{ raiplay: "No collector is registered" }}
      />
    );

    expect(screen.getByText("No collector is registered")).toBeInTheDocument();
    // The sibling card keeps its own (last successful run) state.
    expect(screen.getByText("Last run 10d ago")).toBeInTheDocument();
  });

  it("says Last run failed instead of Never run when an error precedes any success", () => {
    render(
      <ProviderGrid
        providers={[
          {
            ...refreshableProviders[0],
            last_run: null,
            last_run_error: "DataError: value too long",
          },
        ]}
        onToggle={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    expect(screen.getByText("Last run failed")).toBeInTheDocument();
    expect(screen.queryByText("Last run Never run")).toBeNull();
    expect(screen.getByText("DataError: value too long")).toBeInTheDocument();
  });

  it("offers remove only where the caller provides it, without toggling", async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const onRemove = vi.fn();
    render(
      <ProviderGrid
        providers={[refreshableProviders[1]]}
        onToggle={onToggle}
        onRemove={onRemove}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove KissKH" }));

    await waitFor(() => {
      expect(onRemove).toHaveBeenCalledWith("kisskh");
    });
    expect(onToggle).not.toHaveBeenCalled();
  });
});

