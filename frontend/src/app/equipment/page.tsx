import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { RegistryView } from "@/components/equipment/registry-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("registry") };
}

export default function RegistryPage() {
  return (
    <EquipmentShell>
      <RegistryView />
    </EquipmentShell>
  );
}
