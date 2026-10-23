import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { MaterialsView } from "@/components/stock/materials-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Stock.materials");
  return { title: t("title") };
}

export default function MaterialsPage() {
  return (
    <AuthGate>
      <MaterialsView />
    </AuthGate>
  );
}
