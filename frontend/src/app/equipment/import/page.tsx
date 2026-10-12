import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ImportView } from "@/components/equipment/import-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("import") };
}

export default function ImportPage() {
  return (
    <EquipmentShell>
      <ImportView />
    </EquipmentShell>
  );
}
