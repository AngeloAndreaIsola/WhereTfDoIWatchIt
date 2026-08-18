"use client";

import React, { useState } from "react";

export interface RankingInfo {
  audio_rank: number;
  subtitle_rank: number;
  quality_rank: number;
}

export interface Offer {
  country: string;
  service: string;
  offer_type: string;
  quality: string;
  audio_languages: string[];
  subtitle_languages: string[];
  ranking: RankingInfo;
}

interface OfferListProps {
  tmdbId: number;
  offers: Offer[];
  onRefreshAvailability: () => void | Promise<void>;
}

function qualityLabel(q: string): string {
  const map: Record<string, string> = {
    "4k": "4K",
    uhd: "4K",
    hdr: "HDR",
    hd: "HD",
    sd: "SD",
  };
  return map[q.toLowerCase()] ?? q.toUpperCase();
}

function rankBadge(rank: number): string {
  if (rank === 0) return "★";
  if (rank < 99) return `#${rank + 1}`;
  return "—";
}

function parseAndValidateCodes(input: string): { codes: string[]; error: string | null } {
  const rawParts = input.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  const codes: string[] = [];
  const seen = new Set<string>();

  for (const part of rawParts) {
    if (!/^[a-z]{2}$/.test(part)) {
      return {
        codes: [],
        error: `Invalid ISO 639-1 code "${part}". Language codes must be 2 letters (e.g., en, es, fr).`,
      };
    }
    if (!seen.has(part)) {
      seen.add(part);
      codes.push(part);
    }
  }

  return { codes, error: null };
}

export default function OfferList({ tmdbId, offers, onRefreshAvailability }: OfferListProps) {
  const [editingOffer, setEditingOffer] = useState<Offer | null>(null);
  const [audioInput, setAudioInput] = useState("");
  const [subInput, setSubInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [formSuccess, setFormSuccess] = useState("");

  const handleOpenEdit = (offer: Offer) => {
    setEditingOffer(offer);
    setAudioInput(offer.audio_languages.join(", "));
    setSubInput(offer.subtitle_languages.join(", "));
    setFormError("");
    setFormSuccess("");
  };

  const handleCloseEdit = () => {
    setEditingOffer(null);
    setAudioInput("");
    setSubInput("");
    setFormError("");
    setFormSuccess("");
  };

  const handleSaveLanguageInfo = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingOffer) return;

    setFormError("");
    setFormSuccess("");

    const audioRes = parseAndValidateCodes(audioInput);
    if (audioRes.error) {
      setFormError(`Audio: ${audioRes.error}`);
      return;
    }

    const subRes = parseAndValidateCodes(subInput);
    if (subRes.error) {
      setFormError(`Subtitle: ${subRes.error}`);
      return;
    }

    setSaving(true);

    try {
      const res = await fetch(`/api/movie/${tmdbId}/language-info/`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: editingOffer.service,
          country: editingOffer.country,
          audio_languages: audioRes.codes,
          subtitle_languages: subRes.codes,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error ?? `Failed to save language info (${res.status})`);
      }

      setFormSuccess("Language info saved successfully!");
      setTimeout(async () => {
        handleCloseEdit();
        await onRefreshAvailability();
      }, 600);
    } catch (err: unknown) {
      setFormError(err instanceof Error ? err.message : "Failed to save language info");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      {offers.map((o, i) => {
        const hasAudio = o.audio_languages.length > 0;
        const hasSubs = o.subtitle_languages.length > 0;
        const isMissingAll = !hasAudio && !hasSubs;

        return (
          <div
            key={`${o.country}-${o.service}-${o.quality}-${i}`}
            className={`rounded-lg border p-4 text-sm transition-colors ${
              i === 0
                ? "border-green-400 bg-green-50 shadow-sm dark:border-green-600 dark:bg-green-900/20"
                : "border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800/50"
            }`}
          >
            {/* Top row: service + country + missing badge + quality/best */}
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{o.service}</span>
                <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-medium text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                  {o.country}
                </span>
                <span className="text-xs text-gray-400">{o.offer_type}</span>
                {isMissingAll && (
                  <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                    Missing language info
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {o.quality && (
                  <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-xs font-bold dark:bg-gray-600 dark:text-gray-200">
                    {qualityLabel(o.quality)}
                  </span>
                )}
                {i === 0 && (
                  <span className="rounded-full bg-green-600 px-2 py-0.5 text-xs font-bold text-white">
                    Best
                  </span>
                )}
              </div>
            </div>

            {/* Language info + Edit Button */}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                <span>
                  🔊 Audio:{" "}
                  {hasAudio ? (
                    <span className="font-medium text-gray-700 dark:text-gray-200">
                      {o.audio_languages.join(", ")}
                    </span>
                  ) : (
                    <span className="font-medium text-amber-600 dark:text-amber-400">
                      Missing
                    </span>
                  )}
                  <span className="ml-1 text-gray-300 dark:text-gray-600">
                    {rankBadge(o.ranking.audio_rank)}
                  </span>
                </span>
                <span>
                  💬 Subs:{" "}
                  {hasSubs ? (
                    <span className="font-medium text-gray-700 dark:text-gray-200">
                      {o.subtitle_languages.join(", ")}
                    </span>
                  ) : (
                    <span className="font-medium text-amber-600 dark:text-amber-400">
                      Missing
                    </span>
                  )}
                  <span className="ml-1 text-gray-300 dark:text-gray-600">
                    {rankBadge(o.ranking.subtitle_rank)}
                  </span>
                </span>
              </div>

              <button
                onClick={() => handleOpenEdit(o)}
                className="rounded text-xs text-blue-600 hover:underline dark:text-blue-400 font-medium"
              >
                {isMissingAll ? "+ Add Language Info" : "✏️ Edit Language Info"}
              </button>
            </div>
          </div>
        );
      })}

      {/* Edit Language Info Modal */}
      {editingOffer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-700 dark:bg-gray-800">
            <h3 className="mb-1 text-lg font-semibold text-gray-900 dark:text-gray-100">
              Edit Language Info
            </h3>
            <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
              {editingOffer.service} ({editingOffer.country})
            </p>

            <form onSubmit={handleSaveLanguageInfo} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300">
                  Audio Language Codes
                </label>
                <input
                  type="text"
                  placeholder="e.g. en, es, fr"
                  value={audioInput}
                  onChange={(e) => setAudioInput(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                />
                <span className="text-[11px] text-gray-400">
                  Comma-separated 2-letter ISO 639-1 codes
                </span>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300">
                  Subtitle Language Codes
                </label>
                <input
                  type="text"
                  placeholder="e.g. en, es, fr"
                  value={subInput}
                  onChange={(e) => setSubInput(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
                />
                <span className="text-[11px] text-gray-400">
                  Comma-separated 2-letter ISO 639-1 codes
                </span>
              </div>

              <div className="rounded bg-amber-50 p-2.5 text-xs text-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
                Note: Language info is manually entered and may be incomplete.
              </div>

              {formError && (
                <div className="rounded bg-red-50 p-2.5 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-400">
                  {formError}
                </div>
              )}

              {formSuccess && (
                <div className="rounded bg-green-50 p-2.5 text-xs text-green-700 dark:bg-green-900/30 dark:text-green-400">
                  {formSuccess}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={handleCloseEdit}
                  disabled={saving}
                  className="rounded-md border border-gray-300 px-4 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 dark:bg-blue-500 dark:hover:bg-blue-600"
                >
                  {saving ? "Saving…" : "Save Language Info"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
