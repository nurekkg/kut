import type { MetadataRoute } from "next";
import { getTranslations } from "next-intl/server";

// Манифест PWA: приложение ставится на главный экран телефона и открывается экраном мастера (/m).
// Свой минимальный service worker (public/sw.js) без офлайн-кэша: офлайн-режим - вторая волна.
export default async function manifest(): Promise<MetadataRoute.Manifest> {
  const t = await getTranslations("Pwa");
  return {
    id: "/m",
    name: t("name"),
    short_name: t("shortName"),
    description: t("description"),
    lang: "ru",
    dir: "ltr",
    start_url: "/m",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#ffffff",
    theme_color: "#171717",
    categories: ["business", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        src: "/icons/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      { name: t("shortcutField"), url: "/m" },
      { name: t("shortcutApprovals"), url: "/approvals" },
    ],
  };
}
