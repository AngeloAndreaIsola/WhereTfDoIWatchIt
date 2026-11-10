"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react";
import {
  DEFAULT_LOCALE,
  isSupportedLocale,
  type SupportedLocale,
  translate,
  setLocaleCookie,
  resolveInitialLocale,
} from "./index";

export interface LocaleContextValue {
  locale: SupportedLocale;
  setLocale: (newLocale: SupportedLocale) => void;
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
  initialLocale?: SupportedLocale;
}

export function LocaleProvider({ children, initialLocale = DEFAULT_LOCALE }: LocaleProviderProps) {
  const [locale, setLocaleState] = useState<SupportedLocale>(initialLocale);

  useEffect(() => {
    // If initialLocale wasn't provided or differed from client cookie/preference, resolve on mount
    const detected = resolveInitialLocale();
    if (detected !== locale) {
      setLocaleState(detected);
    }
  }, [locale]);

  const setLocale = useCallback((newLocale: SupportedLocale) => {
    if (isSupportedLocale(newLocale)) {
      setLocaleState(newLocale);
      setLocaleCookie(newLocale);
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
