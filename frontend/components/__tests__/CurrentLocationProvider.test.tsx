import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { CurrentLocationProvider, useCurrentLocation } from "../CurrentLocationProvider";

function TestComponent() {
  const { currentCountry, loading, error } = useCurrentLocation();
  return (
    <div>
      <span data-testid="country">{currentCountry}</span>
      <span data-testid="loading">{loading ? "loading" : "loaded"}</span>
      <span data-testid="error">{error || "none"}</span>
    </div>
  );
}

describe("CurrentLocationProvider", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses persisted current_country when available", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/api/preferences/") {
        return { ok: true, status: 200, json: async () => ({ current_country: "IT" }) } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CurrentLocationProvider><TestComponent /></CurrentLocationProvider>);
    await waitFor(() => expect(screen.getByTestId("country").textContent).toBe("IT"));
    expect(screen.getByTestId("loading").textContent).toBe("loaded");
  });

  it("falls back to geo-ip detection when no persisted country exists", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/preferences/" && (!init || init.method !== "PUT")) {
        return { ok: true, status: 200, json: async () => ({}) } as Response;
      }
      if (url === "/api/geo/ip/") {
        return { ok: true, status: 200, json: async () => ({ country: "IN" }) } as Response;
      }
      if (url === "/api/preferences/" && init?.method === "PUT") {
        return { ok: true, status: 200, json: async () => ({ current_country: "IN" }) } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CurrentLocationProvider><TestComponent /></CurrentLocationProvider>);
    await waitFor(() => expect(screen.getByTestId("country").textContent).toBe("IN"));
  });
});
