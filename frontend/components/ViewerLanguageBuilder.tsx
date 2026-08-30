"use client";

import React, { useState } from "react";
import { CANONICAL_LANGUAGE_MAP, normalizeLanguageCode } from "@/lib/languages";

export interface Viewer {
  name: string;
  languages: string[];
}

export interface ViewersRuleNode {
  type: "viewers";
  viewers: Viewer[];
}

interface ViewerLanguageBuilderProps {
  rule: ViewersRuleNode;
  onChange: (updatedRule: ViewersRuleNode) => void;
}

export default function ViewerLanguageBuilder({ rule, onChange }: ViewerLanguageBuilderProps) {
  const [newViewerName, setNewViewerName] = useState("");
  const [newViewerLangsInput, setNewViewerLangsInput] = useState("");
  const [addError, setAddError] = useState("");

  const handleAddViewer = (e: React.FormEvent) => {
    e.preventDefault();
    setAddError("");

    const trimmedName = newViewerName.trim();
    if (!trimmedName) {
      setAddError("Please enter a viewer name.");
      return;
    }

    const rawTokens = newViewerLangsInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    if (rawTokens.length === 0) {
      setAddError("Please enter at least one language code (e.g., EN, IT, JA).");
      return;
    }

    const validLangs: string[] = [];
    const invalidTokens: string[] = [];
    const seen = new Set<string>();

    for (const token of rawTokens) {
      const canonical = normalizeLanguageCode(token);
      if (canonical) {
        if (!seen.has(canonical)) {
          seen.add(canonical);
          validLangs.push(canonical);
        }
      } else {
        invalidTokens.push(token);
      }
    }

    if (invalidTokens.length > 0) {
      setAddError(`Invalid language code(s): ${invalidTokens.join(", ")}. Please use valid 2-character language codes (e.g. EN, IT, JA).`);
      return;
    }

    const updatedViewers = [...rule.viewers, { name: trimmedName, languages: validLangs }];
    onChange({ type: "viewers", viewers: updatedViewers });
    setNewViewerName("");
    setNewViewerLangsInput("");
  };

  const handleRemoveViewer = (index: number) => {
    const updatedViewers = rule.viewers.filter((_, i) => i !== index);
    onChange({ type: "viewers", viewers: updatedViewers });
  };

  const handleUpdateViewerLangs = (index: number, rawInput: string) => {
    const rawTokens = rawInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const validLangs: string[] = [];
    const seen = new Set<string>();

    for (const token of rawTokens) {
      const canonical = normalizeLanguageCode(token);
      if (canonical && !seen.has(canonical)) {
        seen.add(canonical);
        validLangs.push(canonical);
      }
    }

    const updatedViewers = [...rule.viewers];
    updatedViewers[index] = { ...updatedViewers[index], languages: validLangs };
    onChange({ type: "viewers", viewers: updatedViewers });
  };

  return (
    <div className="rounded-xl border border-gray-300 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      <div className="mb-4 border-b border-gray-200 pb-3 dark:border-gray-700">
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Viewer Language Intersection Filter (V2)
        </h3>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Add people and the languages they speak or understand. Filter finds movies that satisfy all added viewers (intersection).
        </p>
      </div>

      {/* List of current viewers */}
      <div className="space-y-3 mb-6">
        {rule.viewers.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-300 p-4 text-center text-xs text-gray-500 dark:border-gray-700 dark:text-gray-400">
            No viewers configured yet. Add viewers below (e.g. Andrea: EN, IT & Mayu: JA).
          </div>
        ) : (
          rule.viewers.map((viewer, index) => (
            <ViewerRow
              key={index}
              viewer={viewer}
              onRemove={() => handleRemoveViewer(index)}
              onUpdateLangs={(input) => handleUpdateViewerLangs(index, input)}
            />
          ))
        )}
      </div>

      {/* Container to add a new viewer */}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/60">
        <h4 className="mb-3 text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300">
          + Add Person
        </h4>

        {addError && (
          <div className="mb-3 rounded border border-red-300 bg-red-50 p-2 text-xs text-red-600 dark:border-red-800 dark:bg-red-950/40 dark:text-red-400">
            {addError}
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-12">
          <div className="sm:col-span-4">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              Name
            </label>
            <input
              type="text"
              placeholder="e.g. Andrea"
              value={newViewerName}
              onChange={(e) => setNewViewerName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddViewer(e); }}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          <div className="sm:col-span-6">
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
              Languages Spoken / Understood
            </label>
            <input
              type="text"
              placeholder="e.g. EN, IT"
              value={newViewerLangsInput}
              onChange={(e) => setNewViewerLangsInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddViewer(e); }}
              className="w-full rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-mono text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          <div className="sm:col-span-2 flex items-end">
            <button
              type="button"
              onClick={handleAddViewer}
              className="w-full rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-500 dark:bg-blue-500 dark:hover:bg-blue-400"
            >
              Add Person
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

interface ViewerRowProps {
  viewer: Viewer;
  onRemove: () => void;
  onUpdateLangs: (rawInput: string) => void;
}

function ViewerRow({ viewer, onRemove, onUpdateLangs }: ViewerRowProps) {
  const [editingLangs, setEditingLangs] = useState(viewer.languages.join(", "));
  const [error, setError] = useState("");

  React.useEffect(() => {
    setEditingLangs(viewer.languages.join(", "));
  }, [viewer.languages]);

  const handleBlur = () => {
    setError("");
    const rawTokens = editingLangs
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    const invalidTokens: string[] = [];
    for (const token of rawTokens) {
      if (!normalizeLanguageCode(token)) {
        invalidTokens.push(token);
      }
    }

    if (invalidTokens.length > 0) {
      setError(`Invalid language code(s): ${invalidTokens.join(", ")}`);
      return;
    }

    onUpdateLangs(editingLangs);
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 shadow-xs dark:border-gray-700 dark:bg-gray-900/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-blue-100 font-bold text-xs text-blue-700 dark:bg-blue-900/60 dark:text-blue-300">
            {viewer.name.charAt(0).toUpperCase()}
          </div>
          <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {viewer.name}
          </span>
        </div>

        <div className="flex flex-1 items-center gap-2 min-w-[200px]">
          <span className="text-xs text-gray-500 dark:text-gray-400">Speaks:</span>
          <input
            type="text"
            value={editingLangs}
            onChange={(e) => setEditingLangs(e.target.value)}
            onBlur={handleBlur}
            placeholder="EN, IT"
            className="flex-1 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-mono shadow-xs focus:border-blue-500 focus:outline-none dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          />
        </div>

        <button
          type="button"
          onClick={onRemove}
          className="rounded-lg p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40 dark:hover:text-red-400"
          title={`Remove ${viewer.name}`}
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {error && (
        <div className="mt-1 text-[11px] text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Spoken language badges */}
      <div className="mt-2 flex flex-wrap gap-1">
        {viewer.languages.map((code) => {
          const canonicalName = CANONICAL_LANGUAGE_MAP[code];
          return (
            <span
              key={code}
              className="inline-flex items-center rounded-md bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:bg-blue-950/60 dark:text-blue-300"
            >
              {code} {canonicalName ? `(${canonicalName})` : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
}
