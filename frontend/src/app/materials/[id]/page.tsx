import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { MaterialCard } from "@/components/stock/material-card";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Stock.materials");
  return { title: t("cardTitle") };
}

export default async function MaterialPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // Идентификатор попадает в адрес запроса к API: принимаем только UUID.
  if (!UUID.test(id)) notFound();
  return (
    <AuthGate>
      <MaterialCard materialId={id} />
    </AuthGate>
  );
}
