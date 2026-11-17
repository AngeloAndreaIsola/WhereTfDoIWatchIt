import type { Metadata } from "next";
import localFont from "next/font/local";
import { cookies } from "next/headers";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { ViewerPreferencesProvider } from "@/components/ViewerPreferencesProvider";
import { CurrentLocationProvider } from "@/components/CurrentLocationProvider";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { LOCALE_COOKIE_NAME } from "@/lib/i18n";
import { normalizeLocale, DEFAULT_LOCALE, type SupportedLocale } from "@/messages";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "Where TF Do I Watch It",
  description: "Find where to stream any movie — ranked by your preferences",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Only a valid cookie is an explicit preference. The server default must not
  // prevent the provider from detecting storage/browser preferences on mount.
  let initialLocale: SupportedLocale | undefined;
  try {
    const cookieStore = cookies();
    const cookieVal = cookieStore.get(LOCALE_COOKIE_NAME)?.value;
    const normalized = normalizeLocale(cookieVal);
    if (normalized) {
      initialLocale = normalized;
    }
  } catch {
    // Without a server cookie, render the default and detect after hydration.
  }

  return (
    <html lang={initialLocale ?? DEFAULT_LOCALE}>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <LocaleProvider initialLocale={initialLocale}>
          <ViewerPreferencesProvider>
            <CurrentLocationProvider>
              <AppShell>{children}</AppShell>
            </CurrentLocationProvider>
          </ViewerPreferencesProvider>
        </LocaleProvider>
      </body>
    </html>
  );
}
