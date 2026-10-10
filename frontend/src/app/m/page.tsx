import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { FieldApp } from "@/components/field/field-app";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Field");
  return { title: t("metadataTitle") };
}

export default function FieldPage() {
  return (
    <AuthGate>
      <FieldApp />
    </AuthGate>
  );
}
