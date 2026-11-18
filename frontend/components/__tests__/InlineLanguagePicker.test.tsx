import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import InlineLanguagePicker from "../InlineLanguagePicker";

function renderPicker(selectedCodes: string[] = [], onChange = vi.fn()) {
  render(
    <InlineLanguagePicker
      selectedCodes={selectedCodes}
      onChange={onChange}
      ariaLabel="Test languages"
    />
  );
  return { onChange };
}

const input = () => screen.getByRole("combobox", { name: "Test languages" });

describe("InlineLanguagePicker", () => {
  it("renders selected languages as removable chips inside the field", () => {
    renderPicker(["EN", "IT"]);

    expect(screen.getByText("English (EN)")).toBeInTheDocument();
    expect(screen.getByText("Italiano (IT)")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove English (EN)" })
    ).toBeInTheDocument();
  });

  it("shows canonical suggestions when the input is focused", () => {
    renderPicker();
    fireEvent.focus(input());

    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getAllByRole("option").length).toBeGreaterThan(100);
  });

  it("filters suggestions by language name, case-insensitively", () => {
    renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "jApAnEsE" } });

    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("日本語 (JA)");
  });

  it("matches languages by their English display name", () => {
    renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "japan" } });

    const options = screen.getAllByRole("option");
    expect(options.some((o) => o.textContent?.includes("(JA)"))).toBe(true);
  });

  it("filters suggestions by ISO code", () => {
    renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "ja" } });

    const options = screen.getAllByRole("option");
    expect(options.some((o) => o.textContent?.includes("(JA)"))).toBe(true);
  });

  it("adds a language when a suggestion is clicked", () => {
    const { onChange } = renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "japanese" } });
    fireEvent.click(screen.getByRole("option", { name: /日本語 \(JA\)/ }));

    expect(onChange).toHaveBeenCalledWith(["JA"]);
  });

  it("adds the highlighted suggestion with Enter", () => {
    const { onChange } = renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "korean" } });
    fireEvent.keyDown(input(), { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith(["KO"]);
  });

  it("navigates suggestions with arrow keys", () => {
    const { onChange } = renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "eng" } });

    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);

    fireEvent.keyDown(input(), { key: "ArrowDown" });
    fireEvent.keyDown(input(), { key: "Enter" });

    // After ArrowDown the second match is highlighted and Enter selects it.
    const code = options[1].textContent?.match(/\(([A-Z]{2})\)/)?.[1];
    expect(onChange).toHaveBeenCalledWith([code]);
  });

  it("closes the dropdown on Escape", () => {
    renderPicker();
    fireEvent.focus(input());
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(input(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes the dropdown on outside click", () => {
    renderPicker();
    fireEvent.focus(input());
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("rejects unknown language codes instead of adding them", () => {
    const { onChange } = renderPicker();
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "ZZ" } });

    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(screen.getByText(/No matching languages found/)).toBeInTheDocument();

    fireEvent.keyDown(input(), { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText("ZZ (ZZ)")).not.toBeInTheDocument();
  });

  it("does not offer already-selected languages, preventing duplicates", () => {
    renderPicker(["EN"]);
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "english" } });

    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove English (EN)" })
    ).toBeInTheDocument();
  });

  it("removes the last chip with Backspace when the input is empty", () => {
    const { onChange } = renderPicker(["EN", "IT"]);
    fireEvent.focus(input());
    fireEvent.keyDown(input(), { key: "Backspace" });

    expect(onChange).toHaveBeenCalledWith(["EN"]);
  });

  it("does not remove a chip on Backspace when the input has text", () => {
    const { onChange } = renderPicker(["EN"]);
    fireEvent.focus(input());
    fireEvent.change(input(), { target: { value: "a" } });
    fireEvent.keyDown(input(), { key: "Backspace" });

    expect(onChange).not.toHaveBeenCalled();
  });

  it("removes a chip via its button", () => {
    const { onChange } = renderPicker(["EN", "JA"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove 日本語 (JA)" }));
    expect(onChange).toHaveBeenCalledWith(["EN"]);
  });

  it("focuses the input when the chip container is clicked", () => {
    renderPicker(["EN"]);
    const chip = screen.getByText("English (EN)");
    fireEvent.click(chip.parentElement!);
    expect(input()).toHaveFocus();
  });
});
