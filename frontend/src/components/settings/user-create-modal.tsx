"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/auth/form-parts";
import { FormActions, Modal, PendingIcon, useRoleLabel } from "@/components/settings/dialogs";
import { InviteView } from "@/components/settings/invite-view";
import { FormMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { COUNTERPARTIES_KEY, USERS_KEY } from "@/lib/settings/keys";
import type { AdminUser, Counterparty, Invite, Role } from "@/lib/settings/types";

const BASE = "/api/v1/settings/users";

type Props = { roles: Role[]; onClose: () => void };

export function UserCreateModal({ roles, onClose }: Props) {
  const t = useTranslations("Settings.users");
  const common = useTranslations("Settings.common");
  const roleLabel = useRoleLabel();
  const queryClient = useQueryClient();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [roleId, setRoleId] = useState("");
  const [counterpartyId, setCounterpartyId] = useState("");
  const [created, setCreated] = useState<AdminUser | null>(null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [key] = useState(newIdempotencyKey);

  const role = roles.find((item) => item.id === roleId);
  const external = role?.scope === "counterparty";

  const counterparties = useQuery({
    queryKey: [...COUNTERPARTIES_KEY, { kind: "", q: "" }],
    queryFn: () => api.get<Counterparty[]>("/api/v1/settings/counterparties", { limit: 500 }),
    enabled: external,
  });

  const issue = useMutation({
    mutationFn: (userId: string) => api.post<Invite>(`${BASE}/${userId}/invite`),
    onSuccess: setInvite,
  });

  const create = useMutation({
    mutationFn: (body: Record<string, string | null>) => api.post<AdminUser>(BASE, body, key),
    onSuccess: async (user) => {
      setCreated(user);
      await queryClient.invalidateQueries({ queryKey: USERS_KEY });
      issue.mutate(user.id);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({
      full_name: fullName,
      email: email.trim() || null,
      phone: phone.trim() || null,
      role_id: roleId,
      counterparty_id: external ? counterpartyId || null : null,
    });
  }

  if (created) {
    return (
      <Modal title={t("inviteTitle")} description={created.full_name} onClose={onClose}>
        {invite ? <InviteView invite={invite} /> : null}
        <FormMessage error={issue.error} />
        <DialogFooter>
          {issue.isError ? (
            <Button type="button" variant="outline" size="lg" onClick={() => issue.mutate(created.id)}>
              {t("newInvite")}
            </Button>
          ) : null}
          <Button type="button" size="lg" onClick={onClose} disabled={issue.isPending}>
            <PendingIcon pending={issue.isPending} />
            {t("done")}
          </Button>
        </DialogFooter>
      </Modal>
    );
  }

  return (
    <Modal title={t("createTitle")} description={t("createText")} onClose={onClose}>
      <form method="post" onSubmit={submit} className="space-y-4">
        <Field id="u-name" label={t("fullName")}>
          <Input id="u-name" required maxLength={200} autoFocus value={fullName} onChange={(event) => setFullName(event.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="u-phone" label={t("phone")}>
            <Input id="u-phone" type="tel" autoComplete="off" maxLength={32} placeholder="+996 555 123 456" value={phone} onChange={(event) => setPhone(event.target.value)} />
          </Field>
          <Field id="u-email" label={t("email")}>
            <Input id="u-email" type="email" autoComplete="off" maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} />
          </Field>
        </div>
        <p className="-mt-2 text-sm text-muted-foreground">{t("contactHint")}</p>
        <Field id="u-role" label={t("role")}>
          <NativeSelect id="u-role" required value={roleId} onChange={(event) => setRoleId(event.target.value)}>
            <option value="" disabled>
              {t("choose")}
            </option>
            {roles.map((item) => (
              <option key={item.id} value={item.id}>
                {roleLabel(item.code, item.name)}
              </option>
            ))}
          </NativeSelect>
        </Field>
        {external ? (
          <Field id="u-counterparty" label={t("counterparty")} hint={t("counterpartyHint")}>
            <NativeSelect
              id="u-counterparty"
              required
              value={counterpartyId}
              onChange={(event) => setCounterpartyId(event.target.value)}
              aria-describedby="u-counterparty-hint"
            >
              <option value="" disabled>
                {t("choose")}
              </option>
              {(counterparties.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
        ) : null}
        <FormActions
          pending={create.isPending}
          submitLabel={common("create")}
          pendingLabel={common("creating")}
          onCancel={onClose}
          error={create.error}
        />
      </form>
    </Modal>
  );
}
