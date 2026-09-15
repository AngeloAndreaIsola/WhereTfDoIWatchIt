import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
import { ViewerPreferencesProvider } from "@/components/ViewerPreferencesProvider";
import { CurrentLocationProvider } from "@/components/CurrentLocationProvider";

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
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        <ViewerPreferencesProvider>
          <CurrentLocationProvider>
            <AppShell>{children}</AppShell>
          </CurrentLocationProvider>
        </ViewerPreferencesProvider>
      </body>
    </html>
  );
}
