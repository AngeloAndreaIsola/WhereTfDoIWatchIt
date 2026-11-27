import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import SubscriptionsPage from "@/app/subscriptions/page";
import type { MarketSelection, ProviderItem } from "@/lib/api";
import { catalogPageCache, leavingSoonCache, watchlistPageCache } from "@/lib/navigationCache";

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

const pageCaches = [catalogPageCache, watchlistPageCache, leavingSoonCache];

function cacheDependentPages() {
  for (const cache of pageCaches) {
    cache.set({
      key: "before",
      items: [],
      page: 1,
      paginationInfo: { totalPages: 1, hasNext: false, hasPrevious: false },
      filters: {},
      scrollY: 0,
      cachedAt: Date.now(),
    });
  }
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
    cacheDependentPages();
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
      for (const cache of pageCaches) expect(cache.get("before")).toBeUndefined();
    });
    expect(calls.some((c) => c.url.includes("/refresh/"))).toBe(false);
  });

  it("updates the displayed market selection after legacy revocation", async () => {
    let providers = catalogue("NL");
    cacheDependentPages();
    vi.stubGlobal("fetch", vi.fn(async (_url: unknown, init?: { method?: string }) => {
      if (init?.method === "POST") {
        const result = { is_subscribed: false, access_mode: "none" };
        providers = [{
          ...providers[0],
          ...result,
          access_mode: "none",
          selection: {
            holding: "no", plan_state: "unspecified", plan_id: null, channels: [], revision: 2,
          },
        }];
        return { ok: true, json: async () => result };
      }
      return { ok: true, json: async () => providers };
    }));

    render(<SubscriptionsPage />);
    expect(await screen.findByLabelText("Plan for Aster in NL")).toHaveValue("base");
    fireEvent.change(screen.getByLabelText("Access mode for Aster"), { target: { value: "none" } });

    await waitFor(() => expect(screen.getByLabelText("Plan for Aster in NL")).not.toHaveValue("base"));
    for (const cache of pageCaches) expect(cache.get("before")).toBeUndefined();
  });

  it("keeps the current market when the previous market response arrives last", async () => {
    let catalogueReads = 0;
    let finishPreviousMarket: (providers: ProviderItem[]) => void = () => {};
    const previousMarket = new Promise<ProviderItem[]>((resolve) => {
      finishPreviousMarket = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(async (url: unknown, init?: { method?: string }) => {
      if (init?.method === "PUT") return { ok: true, json: async () => catalogue("NL")[0].selection };
      catalogueReads += 1;
      const country = String(url).includes("billing_country=DE") ? "DE" : "NL";
      return {
        ok: true,
        json: () => country === "NL" && catalogueReads > 1
          ? previousMarket
          : Promise.resolve(catalogue(country)),
      };
    }));

    render(<SubscriptionsPage />);
    expect(await screen.findByLabelText("Plan for Aster in NL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));
    await waitFor(() => expect(catalogueReads).toBe(2));
    fireEvent.change(screen.getByLabelText("Billing country"), { target: { value: "DE" } });
    expect(await screen.findByLabelText("Plan for Aster in DE")).toBeInTheDocument();
    await act(async () => finishPreviousMarket(catalogue("NL")));
    expect(screen.getByLabelText("Plan for Aster in DE")).toBeInTheDocument();
    expect(screen.queryByLabelText("Plan for Aster in NL")).toBeNull();
  });

  it("preserves retired plan ID when saving channel modification over HTTP (U3 regression)", async () => {
    const retiredSelection: MarketSelection = {
      holding: "yes",
      plan_state: "unsupported",
      plan_id: "legacy_plus",
      plan_display_name: "Legacy Plus",
      channels: [],
      revision: 2,
    };
    const initialProvider: ProviderItem = {
      ...catalogue("NL")[0],
      selection: retiredSelection,
    };
    let putBody: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: unknown, init?: { method?: string; body?: string }) => {
        if (init?.method === "PUT") {
          putBody = JSON.parse(init?.body || "{}");
          return { ok: true, status: 200, json: async () => ({ ...retiredSelection, revision: 3 }) };
        }
        return { ok: true, status: 200, json: async () => [initialProvider] };
      })
    );

    render(<SubscriptionsPage />);
    expect(await screen.findByTestId("retired-plan-notice")).toBeInTheDocument();
    expect(screen.getByTestId("retired-plan-notice")).toHaveTextContent("Legacy Plus");

    fireEvent.change(screen.getByLabelText("Channel Orbit via Aster"), { target: { value: "yes" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    await waitFor(() => {
      expect(putBody).not.toBeNull();
    });
    expect(putBody).toEqual({
      holding: "yes",
      plan_state: "unsupported",
      plan_id: "legacy_plus",
      channels: [{ package_id: "orbit", holding: "yes" }],
      revision: 2,
    });
  });

  it("preserves unsaved conflict draft across background catalogue re-fetches over HTTP (U2 regression)", async () => {
    const newerSelection: MarketSelection = {
      holding: "yes",
      plan_state: "selected",
      plan_id: "ads",
      channels: [],
      revision: 5,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: unknown, init?: { method?: string }) => {
        if (init?.method === "PUT") {
          return {
            ok: false,
            status: 409,
            json: async () => ({
              code: "revision_conflict",
              selection: newerSelection,
            }),
          };
        }
        return { ok: true, status: 200, json: async () => catalogue("NL") };
      })
    );

    render(<SubscriptionsPage />);
    expect(await screen.findByLabelText("Plan for Aster in NL")).toHaveValue("base");

    // Draft a change to ads
    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "ads" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    expect(await screen.findByText(/not saved: this selection changed elsewhere/i)).toBeInTheDocument();
    expect((screen.getByLabelText("Plan for Aster in NL") as HTMLSelectElement).value).toBe("ads");
  });

  it("renders market-specific add-on caveats over HTTP (U5 regression)", async () => {
    const providerWithCaveat: ProviderItem = {
      ...catalogue("NL")[0],
      channel_options: [
        {
          package_id: "orbit",
          parent_provider: "aster",
          country: "NL",
          display_name: "Orbit",
          caveat: "Requires separate billing agreement with host operator.",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => [providerWithCaveat] }))
    );

    render(<SubscriptionsPage />);
    expect(await screen.findByText("Requires separate billing agreement with host operator.")).toBeInTheDocument();
    expect(screen.getByTestId("channel-caveat-orbit")).toHaveTextContent(
      "Requires separate billing agreement with host operator."
    );
  });
});
