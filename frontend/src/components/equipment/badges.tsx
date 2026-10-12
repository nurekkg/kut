"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { type DocumentState, type UnitTone, unitStatusTone } from "@/lib/equipment/status";
import type { CalendarMaintenance, ContractStatus, RequestStatus, UnitStatus } from "@/lib/equipment/types";
import { cn } from "@/lib/utils";

const TONE: Record<UnitTone, string> = {
  ok: "border-emerald-600/30 bg-emerald-600/10 text-emerald-800 dark:text-emerald-300",
  info: "border-sky-600/30 bg-sky-600/10 text-sky-800 dark:text-sky-300",
  warn: "border-amber-600/40 bg-amber-500/15 text-amber-900 dark:text-amber-300",
  bad: "border-destructive/30 bg-destructive/10 text-destructive",
};

export function UnitStatusBadge({ status }: { status: UnitStatus }) {
  const t = useTranslations("Equipment.status");
  return (
    <Badge variant="outline" className={cn("h-6 px-2", TONE[unitStatusTone(status)])} data-status={status}>
      {t(status)}
    </Badge>
  );
}

export function DocumentStateBadge({ state }: { state: DocumentState }) {
  const t = useTranslations("Equipment.documentState");
  const tone = state === "expired" ? "bad" : state === "expiring" ? "warn" : "ok";
  return (
    <Badge variant="outline" className={cn("h-6 px-2", TONE[tone])} data-state={state}>
      {t(state)}
    </Badge>
  );
}

export function RequestStatusBadge({ status }: { status: RequestStatus }) {
  const t = useTranslations("Equipment.requestStatus");
  const tone: UnitTone =
    status === "assigned" || status === "closed"
      ? "ok"
      : status === "submitted"
        ? "info"
        : status === "rejected"
          ? "bad"
          : "warn";
  return (
    <Badge variant="outline" className={cn("h-6 px-2", TONE[tone])} data-status={status}>
      {t(status)}
    </Badge>
  );
}

export function ContractStatusBadge({ status }: { status: ContractStatus }) {
  const t = useTranslations("Equipment.contractStatus");
  const tone: UnitTone = status === "active" ? "ok" : status === "draft" ? "warn" : "bad";
  return (
    <Badge variant="outline" className={cn("h-6 px-2", TONE[tone])} data-status={status}>
      {t(status)}
    </Badge>
  );
}

export function MaintenanceKindBadge({ item }: { item: Pick<CalendarMaintenance, "kind"> }) {
  const t = useTranslations("Equipment.maintenanceKind");
  return (
    <Badge variant="outline" className="h-6 px-2" data-kind={item.kind}>
      {t(item.kind)}
    </Badge>
  );
}
