import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WatchlistPage from "@/app/watchlist/page";
import WatchlistImportPage from "@/app/watchlist/import/page";
import AmbiguousPage from "@/app/watchlist/ambiguous/page";

const routerPush = vi.fn();
const routerReplace = vi.fn();
let searchParamsString = "";

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
    replace: routerReplace,
    back: vi.fn(),
  }),
  usePathname: () => "/watchlist",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}));

type RouteResponse = { ok?: boolean; status?: number; body: unknown };

/** Route-matched fetch stub; first matching prefix wins, otherwise fallback. */
function mockRoutes(
  routes: Array<[string, RouteResponse]>,
  fallback: RouteResponse = { ok: true, status: 200, body: { results: [] } }
) {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    const match = routes.find(
      ([pattern]) => typeof url === "string" && url.startsWith(pattern)
    );
    const response = match ? match[1] : fallback;
    const ok =
      response.ok ??
      (response.status
        ? response.status >= 200 && response.status < 300
        : true);
    return {
      ok,
      status: response.status ?? 200,
      json: async () => response.body,
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const watchlistReportRoute: [string, RouteResponse] = [
  "/api/watchlist/report",
  {
    body: [
      {
        id: 1,
        movie: { id: 1, tmdb_id: 603, title: "The Matrix", year: 1999 },
      },
    ],
  },
];

const letterboxdSuccessRoute: [string, RouteResponse] = [
  "/api/importers/letterboxd-url/",
  {
    body: {
      source: "letterboxd_url",
      username: "someone",
      import_type: "watchlist",
      pages_fetched: 1,
      rows_found: 2,
      resolved: [{ movie: { tmdb_id: 603, title: "The Matrix" } }],
      ambiguous: [
        {
          title: "Dune",
          year: 1984,
          candidates: [{ tmdb_id: 841, title: "Dune", year: 1984 }],
        },
      ],
      skipped: [],
      watched_count: 0,
      ratings_count: 0,
      reviews_count: 0,
      diary_events_count: 0,
    },
  },
];

describe("watchlist import IA refactor", () => {
  beforeEach(() => {
    routerPush.mockReset();
    routerReplace.mockReset();
    searchParamsString = "";
  });

  describe("/watchlist", () => {
    it("no longer renders the large import sections", async () => {
      mockRoutes([watchlistReportRoute]);

      render(React.createElement(WatchlistPage));

      expect(await screen.findByText("The Matrix")).toBeInTheDocument();

      expect(
        screen.queryByRole("heading", { name: "Import from File" })
      ).toBeNull();
      expect(
        screen.queryByRole("heading", { name: "Import from a Letterboxd URL" })
      ).toBeNull();
      expect(
        screen.queryByRole("heading", { name: "Import from URL" })
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Export Watchlist" })
      ).toBeNull();
      expect(
        screen.queryByPlaceholderText("https://letterboxd.com/username/")
      ).toBeNull();
      expect(
        screen.queryByPlaceholderText(/interests\/saved\/list/)
      ).toBeNull();
      expect(document.getElementById("file-upload-input")).toBeNull();
    });

    it("renders a compact Import / Export entry point linking to /watchlist/import", async () => {
      mockRoutes([watchlistReportRoute]);

      render(React.createElement(WatchlistPage));

      await screen.findByText("The Matrix");

      expect(
        screen.getByRole("heading", { name: "Import / Export" })
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          "Import your watchlist from Letterboxd, Google, or CSV — or export a backup."
        )
      ).toBeInTheDocument();

      const importLink = screen.getByRole("link", { name: "Import / Export" });
      expect(importLink).toHaveAttribute("href", "/watchlist/import");
    });

    it("navigates to /watchlist/import in the same tab when Import / Export is clicked", async () => {
      mockRoutes([watchlistReportRoute]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistPage));

      await screen.findByText("The Matrix");
      await user.click(screen.getByRole("link", { name: "Import / Export" }));

      expect(routerPush).toHaveBeenCalledWith("/watchlist/import");
    });

    it("carries the current filter query string to the import page", async () => {
      searchParamsString = "q=matrix";
      mockRoutes([watchlistReportRoute]);

      render(React.createElement(WatchlistPage));

      await screen.findByText("The Matrix");

      expect(
        screen.getByRole("link", { name: "Import / Export" })
      ).toHaveAttribute("href", "/watchlist/import/?q=matrix");
    });
  });

  describe("/watchlist/import", () => {
    it("renders a Back to Watchlist link pointing at /watchlist", () => {
      mockRoutes([]);

      render(React.createElement(WatchlistImportPage));

      expect(
        screen.getByRole("link", { name: "← Back to Watchlist" })
      ).toHaveAttribute("href", "/watchlist");
    });

    it("preserves the watchlist query string in the back link", () => {
      searchParamsString = "q=matrix";
      mockRoutes([]);

      render(React.createElement(WatchlistImportPage));

      expect(
        screen.getByRole("link", { name: "← Back to Watchlist" })
      ).toHaveAttribute("href", "/watchlist/?q=matrix");
    });

    it("navigates back to /watchlist when the back link is clicked", async () => {
      mockRoutes([]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistImportPage));

      await user.click(
        screen.getByRole("link", { name: "← Back to Watchlist" })
      );

      expect(routerPush).toHaveBeenCalledWith("/watchlist");
    });

    it("renders the Letterboxd, Google Interests, and CSV import sections", () => {
      mockRoutes([]);

      render(React.createElement(WatchlistImportPage));

      // Group headings split importing from exporting
      expect(
        screen.getByRole("heading", { name: "Import", level: 2 })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "Export", level: 2 })
      ).toBeInTheDocument();

      // CSV / export file import
      expect(
        screen.getByRole("heading", { name: "Import from File" })
      ).toBeInTheDocument();
      expect(document.getElementById("file-upload-input")).not.toBeNull();
      expect(screen.getByText(/Supports \.csv, \.zip/)).toBeInTheDocument();

      // Letterboxd URL import
      expect(
        screen.getByRole("heading", { name: "Import from a Letterboxd URL" })
      ).toBeInTheDocument();
      expect(
        screen.getByPlaceholderText("https://letterboxd.com/username/")
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Import Watchlist" })
      ).toBeInTheDocument();

      // Google Interests import
      expect(
        screen.getByRole("heading", { name: "Import from URL" })
      ).toBeInTheDocument();
      expect(screen.getByText("Google Interests list URLs")).toBeInTheDocument();
      expect(
        screen.getByPlaceholderText(/interests\/saved\/list/)
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Import from URL" })
      ).toBeInTheDocument();
    });

    it("renders the Export section with watchlist, watched, and combined controls", () => {
      mockRoutes([]);

      render(React.createElement(WatchlistImportPage));

      expect(
        screen.getByRole("heading", { name: "Export", level: 2 })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "Watchlist", level: 3 })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "Watched", level: 3 })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Export Watchlist" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Export Watched" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Export Both" })
      ).toBeInTheDocument();
      expect(
        screen.getByText(/re-uploaded on this page/)
      ).toBeInTheDocument();
    });

    it("keeps the Letterboxd import handler wired to the importer API", async () => {
      const fetchMock = mockRoutes([letterboxdSuccessRoute]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistImportPage));

      await user.type(
        screen.getByPlaceholderText("https://letterboxd.com/username/"),
        "https://letterboxd.com/someone/"
      );
      await user.click(screen.getByRole("button", { name: "Import Watchlist" }));

      expect(
        await screen.findByText("Letterboxd Import Summary")
      ).toBeInTheDocument();
      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith(
          "/api/importers/letterboxd-url/",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({
              url: "https://letterboxd.com/someone/",
              type: "watchlist",
            }),
          })
        )
      );
    });

    it("keeps the Google Interests import handler wired to the importer API", async () => {
      const fetchMock = mockRoutes([
        [
          "/api/importers/google-interests/",
          { body: { resolved: [], ambiguous: [], skipped: [], watchlist_count: 0 } },
        ],
      ]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistImportPage));

      await user.type(
        screen.getByPlaceholderText(/interests\/saved\/list/),
        "https://www.google.com/interests/saved/list/abc"
      );
      await user.click(screen.getByRole("button", { name: "Import from URL" }));

      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith(
          "/api/importers/google-interests/",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({
              cookies: {},
              urls: ["https://www.google.com/interests/saved/list/abc"],
            }),
          })
        )
      );
      expect(await screen.findByText("Import Summary")).toBeInTheDocument();
    });

    it("keeps the CSV/file import handler wired to the watchlist API", async () => {
      const fetchMock = mockRoutes([
        watchlistReportRoute,
        ["/api/watchlist/", { body: { resolved: [], ambiguous: [], skipped: [] } }],
      ]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistImportPage));

      const input = document.getElementById(
        "file-upload-input"
      ) as HTMLInputElement;
      expect(input).not.toBeNull();
      fireEvent.change(input, {
        target: {
          files: [
            new File(["title,year\nDune,1984\n"], "watchlist.csv", {
              type: "text/csv",
            }),
          ],
        },
      });

      await user.click(screen.getByRole("button", { name: "Import File" }));

      await waitFor(() =>
        expect(fetchMock).toHaveBeenCalledWith(
          "/api/watchlist/",
          expect.objectContaining({ method: "POST", body: expect.any(FormData) })
        )
      );
      expect(await screen.findByText("Import Summary")).toBeInTheDocument();
    });

    it("still routes ambiguous matches to /watchlist/ambiguous", async () => {
      mockRoutes([letterboxdSuccessRoute]);

      const user = userEvent.setup();
      render(React.createElement(WatchlistImportPage));

      await user.type(
        screen.getByPlaceholderText("https://letterboxd.com/username/"),
        "https://letterboxd.com/someone/"
      );
      await user.click(screen.getByRole("button", { name: "Import Watchlist" }));

      const reviewLink = await screen.findByRole("link", {
        name: "Review ambiguous matches",
      });
      expect(reviewLink).toHaveAttribute("href", "/watchlist/ambiguous");

      await user.click(reviewLink);
      expect(routerPush).toHaveBeenCalledWith("/watchlist/ambiguous");
    });
  });
});

describe("typed import review", () => {
  it("submits the selected series when a movie shares its TMDb ID", async () => {
    searchParamsString = new URLSearchParams({
      items: JSON.stringify([{
        title: "Same Name",
        year: 2020,
        candidates: [
          { tmdb_id: 108, title: "Same Name", year: 2020, content_type: "movie" },
          { tmdb_id: 108, title: "Same Name", year: 2020, content_type: "series" },
        ],
      }]),
    }).toString();
    const fetchMock = mockRoutes([["/api/watchlist/resolve/", { body: { resolved_count: 1 } }]]);
    const user = userEvent.setup();

    render(<AmbiguousPage />);
    await user.click(screen.getByRole("radio", { name: /TV series/ }));
    expect(screen.getByRole("radio", { name: /Movie/ })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Confirm & Save Selections" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/watchlist/resolve/",
      expect.objectContaining({
        body: JSON.stringify({
          selections: [{ original_title: "Same Name", year: 2020, tmdb_id: 108, content_type: "series" }],
        }),
      }),
    ));
  });
});
