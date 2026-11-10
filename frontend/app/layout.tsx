import type { Metadata } from "next";
import localFont from "next/font/local";
import { cookies } from "next/headers";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { ViewerPreferencesProvider } from "@/components/ViewerPreferencesProvider";
import { CurrentLocationProvider } from "@/components/CurrentLocationProvider";
import { LocaleProvider } from "@/lib/i18n/LocaleProvider";
import { LOCALE_COOKIE_NAME } from "@/lib/i18n";
import { isSupportedLocale, DEFAULT_LOCALE, type SupportedLocale } from "@/messages";

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
  let initialLocale: SupportedLocale = DEFAULT_LOCALE;
  try {
    const cookieStore = cookies();
    const cookieVal = cookieStore.get(LOCALE_COOKIE_NAME)?.value;
    if (isSupportedLocale(cookieVal)) {
      initialLocale = cookieVal;
    }
  } catch {
    // If cookies() is unavailable (e.g. static rendering), fallback safely to DEFAULT_LOCALE
  }

  return (
    <html lang={initialLocale}>
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
