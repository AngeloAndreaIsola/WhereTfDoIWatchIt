import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import SubscriptionsPage from "@/app/subscriptions/page";
import type { ProviderItem } from "@/lib/api";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
    [key: string]: unknown;
  }) => React.createElement("a", { href, ...props }, children),
}));

function catalogue(country: string): ProviderItem[] {
  return [
    {
      id: 1,
      slug: "aster",
      name: "Aster",
      category: "official",
      is_major: true,
      is_enabled: true,
      icon_name: "aster",
      is_subscribed: true,
      billing_country: country,
      plan_options: [
        { plan_id: "base", display_name: "Base", class: "base", billing_country: country, caveat: "" },
        { plan_id: "ads", display_name: "With ads", class: "paid_ads", billing_country: country, caveat: "" },
      ],
      channel_options: [
        { package_id: "orbit", parent_provider: "aster", country, display_name: "Orbit", caveat: "" },
      ],
      unlisted_plan_option: {
        plan_id: null,
        plan_state: "unsupported",
        display_name: "I don't know / my plan isn't listed",
      },
      selection: { holding: "yes", plan_state: "selected", plan_id: "base", channels: [], revision: 1 },
    },
  ];
}

describe("Subscriptions page market selection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    try {
      window.localStorage.removeItem("wtdw.billingCountry");
    } catch {
      // Storage is optional in jsdom.
    }
  });

  it("loads and re-loads the catalogue by billing country, with plan controls", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown) => {
        const href = String(url);
        urls.push(href);
        const country = href.includes("billing_country=DE") ? "DE" : "NL";
        return { ok: true, status: 200, json: async () => catalogue(country) };
      })
    );

    render(<SubscriptionsPage />);

    expect(await screen.findByText("Aster")).toBeInTheDocument();
    expect(urls[0]).toBe("/api/providers/?billing_country=NL");
    expect(screen.getByLabelText("Plan for Aster in NL")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Billing country"), { target: { value: "DE" } });

    await waitFor(() => expect(urls).toContain("/api/providers/?billing_country=DE"));
    await waitFor(() => expect(screen.getByLabelText("Plan for Aster in DE")).toBeInTheDocument());
  });

  it("writes a plan selection without refreshing any availability source", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: { method?: string }) => {
        const href = String(url);
        const method = init?.method ?? "GET";
        calls.push({ url: href, method });
        return { ok: true, status: 200, json: async () => catalogue("NL") };
      })
    );

    render(<SubscriptionsPage />);
    expect(await screen.findByText("Aster")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "ads" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    await waitFor(() => {
      expect(calls).toContainEqual({
        url: "/api/subscriptions/aster/markets/NL/",
        method: "PUT",
      });
    });
    expect(calls.some((c) => c.url.includes("/refresh/"))).toBe(false);
  });
});
