"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "@/components/auth/auth-provider";
import { APPROVAL_LIMIT, useApprovalQueue } from "@/lib/approvals";
import { approvalTotal } from "@/lib/approvals-count";
import { useShifts } from "@/lib/equipment/shifts";
import { cn } from "@/lib/utils";

// Счётчик «на согласовании»: сумма очередей по всем видимым объектам. Те же запросы, что у очереди
// (общий кэш), обновление раз в минуту. «N+» - только если хотя бы один объект упёрся в лимит выборки.
function ApprovalsCount({ approveFacts, equipment }: { approveFacts: boolean; equipment: boolean }) {
  const t = useTranslations("AppNav");
  // Факты считает только тот, кто их подтверждает (work_actual:approve); мастер счётчик фактов не видит.
  const { perProject } = useApprovalQueue(approveFacts);
  // Листы техники, которые пользователь может подтвердить: сервер отбирает по правилу гарда (approvable).
  const shifts = useShifts({ status: "submitted", approvable: true }, equipment);
  const { total: facts, capped } = approvalTotal(perProject, APPROVAL_LIMIT);
  const total = facts + (equipment ? (shifts.data?.length ?? 0) : 0);
  if (total === 0) return null;
  return (
    <span
      className="ml-1.5 inline-flex min-w-5 items-center justify-center rounded-full bg-destructive px-1.5 text-xs font-semibold text-white"
      data-testid="approvals-count"
      aria-label={t("approvalsCount", { count: total })}
      title={t("approvalsAllObjects")}
    >
      {capped ? `${total}+` : String(total)}
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
  const equipmentApprover = can("equipment:approve:shift") || can("equipment:write:after_approval");
  const equipmentListed = equipmentApprover || can("equipment:write:shift");
  const items = [
    { href: "/projects", label: t("projects"), show: can("objects:read"), counter: false },
    { href: "/dashboard", label: dash("nav"), show: user?.scope === "group" && can("objects:read"), counter: false },
    { href: "/works", label: t("works"), show: can("works:read"), counter: false },
    {
      href: "/approvals",
      label: t("approvals"),
      // Очередь читает список объектов (objects:read): без него раздела нет, счётчик не запрашивает /projects.
      show: can("objects:read") && (can("work_actual:approve") || equipmentListed),
      counter: true,
    },
    { href: "/m", label: t("field"), show: can("work_actual:write"), counter: false },
    { href: "/equipment", label: t("equipment"), show: can("equipment:read") && user?.role !== "machinist", counter: false },
    { href: "/equipment/my", label: t("equipmentMy"), show: user?.role === "machinist", counter: false },
    { href: "/m/equipment", label: t("equipmentField"), show: user?.role === "machinist", counter: false },
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
            {item.counter ? (
              <ApprovalsCount
                approveFacts={can("work_actual:approve")}
                equipment={equipmentListed}
              />
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}
