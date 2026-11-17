import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FilterBar, FilterState } from "@/components/FilterBar";

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

const BASE: FilterState = {
  q: "",
  content_type: "all",
  genre: "",
  year_min: "",
  year_max: "",
  actors: [],
  providers: [],
  country: "",
  status: "",
  quality: "",
  score_min: "",
  sort_by: "",
  watched: "all",
};

/**
 * FilterBar stages its changes: the controls mutate an internal draft and only
 * "Apply filters" calls onChange. So no fetch can happen until Apply.
 */
function setup(initial: Partial<FilterState> = {}) {
  const onChange = vi.fn();
  const utils = render(
    React.createElement(FilterBar, {
      filters: { ...BASE, ...initial },
      onChange,
      showStatusFilter: true,
    })
  );
  const apply = () => screen.getByRole("button", { name: "Apply filters" });
  const reset = () => screen.getByRole("button", { name: "Reset" });
  return { onChange, apply, reset, ...utils };
}

describe("FilterBar apply/reset staging", () => {
  it("renders an Apply filters button", () => {
    setup();
    expect(screen.getByRole("button", { name: "Apply filters" })).toBeInTheDocument();
  });

  it("disables Apply and Reset until something actually changes", () => {
    const { apply, reset } = setup();

    expect(apply()).toBeDisabled();
    expect(reset()).toBeDisabled();
  });

  it("does not emit onChange when a control changes", async () => {
    const user = userEvent.setup();
    const { onChange } = setup();

    await user.type(screen.getByPlaceholderText("Search by title..."), "matrix");
    await user.click(screen.getByRole("button", { name: "Movies" }));
    await user.selectOptions(screen.getByLabelText("Watched status"), "watched");
    await user.type(screen.getByPlaceholderText("e.g. 2020"), "2019");

    expect(onChange).not.toHaveBeenCalled();
  });

  it("emits once on Apply, carrying every staged change", async () => {
    const user = userEvent.setup();
    const { onChange, apply } = setup();

    await user.type(screen.getByPlaceholderText("Search by title..."), "matrix");
    await user.click(screen.getByRole("button", { name: "Movies" }));
    await user.selectOptions(screen.getByLabelText("Watched status"), "unwatched");

    await user.click(apply());

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        q: "matrix",
        content_type: "movie",
        watched: "unwatched",
      })
    );
  });

  it("keeps Apply disabled until Apply has been pressed", async () => {
    const user = userEvent.setup();
    const { apply } = setup();

    await user.selectOptions(screen.getByLabelText("Watched status"), "watched");
    expect(apply()).toBeEnabled();
    expect(
      screen.getByText("Unapplied filter changes")
    ).toBeInTheDocument();

    await user.click(apply());
    expect(apply()).toBeDisabled();
    expect(screen.queryByText("Unapplied filter changes")).toBeNull();
  });

  it("shows the staged value in the control while results are unchanged", async () => {
    const user = userEvent.setup();
    setup();

    const select = screen.getByLabelText("Watched status") as HTMLSelectElement;
    expect(select.value).toBe("all");

    await user.selectOptions(select, "watched");
    // The control moved; the applied value has not.
    expect(select.value).toBe("watched");
  });

  it("Reset clears the draft and emits all-defaults", async () => {
    const user = userEvent.setup();
    const { onChange, reset } = setup();

    await user.type(screen.getByPlaceholderText("Search by title..."), "matrix");
    await user.click(screen.getByRole("button", { name: "Movies" }));
    await user.selectOptions(screen.getByLabelText("Watched status"), "watched");

    await user.click(reset());

    expect(onChange).toHaveBeenCalledWith({
      q: "",
      content_type: "all",
      genre: "",
      year_min: "",
      year_max: "",
      actors: [],
      providers: [],
      country: "",
      status: "",
      quality: "",
      score_min: "",
      sort_by: "",
      watched: "all",
    });
  });

  it("Reset is inert when there is nothing to clear", async () => {
    const user = userEvent.setup();
    const { onChange, reset } = setup();

    expect(reset()).toBeDisabled();
    // Force the click anyway: a disabled button must not emit.
    reset().click();
    await Promise.resolve();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("re-seeds the draft when the parent replaces the applied filters", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    const { rerender } = render(
      React.createElement(FilterBar, {
        filters: { ...BASE, q: "alien" },
        onChange,
        showStatusFilter: true,
      })
    );

    // Type into the draft without applying.
    await user.clear(screen.getByPlaceholderText("Search by title..."));
    await user.type(screen.getByPlaceholderText("Search by title..."), "draft");
    expect((screen.getByPlaceholderText("Search by title...") as HTMLInputElement).value).toBe("draft");

    // The parent pushes a different applied value (e.g. "Clear filters").
    rerender(
      React.createElement(FilterBar, {
        filters: { ...BASE, q: "blade runner" },
        onChange,
        showStatusFilter: true,
      })
    );

    await Promise.resolve();
    expect(
      (screen.getByPlaceholderText("Search by title...") as HTMLInputElement).value
    ).toBe("blade runner");
  });

  it("keeps an unapplied draft when the parent re-sends the same applied value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    const filters = { ...BASE, q: "alien" };
    const { rerender } = render(
      React.createElement(FilterBar, { filters, onChange, showStatusFilter: true })
    );

    await user.type(screen.getByPlaceholderText("Search by title..."), "!");
    expect(onChange).not.toHaveBeenCalled();

    // Parent echoes the unchanged applied value — must not clobber the draft.
    rerender(
      React.createElement(FilterBar, {
        filters: { ...filters },
        onChange,
        showStatusFilter: true,
      })
    );

    await Promise.resolve();
    expect(
      (screen.getByPlaceholderText("Search by title...") as HTMLInputElement).value
    ).toBe("alien!");
  });
});
