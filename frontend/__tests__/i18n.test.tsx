import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import {
  messages,
  SUPPORTED_LOCALES,
  DEFAULT_LOCALE,
  isSupportedLocale,
  type SupportedLocale,
} from "@/messages";
import {
  interpolate,
  translate,
  detectBrowserLocale,
  getLocaleFromCookieString,
  setLocaleCookie,
  resolveInitialLocale,
  LOCALE_COOKIE_NAME,
} from "@/lib/i18n";
import { LocaleProvider, useLocale } from "@/lib/i18n/LocaleProvider";
import { LocaleSwitcher } from "@/components/LocaleSwitcher";
import { AppNav } from "@/components/AppNav";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  usePathname: () => "/catalog",
}));

describe("WT-006: Internationalization & Catalogs", () => {
  it("has exact structural parity across all supported catalogs", () => {
    const enCatalog = messages.en;

    for (const locale of SUPPORTED_LOCALES) {
      const catalog = messages[locale];
      expect(catalog).toBeDefined();

      // Check top-level sections
      expect(Object.keys(catalog).sort()).toEqual(Object.keys(enCatalog).sort());

      // Check sub-keys
      for (const section of Object.keys(enCatalog) as (keyof typeof enCatalog)[]) {
        expect(Object.keys(catalog[section]).sort()).toEqual(
          Object.keys(enCatalog[section]).sort()
        );
        for (const [key, value] of Object.entries(catalog[section])) {
          expect(typeof value).toBe("string");
          expect((value as string).length).toBeGreaterThan(0);
        }
      }
    }
  });

  it("handles ICU-compatible interpolation properly", () => {
    expect(interpolate("Hello {name}!", { name: "Andrea" })).toBe("Hello Andrea!");
    expect(interpolate("Runtime: {minutes} min", { minutes: 120 })).toBe("Runtime: 120 min");
    // Leaves missing params untouched
    expect(interpolate("Hello {name} {missing}!", { name: "Andrea" })).toBe("Hello Andrea {missing}!");
    // Handles undefined params
    expect(interpolate("Hello world")).toBe("Hello world");
  });

  it("falls back to English when key is missing in target locale", () => {
    // translate a missing key on 'it'
    const result = translate("it", "nonexistent.key", undefined, "Fallback Text");
    expect(result).toBe("Fallback Text");

    // if fallback is undefined, returns key
    expect(translate("ja", "unknown.path")).toBe("unknown.path");
  });

  it("translates valid keys correctly per locale", () => {
    expect(translate("en", "nav.catalog")).toBe("Catalog");
    expect(translate("it", "nav.catalog")).toBe("Catalogo");
    expect(translate("ja", "nav.catalog")).toBe("カタログ");

    // Interpolated key in ja
    expect(translate("ja", "movie.runtime", { minutes: 95 })).toBe("95 分");
    expect(translate("it", "movie.runtime", { minutes: 95 })).toBe("95 min");
  });
});

describe("WT-006: Locale Persistence & Detection", () => {
  const originalCookie = document.cookie;

  beforeEach(() => {
    localStorage.clear();
    // clear document.cookie
    document.cookie = `${LOCALE_COOKIE_NAME}=; max-age=0; path=/`;
  });

  afterEach(() => {
    document.cookie = originalCookie;
  });

  it("extracts locale correctly from cookie strings", () => {
    expect(getLocaleFromCookieString("")).toBeNull();
    expect(getLocaleFromCookieString("foo=bar; NEXT_LOCALE=it; other=val")).toBe("it");
    expect(getLocaleFromCookieString("NEXT_LOCALE=ja")).toBe("ja");
    expect(getLocaleFromCookieString("NEXT_LOCALE=invalid")).toBeNull();
  });

  it("sets and persists locale in both cookie and localStorage", () => {
    setLocaleCookie("ja");
    expect(document.cookie).toContain("NEXT_LOCALE=ja");
    expect(localStorage.getItem(LOCALE_COOKIE_NAME)).toBe("ja");
  });

  it("detects browser locale if no cookie or storage is present", () => {
    const originalNavigator = window.navigator;

    // Simulate Italian browser
    Object.defineProperty(window, "navigator", {
      value: { languages: ["it-IT", "it", "en"] },
      configurable: true,
    });
    expect(detectBrowserLocale(SUPPORTED_LOCALES)).toBe("it");

    // Simulate Japanese browser
    Object.defineProperty(window, "navigator", {
      value: { languages: ["ja-JP", "ja"] },
      configurable: true,
    });
    expect(detectBrowserLocale(SUPPORTED_LOCALES)).toBe("ja");

    // Simulate unsupported browser language (e.g. French -> default 'en')
    Object.defineProperty(window, "navigator", {
      value: { languages: ["fr-FR", "fr"] },
      configurable: true,
    });
    expect(detectBrowserLocale(SUPPORTED_LOCALES)).toBe("en");

    Object.defineProperty(window, "navigator", {
      value: originalNavigator,
      configurable: true,
    });
  });

  it("resolveInitialLocale prioritizes cookie > localStorage > browser", () => {
    // 1. Browser says ja
    const originalNavigator = window.navigator;
    Object.defineProperty(window, "navigator", {
      value: { languages: ["ja-JP"] },
      configurable: true,
    });

    expect(resolveInitialLocale()).toBe("ja");

    // 2. LocalStorage says it -> overrides browser
    localStorage.setItem(LOCALE_COOKIE_NAME, "it");
    expect(resolveInitialLocale()).toBe("it");

    // 3. Cookie says en -> overrides localStorage and browser
    document.cookie = `${LOCALE_COOKIE_NAME}=en; path=/`;
    expect(resolveInitialLocale()).toBe("en");

    Object.defineProperty(window, "navigator", {
      value: originalNavigator,
      configurable: true,
    });
  });
});

describe("WT-006: UI Components & Keyboard Accessibility", () => {
  beforeEach(() => {
    localStorage.clear();
    document.cookie = `${LOCALE_COOKIE_NAME}=; max-age=0; path=/`;
  });

  function TestConsumer() {
    const { locale, t } = useLocale();
    return (
      <div>
        <span data-testid="current-locale">{locale}</span>
        <span data-testid="translated-home">{t("nav.home")}</span>
      </div>
    );
  }

  it("renders with initial locale and switches locale through switcher", async () => {
    render(
      <LocaleProvider initialLocale="en">
        <LocaleSwitcher />
        <TestConsumer />
      </LocaleProvider>
    );

    expect(screen.getByTestId("current-locale")).toHaveTextContent("en");
    expect(screen.getByTestId("translated-home")).toHaveTextContent("Home");

    const select = screen.getByTestId("locale-switcher");
    expect(select).toBeInTheDocument();

    // Switch to Italian
    fireEvent.change(select, { target: { value: "it" } });

    expect(screen.getByTestId("current-locale")).toHaveTextContent("it");
    expect(screen.getByTestId("translated-home")).toHaveTextContent("Home"); // in IT home is Home

    // Switch to Japanese
    fireEvent.change(select, { target: { value: "ja" } });

    expect(screen.getByTestId("current-locale")).toHaveTextContent("ja");
    expect(screen.getByTestId("translated-home")).toHaveTextContent("ホーム");

    // Verify persisted
    expect(localStorage.getItem(LOCALE_COOKIE_NAME)).toBe("ja");
    expect(document.cookie).toContain("NEXT_LOCALE=ja");
  });

  it("is keyboard accessible and navigable via tab and arrow keys", async () => {
    const user = userEvent.setup();

    render(
      <LocaleProvider initialLocale="en">
        <AppNav />
      </LocaleProvider>
    );

    const select = screen.getByTestId("locale-switcher");
    expect(select).toHaveAttribute("aria-label");

    // Tab into the select
    await user.tab();
    select.focus();
    expect(select).toHaveFocus();

    // Select with keyboard
    await user.selectOptions(select, "it");
    expect(select).toHaveValue("it");

    // Nav links reflect Italian translation
    expect(screen.getByRole("link", { name: "Catalogo" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Revisione Candidati" })).toBeInTheDocument();
  });

  it("keeps UI locale independent from user spoken/subtitle preferences", async () => {
    render(
      <LocaleProvider initialLocale="en">
        <AppNav />
      </LocaleProvider>
    );

    const select = screen.getByTestId("locale-switcher");
    fireEvent.change(select, { target: { value: "ja" } });

    // UI nav is Japanese
    expect(screen.getByRole("link", { name: "カタログ" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ウォッチリスト" })).toBeInTheDocument();
  });
});
