import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchProviderMarketCatalogue,
  MarketSelectionConflictError,
  saveMarketSelection,
  type MarketSelection,
} from "@/lib/api";

const saved: MarketSelection = {
  holding: "yes",
  plan_state: "selected",
  plan_id: "base",
  channels: [],
  revision: 4,
};

const input = {
  holding: "yes" as const,
  plan_state: "selected" as const,
  plan_id: "base",
  channels: [{ package_id: "orbit", holding: "yes" as const }],
  revision: 3,
};

describe("issue103 market selection API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the reviewed catalogue for an explicit billing market", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchProviderMarketCatalogue("NL");

    expect(fetchMock).toHaveBeenCalledWith("/api/providers/?billing_country=NL");
  });

  it("writes exact keys with an optimistic revision", async () => {
    const fetchMock = vi.fn(
      async (_url: unknown, _init?: RequestInit) => ({ ok: true, status: 200, json: async () => saved })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(saveMarketSelection("aster", "NL", input)).resolves.toEqual(saved);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/subscriptions/aster/markets/NL/");
    expect(init!.method).toBe("PUT");
    expect(JSON.parse(String(init!.body))).toEqual(input);
  });

  it("treats HTTP 409 as a conflict carrying the freshly saved selection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 409,
        json: async () => ({ code: "revision_conflict", selection: saved }),
      }))
    );

    const error = await saveMarketSelection("aster", "NL", input).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(MarketSelectionConflictError);
    expect((error as MarketSelectionConflictError).selection).toEqual(saved);
  });

  it("surfaces a validation failure instead of a silent overwrite", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ plan_id: ["Plan is unavailable for this provider and market."] }),
      }))
    );

    await expect(saveMarketSelection("aster", "NL", input)).rejects.toThrow();
  });
});
