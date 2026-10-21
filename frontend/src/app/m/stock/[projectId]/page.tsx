import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { StockPhoneApp } from "@/components/stock/mobile/stock-phone-app";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Stock.phone");
  return { title: t("title") };
}

export default async function PhoneStockPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  // Идентификатор попадает в адреса запросов к API: принимаем только UUID.
  if (!UUID.test(projectId)) notFound();
  return (
    <AuthGate>
      <StockPhoneApp projectId={projectId} />
    </AuthGate>
  );
}
