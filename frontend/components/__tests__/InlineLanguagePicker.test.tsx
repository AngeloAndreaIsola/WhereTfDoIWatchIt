import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it.each([
    ["EN", "English"],
    ["IT", "Italiano"],
    ["JA", "日本語"],
    ["KO", "한국어"],
    ["FR", "Français"],
    ["ZH", "中文"],
  ])(
    "prioritizes the exact ISO-code match for %s over substring matches",
    (code, name) => {
      const { onChange } = renderPicker();
      fireEvent.focus(input());
      fireEvent.change(input(), { target: { value: code } });

      const options = screen.getAllByRole("option");
      expect(options[0]).toHaveTextContent(`${name} (${code})`);

      fireEvent.keyDown(input(), { key: "Enter" });
      expect(onChange).toHaveBeenCalledWith([code]);
    }
  );

  it("prioritizes prefix matches over substring matches", () => {
    renderPicker();
    fireEvent.focus(input());
    // "eng" is a prefix of English's name but only a substring of others.
    fireEvent.change(input(), { target: { value: "eng" } });

    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveTextContent("English (EN)");
  });

  it("does not select a different language when an already-selected exact code is typed", () => {
    const { onChange } = renderPicker(["EN"]);
    fireEvent.focus(input());
    // "en" is an exact match for the already-selected EN; substring matches
    // like Bengali must not be selectable via Enter.
    fireEvent.change(input(), { target: { value: "en" } });

    expect(screen.queryByRole("option")).not.toBeInTheDocument();
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("closes the dropdown when keyboard focus leaves the picker", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <InlineLanguagePicker
          selectedCodes={[]}
          onChange={vi.fn()}
          ariaLabel="Test languages"
        />
        <button type="button">next field</button>
      </div>
    );

    await user.click(input());
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.tab();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("keeps the dropdown open when focus moves inside the picker", async () => {
    const user = userEvent.setup();
    renderPicker(["EN"]);

    await user.click(input());
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    // Shift+Tab moves focus to the chip remove button, still inside the
    // picker container, so the dropdown must stay open.
    await user.tab({ shift: true });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("scrolls the highlighted option into view during keyboard navigation", () => {
    const scrollSpy = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy;
    try {
      renderPicker();
      fireEvent.focus(input());
      fireEvent.change(input(), { target: { value: "a" } });

      // Navigate well beyond the first viewport of suggestions.
      for (let i = 0; i < 30; i++) {
        fireEvent.keyDown(input(), { key: "ArrowDown" });
      }

      expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" });
      expect(scrollSpy.mock.calls.length).toBeGreaterThan(30);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
  });
});
