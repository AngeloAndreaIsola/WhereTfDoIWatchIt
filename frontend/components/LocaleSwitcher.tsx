"use client";

import React, { useId } from "react";
import { useLocale } from "@/lib/i18n/LocaleProvider";
import { SUPPORTED_LOCALES, type SupportedLocale } from "@/messages";

const LOCALE_LABELS: Record<SupportedLocale, string> = {
  en: "English",
  it: "Italiano",
  ja: "日本語",
};

export function LocaleSwitcher() {
  const { locale, setLocale, t } = useLocale();
  const selectId = useId();

  return (
    <div className="relative inline-flex items-center">
      <label htmlFor={selectId} className="sr-only">
        {t("locale.switchLanguage", undefined, "Switch interface language")}
      </label>
      <select
        id={selectId}
        value={locale}
        onChange={(e) => setLocale(e.target.value as SupportedLocale)}
        aria-label={t("locale.switchLanguage", undefined, "Switch interface language")}
        data-testid="locale-switcher"
        className="cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-1 pl-2.5 pr-7 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
      >
        {SUPPORTED_LOCALES.map((code) => (
          <option key={code} value={code}>
            {LOCALE_LABELS[code]}
          </option>
        ))}
      </select>
      <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center px-1.5 text-gray-500 dark:text-gray-400">
        <svg
          className="h-3 w-3"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </div>
  );
}
