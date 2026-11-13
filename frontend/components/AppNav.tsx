"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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

export function AppNav() {
  const pathname = usePathname();
  const { t } = useLocale();

  return (
    <nav className="flex items-center gap-1 sm:gap-2">
      {navItems.filter((item) => !item.hidden).map((item) => {
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : pathname === item.href || pathname.startsWith(`${item.href}/`);

        return (
          <Link
            key={item.href}
            href={item.href}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              isActive
                ? "bg-gray-100 text-gray-900 dark:bg-gray-800 dark:text-gray-100"
                : "text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800/50 dark:hover:text-gray-100"
            }`}
          >
            {t(item.labelKey, undefined, item.defaultLabel)}
          </Link>
        );
      })}
      <div className="ml-1 pl-1 sm:ml-2 sm:pl-2 border-l border-gray-200 dark:border-gray-800">
        <LocaleSwitcher />
      </div>
    </nav>
  );
}
