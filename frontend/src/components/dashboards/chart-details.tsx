"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

// Текстовая альтернатива графика: те же числа в таблице, без наведения мышью.
export function ChartDetails({ children }: { children: ReactNode }) {
  const t = useTranslations("Dashboard");
  return (
    <details className="text-sm">
      <summary className="inline-flex min-h-10 cursor-pointer items-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50">
        {t("chartTable")}
      </summary>
      <div className="mt-2 overflow-x-auto">{children}</div>
    </details>
  );
}
