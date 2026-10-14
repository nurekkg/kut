import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { ProjectEquipment } from "@/components/projects/project-equipment";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("ProjectEquipment");
  return { title: t("title") };
}

export default async function ProjectEquipmentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Идентификатор попадает в адреса запросов к API: принимаем только UUID.
  if (!UUID.test(id)) notFound();
  return (
    <AuthGate>
      <ProjectEquipment projectId={id} />
    </AuthGate>
  );
}
