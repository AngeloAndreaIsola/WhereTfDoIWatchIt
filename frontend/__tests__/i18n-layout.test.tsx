import React, { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot, type Root } from "react-dom/client";
import RootLayout from "@/app/layout";
import { AppNav } from "@/components/AppNav";
import { getLocaleFromCookieString, LOCALE_COOKIE_NAME } from "@/lib/i18n";

const serverCookie = vi.hoisted(() => ({ value: undefined as string | undefined }));

vi.mock("next/font/local", () => ({ default: () => ({ variable: "test-font" }) }));
vi.mock("next/headers", () => ({
  cookies: () => ({ get: () => serverCookie.value === undefined ? undefined : { value: serverCookie.value } }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/catalog" }));
vi.mock("@/components/AppShell", () => ({ AppShell: ({ children }: { children: React.ReactNode }) => children }));
vi.mock("@/components/ViewerPreferencesProvider", () => ({ ViewerPreferencesProvider: ({ children }: { children: React.ReactNode }) => children }));
vi.mock("@/components/CurrentLocationProvider", () => ({ CurrentLocationProvider: ({ children }: { children: React.ReactNode }) => children }));

describe("WT-006: root layout locale hydration", () => {
  let root: Root | undefined;
  const recoverableErrors: unknown[] = [];

  beforeEach(() => {
    serverCookie.value = undefined;
    localStorage.clear();
    document.cookie = `${LOCALE_COOKIE_NAME}=; max-age=0; path=/`;
    vi.spyOn(window.navigator, "languages", "get").mockReturnValue(["ja-JP"]);
    recoverableErrors.length = 0;
  });

  afterEach(async () => {
    if (root) await act(async () => root?.unmount());
    root = undefined;
    document.body.innerHTML = "";
  });

  async function renderAndHydrate(expectedServerLocale: string) {
    // Exercise the actual server layout and its exact LocaleProvider props.
    const layout = RootLayout({ children: <AppNav /> });
    expect(renderToString(layout)).toContain(`<html lang="${expectedServerLocale}">`);
    document.documentElement.lang = layout.props.lang;
    // Next owns document hydration. Hydrate the layout's actual provider subtree
    // here so browser-inserted <head> nodes do not require Next's head manager.
    const content = layout.props.children.props.children;
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    document.body.appendChild(container);
    const consoleError = vi.spyOn(console, "error");
    await act(async () => {
      root = hydrateRoot(container, content, {
        onRecoverableError: error => recoverableErrors.push(error),
      });
    });
    expect(recoverableErrors).toEqual([]);
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  }

  it("uses a Japanese browser on first visit after hydrating the English server default", async () => {
    await renderAndHydrate("en");
    expect(screen.getByRole("link", { name: "カタログ" })).toHaveAttribute("href", "/catalog");
    expect(screen.getByTestId("locale-switcher")).toHaveValue("ja");
    expect(document.documentElement.lang).toBe("ja");
  });

  it("uses saved Italian ahead of the browser when no cookie exists", async () => {
    localStorage.setItem(LOCALE_COOKIE_NAME, "It");
    await renderAndHydrate("en");
    expect(screen.getByRole("link", { name: "Catalogo" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("it");
  });

  it("respects an explicit English cookie ahead of storage and browser", async () => {
    serverCookie.value = "en";
    document.cookie = `${LOCALE_COOKIE_NAME}=en; path=/`;
    localStorage.setItem(LOCALE_COOKIE_NAME, "it");
    await renderAndHydrate("en");
    expect(screen.getByRole("link", { name: "Catalog" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });

  it("normalizes a mixed-case server cookie before server rendering and hydration", async () => {
    serverCookie.value = "Ja";
    await renderAndHydrate("ja");
    expect(screen.getByRole("link", { name: "カタログ" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("ja");
  });

  it("persists a switch through the next server render and reload", async () => {
    await renderAndHydrate("en");
    fireEvent.change(screen.getByTestId("locale-switcher"), { target: { value: "it" } });
    expect(document.documentElement.lang).toBe("it");
    expect(localStorage.getItem(LOCALE_COOKIE_NAME)).toBe("it");
    serverCookie.value = getLocaleFromCookieString(document.cookie) ?? undefined;
    await act(async () => root?.unmount());
    root = undefined;
    document.body.innerHTML = "";
    await renderAndHydrate("it");
    expect(screen.getByTestId("locale-switcher")).toHaveValue("it");
    expect(screen.getByRole("link", { name: "Catalogo" })).toHaveAttribute("href", "/catalog");
    expect(document.documentElement.lang).toBe("it");
  });

  it.each(["fr", "%E0%A4%A"])("ignores unsupported or malformed cookie %s and uses storage", async cookie => {
    serverCookie.value = cookie;
    document.cookie = `${LOCALE_COOKIE_NAME}=${cookie}; path=/`;
    localStorage.setItem(LOCALE_COOKIE_NAME, "it");
    await renderAndHydrate("en");
    expect(screen.getByTestId("locale-switcher")).toHaveValue("it");
    expect(document.documentElement.lang).toBe("it");
  });

  it("falls back to English for unsupported storage and browser locales", async () => {
    localStorage.setItem(LOCALE_COOKIE_NAME, "not-a-locale");
    vi.spyOn(window.navigator, "languages", "get").mockReturnValue(["fr-FR"]);
    await renderAndHydrate("en");
    expect(screen.getByTestId("locale-switcher")).toHaveValue("en");
    expect(document.documentElement.lang).toBe("en");
  });
});
