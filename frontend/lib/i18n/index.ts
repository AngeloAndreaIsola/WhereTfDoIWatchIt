import {
  messages,
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  normalizeLocale,
  type SupportedLocale,
} from "../../messages";

export * from "../../messages";

export const LOCALE_COOKIE_NAME = "NEXT_LOCALE";

/**
 * Resolves a nested key (e.g. "nav.home") from a catalog.
 */
function getNestedValue(catalog: unknown, path: string): string | undefined {
  if (!catalog || typeof catalog !== "object") return undefined;
  const parts = path.split(".");
  let curr: unknown = catalog;
  for (const part of parts) {
    if (curr && typeof curr === "object" && part in curr) {
      curr = (curr as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return typeof curr === "string" ? curr : undefined;
}

/**
 * ICU-compatible template interpolation: replaces `{var}` with provided params.
 */
export function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, key) => {
    if (key in params) {
      return String(params[key]);
    }
    return match;
  });
}

/**
 * Translates a key with fallback chain:
 * 1. Target locale catalog
 * 2. Default locale ('en') catalog
 * 3. Fallback string / raw key if not found
 */
export function translate(
  locale: SupportedLocale,
  key: string,
  params?: Record<string, string | number>,
  fallback?: string
): string {
  const normLocale = normalizeLocale(locale) ?? DEFAULT_LOCALE;
  const targetCatalog = messages[normLocale] ?? messages[DEFAULT_LOCALE];
  let msg = getNestedValue(targetCatalog, key);

  if (msg === undefined && normLocale !== DEFAULT_LOCALE) {
    msg = getNestedValue(messages[DEFAULT_LOCALE], key);
  }

  if (msg === undefined) {
    return fallback ?? key;
  }

  return interpolate(msg, params);
}

/**
 * Detects locale from browser settings (navigator.languages or navigator.language).
 */
export function detectBrowserLocale(acceptedLocales: readonly SupportedLocale[] = SUPPORTED_LOCALES): SupportedLocale {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return DEFAULT_LOCALE;
  }

  const browserLangs = navigator.languages || [navigator.language || ""];
  for (const lang of browserLangs) {
    if (!lang) continue;
    const prefix = lang.split("-")[0].trim().toLowerCase();
    const normalized = normalizeLocale(prefix);
    if (normalized && acceptedLocales.includes(normalized)) {
      return normalized;
    }
  }

  return DEFAULT_LOCALE;
}

/**
 * Reads the locale cookie from document.cookie (client-side) or a cookie string.
 * Normalizes to exact lowercase canonical SupportedLocale.
 */
export function getLocaleFromCookieString(cookieString?: string): SupportedLocale | null {
  if (!cookieString) return null;
  const match = cookieString.match(new RegExp(`(?:^|; )${LOCALE_COOKIE_NAME}=([^;]*)`));
  if (match) {
    const val = decodeURIComponent(match[1]);
    return normalizeLocale(val);
  }
  return null;
}

/**
 * Sets the locale cookie in document.cookie for client persistence across navigation and reloads.
 * Always stores the canonical lowercase locale.
 */
export function setLocaleCookie(locale: string): void {
  if (typeof document === "undefined") return;
  const normalized = normalizeLocale(locale) ?? DEFAULT_LOCALE;
  const maxAge = 60 * 60 * 24 * 365; // 1 year
  document.cookie = `${LOCALE_COOKIE_NAME}=${normalized}; path=/; max-age=${maxAge}; SameSite=Lax`;
  try {
    localStorage.setItem(LOCALE_COOKIE_NAME, normalized);
  } catch {
    // Ignore storage quota / access issues
  }
}

/**
 * Retrieves the preferred locale checking cookie, then localStorage, then browser detection, defaulting to 'en'.
 * Guarantees a canonical lowercase SupportedLocale return value.
 */
export function resolveInitialLocale(): SupportedLocale {
  if (typeof window === "undefined") {
    return DEFAULT_LOCALE;
  }

  // 1. Check cookie
  const cookieLocale = getLocaleFromCookieString(document.cookie);
  if (cookieLocale) return cookieLocale;

  // 2. Check localStorage
  try {
    const stored = localStorage.getItem(LOCALE_COOKIE_NAME);
    const normalizedStored = normalizeLocale(stored);
    if (normalizedStored) return normalizedStored;
  } catch {
    // Ignore error
  }

  // 3. Fallback to browser detection
  return detectBrowserLocale();
}
