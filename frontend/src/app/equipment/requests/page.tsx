import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { RequestsView } from "@/components/equipment/requests-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("requests") };
}

export default function RequestsPage() {
  return (
    <EquipmentShell>
      <RequestsView />
    </EquipmentShell>
  );
}
