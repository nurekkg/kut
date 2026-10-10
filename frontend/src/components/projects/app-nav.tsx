"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth/auth-provider";
import { APPROVAL_LIMIT, useApprovalQueue } from "@/lib/approvals";
import { cn } from "@/lib/utils";

// Счётчик «на согласовании»: те же запросы, что у очереди (общий кэш), обновление раз в минуту.
function ApprovalsCount() {
  const t = useTranslations("AppNav");
  const { facts } = useApprovalQueue(true);
  if (facts.length === 0) return null;
  const text = facts.length >= APPROVAL_LIMIT ? `${APPROVAL_LIMIT}+` : String(facts.length);
  return (
    <span
      className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-destructive px-1.5 text-xs font-semibold text-white"
      data-testid="approvals-count"
      aria-label={t("approvalsCount", { count: facts.length })}
    >
      {text}
    </span>
  );
}

// Разделы по правам: справочник работ - works:read, очередь подтверждения - work_actual:approve
// (со счётчиком), экран мастера - work_actual:write.
export function AppNav() {
  const t = useTranslations("AppNav");
  const dash = useTranslations("Dashboard");
  const pathname = usePathname();
  const { user } = useAuth();
  const can = (permission: string) => user?.permissions.includes(permission) ?? false;
  const items = [
    { href: "/projects", label: t("projects"), show: true, counter: false },
    { href: "/dashboard", label: dash("nav"), show: user?.scope === "group", counter: false },
    { href: "/works", label: t("works"), show: can("works:read"), counter: false },
    {
      href: "/approvals",
      label: t("approvals"),
      show: can("work_actual:approve"),
      counter: true,
    },
    { href: "/m", label: t("field"), show: can("work_actual:write"), counter: false },
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
              "-mb-px inline-flex items-center rounded-t-md border-b-2 px-3 py-2.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              active
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {item.label}
            {item.counter ? <ApprovalsCount /> : null}
          </Link>
        );
      })}
    </nav>
  );
}
