"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareWarning, Pencil, Send, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState, type FormEvent } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PhotoStrip } from "@/components/field/photo-strip";
import { normalizeVolume, isValidVolume } from "@/components/field/volume-step";
import { FormMessage, QueryState } from "@/components/settings/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { resubmitEntry } from "@/lib/field/entry";
import { enqueue, useOutbox } from "@/lib/field/outbox";
import type { OutboxPhoto } from "@/lib/field/outbox-types";
import { isActualStatus, type Actual } from "@/lib/field/types";
import { API, PROJECTS_KEY } from "@/lib/projects/keys";
import { useNodeLabels } from "@/lib/projects/labels";
import type { Project } from "@/lib/projects/types";
import { formatShortDate, todayIso } from "@/lib/schedule/dates";
import { formatVolume } from "@/lib/schedule/format";
import { actualsKey } from "@/lib/schedule/keys";
import { invalidateProgress } from "@/lib/schedule/queries";
import { cn } from "@/lib/utils";

const TONE: Record<string, string> = {
  draft: "bg-secondary text-muted-foreground",
  submitted: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  rejected: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

export function Records() {
  const t = useTranslations("Field.records");
  const status = useTranslations("Field.status");
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const outbox = useOutbox();
  const [fixing, setFixing] = useState<Actual | null>(null);

  const projects = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
  });
  const ids = useMemo(() => (projects.data ?? []).map((project) => project.id), [projects.data]);

  const facts = useQueries({
    queries: ids.map((id) => ({
      queryKey: actualsKey(id, "mine"),
      queryFn: () => api.get<Actual[]>(`${API}/projects/${id}/work-actuals`, { mine: true, limit: 100 }),
      staleTime: 5_000,
    })),
  });
  const labels = useNodeLabels(ids);

  const rows = facts
    .flatMap((result) => result.data ?? [])
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const pending = facts.some((result) => result.isPending) || projects.isPending;
  const failure = facts.find((result) => result.error)?.error ?? projects.error;

  const submit = useMutation({
    mutationFn: (fact: Actual) =>
      api.post<Actual>(`${API}/projects/${fact.project_id}/work-actuals/${fact.id}/submit`),
    onSuccess: (_result, fact) => invalidateProgress(queryClient, fact.project_id),
  });
  const remove = useMutation({
    mutationFn: (fact: Actual) =>
      api.delete(`${API}/projects/${fact.project_id}/work-actuals/${fact.id}`),
    onSuccess: (_result, fact) => invalidateProgress(queryClient, fact.project_id),
  });

  return (
    <section aria-labelledby="records-title" className="space-y-4" data-testid="records">
      <h1 id="records-title" className="text-2xl font-semibold tracking-tight">
        {t("title")}
      </h1>
      <QueryState
        loading={pending}
        error={failure}
        onRetry={() => {
          void projects.refetch();
          facts.forEach((result) => void result.refetch());
        }}
      >
        {rows.length === 0 ? (
          <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
            {t("empty")}
          </p>
        ) : (
          <ul className="space-y-3">
            {rows.map((fact) => {
              const inQueue = outbox.entries.some((entry) => entry.factId === fact.id);
              return (
                <li
                  key={fact.id}
                  data-testid="record"
                  data-status={fact.status}
                  className="space-y-2 rounded-xl border p-3 text-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium break-words">{fact.work_name ?? "—"}</p>
                    <Badge
                      variant="secondary"
                      className={cn("shrink-0", TONE[fact.status])}
                      data-testid="record-status"
                    >
                      {isActualStatus(fact.status) ? status(fact.status) : fact.status}
                    </Badge>
                  </div>
                  <p className="text-muted-foreground break-words">{labels.get(fact.node_id) ?? "…"}</p>
                  <p className="tabular-nums">
                    {formatVolume(fact.volume)} {fact.unit ?? ""} · {formatShortDate(fact.work_date)}
                    {fact.photo_count ? ` · ${t("photos", { count: fact.photo_count })}` : ""}
                  </p>
                  {fact.unplanned ? (
                    <p className="text-xs text-amber-700 dark:text-amber-400">{t("unplanned")}</p>
                  ) : null}
                  {fact.status === "rejected" ? (
                    <div
                      role="note"
                      className="flex items-start gap-2 rounded-lg bg-red-50 p-2.5 text-red-900 dark:bg-red-950 dark:text-red-200"
                    >
                      <MessageSquareWarning className="mt-0.5 size-4 shrink-0" aria-hidden />
                      <p>
                        <span className="font-medium">{t("reason")}: </span>
                        <span data-testid="reject-reason">{fact.reject_reason}</span>
                      </p>
                    </div>
                  ) : null}
                  {inQueue ? (
                    <p className="text-xs text-muted-foreground">{t("inQueue")}</p>
                  ) : fact.status === "rejected" ? (
                    <Button
                      type="button"
                      size="lg"
                      className="h-12 w-full"
                      onClick={() => setFixing(fact)}
                      data-testid="fix"
                    >
                      <Pencil data-icon="inline-start" aria-hidden />
                      {t("fix")}
                    </Button>
                  ) : fact.status === "draft" ? (
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        size="lg"
                        className="h-12 flex-1"
                        disabled={submit.isPending}
                        onClick={() => submit.mutate(fact)}
                      >
                        <Send data-icon="inline-start" aria-hidden />
                        {t("submit")}
                      </Button>
                      <Button
                        type="button"
                        variant="destructive"
                        size="lg"
                        className="h-12 flex-1"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(fact)}
                      >
                        <Trash2 data-icon="inline-start" aria-hidden />
                        {t("delete")}
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        <FormMessage error={submit.error ?? remove.error} />
      </QueryState>

      {fixing && user ? (
        <FixSheet
          fact={fixing}
          userId={user.id}
          label={labels.get(fixing.node_id) ?? ""}
          onClose={() => setFixing(null)}
        />
      ) : null}
    </section>
  );
}

// Исправление отклонённого факта: объём и дата, новые снимки; запись уходит в очередь устройства
// так же, как новый факт (правка, загрузка снимков, повторная отправка на проверку).
function FixSheet({
  fact,
  userId,
  label,
  onClose,
}: {
  fact: Actual;
  userId: string;
  label: string;
  onClose: () => void;
}) {
  const t = useTranslations("Field.fix");
  const [volume, setVolume] = useState(fact.volume);
  const [workDate, setWorkDate] = useState(fact.work_date);
  const [photos, setPhotos] = useState<OutboxPhoto[]>([]);
  const [error, setError] = useState<string | null>(null);
  const today = todayIso();
  const valid = isValidVolume(volume) && workDate !== "" && workDate <= today;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid) return;
    const patch: Record<string, string | null> = {};
    if (Number(normalizeVolume(volume)) !== Number(fact.volume)) patch.volume = normalizeVolume(volume);
    if (workDate !== fact.work_date) patch.work_date = workDate;
    try {
      await enqueue(
        resubmitEntry({
          userId,
          projectId: fact.project_id,
          factId: fact.id,
          patch,
          label: { node: label, work: fact.work_name ?? "", unit: fact.unit ?? "" },
          payload: {
            node_id: fact.node_id,
            work_id: fact.work_id,
            schedule_item_id: fact.schedule_item_id,
            volume: normalizeVolume(volume),
            work_date: workDate,
            latitude: fact.latitude,
            longitude: fact.longitude,
            accuracy_m: fact.accuracy_m,
          },
          photos,
        }),
      );
      onClose();
    } catch {
      setError(t("saveFailed"));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent closeLabel={t("close")} className="max-h-[92dvh]">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription className="break-words">
            {fact.work_name} · {label}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => void submit(event)} className="space-y-4">
          <p role="note" className="rounded-lg bg-red-50 p-2.5 text-sm text-red-900 dark:bg-red-950 dark:text-red-200">
            {t("reason")}: {fact.reject_reason}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="fix-volume" className="text-base">
              {t("volume", { unit: fact.unit ?? "" })}
            </Label>
            <Input
              id="fix-volume"
              inputMode="decimal"
              autoComplete="off"
              className="h-14 text-2xl font-semibold tabular-nums"
              value={volume}
              onChange={(event) => setVolume(event.target.value)}
              aria-invalid={volume !== "" && !isValidVolume(volume)}
              data-testid="fix-volume"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fix-date" className="text-base">
              {t("date")}
            </Label>
            <Input
              id="fix-date"
              type="date"
              max={today}
              required
              className="h-14 text-base"
              value={workDate}
              onChange={(event) => setWorkDate(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <p className="text-sm font-medium">{t("photos")}</p>
            <PhotoStrip
              photos={photos}
              onAdd={(added) => setPhotos((previous) => [...previous, ...added])}
              onRemove={(id) => setPhotos((previous) => previous.filter((photo) => photo.id !== id))}
            />
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" size="lg" className="h-14 w-full text-base" disabled={!valid} data-testid="resubmit">
            <Send data-icon="inline-start" className="size-5" aria-hidden />
            {t("send")}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
