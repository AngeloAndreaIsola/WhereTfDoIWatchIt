"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  ChannelDeclaration,
  MarketHolding,
  MarketPlanState,
  MarketSelection,
  MarketSelectionInput,
  ProviderItem,
} from "@/lib/api";
import { MarketSelectionConflictError } from "@/lib/api";

export interface MarketPlanSelectorProps {
  provider: ProviderItem;
  /** slug → display name, used to label a channel with its host platform. */
  hostNames?: Record<string, string>;
  onSave: (slug: string, input: MarketSelectionInput) => Promise<void>;
}

type ChannelAnswer = "yes" | "no" | "unspecified";

interface Draft {
  holding: MarketHolding;
  planChoice: string;
  channels: Record<string, ChannelAnswer>;
  revision: number;
}

const UNLISTED_CHOICE = "__unlisted__";
const UNSPECIFIED_CHOICE = "__unspecified__";

function retiredChoice(planId: string): string {
  return `retired:${planId}`;
}

function draftFromSelection(selection: MarketSelection | undefined): Draft {
  const saved = selection ?? {
    holding: "unspecified" as const,
    plan_state: "unspecified" as const,
    plan_id: null,
    channels: [],
    revision: 0,
  };
  let planChoice = UNSPECIFIED_CHOICE;
  if (saved.plan_state === "unsupported") {
    planChoice = saved.plan_id ? retiredChoice(saved.plan_id) : UNLISTED_CHOICE;
  } else if (saved.plan_state === "selected" && saved.plan_id) {
    planChoice = saved.plan_id;
  }
  const channels: Record<string, ChannelAnswer> = {};
  for (const child of saved.channels ?? []) channels[child.package_id] = child.holding;
  return { holding: saved.holding, planChoice, channels, revision: saved.revision ?? 0 };
}

function planInput(choice: string): { plan_state: MarketPlanState; plan_id: string | null } {
  if (choice === UNSPECIFIED_CHOICE) return { plan_state: "unspecified", plan_id: null };
  if (choice === UNLISTED_CHOICE) return { plan_state: "unsupported", plan_id: null };
  if (choice.startsWith("retired:")) return { plan_state: "unsupported", plan_id: choice.slice("retired:".length) };
  return { plan_state: "selected", plan_id: choice };
}

function selectionKey(selection: MarketSelection | undefined): string {
  if (!selection) return "none";
  return JSON.stringify([
    selection.revision,
    selection.holding,
    selection.plan_state,
    selection.plan_id,
    selection.channels,
  ]);
}

export default function MarketPlanSelector({
  provider,
  hostNames,
  onSave,
}: MarketPlanSelectorProps) {
  const selection = provider.selection;
  const selectionIdentity = useMemo(() => selectionKey(selection), [selection]);
  const providerKey = `${provider.slug}:${provider.billing_country}`;
  const prevProviderKey = useRef(providerKey);
  const planOptions = provider.plan_options ?? [];
  const channelOptions = provider.channel_options ?? [];
  const unlisted = provider.unlisted_plan_option;

  const [draft, setDraft] = useState<Draft>(() => draftFromSelection(selection));
  const [savedState, setSavedState] = useState<MarketSelection | undefined>(selection);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [conflicted, setConflicted] = useState(false);

  useEffect(() => {
    const marketChanged = prevProviderKey.current !== providerKey;
    prevProviderKey.current = providerKey;

    if (!marketChanged && conflicted) {
      // Subscriptions polling or catalogue reload for the same provider/market
      // while in conflict: update the saved comparison from the server and advance
      // the base revision, but preserve unsaved draft edits and keep the conflict summary visible.
      setSavedState(selection);
      if (selection?.revision !== undefined) {
        setDraft((prev) => ({ ...prev, revision: selection.revision }));
      }
      return;
    }

    setDraft(draftFromSelection(selection));
    setSavedState(selection);
    setMessage("");
    setError("");
    setConflicted(false);
    // A new catalogue read (market change or successful save) is the only
    // reason to re-seed the editable draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionIdentity, providerKey]);

  const selectedPlan = planOptions.find((p) => p.plan_id === draft.planChoice);
  const plan = planInput(draft.planChoice);
  const needsHolder = plan.plan_state === "selected" && draft.holding !== "yes";

  const handleSave = async () => {
    if (saving || needsHolder) return;
    setSaving(true);
    setMessage("");
    setError("");
    const channels: ChannelDeclaration[] = channelOptions
      .filter((option) => draft.channels[option.package_id] === "yes" || draft.channels[option.package_id] === "no")
      .map((option) => ({
        package_id: option.package_id,
        holding: draft.channels[option.package_id] as "yes" | "no",
      }));
    const input: MarketSelectionInput = {
      holding: draft.holding,
      plan_state: plan.plan_state,
      plan_id: plan.plan_id,
      channels,
      revision: draft.revision,
    };
    try {
      await onSave(provider.slug, input);
      setConflicted(false);
      setMessage("Saved. No source refresh is needed to apply a plan selection.");
    } catch (err: unknown) {
      if (err instanceof MarketSelectionConflictError) {
        setSavedState(err.selection);
        setConflicted(true);
        setDraft((prev) => ({ ...prev, revision: err.selection.revision }));
        setMessage(
          "Not saved: this selection changed elsewhere. The current saved state " +
            "is shown below and your edits are still here — review and save again."
        );
      } else {
        setError(err instanceof Error ? err.message : "Failed to save plan selection");
      }
    } finally {
      setSaving(false);
    }
  };

  const hostLabel = (parentProvider: string): string => {
    if (parentProvider === provider.slug) return provider.name;
    return hostNames?.[parentProvider] ?? parentProvider;
  };

  return (
    <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50/70 p-3 text-left dark:border-gray-700 dark:bg-gray-800/40">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300">
          Plan in {provider.billing_country}
        </span>
        <span className="text-[10px] text-gray-500 dark:text-gray-400" data-testid="saved-revision">
          Saved revision {savedState?.revision ?? 0}
        </span>
      </div>

      {savedState?.plan_state === "unsupported" && savedState.plan_id && (
        <p
          className="mb-2 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
          role="status"
          data-testid="retired-plan-notice"
        >
          We can’t currently confirm coverage for your saved plan
          {" "}&ldquo;{savedState.plan_display_name ?? savedState.plan_id}&rdquo; in{" "}
          {provider.billing_country}. It remains unsupported in our catalogue, so
          plan-dependent offers are unconfirmed.
        </p>
      )}

      <label className="mb-2 block text-[11px] text-gray-700 dark:text-gray-300">
        Do you have this service?
        <select
          value={draft.holding}
          onChange={(e) => setDraft((prev) => ({ ...prev, holding: e.target.value as MarketHolding }))}
          aria-label={`Holding for ${provider.name} in ${provider.billing_country}`}
          className="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-[11px] dark:border-gray-600 dark:bg-gray-900"
        >
          <option value="unspecified">Not answered (unconfirmed)</option>
          <option value="yes">I have it</option>
          <option value="no">I don&apos;t have it</option>
        </select>
      </label>

      <label className="mb-1 block text-[11px] text-gray-700 dark:text-gray-300">
        Your exact plan
        <select
          value={draft.planChoice}
          onChange={(e) => setDraft((prev) => ({ ...prev, planChoice: e.target.value }))}
          aria-label={`Plan for ${provider.name} in ${provider.billing_country}`}
          className="mt-1 w-full rounded border border-gray-300 bg-white px-2 py-1 text-[11px] dark:border-gray-600 dark:bg-gray-900"
        >
          <option value={UNSPECIFIED_CHOICE}>Not specified</option>
          {planOptions.map((option) => (
            <option key={option.plan_id} value={option.plan_id}>
              {option.display_name}
              {option.class === "paid_ads" ? " (paid plan with ads)" : ""}
              {option.class === "premium" ? " (premium)" : ""}
            </option>
          ))}
          {savedState?.plan_state === "unsupported" && savedState.plan_id && !planOptions.some((p) => p.plan_id === savedState.plan_id) && (
            <option value={retiredChoice(savedState.plan_id)}>
              {savedState.plan_display_name ?? savedState.plan_id} (unsupported in our catalogue)
            </option>
          )}
          {unlisted && <option value={UNLISTED_CHOICE}>{unlisted.display_name}</option>}
        </select>
      </label>

      {selectedPlan?.caveat && (
        <p className="mb-1 text-[10px] text-gray-500 dark:text-gray-400">{selectedPlan.caveat}</p>
      )}
      {draft.planChoice === UNLISTED_CHOICE && unlisted && (
        <p className="mb-1 text-[10px] text-gray-500 dark:text-gray-400">
          An unlisted plan stays unsupported; plan-dependent offers are not
          confirmed until a reviewed plan is selected.
        </p>
      )}
      <p className="mb-2 text-[10px] text-gray-500 dark:text-gray-400">
        A paid plan &ldquo;with ads&rdquo; is still a subscription. It is not the
        same product as free ad-funded access.
      </p>

      {needsHolder && (
        <p className="mb-2 text-[10px] font-medium text-amber-700 dark:text-amber-400" role="status">
          A selected plan requires that you have the service.
        </p>
      )}

      {channelOptions.length > 0 && (
        <fieldset className="mb-2">
          <legend className="text-[11px] font-semibold text-gray-700 dark:text-gray-300">
            Channel add-ons
          </legend>
          {channelOptions.map((channel) => (
            <div key={channel.package_id} className="mt-1">
              <label
                className="flex items-center justify-between gap-2 text-[11px] text-gray-700 dark:text-gray-300"
              >
                <span>
                  {channel.display_name}
                  <span className="text-gray-500 dark:text-gray-400">
                    {" "}(via {hostLabel(channel.parent_provider)} · {channel.country})
                  </span>
                </span>
                <select
                  value={draft.channels[channel.package_id] ?? "unspecified"}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      channels: {
                        ...prev.channels,
                        [channel.package_id]: e.target.value as ChannelAnswer,
                      },
                    }))
                  }
                  aria-label={`Channel ${channel.display_name} via ${hostLabel(channel.parent_provider)}`}
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-[11px] dark:border-gray-600 dark:bg-gray-900"
                >
                  <option value="unspecified">Not answered</option>
                  <option value="yes">I have it</option>
                  <option value="no">I don&apos;t have it</option>
                </select>
              </label>
              {channel.caveat && (
                <p
                  data-testid={`channel-caveat-${channel.package_id}`}
                  className="mt-0.5 text-[10px] text-gray-500 dark:text-gray-400"
                >
                  {channel.caveat}
                </p>
              )}
            </div>
          ))}
          <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400">
            A channel held on its own does not grant a channel hosted by another
            provider. Unanswered channels stay unconfirmed.
          </p>
        </fieldset>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || needsHolder}
          className="rounded bg-blue-600 px-3 py-1 text-[11px] font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save plan selection"}
        </button>
        {conflicted && (
          <button
            type="button"
            onClick={() => {
              setConflicted(false);
              setDraft(draftFromSelection(savedState ?? selection));
              setMessage("");
              setError("");
            }}
            className="text-[10px] text-gray-500 underline hover:text-gray-700 dark:text-gray-400"
          >
            Discard changes
          </button>
        )}
      </div>

          {conflicted && savedState && (
            <p
              data-testid="saved-selection-summary"
              className="mt-2 text-[10px] text-gray-600 dark:text-gray-300"
            >
              Saved on server: holding {savedState.holding}; plan{" "}
              {savedState.plan_display_name ?? savedState.plan_id ?? "unspecified"}; channels{" "}
              {savedState.channels.length > 0
                ? savedState.channels.map((c) => `${c.package_id}=${c.holding}`).join(", ")
                : "none"}
            </p>
          )}
          {message && (
            <p className="mt-2 text-[10px] text-blue-700 dark:text-blue-300" role="status">
              {message}
            </p>
          )}
      {error && (
        <p className="mt-2 text-[10px] text-red-700 dark:text-red-300" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
