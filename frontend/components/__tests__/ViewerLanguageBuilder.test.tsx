import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ViewerLanguageBuilder, {
  ViewersRuleNode,
} from "../ViewerLanguageBuilder";

const defaultRule: ViewersRuleNode = {
  type: "viewers",
  viewers: [
    { name: "Andrea", languages: ["EN", "IT"] },
    { name: "Mayu", languages: ["JA"] },
  ],
};

function renderBuilder(rule: ViewersRuleNode = defaultRule) {
  const onChange = vi.fn();
  render(<ViewerLanguageBuilder rule={rule} onChange={onChange} />);
  return { onChange };
}

describe("ViewerLanguageBuilder", () => {
  it("renders existing saved languages as chips for each viewer", () => {
    renderBuilder();

    expect(screen.getByText("Andrea")).toBeInTheDocument();
    expect(screen.getByText("Mayu")).toBeInTheDocument();
    expect(screen.getByText("English (EN)")).toBeInTheDocument();
    expect(screen.getByText("Italiano (IT)")).toBeInTheDocument();
    expect(screen.getByText("日本語 (JA)")).toBeInTheDocument();
  });

  it("adds a language to an existing viewer via the picker", () => {
    const { onChange } = renderBuilder();

    const andreaInput = screen.getByRole("combobox", {
      name: "Languages spoken or understood by Andrea",
    });
    fireEvent.focus(andreaInput);
    fireEvent.change(andreaInput, { target: { value: "french" } });
    fireEvent.click(screen.getByRole("option", { name: /Français \(FR\)/ }));

    expect(onChange).toHaveBeenCalledWith({
      type: "viewers",
      viewers: [
        { name: "Andrea", languages: ["EN", "IT", "FR"] },
        { name: "Mayu", languages: ["JA"] },
      ],
    });
  });

  it("removes a language chip from an existing viewer", () => {
    const { onChange } = renderBuilder();

    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove English (EN)" })[0]
    );

    expect(onChange).toHaveBeenCalledWith({
      type: "viewers",
      viewers: [
        { name: "Andrea", languages: ["IT"] },
        { name: "Mayu", languages: ["JA"] },
      ],
    });
  });

  it("rejects invalid codes when adding a new viewer", () => {
    const { onChange } = renderBuilder({ type: "viewers", viewers: [] });

    fireEvent.change(screen.getByPlaceholderText("e.g. Andrea"), {
      target: { value: "Test" },
    });
    const picker = screen.getByRole("combobox", {
      name: "Languages spoken or understood by the new viewer",
    });
    fireEvent.focus(picker);
    fireEvent.change(picker, { target: { value: "ZZ" } });
    fireEvent.keyDown(picker, { key: "Enter" });

    fireEvent.click(screen.getByRole("button", { name: "Add Person" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Please add at least one language/)
    ).toBeInTheDocument();
  });

  it("adds a new viewer with picker-selected languages", () => {
    const { onChange } = renderBuilder({ type: "viewers", viewers: [] });

    fireEvent.change(screen.getByPlaceholderText("e.g. Andrea"), {
      target: { value: "Sven" },
    });
    const picker = screen.getByRole("combobox", {
      name: "Languages spoken or understood by the new viewer",
    });
    fireEvent.focus(picker);
    fireEvent.change(picker, { target: { value: "swedish" } });
    fireEvent.click(screen.getByRole("option", { name: /Svenska \(SV\)/ }));
    fireEvent.change(picker, { target: { value: "deutsch" } });
    fireEvent.keyDown(picker, { key: "Enter" });

    fireEvent.click(screen.getByRole("button", { name: "Add Person" }));

    expect(onChange).toHaveBeenCalledWith({
      type: "viewers",
      viewers: [{ name: "Sven", languages: ["SV", "DE"] }],
    });
  });

  it("shows an error when adding a viewer without a name", () => {
    const { onChange } = renderBuilder({ type: "viewers", viewers: [] });

    const picker = screen.getByRole("combobox", {
      name: "Languages spoken or understood by the new viewer",
    });
    fireEvent.focus(picker);
    fireEvent.change(picker, { target: { value: "en" } });
    fireEvent.keyDown(picker, { key: "Enter" });

    fireEvent.click(screen.getByRole("button", { name: "Add Person" }));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Please enter a viewer name.")).toBeInTheDocument();
  });
});
