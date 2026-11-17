import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SubscriptionsPage from "@/app/subscriptions/page";

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

interface ProviderSeed {
  id: number;
  slug: string;
  name: string;
  category: string;
  is_major?: boolean;
  is_enabled?: boolean;
  icon_name?: string;
  domain?: string;
  is_subscribed?: boolean;
  scraped_content_count?: number;
  has_scraper?: boolean;
  last_scraped_at?: string | null;
  refresh_kind?: "catalogue" | "scraper" | null;
  source_slug?: string | null;
  last_run?: string | null;
  last_run_error?: string | null;
  available_items?: number;
  refreshing?: boolean;
}

function provider(seed: ProviderSeed) {
  return {
    is_major: false,
    is_enabled: true,
    icon_name: seed.slug,
    domain: "",
    is_subscribed: false,
    scraped_content_count: 0,
    has_scraper: false,
    last_scraped_at: null,
    // Batched card status defaults: no registered collector, never run.
    refresh_kind: null,
    source_slug: null,
    last_run: null,
    last_run_error: "",
    available_items: 0,
    refreshing: false,
    ...seed,
  };
}

const seedProviders: ProviderSeed[] = [
  provider({ id: 1, slug: "prime", name: "Prime Video", category: "official", is_major: true }),
  provider({ id: 14, slug: "raiplay", name: "RaiPlay", category: "bvod", domain: "raiplay.it" }),
  provider({
    id: 29,
    slug: "bbc_iplayer",
    name: "BBC iPlayer",
    category: "bvod",
    domain: "bbc.co.uk/iplayer",
  }),
  provider({ id: 42, slug: "nhk_world", name: "NHK WORLD-JAPAN", category: "bvod" }),
  provider({ id: 46, slug: "kisskh", name: "KissKH", category: "scraper", has_scraper: true }),
  // A scraper whose slug used to collide with a broadcaster alias: the old
  // PUBLIC_BROADCASTER_SLUGS allowlist silently hid it from this page.
  provider({ id: 47, slug: "bbc", name: "Custom BBC Mirror", category: "scraper" }),
];

function stubProvidersFetch(list: ProviderSeed[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown) => ({
      ok: true,
      status: 200,
      json: async () => (String(url).includes("/api/providers/") ? list : []),
    }))
  );
}

interface ApiCall {
  url: string;
  method: string;
}

const TEN_DAYS_AGO = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();

/**
 * Endpoint-aware fetch stub that records every call, so a test can prove one
 * card press hit *exactly* one source's refresh endpoint and nothing else.
 * `refreshError` makes that slug's refresh press fail like a 409/500.
 */
function stubApi(list: ProviderSeed[], refreshError?: { slug: string; error: string }) {
  const calls: ApiCall[] = [];
  const json = (body: unknown, ok = true, status = 200) => ({
    ok,
    status,
    json: async () => body,
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: unknown, init?: { method?: string }) => {
      const href = String(url);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url: href, method });

      if (href.endsWith("/refresh/")) {
        const slug = href.split("/api/providers/")[1]?.split("/")[0] ?? "";
        if (refreshError?.slug === slug) {
          return json({ error: refreshError.error }, false, 500);
        }
        return json({ provider: slug, kind: "catalogue", source: slug, queued: true });
      }
      if (href.endsWith("/toggle/")) {
        return json({ is_subscribed: true });
      }
      return json(list);
    })
  );
  return calls;
}

/** The square card for `name` (the button carrying the active state). */
function cardFor(name: string): HTMLElement | undefined {
  return screen
    .getAllByRole("button")
    .find((b) => b.hasAttribute("aria-pressed") && (b.textContent ?? "").includes(name));
}

describe("Subscriptions page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders each provider once, grouped by its category", async () => {
    stubProvidersFetch(seedProviders);
    render(<SubscriptionsPage />);

    // SVOD band
    expect(await screen.findByText("Prime Video")).toBeInTheDocument();

    // BVOD band: one card per broadcaster, no duplicate alias cards
    for (const name of ["RaiPlay", "BBC iPlayer", "NHK WORLD-JAPAN"]) {
      expect(screen.getAllByText(name)).toHaveLength(1);
    }

    // Imported sources list
    expect(screen.getByText("Imported Sources (2)")).toBeInTheDocument();
    expect(screen.getByText("KissKH")).toBeInTheDocument();
    expect(screen.getByText("Custom BBC Mirror")).toBeInTheDocument();
  });

  it("does not hide imported sources whose slug matches a broadcaster", async () => {
    stubProvidersFetch([
      provider({ id: 47, slug: "bbc", name: "Custom BBC Mirror", category: "scraper" }),
    ]);
    render(<SubscriptionsPage />);

    expect(await screen.findByText("Custom BBC Mirror")).toBeInTheDocument();
    expect(screen.getByText("Imported Sources (1)")).toBeInTheDocument();
  });

  it("shows an empty imported-sources message when nothing is imported", async () => {
    stubProvidersFetch([
      provider({ id: 14, slug: "raiplay", name: "RaiPlay", category: "bvod" }),
    ]);
    render(<SubscriptionsPage />);

    expect(await screen.findByText("RaiPlay")).toBeInTheDocument();
    expect(screen.getByText("No custom sources imported yet.")).toBeInTheDocument();
  });

  it("renders imported sources as activatable square cards with their meta and controls", async () => {
    stubApi([
      provider({
        id: 46,
        slug: "kisskh",
        name: "KissKH",
        category: "scraper",
        has_scraper: true,
        refresh_kind: "scraper",
        available_items: 61,
        last_run: TEN_DAYS_AGO,
      }),
    ]);
    render(<SubscriptionsPage />);

    const card = await waitFor(() => cardFor("KissKH"));
    expect(card).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("61 active mappings")).toBeInTheDocument();
    expect(screen.getByText("Last run 10d ago")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh KissKH" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove KissKH" })).toBeInTheDocument();
    // The old list-row controls are gone: cards own activation.
    expect(screen.queryByRole("button", { name: "Run Scraper" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Enable" })).toBeNull();
  });

  it("toggles a source by clicking its card", async () => {
    const calls = stubApi([
      provider({ id: 46, slug: "kisskh", name: "KissKH", category: "scraper", refresh_kind: "scraper" }),
    ]);
    render(<SubscriptionsPage />);

    const card = await waitFor(() => cardFor("KissKH"));
    fireEvent.click(card!);

    await waitFor(() => {
      expect(calls).toContainEqual({ url: "/api/providers/kisskh/toggle/", method: "POST" });
    });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("refreshes exactly one source from the card control and never toggles it", async () => {
    const calls = stubApi([
      provider({ id: 14, slug: "raiplay", name: "RaiPlay", category: "bvod", refresh_kind: "catalogue", source_slug: "raiplay" }),
      provider({ id: 46, slug: "kisskh", name: "KissKH", category: "scraper", refresh_kind: "scraper" }),
    ]);
    render(<SubscriptionsPage />);

    await waitFor(() => expect(cardFor("RaiPlay")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Refresh RaiPlay" }));

    await waitFor(() => {
      expect(calls).toContainEqual({
        url: "/api/providers/raiplay/refresh/",
        method: "POST",
      });
    });
    // One source, one press: no neighbour source, no subscription change.
    expect(calls.filter((c) => c.url.endsWith("/refresh/"))).toHaveLength(1);
    expect(calls.some((c) => c.url.includes("kisskh"))).toBe(false);
    expect(calls.some((c) => c.url.endsWith("/toggle/"))).toBe(false);
  });

  it("shows no refresh control for a provider without a registered collector", async () => {
    stubApi([
      provider({ id: 1, slug: "prime", name: "Prime Video", category: "official", is_major: true, refresh_kind: null }),
    ]);
    render(<SubscriptionsPage />);

    await waitFor(() => expect(cardFor("Prime Video")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Refresh Prime Video" })).toBeNull();
    // Only the activation control remains (plus the unrelated import form's submit).
    expect(screen.getAllByRole("button").filter((b) => b.hasAttribute("aria-pressed"))).toHaveLength(1);
  });

  it("reports a failed refresh on the card that pressed it", async () => {
    const calls = stubApi(
      [
        provider({ id: 14, slug: "raiplay", name: "RaiPlay", category: "bvod", refresh_kind: "catalogue", source_slug: "raiplay" }),
        provider({ id: 46, slug: "kisskh", name: "KissKH", category: "scraper", refresh_kind: "scraper" }),
      ],
      { slug: "raiplay", error: "No collector is registered for 'RaiPlay'" }
    );
    render(<SubscriptionsPage />);

    await waitFor(() => expect(cardFor("RaiPlay")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Refresh RaiPlay" }));

    expect(
      await screen.findByText("No collector is registered for 'RaiPlay'")
    ).toBeInTheDocument();
    // Failure stays local: no refresh press for the neighbour, no retry loop.
    expect(calls.filter((c) => c.url.endsWith("/refresh/"))).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Refresh KissKH" })).toBeInTheDocument();
  });

  it("shows the server-reported refreshing state without polling for it", async () => {
    stubApi([
      provider({
        id: 14,
        slug: "raiplay",
        name: "RaiPlay",
        category: "bvod",
        refresh_kind: "catalogue",
        source_slug: "raiplay",
        refreshing: true,
      }),
    ]);
    render(<SubscriptionsPage />);

    await waitFor(() => expect(cardFor("RaiPlay")).toBeTruthy());
    expect(screen.getByText("Refreshing…")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Refresh RaiPlay" }) as HTMLButtonElement
    ).toBeDisabled();
  });
});
