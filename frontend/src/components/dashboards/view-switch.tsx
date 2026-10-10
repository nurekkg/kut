"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

// Вид объекта: дашборд - по умолчанию, карточка - по ?view=card.
export function ObjectViewSwitch({ view }: { view: "dashboard" | "card" }) {
  const t = useTranslations("Dashboard");
  const pathname = usePathname();
  const options = [
    { key: "dashboard", href: pathname, label: t("viewDashboard"), testId: "view-dashboard" },
    { key: "card", href: `${pathname}?view=card`, label: t("viewCard"), testId: "view-card" },
  ] as const;
  return (
    <nav aria-label={t("viewSwitch")} className="inline-flex rounded-lg bg-muted p-1">
      {options.map((option) => {
        const active = option.key === view;
        return (
          <Link
            key={option.key}
            href={option.href}
            scroll={false}
            aria-current={active ? "true" : undefined}
            data-testid={option.testId}
            className={cn(
              "inline-flex min-h-10 items-center rounded-md px-3 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50 md:min-h-9",
              active
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </Link>
        );
      })}
    </nav>
  );
}
