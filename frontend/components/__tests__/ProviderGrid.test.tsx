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

describe("ProviderGrid Component", () => {
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
});
