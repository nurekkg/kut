import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { MaintenanceView } from "@/components/equipment/maintenance-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("maintenance") };
}

export default function MaintenancePage() {
  return (
    <EquipmentShell>
      <MaintenanceView />
    </EquipmentShell>
  );
}
