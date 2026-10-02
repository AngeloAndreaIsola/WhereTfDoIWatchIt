import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
