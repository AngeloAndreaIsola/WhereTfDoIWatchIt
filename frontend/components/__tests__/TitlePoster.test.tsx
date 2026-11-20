import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TitlePoster from "../TitlePoster";

const battleRoyale = { tmdbId: 3176, contentType: "movie", title: "Battle Royale" };
const secretary = { tmdbId: 11013, contentType: "movie", title: "Secretary" };

function image(path: string | null, status = 200) {
  return Response.json({ poster_path: path }, { status });
}

afterEach(() => vi.unstubAllGlobals());

describe("TitlePoster", () => {
  it("updates a missing poster on initial load and when the page changes", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(image("/battle.jpg"))
      .mockResolvedValueOnce(image("/secretary.jpg"));
    vi.stubGlobal("fetch", fetcher);

    const { rerender } = render(<TitlePoster {...battleRoyale} />);
    expect(screen.getByText("No Poster")).toBeInTheDocument();
    expect(await screen.findByAltText("Battle Royale")).toHaveAttribute("src", expect.stringContaining("/battle.jpg"));
    expect(fetcher).toHaveBeenCalledTimes(1);

    rerender(<TitlePoster {...secretary} />);
    expect(await screen.findByAltText("Secretary")).toHaveAttribute("src", expect.stringContaining("/secretary.jpg"));
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(["/api/movie/3176/poster/", "/api/movie/11013/poster/"]);
  });

  it("deduplicates multiple mounted cards and serializes different title lookups", async () => {
    let resolveFirst: (response: Response) => void = () => {};
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce(image("/secretary.jpg"));
    vi.stubGlobal("fetch", fetcher);

    render(<><TitlePoster {...battleRoyale} /><TitlePoster {...battleRoyale} /><TitlePoster {...secretary} /></>);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    resolveFirst(image("/battle.jpg"));

    expect(await screen.findAllByAltText("Battle Royale")).toHaveLength(2);
    await screen.findByAltText("Secretary");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("leaves a valid poster alone and gives a broken saved poster one fallback attempt", async () => {
    const fetcher = vi.fn().mockResolvedValue(image("/replacement.jpg"));
    vi.stubGlobal("fetch", fetcher);

    render(<TitlePoster tmdbId={25538} contentType="movie" title="Yi Yi" posterPath="/saved.jpg" />);
    const saved = screen.getByAltText("Yi Yi");
    expect(saved).toHaveAttribute("src", expect.stringContaining("/saved.jpg"));
    expect(fetcher).not.toHaveBeenCalled();

    fireEvent.error(saved);
    await waitFor(() => expect(screen.getByAltText("Yi Yi")).toHaveAttribute("src", expect.stringContaining("/replacement.jpg")));
    fireEvent.error(screen.getByAltText("Yi Yi"));
    expect(screen.getByText("No Poster")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps the fallback for unusable paths and never sends an unresolved title", async () => {
    const fetcher = vi.fn().mockResolvedValue(image(null));
    vi.stubGlobal("fetch", fetcher);

    render(<><TitlePoster {...battleRoyale} posterPath="invalid" /><TitlePoster title="Unknown" /></>);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.getAllByText("No Poster")).toHaveLength(2);
  });
});
