import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchBar from "@/components/SearchBar";

const routerPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

function mockFetch(responses: Array<{ ok?: boolean; status?: number; body: unknown }>) {
  const fetchMock = vi.fn();
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce({
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body,
    });
  }
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("SearchBar component", () => {
  beforeEach(() => {
    routerPush.mockReset();
    vi.restoreAllMocks();
  });

  it("debounces user input and displays search results, navigating on click", async () => {
    const fetchMock = mockFetch([
      {
        body: {
          results: [{ tmdb_id: 550, title: "Fight Club", year: 1999, content_type: "movie" }],
        },
      },
    ]);

    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search for a movie or TV series by title...");
    await user.type(input, "Fight");

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/search/?q=Fight&type=all");
    });

    const resultBtn = await screen.findByRole("button", { name: /Fight Club/i });
    expect(screen.getByText("1999")).toBeInTheDocument();

    await user.click(resultBtn);
    expect(routerPush).toHaveBeenCalledWith("/movie/550");
  });

  it("navigates series results to the /tv route", async () => {
    mockFetch([
      {
        body: {
          results: [{ tmdb_id: 1396, title: "Breaking Bad", year: 2008, content_type: "series" }],
        },
      },
    ]);

    const user = userEvent.setup();
    render(<SearchBar />);

    await user.type(screen.getByPlaceholderText("Search for a movie or TV series by title..."), "Breaking");

    const resultBtn = await screen.findByRole("button", { name: /Breaking Bad/i });
    await user.click(resultBtn);
    expect(routerPush).toHaveBeenCalledWith("/tv/1396");
  });

  it("handles empty search query gracefully", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<SearchBar />);

    const input = screen.getByPlaceholderText("Search for a movie or TV series by title...");
    await user.type(input, "   ");

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
