import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { StockIndex } from "@/components/stock/stock-index";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Stock.index");
  return { title: t("title") };
}

export default function StockPage() {
  return (
    <AuthGate>
      <StockIndex />
    </AuthGate>
  );
}
