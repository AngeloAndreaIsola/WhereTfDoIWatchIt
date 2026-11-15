"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ViewersRuleNode } from "./ViewerLanguageBuilder";

interface PreferencesData {
  audio_languages?: string[];
  subtitle_languages?: string[];
  language_rule?: ViewersRuleNode | unknown;
}

interface ViewerPreferencesContextValue {
  viewersRule: ViewersRuleNode;
  setViewersRule: (rule: ViewersRuleNode) => void;
  loading: boolean;
  error: string | null;
}

const DEFAULT_VIEWERS_RULE: ViewersRuleNode = {
  type: "viewers",
  viewers: [
    { name: "Andrea", languages: ["EN", "IT"] },
    { name: "Mayu", languages: ["JA"] },
  ],
};

const ViewerPreferencesContext = createContext<ViewerPreferencesContextValue>({
  viewersRule: DEFAULT_VIEWERS_RULE,
  setViewersRule: () => {},
  loading: false,
  error: null,
});

function isViewersRuleNode(value: unknown): value is ViewersRuleNode {
  return !!value && typeof value === "object" && (value as ViewersRuleNode).type === "viewers" && Array.isArray((value as ViewersRuleNode).viewers);
}

export function extractViewerLanguages(rule: ViewersRuleNode): string[] {
  const languages = new Set<string>();

  for (const viewer of rule.viewers) {
    for (const language of viewer.languages) {
      const normalized = language.trim().toUpperCase();
      if (normalized) languages.add(normalized);
    }
  }

  return Array.from(languages);
}

export function ViewerPreferencesProvider({ children }: { children: React.ReactNode }) {
  const [viewersRule, setViewersRuleState] = useState<ViewersRuleNode>(DEFAULT_VIEWERS_RULE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const skipNextSave = useRef(false);

  useEffect(() => {
    let active = true;

    fetch("/api/preferences/")
      .then(async (response) => {
        if (!response.ok) throw new Error(`Failed to load preferences (${response.status})`);
        return response.json() as Promise<PreferencesData>;
      })
      .then((data) => {
        if (!active) return;
        if (isViewersRuleNode(data.language_rule)) {
          skipNextSave.current = true;
          setViewersRuleState(data.language_rule);
        }
      })
      .catch((loadError: unknown) => {
        if (active) setError(loadError instanceof Error ? loadError.message : "Failed to load preferences");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (loading) return;
    if (skipNextSave.current) {
      skipNextSave.current = false;
      return;
    }

    const timeout = window.setTimeout(() => {
      const languages = extractViewerLanguages(viewersRule);

      fetch("/api/preferences/", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_languages: languages,
          subtitle_languages: languages,
          language_rule: viewersRule,
        }),
      })
        .then((response) => {
          if (!response.ok) throw new Error(`Failed to save preferences (${response.status})`);
          setError(null);
        })
        .catch((saveError: unknown) => {
          setError(saveError instanceof Error ? saveError.message : "Failed to save preferences");
        });
    }, 500);

    return () => window.clearTimeout(timeout);
  }, [loading, viewersRule]);

  const setViewersRule = useCallback((rule: ViewersRuleNode) => {
    setViewersRuleState(rule);
  }, []);

  return (
    <ViewerPreferencesContext.Provider value={{ viewersRule, setViewersRule, loading, error }}>
      {children}
    </ViewerPreferencesContext.Provider>
  );
}

export function useViewerPreferences() {
  return useContext(ViewerPreferencesContext);
}
