"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useLocale } from "@/lib/i18n/LocaleProvider";
import { LocaleSwitcher } from "./LocaleSwitcher";

interface NavConfig {
  href: string;
  labelKey: string;
  defaultLabel: string;
  hidden?: boolean;
}

const navItems: NavConfig[] = [
  { href: "/", labelKey: "nav.home", defaultLabel: "Home" },
  { href: "/catalog", labelKey: "nav.catalog", defaultLabel: "Catalog" },
  { href: "/watchlist", labelKey: "nav.watchlist", defaultLabel: "Watchlist" },
  { href: "/watched", labelKey: "nav.watched", defaultLabel: "Watched" },
  { href: "/preferences", labelKey: "nav.preferences", defaultLabel: "Preferences", hidden: true },
  { href: "/subscriptions", labelKey: "nav.subscriptions", defaultLabel: "Subscriptions" },
  { href: "/candidate-review", labelKey: "nav.candidateReview", defaultLabel: "Candidate Review" },
];

function isActivePath(pathname: string, href: string): boolean {
  return href === "/"
    ? pathname === "/"
    : pathname === href || pathname.startsWith(`${href}/`);
}

function NavLinks({
  pathname,
  t,
  layout,
}: {
  pathname: string;
  t: (key: string, params?: Record<string, string | number>, fallback?: string) => string;
  layout: "inline" | "stacked";
}) {
  return (
    <>
      {navItems.filter((item) => !item.hidden).map((item) => {
        const isActive = isActivePath(pathname, item.href);
        const base =
          layout === "inline"
            ? "rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
            : "block rounded-md px-3 py-2.5 text-sm font-medium transition-colors";
        const active = isActive
          ? "bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100"
          : "text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/50 dark:hover:text-gray-100";
        return (
          <Link key={item.href} href={item.href} className={`${base} ${active}`}>
            {t(item.labelKey, undefined, item.defaultLabel)}
          </Link>
        );
      })}
    </>
  );
}

export function AppNav() {
  const pathname = usePathname();
  const { t } = useLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;

    function handlePointerDown(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMenuOpen(false);
        buttonRef.current?.focus();
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [menuOpen]);

  const openLabel = t("nav.openMenu", undefined, "Open menu");
  const closeLabel = t("nav.closeMenu", undefined, "Close menu");
  const navLabel = t("nav.mainNav", undefined, "Main navigation");

  return (
    <>
      <nav aria-label={navLabel} className="hidden items-center gap-2 lg:flex">
        <NavLinks pathname={pathname} t={t} layout="inline" />
        <div className="ml-2 pl-2 border-l border-gray-200 dark:border-gray-800">
          <LocaleSwitcher />
        </div>
      </nav>

      <div ref={menuRef} className="relative lg:hidden">
        <button
          ref={buttonRef}
          type="button"
          aria-expanded={menuOpen}
          aria-controls="mobile-nav-menu"
          aria-label={menuOpen ? closeLabel : openLabel}
          onClick={() => setMenuOpen((open) => !open)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-md text-gray-600 transition-colors hover:bg-gray-50 hover:text-gray-900 focus:outline-none focus:ring-2 focus:ring-indigo-500 dark:text-gray-400 dark:hover:bg-gray-800/50 dark:hover:text-gray-100"
        >
          {menuOpen ? (
            <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          )}
        </button>

        {menuOpen && (
          <nav
            id="mobile-nav-menu"
            aria-label={navLabel}
            className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-gray-200 bg-white py-2 shadow-lg dark:border-gray-800 dark:bg-gray-900"
          >
            <div className="flex flex-col gap-0.5 px-2">
              <NavLinks pathname={pathname} t={t} layout="stacked" />
            </div>
            <div className="mt-2 border-t border-gray-200 px-4 pb-1 pt-3 dark:border-gray-800">
              <LocaleSwitcher />
            </div>
          </nav>
        )}
      </div>
    </>
  );
}
