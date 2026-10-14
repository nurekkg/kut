import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { EquipmentShell } from "@/components/equipment/shell";
import { JournalView } from "@/components/equipment/journal-view";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("journal") };
}

export default function JournalPage() {
  return (
    <EquipmentShell>
      <JournalView />
    </EquipmentShell>
  );
}
