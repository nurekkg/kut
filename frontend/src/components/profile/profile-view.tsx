"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useId } from "react";

import { UserMenu } from "@/components/auth/user-menu";
import { useErrorMessage } from "@/components/settings/shared";
import { api } from "@/lib/api";
import { NOTIFICATIONS_API, NOTIFY_SETTINGS_KEY, type NotifySettings } from "@/lib/notifications/types";

export function ProfileView() {
  const t = useTranslations("Profile");
  const queryClient = useQueryClient();
  const describeError = useErrorMessage();
  const checkboxId = useId();

  const settings = useQuery({
    queryKey: [...NOTIFY_SETTINGS_KEY],
    queryFn: () => api.get<NotifySettings>(`${NOTIFICATIONS_API}/settings`),
  });

  const save = useMutation({
    mutationFn: (value: boolean) =>
      api.patch<NotifySettings>(`${NOTIFICATIONS_API}/settings`, { notify_whatsapp: value }),
    onSuccess: (data) => queryClient.setQueryData([...NOTIFY_SETTINGS_KEY], data),
  });

  const current = settings.data;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-6">
      <UserMenu />
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
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
          <label htmlFor={checkboxId} className="flex items-start gap-3">
            <input
              id={checkboxId}
              type="checkbox"
              className="mt-1 size-5"
              data-testid="notify-whatsapp"
              checked={current.notify_whatsapp}
              disabled={save.isPending}
              onChange={(event) => save.mutate(event.target.checked)}
            />
            <span className="space-y-1">
              <span className="block font-medium">{t("notifications.label")}</span>
              <span className="block text-sm text-muted-foreground">{t("notifications.hint")}</span>
            </span>
          </label>
        ) : null}
        {current && !current.can_notify ? (
          <p className="text-sm text-muted-foreground">{t("notifications.unavailable")}</p>
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
    </main>
  );
}
