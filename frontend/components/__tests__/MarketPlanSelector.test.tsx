import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import MarketPlanSelector from "@/components/MarketPlanSelector";
import {
  MarketSelectionConflictError,
  type MarketSelection,
  type MarketSelectionInput,
  type ProviderItem,
} from "@/lib/api";

const provider: ProviderItem = {
  id: 1,
  slug: "aster",
  name: "Aster",
  category: "official",
  is_major: true,
  is_enabled: true,
  icon_name: "aster",
  is_subscribed: true,
  billing_country: "NL",
  plan_options: [
    { plan_id: "base", display_name: "Base", class: "base", billing_country: "NL", caveat: "Base plan caveat" },
    { plan_id: "ads", display_name: "With ads", class: "paid_ads", billing_country: "NL", caveat: "Paid plan with ads caveat" },
    { plan_id: "premium", display_name: "Premium", class: "premium", billing_country: "NL", caveat: "Premium caveat" },
  ],
  channel_options: [
    { package_id: "orbit", parent_provider: "aster", country: "NL", display_name: "Orbit", caveat: "Hosted channel" },
    { package_id: "comet", parent_provider: "aster", country: "NL", display_name: "Comet", caveat: "Hosted channel" },
  ],
  unlisted_plan_option: {
    plan_id: null,
    plan_state: "unsupported",
    display_name: "I don't know / my plan isn't listed",
  },
  selection: {
    holding: "yes",
    plan_state: "selected",
    plan_id: "base",
    channels: [],
    revision: 3,
  },
};

describe("MarketPlanSelector", () => {
  it("offers only catalogue plans/channels and labels a hosted channel with its base", () => {
    render(<MarketPlanSelector provider={provider} onSave={vi.fn(async () => {})} />);

    const plan = screen.getByLabelText("Plan for Aster in NL") as HTMLSelectElement;
    expect(Array.from(plan.options).map((o) => o.value)).toEqual([
      "__unspecified__", "base", "ads", "premium", "__unlisted__",
    ]);
    expect(screen.getByText("With ads (paid plan with ads)")).toBeInTheDocument();
    expect(screen.getByLabelText("Channel Orbit via Aster")).toBeInTheDocument();
    expect(screen.getByText(/paid plan .with ads. is still a subscription/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Holding for Aster in NL")).toHaveValue("yes");
    expect(screen.queryByText(/included with/i)).toBeNull();
  });

  it("writes exact keys with the optimistic revision and explicit channel answers", async () => {
    const onSave = vi.fn(async () => {});
    render(<MarketPlanSelector provider={provider} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "ads" } });
    fireEvent.change(screen.getByLabelText("Channel Orbit via Aster"), { target: { value: "yes" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith("aster", {
        holding: "yes",
        plan_state: "selected",
        plan_id: "ads",
        channels: [{ package_id: "orbit", holding: "yes" }],
        revision: 3,
      } satisfies MarketSelectionInput);
    });
  });

  it("omits unanswered channels and maps the unlisted choice to an unsupported null plan", async () => {
    const onSave = vi.fn<(slug: string, input: MarketSelectionInput) => Promise<void>>(async () => {});
    render(<MarketPlanSelector provider={provider} onSave={onSave} />);

    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "__unlisted__" } });
    fireEvent.change(screen.getByLabelText("Holding for Aster in NL"), { target: { value: "no" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][1]).toEqual({
      holding: "no",
      plan_state: "unsupported",
      plan_id: null,
      channels: [],
      revision: 3,
    });
  });

  it("keeps the draft visible and reloads the saved state on HTTP 409", async () => {
    const newer: MarketSelection = {
      holding: "yes",
      plan_state: "selected",
      plan_id: "premium",
      channels: [{ package_id: "comet", holding: "yes" }],
      revision: 7,
    };
    const onSave = vi
      .fn<(slug: string, input: MarketSelectionInput) => Promise<void>>()
      .mockRejectedValueOnce(new MarketSelectionConflictError(newer))
      .mockResolvedValueOnce(undefined);

    render(<MarketPlanSelector provider={provider} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "ads" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    expect(await screen.findByText(/not saved: this selection changed elsewhere/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("ads");
    expect(screen.getByTestId("saved-revision")).toHaveTextContent("7");
    expect(screen.getByTestId("saved-selection-summary")).toHaveTextContent("premium");
    expect(screen.getByTestId("saved-selection-summary")).toHaveTextContent("comet=yes");
    expect(screen.queryByText(/saved\. no source refresh/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2));
    expect(onSave.mock.calls[1][1].revision).toBe(7);
    expect(onSave.mock.calls[1][1].plan_id).toBe("ads");
  });

  it("shows a retired selection with its last catalogue label and keeps it selectable", () => {
    render(
      <MarketPlanSelector
        provider={{
          ...provider,
          plan_options: [provider.plan_options![2]],
          selection: {
            holding: "yes",
            plan_state: "unsupported",
            plan_id: "base",
            plan_display_name: "Base",
            channels: [],
            revision: 5,
          },
        }}
        onSave={vi.fn(async () => {})}
      />
    );

    expect(screen.getByTestId("retired-plan-notice")).toHaveTextContent("Base");
    expect(screen.getByText(/unsupported — no longer offered/)).toBeInTheDocument();
    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("retired:base");
    expect(screen.getByText(/plan-dependent offers remain unconfirmed/i)).toBeInTheDocument();
  });

  it("requires a held base for a selected plan and never infers coverage from a logo or name", () => {
    render(
      <MarketPlanSelector
        provider={{ ...provider, selection: { ...provider.selection!, holding: "unspecified" } }}
        onSave={vi.fn(async () => {})}
      />
    );

    expect(screen.getByLabelText("Holding for Aster in NL")).toHaveValue("unspecified");
    expect(screen.getByText(/a selected plan requires that you have the service/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save plan selection" })).toBeDisabled();
    expect(screen.queryByText(/included with your/i)).toBeNull();
  });

  it("preserves conflict draft through subsequent catalogue polling (U2)", async () => {
    const newer: MarketSelection = {
      holding: "yes",
      plan_state: "selected",
      plan_id: "premium",
      channels: [],
      revision: 4,
    };
    const onSave = vi
      .fn<(slug: string, input: MarketSelectionInput) => Promise<void>>()
      .mockRejectedValueOnce(new MarketSelectionConflictError(newer));

    const { rerender } = render(<MarketPlanSelector provider={provider} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Plan for Aster in NL"), { target: { value: "ads" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    expect(await screen.findByText(/not saved: this selection changed elsewhere/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("ads");
    expect(screen.getByTestId("saved-revision")).toHaveTextContent("4");
    expect(screen.getByTestId("saved-selection-summary")).toHaveTextContent("premium");

    // Simulate background catalogue poll returning revision 4 with premium
    rerender(
      <MarketPlanSelector
        provider={{
          ...provider,
          selection: newer,
        }}
        onSave={onSave}
      />
    );

    // Unsaved edit (ads) and conflict summary MUST be preserved, not overwritten by polling
    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("ads");
    expect(screen.getByText(/not saved: this selection changed elsewhere/i)).toBeInTheDocument();
    expect(screen.getByTestId("saved-revision")).toHaveTextContent("4");
    expect(screen.getByTestId("saved-selection-summary")).toHaveTextContent("premium");

    // Discarding changes reverts to the server's saved state
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("premium");
    expect(screen.queryByText(/not saved: this selection changed elsewhere/i)).toBeNull();
    expect(screen.queryByTestId("saved-selection-summary")).toBeNull();
  });

  it("preserves retired plan ID on unrelated channel edit instead of sending null (U3)", async () => {
    const onSave = vi.fn<(slug: string, input: MarketSelectionInput) => Promise<void>>(async () => {});
    render(
      <MarketPlanSelector
        provider={{
          ...provider,
          plan_options: [provider.plan_options![2]],
          selection: {
            holding: "yes",
            plan_state: "unsupported",
            plan_id: "base",
            plan_display_name: "Base",
            channels: [],
            revision: 5,
          },
        }}
        onSave={onSave}
      />
    );

    expect(screen.getByLabelText("Plan for Aster in NL")).toHaveValue("retired:base");

    // Change only a channel
    fireEvent.change(screen.getByLabelText("Channel Orbit via Aster"), { target: { value: "yes" } });
    fireEvent.click(screen.getByRole("button", { name: "Save plan selection" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][1]).toEqual({
      holding: "yes",
      plan_state: "unsupported",
      plan_id: "base",
      channels: [{ package_id: "orbit", holding: "yes" }],
      revision: 5,
    });
  });

  it("renders reviewed market-specific channel caveat scoped to its channel (U5)", () => {
    render(
      <MarketPlanSelector
        provider={{
          ...provider,
          channel_options: [
            { package_id: "orbit", parent_provider: "aster", country: "NL", display_name: "Orbit", caveat: "Reviewed NL channel caveat" },
            { package_id: "comet", parent_provider: "aster", country: "NL", display_name: "Comet", caveat: "" },
          ],
        }}
        onSave={vi.fn(async () => {})}
      />
    );

    expect(screen.getByText("Reviewed NL channel caveat")).toBeInTheDocument();
    expect(screen.getByTestId("channel-caveat-orbit")).toHaveTextContent("Reviewed NL channel caveat");
    expect(screen.queryByTestId("channel-caveat-comet")).toBeNull();
  });
});
