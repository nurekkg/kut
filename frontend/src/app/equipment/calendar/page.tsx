import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { CalendarView } from "@/components/equipment/calendar-view";
import { EquipmentShell } from "@/components/equipment/shell";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("calendar") };
}

export default function CalendarPage() {
  return (
    <EquipmentShell>
      <CalendarView />
    </EquipmentShell>
  );
}
