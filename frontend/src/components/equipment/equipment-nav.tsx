"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { useEquipmentAccess } from "@/lib/equipment/access";
import { cn } from "@/lib/utils";

// Разделы модуля видны по правам: заявки - тем, кто подаёт или назначает, договоры - тарифному
// кругу, импорт - ведущим реестр. Машинист видит только свои назначения.
export function EquipmentNav() {
  const t = useTranslations("Equipment.nav");
  const pathname = usePathname();
  const access = useEquipmentAccess();
  const items = [
    { href: "/equipment", label: t("registry"), show: access.canRegistry },
    { href: "/equipment/calendar", label: t("calendar"), show: access.canRegistry },
    {
      href: "/equipment/requests",
      label: t("requests"),
      show: access.canRegistry || access.canRequest || access.canAssign,
    },
    { href: "/equipment/maintenance", label: t("maintenance"), show: access.canRegistry },
    { href: "/equipment/contracts", label: t("contracts"), show: access.canContracts },
    { href: "/equipment/import", label: t("import"), show: access.canWrite },
    { href: "/equipment/my", label: t("my"), show: access.isMachinist },
  ].filter((item) => item.show);
  if (items.length < 2) return null;
  return (
    <nav aria-label={t("label")} className="flex flex-wrap items-center gap-1 border-b">
      {items.map((item) => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px inline-flex items-center rounded-t-md border-b-2 px-3 py-2 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
