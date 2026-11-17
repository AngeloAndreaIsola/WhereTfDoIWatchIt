import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  AccessTypeFilter,
  parseAccessTypes,
  serialiseAccessTypes,
} from "../AccessTypeFilter";

describe("AccessTypeFilter", () => {
  it("round-trips the comma-separated API encoding", () => {
    expect(parseAccessTypes("")).toEqual([]);
    expect(parseAccessTypes("all")).toHaveLength(6);
    expect(parseAccessTypes("subscription,free")).toEqual([
      "subscription",
      "free",
    ]);
    expect(serialiseAccessTypes([])).toBe("");
    expect(serialiseAccessTypes(["free"])).toBe("free");
    expect(
      serialiseAccessTypes([
        "subscription",
        "addon",
        "free",
        "free_with_ads",
        "rent",
        "buy",
      ]),
    ).toBe("all");
  });

  it("summarises the selection on the toggle", () => {
    render(<AccessTypeFilter access="" onChange={() => {}} />);
    expect(screen.getByTestId("access-type-toggle")).toHaveTextContent(
      "Any access",
    );
  });

  it("toggles individual offer types from the checkbox dropdown", () => {
    let access = "";
    const onChange = (next: string) => {
      access = next;
    };
    const { rerender } = render(
      <AccessTypeFilter access={access} onChange={onChange} />,
    );

    fireEvent.click(screen.getByTestId("access-type-toggle"));
    fireEvent.click(screen.getByLabelText("Free"));
    expect(access).toBe("free");

    rerender(<AccessTypeFilter access={access} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Available to rent"));
    expect(access).toBe("free,rent");
  });

  it("selects and clears all types at once", () => {
    let access = "";
    const onChange = (next: string) => {
      access = next;
    };
    const { rerender } = render(
      <AccessTypeFilter access={access} onChange={onChange} />,
    );

    fireEvent.click(screen.getByTestId("access-type-toggle"));
    fireEvent.click(screen.getByLabelText("All access types"));
    expect(access).toBe("all");

    rerender(<AccessTypeFilter access={access} onChange={onChange} />);
    fireEvent.click(screen.getByText("Clear"));
    expect(access).toBe("");
  });
});
