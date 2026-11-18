import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { AppNav } from "@/components/AppNav";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";

vi.mock("next/navigation", () => ({
  usePathname: () => "/catalog",
}));

function renderNav() {
  return render(
    <LocaleProvider initialLocale="en">
      <AppNav />
    </LocaleProvider>
  );
}

describe("AppNav mobile menu", () => {
  it("renders the menu toggle button collapsed by default", () => {
    renderNav();
    const button = screen.getByRole("button", { name: "Open menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "mobile-nav-menu");
    expect(screen.getAllByRole("navigation", { name: "Main navigation" })).toHaveLength(1);
  });

  it("opens the menu and exposes all navigation links plus the locale switcher", async () => {
    const user = userEvent.setup();
    renderNav();

    await user.click(screen.getByRole("button", { name: "Open menu" }));

    expect(screen.getByRole("button", { name: "Close menu" })).toHaveAttribute("aria-expanded", "true");
    const menus = screen.getAllByRole("navigation", { name: "Main navigation" });
    expect(menus).toHaveLength(2);
    const mobileMenu = document.getElementById("mobile-nav-menu");
    expect(mobileMenu).not.toBeNull();
    for (const label of ["Home", "Catalog", "Watchlist", "Watched", "Subscriptions", "Candidate Review"]) {
      expect(mobileMenu).toHaveTextContent(label);
    }
    expect(mobileMenu?.querySelector('[data-testid="locale-switcher"]')).not.toBeNull();
  });

  it("closes the menu on Escape and returns focus to the toggle", async () => {
    const user = userEvent.setup();
    renderNav();

    const openButton = screen.getByRole("button", { name: "Open menu" });
    await user.click(openButton);
    expect(document.getElementById("mobile-nav-menu")).not.toBeNull();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(document.getElementById("mobile-nav-menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveFocus();
  });

  it("closes the menu when clicking outside", async () => {
    const user = userEvent.setup();
    renderNav();

    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(document.getElementById("mobile-nav-menu")).not.toBeNull();

    fireEvent.mouseDown(document.body);

    expect(document.getElementById("mobile-nav-menu")).toBeNull();
  });

  it("toggles the menu closed when the button is clicked again", async () => {
    const user = userEvent.setup();
    renderNav();

    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(screen.getByRole("button", { name: "Close menu" }));

    expect(document.getElementById("mobile-nav-menu")).toBeNull();
  });

  it("switches between the inline nav and the menu at the lg breakpoint", () => {
    renderNav();
    const inlineNav = screen.getAllByRole("navigation", { name: "Main navigation" })[0];
    expect(inlineNav).toHaveClass("hidden", "lg:flex");
    expect(inlineNav.className).not.toContain("sm:flex");
    const toggleWrapper = screen.getByRole("button", { name: "Open menu" }).parentElement;
    expect(toggleWrapper).toHaveClass("lg:hidden");
    expect(toggleWrapper?.className).not.toContain("sm:hidden");
  });
});
