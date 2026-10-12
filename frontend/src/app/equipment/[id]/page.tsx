import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";

import { EquipmentShell } from "@/components/equipment/shell";
import { UnitCardView } from "@/components/equipment/unit-card-view";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Equipment.metadata");
  return { title: t("unit") };
}

export default async function UnitPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Идентификатор попадает в адреса запросов к API: принимаем только UUID.
  if (!UUID.test(id)) notFound();
  return (
    <EquipmentShell>
      <UnitCardView unitId={id} />
    </EquipmentShell>
  );
}
