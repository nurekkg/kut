import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { EquipmentFieldApp } from "@/components/equipment/field/field-app";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("EquipmentField");
  return { title: t("metadataTitle") };
}

export default function EquipmentFieldPage() {
  return (
    <AuthGate>
      <EquipmentFieldApp />
    </AuthGate>
  );
}
