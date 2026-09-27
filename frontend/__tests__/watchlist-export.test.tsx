import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WatchlistImportPage from "@/app/watchlist/import/page";

const routerPush = vi.fn();

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    onClick,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
    onClick?: (event: React.MouseEvent<HTMLAnchorElement>) => void;
    [key: string]: unknown;
  }) =>
    React.createElement(
      "a",
      {
        href,
        ...props,
        onClick: (event: React.MouseEvent<HTMLAnchorElement>) => {
          onClick?.(event);
          if (!event.defaultPrevented) {
            event.preventDefault();
            routerPush(href);
          }
        },
      },
      children
    ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPush,
    replace: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => "/watchlist/import",
  useSearchParams: () => new URLSearchParams(""),
}));

type FetchInit = { fail?: boolean };

function mockExportFetch(init: FetchInit = {}) {
  const fetchMock = vi.fn().mockImplementation(async () => ({
    ok: !init.fail,
    status: init.fail ? 500 : 200,
    json: async () => ({ error: "Export unavailable" }),
    blob: async () =>
      new Blob(["title,year,letterboxd_uri,status\n"], { type: "text/csv" }),
    headers: { get: () => null },
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("watchlist export controls", () => {
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    routerPush.mockReset();
    Object.assign(URL, {
      createObjectURL: vi.fn(() => "blob:export"),
      revokeObjectURL: vi.fn(),
    });
    clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
  });

  afterEach(() => {
    clickSpy.mockRestore();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("Export Watchlist downloads wheretfdoiwatchit-watchlist.csv", async () => {
    const fetchMock = mockExportFetch();
    const user = userEvent.setup();
    render(React.createElement(WatchlistImportPage));

    await user.click(screen.getByRole("button", { name: "Export Watchlist" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/export/watchlist/")
    );
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("wheretfdoiwatchit-watchlist.csv");
    expect(anchor.href).toBe("blob:export");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:export");
  });

  it("Export Watched downloads wheretfdoiwatchit-watched.csv", async () => {
    const fetchMock = mockExportFetch();
    const user = userEvent.setup();
    render(React.createElement(WatchlistImportPage));

    await user.click(screen.getByRole("button", { name: "Export Watched" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/export/watched/")
    );
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("wheretfdoiwatchit-watched.csv");
  });

  it("Export Both downloads wheretfdoiwatchit-export.csv from /api/export/", async () => {
    const fetchMock = mockExportFetch();
    const user = userEvent.setup();
    render(React.createElement(WatchlistImportPage));

    await user.click(screen.getByRole("button", { name: "Export Both" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/export/"));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("wheretfdoiwatchit-export.csv");
  });

  it("shows the backend error when the export fails", async () => {
    mockExportFetch({ fail: true });
    const user = userEvent.setup();
    render(React.createElement(WatchlistImportPage));

    await user.click(screen.getByRole("button", { name: "Export Watchlist" }));

    expect(await screen.findByText("Export unavailable")).toBeInTheDocument();
    expect(clickSpy).not.toHaveBeenCalled();
  });
});
