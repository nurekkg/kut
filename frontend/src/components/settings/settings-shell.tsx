"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/settings/legal-entities", key: "legalEntities", permission: "settings:admin" },
  { href: "/settings/counterparties", key: "counterparties", permission: "settings:admin" },
  { href: "/settings/users", key: "users", permission: "settings:admin" },
  { href: "/settings/events", key: "events", permission: "settings:admin" },
  { href: "/settings/routes", key: "routes", permission: "documents:write:routes" },
] as const;

export function SettingsNav() {
  const t = useTranslations("Settings.nav");
  const pathname = usePathname();
  const { user } = useAuth();
  return (
    <nav aria-label={t("label")} className="flex flex-wrap items-center gap-1 border-b">
      {TABS.filter((tab) => user?.permissions.includes(tab.permission)).map((tab) => {
        const active = pathname.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t(tab.key)}
          </Link>
        );
      })}
      <Link
        href="/projects"
        className="ml-auto px-3 py-2 text-sm text-muted-foreground underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {t("back")}
      </Link>
    </nav>
  );
}

// Права проверяет сервер (403); эта проверка только прячет раздел от тех, кому он не положен.
export function AdminGate({ children }: { children: ReactNode }) {
  const t = useTranslations("Settings");
  const { user } = useAuth();
  const pathname = usePathname();
  // Шаблоны маршрутов правят владелец и техдиректор (ADR-015 часть 5): их страница открыта по своему праву.
  const routesPage =
    pathname.startsWith("/settings/routes") && (user?.permissions.includes("documents:write:routes") ?? false);
  if (!user?.permissions.includes("settings:admin") && !routesPage) {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
        <p role="alert" className="text-sm">
          {t("forbidden")}
        </p>
        <Link href="/projects" className="text-sm text-primary underline-offset-4 hover:underline">
          {t("nav.back")}
        </Link>
      </main>
    );
  }
  return <>{children}</>;
}
