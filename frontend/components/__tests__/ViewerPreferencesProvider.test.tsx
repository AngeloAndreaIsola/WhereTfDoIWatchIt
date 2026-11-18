import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  ViewerPreferencesProvider,
  useViewerPreferences,
} from "../ViewerPreferencesProvider";
import ViewerLanguageBuilder from "../ViewerLanguageBuilder";

function Consumer() {
  const { viewersRule, setViewersRule, loading, error } = useViewerPreferences();
  if (loading) return <div>loading</div>;
  if (error) return <div>error: {error}</div>;
  return <ViewerLanguageBuilder rule={viewersRule} onChange={setViewersRule} />;
}

describe("ViewerPreferencesProvider persistence", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads saved viewer languages and persists chip changes via PUT", async () => {
    const savedRule = {
      type: "viewers",
      viewers: [{ name: "Andrea", languages: ["EN", "IT"] }],
    };

    const fetchMock = vi.spyOn(global, "fetch").mockImplementation(
      async (input, init) => {
        if (init?.method === "PUT") {
          return { ok: true, json: async () => ({}) } as Response;
        }
        return {
          ok: true,
          json: async () => ({ language_rule: savedRule }),
        } as Response;
      }
    );

    render(
      <ViewerPreferencesProvider>
        <Consumer />
      </ViewerPreferencesProvider>
    );

    // Saved languages render as chips after load.
    expect(await screen.findByText("English (EN)")).toBeInTheDocument();
    expect(screen.getByText("Italiano (IT)")).toBeInTheDocument();

    // Remove the IT chip -> provider should PUT the updated language_rule.
    fireEvent.click(
      screen.getByRole("button", { name: "Remove Italiano (IT)" })
    );

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === "PUT"
      );
      expect(putCall).toBeTruthy();
      const body = JSON.parse(String((putCall![1] as RequestInit).body));
      expect(body.language_rule).toEqual({
        type: "viewers",
        viewers: [{ name: "Andrea", languages: ["EN"] }],
      });
      expect(body.audio_languages).toEqual(["EN"]);
      expect(body.subtitle_languages).toEqual(["EN"]);
    });
  });
});
