import type { Metadata, Viewport } from "next";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";
import { PwaRegister } from "@/components/pwa/pwa-register";

import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Metadata");
  const pwa = await getTranslations("Pwa");
  return {
    title: t("title"),
    description: t("description"),
    applicationName: pwa("shortName"),
    appleWebApp: { capable: true, title: pwa("shortName"), statusBarStyle: "default" },
    icons: { apple: "/icons/apple-touch-icon.png" },
  };
}

// Цвет адресной строки браузера и безопасные поля под вырез телефона (экран мастера на весь экран).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#171717",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();

  return (
    <html lang={locale} className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <NextIntlClientProvider>
          <Providers>{children}</Providers>
        </NextIntlClientProvider>
        <PwaRegister />
      </body>
    </html>
  );
}
