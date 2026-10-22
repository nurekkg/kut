"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { formatQuantity } from "@/lib/documents/format";
import { useMaterial } from "@/lib/stock/queries";

// Карточка материала: единицы с коэффициентом, сертификат, план-цена. Строки цены нет, если API её не
// вернул (нет права procurement:read:prices) - пустой блок в DOM не выводим.
export function MaterialCard({ materialId }: { materialId: string }) {
  const t = useTranslations("Stock.materials");
  const material = useMaterial(materialId);

  if (material.isPending) return <p className="p-6 text-sm text-muted-foreground">{t("loading")}</p>;
  if (material.isError || !material.data) return <p className="p-6 text-sm text-destructive">{t("loadError")}</p>;
  const item = material.data;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
      <Link className="text-sm text-primary underline-offset-4 hover:underline" href="/materials">
        {t("backToList")}
      </Link>
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">{item.name}</h1>
        <p className="text-sm text-muted-foreground">
          {t("code")}: {item.code}
        </p>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline">{t("baseUnit", { unit: item.base_unit })}</Badge>
          {item.requires_certificate && <Badge>{t("certRequired")}</Badge>}
          {!item.active && <Badge variant="outline">{t("inactive")}</Badge>}
        </div>
      </header>

      <section className="space-y-2">
        <h2 className="text-lg font-medium">{t("units")}</h2>
        {item.units.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("noUnits")}</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {item.units.map((unit) => (
                <tr className="border-t" key={unit.id}>
                  <td className="py-2">{unit.unit}</td>
                  <td className="py-2 text-right">{t("factor", { factor: formatQuantity(unit.factor), unit: item.base_unit })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {item.plan_price !== null && (
        <section className="space-y-1">
          <h2 className="text-lg font-medium">{t("planPrice")}</h2>
          <p className="text-sm">{t("priceValue", { price: item.plan_price, unit: item.base_unit })}</p>
        </section>
      )}
    </div>
  );
}
