"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { ConfirmModal, Modal, PendingIcon, useRoleLabel } from "@/components/settings/dialogs";
import { InviteView } from "@/components/settings/invite-view";
import { FormMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { LEGAL_ENTITIES_KEY, SETTINGS_API, USERS_KEY } from "@/lib/settings/keys";
import type { AdminUser, Invite, LegalEntity, ProjectRef, Role } from "@/lib/settings/types";

type Props = { user: AdminUser; roles: Role[]; projects: ProjectRef[]; onClose: () => void };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 border-t pt-4">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

function toggled(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((item) => item !== id) : [...list, id];
}

export function UserDetailModal({ user: initial, roles, projects, onClose }: Props) {
  const t = useTranslations("Settings.users");
  const roleLabel = useRoleLabel();
  const queryClient = useQueryClient();
  const { user: me } = useAuth();
  const [user, setUser] = useState(initial);
  const [roleId, setRoleId] = useState(initial.role_id);
  const [projectIds, setProjectIds] = useState(initial.project_ids);
  const [entityIds, setEntityIds] = useState(initial.legal_entity_ids);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [confirmMfa, setConfirmMfa] = useState(false);

  const base = `${SETTINGS_API}/users/${user.id}`;
  const isSelf = me?.id === user.id;
  const external = user.scope === "counterparty";
  const group = user.scope === "group";

  const entities = useQuery({
    queryKey: LEGAL_ENTITIES_KEY,
    queryFn: () => api.get<LegalEntity[]>(`${SETTINGS_API}/legal-entities`),
    enabled: user.scope === "project",
  });

  const run = useMutation({
    mutationFn: (operation: () => Promise<AdminUser>) => operation(),
    onSuccess: async (updated) => {
      setUser(updated);
      setRoleId(updated.role_id);
      setProjectIds(updated.project_ids);
      setEntityIds(updated.legal_entity_ids);
      setConfirmMfa(false);
      await queryClient.invalidateQueries({ queryKey: USERS_KEY });
    },
  });

  const issue = useMutation({
    mutationFn: () => api.post<Invite>(`${base}/invite`),
    onSuccess: setInvite,
  });

  const sameClass = roles.filter((role) => (role.scope === "counterparty") === external);
  const accessChanged =
    projectIds.length !== user.project_ids.length ||
    entityIds.length !== user.legal_entity_ids.length ||
    projectIds.some((id) => !user.project_ids.includes(id)) ||
    entityIds.some((id) => !user.legal_entity_ids.includes(id));

  return (
    <Modal title={t("detailTitle")} description={user.full_name} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="space-y-1 text-sm">
          {user.phone ? <p>{user.phone}</p> : null}
          {user.email ? <p className="text-muted-foreground">{user.email}</p> : null}
          {user.counterparty_name ? <p className="text-muted-foreground">{user.counterparty_name}</p> : null}
          <p className="text-muted-foreground">
            {roleLabel(user.role, user.role_name)} ·{" "}
            {!user.is_active ? t("statusInactive") : user.pending_activation ? t("statusPending") : t("statusActive")}
            {" · "}
            {!user.requires_2fa ? t("mfaNotRequired") : user.mfa_enabled ? t("mfaOn") : t("mfaOff")}
          </p>
        </div>
        <FormMessage error={run.error ?? issue.error} />
        {isSelf ? <p className="text-sm text-muted-foreground">{t("selfNote")}</p> : null}

        {!isSelf ? (
          <Section title={t("sectionRole")}>
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-64 space-y-1.5">
                <Label htmlFor="ud-role" className="sr-only">
                  {t("role")}
                </Label>
                <NativeSelect id="ud-role" value={roleId} onChange={(event) => setRoleId(event.target.value)}>
                  {sameClass.map((role) => (
                    <option key={role.id} value={role.id}>
                      {roleLabel(role.code, role.name)}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <Button
                type="button"
                variant="outline"
                size="lg"
                disabled={run.isPending || roleId === user.role_id}
                onClick={() => run.mutate(() => api.put<AdminUser>(`${base}/role`, { role_id: roleId }))}
              >
                {t("changeRole")}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">{t("roleHint")}</p>
          </Section>
        ) : null}

        <Section title={t("sectionAccess")}>
          {group ? (
            <p className="text-sm text-muted-foreground">{t("groupAccess")}</p>
          ) : (
            <div className="space-y-3">
              <fieldset className="space-y-2">
                <legend className="text-sm text-muted-foreground">{t("projects")}</legend>
                {projects.length === 0 ? <p className="text-sm text-muted-foreground">{t("noProjects")}</p> : null}
                <div className="grid gap-2 sm:grid-cols-2">
                  {projects.map((project) => (
                    <label key={project.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        className="size-4 accent-primary"
                        checked={projectIds.includes(project.id)}
                        onChange={() => setProjectIds((current) => toggled(current, project.id))}
                      />
                      {project.name}
                    </label>
                  ))}
                </div>
              </fieldset>
              {external ? (
                <p className="text-sm text-muted-foreground">{t("externalNoEntities")}</p>
              ) : (
                <fieldset className="space-y-2">
                  <legend className="text-sm text-muted-foreground">{t("legalEntities")}</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(entities.data ?? []).map((entity) => (
                      <label key={entity.id} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="size-4 accent-primary"
                          checked={entityIds.includes(entity.id)}
                          onChange={() => setEntityIds((current) => toggled(current, entity.id))}
                        />
                        {entity.name}
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
              <Button
                type="button"
                size="lg"
                disabled={run.isPending || !accessChanged}
                onClick={() =>
                  run.mutate(() =>
                    api.put<AdminUser>(`${base}/access`, {
                      project_ids: projectIds,
                      legal_entity_ids: external ? [] : entityIds,
                    }),
                  )
                }
              >
                <PendingIcon pending={run.isPending} />
                {t("saveAccess")}
              </Button>
            </div>
          )}
        </Section>

        {!isSelf ? (
          <Section title={t("sectionStatus")}>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="button"
                variant={user.is_active ? "destructive" : "outline"}
                size="lg"
                disabled={run.isPending}
                onClick={() => run.mutate(() => api.put<AdminUser>(`${base}/active`, { active: !user.is_active }))}
              >
                {user.is_active ? t("deactivate") : t("activate")}
              </Button>
              {user.is_active ? <p className="text-sm text-muted-foreground">{t("deactivateHint")}</p> : null}
            </div>
          </Section>
        ) : null}

        {!isSelf ? (
          <Section title={t("sectionSecurity")}>
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  size="lg"
                  disabled={issue.isPending}
                  onClick={() => {
                    setInvite(null);
                    issue.mutate();
                  }}
                >
                  <PendingIcon pending={issue.isPending} />
                  {t("newInvite")}
                </Button>
                <p className="text-sm text-muted-foreground">{t("inviteHint")}</p>
              </div>
              {invite ? <InviteView invite={invite} /> : null}
              {user.mfa_enabled ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Button type="button" variant="outline" size="lg" onClick={() => setConfirmMfa(true)}>
                    {t("resetMfa")}
                  </Button>
                  <p className="text-sm text-muted-foreground">{t("resetMfaHint")}</p>
                </div>
              ) : null}
            </div>
          </Section>
        ) : null}
      </div>
      {confirmMfa ? (
        <ConfirmModal
          title={t("resetMfa")}
          text={t("resetMfaConfirm", { name: user.full_name })}
          confirmLabel={t("resetMfa")}
          pendingLabel={t("resetMfa")}
          pending={run.isPending}
          error={run.error}
          onConfirm={() => run.mutate(() => api.post<AdminUser>(`${base}/reset-mfa`))}
          onClose={() => setConfirmMfa(false)}
        />
      ) : null}
    </Modal>
  );
}
