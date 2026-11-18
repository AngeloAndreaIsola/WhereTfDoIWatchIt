import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CastList from "../CastList";
import type { TMDbCastMember } from "@/lib/api";

const member = (id: number, name: string, character = ""): TMDbCastMember => ({
  id,
  name,
  character,
});

const makeCast = (n: number): TMDbCastMember[] =>
  Array.from({ length: n }, (_, i) => member(i + 1, `Actor ${i + 1}`, `Role ${i + 1}`));

describe("CastList", () => {
  it("renders nothing for an empty or missing cast", () => {
    const { container: empty } = render(<CastList cast={[]} />);
    expect(empty).toBeEmptyDOMElement();

    const { container: missing } = render(<CastList cast={undefined} />);
    expect(missing).toBeEmptyDOMElement();
  });

  it("shows all members without a toggle when cast has exactly 8 entries", () => {
    render(<CastList cast={makeCast(8)} />);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText(/more/)).not.toBeInTheDocument();
  });

  it("shows 8 entries and expands/collapses the rest", async () => {
    const user = userEvent.setup();
    render(<CastList cast={makeCast(12)} />);

    expect(screen.getAllByRole("link")).toHaveLength(8);
    const toggle = screen.getByRole("button", { name: /show all cast/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Actor 9/)).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getAllByRole("link")).toHaveLength(12);
    expect(screen.getByText(/Actor 12/)).toBeInTheDocument();
    const collapse = screen.getByRole("button", { name: /show less/i });
    expect(collapse).toHaveAttribute("aria-expanded", "true");

    await user.click(collapse);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.queryByText(/Actor 12/)).not.toBeInTheDocument();
  });

  it("links cast members to their TMDb person page with accessible names", () => {
    render(<CastList cast={[member(42, "Ada Lovelace", "Analyst")]} />);
    const link = screen.getByRole("link", { name: /ada lovelace/i });
    expect(link).toHaveAttribute("href", "https://www.themoviedb.org/person/42");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveTextContent("Ada Lovelace as Analyst");
  });

  it("omits the character part of the label when character is missing", () => {
    render(<CastList cast={[member(7, "Grace Hopper")]} />);
    const link = screen.getByRole("link", { name: /grace hopper/i });
    expect(link).toHaveTextContent("Grace Hopper");
    expect(link).not.toHaveTextContent("as");
  });

  it("renders members without a usable id as non-clickable text", () => {
    const cast = [member(0, "No Id Actor"), member(-3, "Negative Id"), member(5, "Real Actor")];
    render(<CastList cast={cast} />);
    expect(screen.getByText("No Id Actor")).toBeInTheDocument();
    expect(screen.getByText("Negative Id")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /no id actor/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /negative id/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /real actor/i })).toBeInTheDocument();
  });
});
