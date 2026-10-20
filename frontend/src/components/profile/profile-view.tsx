"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { QueryState, useErrorMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useMyDelegations } from "@/lib/documents/queries";
import { formatDate, formatDateTime } from "@/lib/format";
import { NOTIFICATIONS_API, NOTIFY_SETTINGS_KEY, type NotifySettings } from "@/lib/notifications/types";
import { isRole } from "@/lib/roles";

export function ProfileView() {
  const t = useTranslations("Profile");
  const common = useTranslations("Settings.common");
  const roles = useTranslations("Roles");
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const describeError = useErrorMessage();
  const checkboxId = useId();
  const [consenting, setConsenting] = useState(false);
  const [accepted, setAccepted] = useState(false);

  const settings = useQuery({
    queryKey: [...NOTIFY_SETTINGS_KEY],
    queryFn: () => api.get<NotifySettings>(`${NOTIFICATIONS_API}/settings`),
  });

  const save = useMutation({
    mutationFn: (value: boolean) =>
      api.patch<NotifySettings>(`${NOTIFICATIONS_API}/settings`, { notify_whatsapp: value }),
    onSuccess: (data) => {
      queryClient.setQueryData([...NOTIFY_SETTINGS_KEY], data);
      setConsenting(false);
      setAccepted(false);
    },
  });

  const current = settings.data;
  const delegations = useMyDelegations(current?.can_notify === true);
  // Отозванные замещения в списке не показываем: сервер отдаёт их с признаком revoked.
  const activeDelegations = (delegations.data ?? []).filter((row) => !row.revoked);
  const closeConsent = () => {
    setConsenting(false);
    setAccepted(false);
  };
  const showSection = settings.isPending || settings.error !== null || current?.can_notify === true;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-6">
      <UserMenu />
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      {showSection ? (
        <section className="space-y-3 rounded-xl p-4 ring-1 ring-foreground/10">
          <h2 className="text-base font-medium">{t("notifications.title")}</h2>
          {settings.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              {t("loading")}
            </p>
          ) : null}
          {settings.error ? (
            <p role="alert" className="text-sm">
              {describeError(settings.error)}
            </p>
          ) : null}
          {current?.can_notify ? (
            <>
              <label htmlFor={checkboxId} className="flex items-start gap-3">
                <input
                  id={checkboxId}
                  type="checkbox"
                  className="mt-1 size-5"
                  data-testid="notify-whatsapp"
                  checked={current.notify_whatsapp || consenting}
                  disabled={save.isPending}
                  onChange={(event) => {
                    if (event.target.checked) {
                      setConsenting(true);
                    } else if (consenting) {
                      closeConsent();
                    } else {
                      save.mutate(false);
                    }
                  }}
                />
                <span className="space-y-1">
                  <span className="block font-medium">{t("notifications.label")}</span>
                  <span className="block text-sm text-muted-foreground">{t("notifications.hint")}</span>
                </span>
              </label>
              {consenting ? (
                <div className="space-y-3 rounded-lg bg-muted p-4 text-sm" data-testid="notify-consent">
                  <p className="font-medium">{t("notifications.consent_title")}</p>
                  <p>{t("notifications.consent_messages")}</p>
                  <p>{t("notifications.consent_content")}</p>
                  <p>{t("notifications.consent_revoke")}</p>
                  <p>{t("notifications.consent_optional")}</p>
                  <label className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-5"
                      data-testid="notify-consent-checkbox"
                      checked={accepted}
                      disabled={save.isPending}
                      onChange={(event) => setAccepted(event.target.checked)}
                    />
                    <span>{t("notifications.consent_checkbox")}</span>
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      size="lg"
                      className="h-11"
                      disabled={!accepted || save.isPending}
                      onClick={() => save.mutate(true)}
                      data-testid="notify-consent-save"
                    >
                      {t("notifications.consent_save")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="lg"
                      className="h-11"
                      disabled={save.isPending}
                      onClick={closeConsent}
                    >
                      {common("cancel")}
                    </Button>
                  </div>
                </div>
              ) : null}
              {current.consent_at ? (
                <p className="text-sm text-muted-foreground">
                  {t("notifications.consent_given", { date: formatDateTime(current.consent_at) })}
                </p>
              ) : null}
              {current.revoked_at ? (
                <p className="text-sm text-muted-foreground">
                  {t("notifications.consent_revoked", { date: formatDateTime(current.revoked_at) })}
                </p>
              ) : null}
            </>
          ) : null}
          {save.error ? (
            <p role="alert" className="text-sm">
              {describeError(save.error)}
            </p>
          ) : null}
          {save.isSuccess ? (
            <p role="status" className="text-sm text-muted-foreground">
              {t("notifications.saved")}
            </p>
          ) : null}
        </section>
      ) : null}
      {current?.can_notify ? (
        <section className="space-y-3 rounded-xl p-4 ring-1 ring-foreground/10" data-testid="my-delegations">
          <h2 className="text-base font-medium">{t("delegations.title")}</h2>
          <QueryState loading={delegations.isPending} error={delegations.error} onRetry={() => void delegations.refetch()}>
            {activeDelegations.length ? (
              <ul className="space-y-3">
                {activeDelegations.map((row) => {
                  const me = user?.id;
                  const delegatorName = row.delegator_name ?? common("dash");
                  const delegateName = row.delegate_name ?? common("dash");
                  let party: string;
                  if (me && row.delegate_id === me) {
                    party = t("delegations.delegator", { name: delegatorName });
                  } else if (me && row.delegator_id === me) {
                    party = t("delegations.delegate", { name: delegateName });
                  } else {
                    party = t("delegations.both", { delegator: delegatorName, delegate: delegateName });
                  }
                  return (
                    <li key={row.id} className="space-y-1 rounded-lg bg-muted p-3 text-sm">
                      <p className="font-medium">{party}</p>
                      <p>
                        {t("delegations.role", {
                          role: isRole(row.role_code) ? roles(row.role_code) : row.role_code,
                        })}
                      </p>
                      <p className="text-muted-foreground">
                        {t("delegations.period", {
                          from: formatDate(row.valid_from) ?? common("dash"),
                          to: formatDate(row.valid_to) ?? common("dash"),
                        })}
                      </p>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">{t("delegations.empty")}</p>
            )}
          </QueryState>
        </section>
      ) : null}
    </main>
  );
}
