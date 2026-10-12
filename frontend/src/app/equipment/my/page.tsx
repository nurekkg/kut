import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { MyAssignmentsView } from "@/components/equipment/my-assignments-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("my") };
}

export default function MyAssignmentsPage() {
  return (
    <EquipmentShell>
      <MyAssignmentsView />
    </EquipmentShell>
  );
}
