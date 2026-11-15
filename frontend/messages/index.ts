import { en, type TranslationCatalog } from "./en";
import { it } from "./it";
import { ja } from "./ja";

export type SupportedLocale = "en" | "it" | "ja";

export const SUPPORTED_LOCALES: readonly SupportedLocale[] = ["en", "it", "ja"] as const;
export const DEFAULT_LOCALE: SupportedLocale = "en";

export const messages: Record<SupportedLocale, TranslationCatalog> = {
  en,
  it,
  ja,
};

export function isSupportedLocale(locale: unknown): locale is SupportedLocale {
  return typeof locale === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(locale);
}

/**
 * Normalizes input to canonical lowercase SupportedLocale, or returns null if unsupported.
 */
export function normalizeLocale(locale: unknown): SupportedLocale | null {
  if (typeof locale !== "string") return null;
  const lower = locale.trim().toLowerCase();
  return (SUPPORTED_LOCALES as readonly string[]).includes(lower) ? (lower as SupportedLocale) : null;
}

export * from "./en";
