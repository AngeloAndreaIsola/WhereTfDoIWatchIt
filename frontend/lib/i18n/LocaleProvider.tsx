"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  DEFAULT_LOCALE,
  normalizeLocale,
  type SupportedLocale,
  translate,
  setLocaleCookie,
  resolveInitialLocale,
} from "./index";

export interface LocaleContextValue {
  locale: SupportedLocale;
  setLocale: (newLocale: SupportedLocale | string) => void;
  t: (key: string, params?: Record<string, string | number>, fallback?: string) => string;
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: DEFAULT_LOCALE,
  setLocale: () => {},
  t: (key: string, params?: Record<string, string | number>, fallback?: string) =>
    translate(DEFAULT_LOCALE, key, params, fallback),
});

interface LocaleProviderProps {
  children: React.ReactNode;
  initialLocale?: SupportedLocale | string;
}

export function LocaleProvider({ children, initialLocale }: LocaleProviderProps) {
  const normalizedInitial = normalizeLocale(initialLocale);
  const [locale, setLocaleState] = useState<SupportedLocale>(normalizedInitial ?? DEFAULT_LOCALE);
  const hasExplicitInitial = useRef(Boolean(normalizedInitial));

  // Synchronize document.documentElement.lang whenever locale changes
  useEffect(() => {
    if (typeof document !== "undefined" && document.documentElement) {
      document.documentElement.lang = locale;
    }
  }, [locale]);

  useEffect(() => {
    // If explicit initialLocale was provided (e.g. from SSR layout cookie), respect it.
    // Otherwise resolve on mount from cookie/storage/browser.
    if (!hasExplicitInitial.current) {
      const detected = resolveInitialLocale();
      if (detected !== locale) {
        setLocaleState(detected);
      }
    }
  }, [locale]);

  const setLocale = useCallback((newLocale: SupportedLocale | string) => {
    const normalized = normalizeLocale(newLocale);
    if (normalized) {
      setLocaleState(normalized);
      setLocaleCookie(normalized);
      if (typeof document !== "undefined" && document.documentElement) {
        document.documentElement.lang = normalized;
      }
    }
  }, []);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>, fallback?: string) => {
      return translate(locale, key, params, fallback);
    },
    [locale]
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  const context = useContext(LocaleContext);
  if (!context) {
    throw new Error("useLocale must be used within a LocaleProvider");
  }
  return context;
}
