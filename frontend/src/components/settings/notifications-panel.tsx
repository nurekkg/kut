"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo } from "react";

import { DataTable, PanelHeader, QueryState } from "@/components/settings/shared";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { DELIVERIES_KEY, NOTIFICATIONS_API, type Delivery } from "@/lib/notifications/types";

const EVENT_TYPES = ["approval_requested", "approval_approved", "approval_rejected", "delegation_assigned"] as const;
const STATUSES = ["logged", "sent", "retry", "deferred", "disabled", "failed", "dead"] as const;
// Коды ошибок доставки -> ключи сообщений (точка в ключе next-intl не годится).
const ERROR_KEYS = {
  "recipient.not_notifiable": "recipient_not_notifiable",
  "template.not_approved": "template_not_approved",
  "render.error": "render_error",
  adapter_error: "adapter_error",
  sending_disabled: "sending_disabled",
  timeout: "timeout",
  network: "network",
  bad_response: "bad_response",
} as const;

type ErrorCode = keyof typeof ERROR_KEYS;

function isErrorCode(value: string): value is ErrorCode {
  return Object.hasOwn(ERROR_KEYS, value);
}

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

  // Четыре колонки, чтобы журнал читался на 375 px: получатель - под событием, телефон и ошибка - в статусе.
  const columns = useMemo<ColumnDef<Delivery, unknown>[]>(
    () => [
      {
        id: "when",
        enableSorting: false,
        header: t("when"),
        cell: ({ row }) => <span className="tabular-nums">{formatDateTime(row.original.created_at)}</span>,
      },
      {
        id: "event",
        enableSorting: false,
        header: t("event"),
        cell: ({ row }) => (
          <div className="space-y-1">
            <p>
              {known(EVENT_TYPES, row.original.event_type)
                ? t(`events.${row.original.event_type}`)
                : row.original.event_type}
            </p>
            {row.original.recipient_role_name ? (
              <p className="text-xs text-muted-foreground">
                {t("recipient", { role: row.original.recipient_role_name })}
              </p>
            ) : null}
          </div>
        ),
      },
      {
        id: "document",
        enableSorting: false,
        header: t("document"),
        cell: ({ row }) => {
          const { document_id: id, document_number: number } = row.original;
          if (!id) return common("dash");
          return (
            <Link
              href={`/documents/${id}`}
              className="break-words font-medium text-primary underline-offset-4 hover:underline"
            >
              {number ?? common("dash")}
            </Link>
          );
        },
      },
      {
        id: "status",
        enableSorting: false,
        header: t("status"),
        cell: ({ row }) => {
          const { status, attempt, error_code: code, phone_masked: phone, defer_until: deferUntil } = row.original;
          let errorText: string | null = null;
          if (code !== null) {
            if (isErrorCode(code)) errorText = t(`errors.${ERROR_KEYS[code]}`);
            else if (code.startsWith("http_")) errorText = t("errors.http", { code: code.slice("http_".length) });
            else errorText = code;
          }
          let statusText: string = known(STATUSES, status) ? t(`statuses.${status}`) : status;
          if (status === "deferred" && deferUntil) {
            statusText = t("statuses.deferred_until", { date: formatDateTime(deferUntil) });
          }
          return (
            <div className="space-y-1">
              <p className="flex flex-wrap items-center gap-2">
                {statusText}
                {attempt > 1 ? (
                  <span className="text-xs text-muted-foreground">{t("attempt", { number: attempt })}</span>
                ) : null}
              </p>
              <p className="font-mono text-xs text-muted-foreground">{phone || common("dash")}</p>
              {errorText ? <p className="text-xs text-muted-foreground">{errorText}</p> : null}
            </div>
          );
        },
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
