"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { DataTable, PanelHeader, QueryState, useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { EVENTS_KEY, SETTINGS_API, USERS_KEY } from "@/lib/settings/keys";
import { EVENT_ACTIONS, type AdminUser, type AuthEvent, type EventAction, type EventPage } from "@/lib/settings/types";

const PAGE_SIZE = 50;

function isAction(value: string): value is EventAction {
  return (EVENT_ACTIONS as readonly string[]).includes(value);
}

// Значение datetime-local — местное время без пояса; серверу уходит момент в UTC.
function toIso(local: string): string {
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

export function EventsPanel() {
  const t = useTranslations("Settings.events");
  const common = useTranslations("Settings.common");
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [ipText, setIpText] = useState("");
  const ip = useDebounced(ipText.trim());
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const users = useQuery({
    queryKey: [...USERS_KEY, { role: "", project: "", active: "", q: "" }],
    queryFn: () => api.get<AdminUser[]>(`${SETTINGS_API}/users`, { limit: 500 }),
  });

  const events = useInfiniteQuery({
    queryKey: [...EVENTS_KEY, { action, actor, ip, from, to }],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      api.get<EventPage>(`${SETTINGS_API}/events`, {
        action,
        actor_id: actor,
        ip,
        date_from: from ? toIso(from) : "",
        date_to: to ? toIso(to) : "",
        before: pageParam,
        limit: PAGE_SIZE,
      }),
    getNextPageParam: (page) => page.next_before,
  });

  const rows = useMemo(() => events.data?.pages.flatMap((page) => page.items) ?? [], [events.data]);
  const filtered = action !== "" || actor !== "" || ip !== "" || from !== "" || to !== "";

  const columns = useMemo<ColumnDef<AuthEvent, unknown>[]>(
    () => [
      {
        id: "when",
        enableSorting: false,
        header: t("when"),
        cell: ({ row }) => <span className="whitespace-nowrap tabular-nums">{formatDateTime(row.original.occurred_at)}</span>,
      },
      {
        id: "who",
        enableSorting: false,
        header: t("who"),
        cell: ({ row }) => {
          const event = row.original;
          const name = event.actor_name ?? (event.actor_kind === "system" ? t("system") : t("unknownActor"));
          const subject =
            event.subject_name && event.subject_id !== event.actor_id ? event.subject_name : null;
          return (
            <div>
              <p className="font-medium">{name}</p>
              {subject ? <p className="text-xs text-muted-foreground">{t("subject", { name: subject })}</p> : null}
            </div>
          );
        },
      },
      {
        id: "what",
        enableSorting: false,
        header: t("what"),
        cell: ({ row }) => (isAction(row.original.action) ? t(`actions.${row.original.action}`) : row.original.action),
      },
      {
        id: "where",
        enableSorting: false,
        header: t("where"),
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.ip ?? common("dash")}</span>,
      },
    ],
    [common, t],
  );

  function reset() {
    setAction("");
    setActor("");
    setIpText("");
    setFrom("");
    setTo("");
  }

  return (
    <section className="space-y-4">
      <PanelHeader title={t("title")} description={t("description")} />
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-60 space-y-1.5">
          <Label htmlFor="ev-action">{t("action")}</Label>
          <NativeSelect id="ev-action" value={action} onChange={(event) => setAction(event.target.value)}>
            <option value="">{common("all")}</option>
            {EVENT_ACTIONS.map((item) => (
              <option key={item} value={item}>
                {t(`actions.${item}`)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-56 space-y-1.5">
          <Label htmlFor="ev-actor">{t("actor")}</Label>
          <NativeSelect id="ev-actor" value={actor} onChange={(event) => setActor(event.target.value)}>
            <option value="">{common("all")}</option>
            {(users.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.full_name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-40 space-y-1.5">
          <Label htmlFor="ev-ip">{t("ip")}</Label>
          <Input id="ev-ip" inputMode="decimal" placeholder={t("ipPlaceholder")} value={ipText} onChange={(event) => setIpText(event.target.value)} />
        </div>
        <div className="w-48 space-y-1.5">
          <Label htmlFor="ev-from">{t("from")}</Label>
          <Input id="ev-from" type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} />
        </div>
        <div className="w-48 space-y-1.5">
          <Label htmlFor="ev-to">{t("to")}</Label>
          <Input id="ev-to" type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} />
        </div>
        {filtered ? (
          <Button type="button" variant="ghost" size="lg" onClick={reset}>
            {t("reset")}
          </Button>
        ) : null}
      </div>
      <QueryState loading={events.isPending} error={events.error} onRetry={() => void events.refetch()}>
        <DataTable columns={columns} data={rows} getRowId={(row) => String(row.id)} empty={t("empty")} />
        {events.hasNextPage ? (
          <div className="flex justify-center">
            <Button
              type="button"
              variant="outline"
              size="lg"
              disabled={events.isFetchingNextPage}
              onClick={() => void events.fetchNextPage()}
            >
              {events.isFetchingNextPage ? (
                <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" aria-hidden />
              ) : null}
              {events.isFetchingNextPage ? t("loadingMore") : t("loadMore")}
            </Button>
          </div>
        ) : null}
      </QueryState>
    </section>
  );
}
