"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { DataTable, PanelHeader, QueryState } from "@/components/settings/shared";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { DELIVERIES_KEY, NOTIFICATIONS_API, type Delivery } from "@/lib/notifications/types";

const EVENT_TYPES = ["approval_requested", "approval_approved", "approval_rejected", "delegation_assigned"] as const;
const STATUSES = ["logged", "sent", "retry", "failed", "dead"] as const;
const ERRORS = ["recipient.not_notifiable", "template.not_approved", "timeout", "network", "bad_response"] as const;

type Known<T extends readonly string[]> = T[number];

function known<T extends readonly string[]>(list: T, value: string): value is Known<T> {
  return (list as readonly string[]).includes(value);
}

export function NotificationsPanel() {
  const t = useTranslations("Settings.notifications");
  const common = useTranslations("Settings.common");

  const deliveries = useQuery({
    queryKey: [...DELIVERIES_KEY],
    queryFn: () => api.get<Delivery[]>(`${NOTIFICATIONS_API}/deliveries`, { limit: 200 }),
  });

  const columns = useMemo<ColumnDef<Delivery, unknown>[]>(
    () => [
      {
        id: "when",
        enableSorting: false,
        header: t("when"),
        cell: ({ row }) => (
          <span className="whitespace-nowrap tabular-nums">{formatDateTime(row.original.created_at)}</span>
        ),
      },
      {
        id: "event",
        enableSorting: false,
        header: t("event"),
        cell: ({ row }) =>
          known(EVENT_TYPES, row.original.event_type) ? t(`events.${row.original.event_type}`) : row.original.event_type,
      },
      {
        id: "status",
        enableSorting: false,
        header: t("status"),
        cell: ({ row }) => {
          const status = row.original.status;
          return (
            <span className="inline-flex items-center gap-2">
              {known(STATUSES, status) ? t(`statuses.${status}`) : status}
              {row.original.attempt > 1 ? (
                <span className="text-xs text-muted-foreground">{t("attempt", { number: row.original.attempt })}</span>
              ) : null}
            </span>
          );
        },
      },
      {
        id: "error",
        enableSorting: false,
        header: t("error"),
        cell: ({ row }) => {
          const code = row.original.error_code;
          if (code === null) return common("dash");
          if (known(ERRORS, code)) return t(`errors.${code.replace(".", "_")}`);
          if (code.startsWith("http_")) return t("errors.http", { code: code.slice("http_".length) });
          return code;
        },
      },
      {
        id: "phone",
        enableSorting: false,
        header: t("phone"),
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.phone_masked || common("dash")}</span>
        ),
      },
    ],
    [common, t],
  );

  return (
    <section className="space-y-4" data-testid="notifications-journal">
      <PanelHeader title={t("title")} description={t("intro")} />
      <QueryState loading={deliveries.isPending} error={deliveries.error} onRetry={() => void deliveries.refetch()}>
        <DataTable
          columns={columns}
          data={deliveries.data ?? []}
          getRowId={(row) => row.id}
          empty={t("empty")}
        />
      </QueryState>
    </section>
  );
}
