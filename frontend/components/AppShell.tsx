import Link from "next/link";
import React from "react";
import { AppNav } from "./AppNav";

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="min-h-screen bg-white text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <header className="border-b border-gray-200 dark:border-gray-800">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4 sm:px-6">
          <Link
            href="/"
            className="text-lg font-bold tracking-tight text-gray-900 hover:opacity-90 dark:text-gray-100"
          >
            Where TF Do I Watch It
          </Link>
          <AppNav />
        </div>
      </header>
      <div>{children}</div>
    </div>
  );
}
