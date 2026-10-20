"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus, UserCog } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { useRoleLabel } from "@/components/settings/dialogs";
import { DataTable, FormMessage, PanelHeader, QueryState, useDebounced } from "@/components/settings/shared";
import { UserCreateModal } from "@/components/settings/user-create-modal";
import { UserDetailModal } from "@/components/settings/user-detail-modal";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { PROJECTS_KEY, ROLES_KEY, SETTINGS_API, USERS_KEY } from "@/lib/settings/keys";
import type { AdminUser, ProjectRef, Role } from "@/lib/settings/types";

export function UsersPanel() {
  const t = useTranslations("Settings.users");
  const common = useTranslations("Settings.common");
  const roleLabel = useRoleLabel();
  const queryClient = useQueryClient();
  const [role, setRole] = useState("");
  const [project, setProject] = useState("");
  const [active, setActive] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<AdminUser | null>(null);

  const whatsappOff = useMutation({
    mutationFn: (id: string) => api.post(`${SETTINGS_API}/users/${id}/whatsapp-off`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: USERS_KEY }),
  });

  const roles = useQuery({ queryKey: ROLES_KEY, queryFn: () => api.get<Role[]>(`${SETTINGS_API}/roles`) });
  const projects = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<ProjectRef[]>(`${SETTINGS_API}/projects`),
  });
  const users = useQuery({
    queryKey: [...USERS_KEY, { role, project, active, q }],
    queryFn: () =>
      api.get<AdminUser[]>(`${SETTINGS_API}/users`, { role, project_id: project, active, q, limit: 500 }),
  });

  const columns = useMemo<ColumnDef<AdminUser, unknown>[]>(
    () => [
      {
        accessorKey: "full_name",
        header: t("colName"),
        cell: ({ row }) => (
          <div>
            <p className="font-medium">{row.original.full_name}</p>
            {row.original.counterparty_name ? (
              <p className="text-xs text-muted-foreground">{row.original.counterparty_name}</p>
            ) : null}
          </div>
        ),
      },
      {
        id: "contact",
        enableSorting: false,
        header: t("colContact"),
        cell: ({ row }) => (
          <div className="text-sm">
            {row.original.phone ? <p>{row.original.phone}</p> : null}
            {row.original.email ? <p className="text-muted-foreground">{row.original.email}</p> : null}
          </div>
        ),
      },
      {
        accessorKey: "role_name",
        header: t("colRole"),
        cell: ({ row }) => roleLabel(row.original.role, row.original.role_name),
      },
      {
        id: "status",
        enableSorting: false,
        header: t("colStatus"),
        cell: ({ row }) => {
          const user = row.original;
          return (
            <div className="flex flex-wrap gap-1">
              {!user.is_active ? (
                <Badge variant="destructive">{t("statusInactive")}</Badge>
              ) : user.pending_activation ? (
                <Badge variant="outline">{t("statusPending")}</Badge>
              ) : (
                <Badge variant="secondary">{t("statusActive")}</Badge>
              )}
              {user.requires_2fa ? (
                <Badge variant={user.mfa_enabled ? "secondary" : "outline"}>
                  {user.mfa_enabled ? t("mfaOn") : t("mfaOff")}
                </Badge>
              ) : null}
            </div>
          );
        },
      },
      {
        id: "whatsapp",
        enableSorting: false,
        header: t("colWhatsapp"),
        cell: ({ row }) => {
          const user = row.original;
          if (!user.notify_whatsapp) return t("whatsappOff");
          return (
            <div className="space-y-1.5 text-sm">
              <p>
                {user.notify_whatsapp_consent_at
                  ? t("whatsappOn", { date: formatDateTime(user.notify_whatsapp_consent_at) })
                  : t("whatsappOnNoDate")}
              </p>
              <Button
                variant="outline"
                size="sm"
                data-testid={`user-whatsapp-off-${user.id}`}
                disabled={whatsappOff.isPending}
                onClick={() => whatsappOff.mutate(user.id)}
              >
                {t("whatsappTurnOff")}
              </Button>
            </div>
          );
        },
      },
      {
        id: "actions",
        enableSorting: false,
        header: () => <span className="sr-only">{common("edit")}</span>,
        cell: ({ row }) => (
          <div className="flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelected(row.original)}
              aria-label={`${t("open")}: ${row.original.full_name}`}
            >
              <UserCog data-icon="inline-start" aria-hidden />
              {t("open")}
            </Button>
          </div>
        ),
      },
    ],
    [common, roleLabel, t, whatsappOff],
  );

  return (
    <section className="space-y-4">
      <PanelHeader
        title={t("title")}
        description={t("description")}
        action={
          <Button size="lg" onClick={() => setCreating(true)} disabled={!roles.data}>
            <Plus data-icon="inline-start" aria-hidden />
            {t("add")}
          </Button>
        }
      />
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-52 space-y-1.5">
          <Label htmlFor="uf-role">{t("filterRole")}</Label>
          <NativeSelect id="uf-role" value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="">{common("all")}</option>
            {(roles.data ?? []).map((item) => (
              <option key={item.id} value={item.code}>
                {roleLabel(item.code, item.name)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-52 space-y-1.5">
          <Label htmlFor="uf-project">{t("filterProject")}</Label>
          <NativeSelect id="uf-project" value={project} onChange={(event) => setProject(event.target.value)}>
            <option value="">{common("all")}</option>
            {(projects.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="w-44 space-y-1.5">
          <Label htmlFor="uf-active">{t("filterActive")}</Label>
          <NativeSelect id="uf-active" value={active} onChange={(event) => setActive(event.target.value)}>
            <option value="">{common("all")}</option>
            <option value="true">{t("active")}</option>
            <option value="false">{t("inactive")}</option>
          </NativeSelect>
        </div>
        <div className="w-64 space-y-1.5">
          <Label htmlFor="uf-search">{t("searchLabel")}</Label>
          <Input
            id="uf-search"
            type="search"
            placeholder={t("searchPlaceholder")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>
      {whatsappOff.error ? <FormMessage error={whatsappOff.error} /> : null}
      <QueryState loading={users.isPending} error={users.error} onRetry={() => void users.refetch()}>
        <DataTable columns={columns} data={users.data ?? []} getRowId={(row) => row.id} empty={t("empty")} />
      </QueryState>
      {creating && roles.data ? <UserCreateModal roles={roles.data} onClose={() => setCreating(false)} /> : null}
      {selected && roles.data ? (
        <UserDetailModal
          user={selected}
          roles={roles.data}
          projects={projects.data ?? []}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </section>
  );
}
