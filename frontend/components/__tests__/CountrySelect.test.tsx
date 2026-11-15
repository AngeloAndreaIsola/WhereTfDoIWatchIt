import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import CountrySelect from "../CountrySelect";

describe("CountrySelect", () => {
  it("renders the current country", () => {
    render(<CountrySelect country="IT" onChange={() => {}} />);
    expect(screen.getByLabelText("Current location")).toHaveValue("IT");
  });

  it("calls onChange with selected country", () => {
    const handleChange = vi.fn();
    render(<CountrySelect country="IT" onChange={handleChange} />);
    fireEvent.change(screen.getByLabelText("Current location"), { target: { value: "IN" } });
    expect(handleChange).toHaveBeenCalledWith("IN");
  });
});
