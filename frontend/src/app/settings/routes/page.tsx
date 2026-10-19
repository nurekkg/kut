import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { RouteTemplatesPanel } from "@/components/settings/route-templates-panel";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Settings.routes");
  return { title: t("metadataTitle") };
}

export default function RoutesPage() {
  return <RouteTemplatesPanel />;
}
