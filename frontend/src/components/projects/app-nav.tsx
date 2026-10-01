"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth/auth-provider";
import { cn } from "@/lib/utils";

// Разделы для внутренних ролей; справочник работ виден только тем, у кого есть works:read.
export function AppNav() {
  const t = useTranslations("AppNav");
  const pathname = usePathname();
  const { user } = useAuth();
  const items = [
    { href: "/projects", label: t("projects"), show: true },
    { href: "/works", label: t("works"), show: user?.permissions.includes("works:read") ?? false },
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
              "-mb-px rounded-t-md border-b-2 px-3 py-2.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
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
