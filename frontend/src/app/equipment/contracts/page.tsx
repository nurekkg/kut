import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ContractsView } from "@/components/equipment/contracts-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("contracts") };
}

export default function ContractsPage() {
  return (
    <EquipmentShell>
      <ContractsView />
    </EquipmentShell>
  );
}
