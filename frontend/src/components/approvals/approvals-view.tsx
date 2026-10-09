"use client";

import { useMutation, useQueries, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { CheckCheck, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { ApprovalCard } from "@/components/approvals/approval-card";
import { RejectDialog } from "@/components/approvals/reject-dialog";
import { PhotoLightbox } from "@/components/field/photo-lightbox";
import { AppNav } from "@/components/projects/app-nav";
import { QueryState, useCodeMessage, useErrorMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { APPROVAL_LIMIT, useApprovalQueue } from "@/lib/approvals";
import { ApiError } from "@/lib/auth/api";
import type { Actual, Photo } from "@/lib/field/types";
import { API } from "@/lib/projects/keys";
import { useNodeLabels } from "@/lib/projects/labels";
import { photosKey } from "@/lib/schedule/keys";
import { invalidateProgress } from "@/lib/schedule/queries";

const PHOTO_CHUNK = 100;
const PARALLEL = 3;

function combinePhotos(results: UseQueryResult<Photo[], Error>[]): Map<string, Photo[]> {
  const byFact = new Map<string, Photo[]>();
  for (const result of results) {
    for (const photo of result.data ?? []) {
      const list = byFact.get(photo.work_actual_id);
      if (list) list.push(photo);
      else byFact.set(photo.work_actual_id, [photo]);
    }
  }
  return byFact;
}

type Batch = { running: boolean; total: number; done: number; failed: Record<string, string> };

// Очередь подтверждения: отправленные факты всех объектов человека, от старых к новым. Подтвердить
// можно по одному и пачкой; отклонение требует причины. После решения запись уходит из очереди,
// а проценты узла и объекта пересчитываются на сервере.
export function ApprovalsView() {
  const t = useTranslations("Approvals");
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canApprove = user?.permissions.includes("work_actual:approve") ?? false;
  const queue = useApprovalQueue(canApprove);
  const message = useErrorMessage();
  const codeMessage = useCodeMessage();
  const labels = useNodeLabels(queue.ids);

  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [rejecting, setRejecting] = useState<Actual | null>(null);
  const [viewer, setViewer] = useState<{ fact: string; index: number } | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [batch, setBatch] = useState<Batch | null>(null);

  // Снимки подгружаются пачками по сто фактов на объект; ссылки короткие, поэтому обновляются.
  const photoRequests = useMemo(() => {
    const byProject = new Map<string, string[]>();
    for (const fact of queue.facts) {
      if (!fact.photo_count) continue;
      const list = byProject.get(fact.project_id) ?? [];
      list.push(fact.id);
      byProject.set(fact.project_id, list);
    }
    const requests: { projectId: string; ids: string[] }[] = [];
    for (const [projectId, ids] of byProject) {
      for (let start = 0; start < ids.length; start += PHOTO_CHUNK) {
        requests.push({ projectId, ids: ids.slice(start, start + PHOTO_CHUNK) });
      }
    }
    return requests;
  }, [queue.facts]);
  const photos = useQueries({
    queries: photoRequests.map((request) => ({
      queryKey: photosKey(request.projectId, `queue:${request.ids.join(",")}`),
      queryFn: () =>
        api.get<Photo[]>(`${API}/projects/${request.projectId}/photos`, {
          work_actual_id: request.ids,
          with_urls: true,
        }),
      staleTime: 0,
      refetchInterval: 90_000,
      enabled: canApprove,
    })),
    combine: combinePhotos,
  });

  const approve = useMutation({
    mutationFn: (fact: Actual) =>
      api.post<Actual>(`${API}/projects/${fact.project_id}/work-actuals/${fact.id}/approve`),
    onSuccess: (_result, fact) => {
      setErrors((current) => {
        const next = { ...current };
        delete next[fact.id];
        return next;
      });
      setSelected((current) => {
        const next = new Set(current);
        next.delete(fact.id);
        return next;
      });
      return invalidateProgress(queryClient, fact.project_id);
    },
    onError: (error, fact) => {
      setErrors((current) => ({ ...current, [fact.id]: message(error) }));
    },
  });

  async function approveSelected() {
    const targets = queue.facts.filter((fact) => selected.has(fact.id));
    if (targets.length === 0) return;
    const failed: Record<string, string> = {};
    let done = 0;
    setBatch({ running: true, total: targets.length, done, failed });
    const waiting = [...targets];
    async function worker() {
      for (let fact = waiting.shift(); fact; fact = waiting.shift()) {
        try {
          await api.post<Actual>(
            `${API}/projects/${fact.project_id}/work-actuals/${fact.id}/approve`,
          );
          done += 1;
        } catch (error) {
          failed[fact.id] = error instanceof ApiError ? codeMessage(error.code) : codeMessage("network");
        }
        setBatch({ running: true, total: targets.length, done, failed: { ...failed } });
      }
    }
    await Promise.all(Array.from({ length: PARALLEL }, () => worker()));
    setBatch({ running: false, total: targets.length, done, failed });
    setErrors((current) => ({ ...current, ...failed }));
    setSelected(new Set(Object.keys(failed)));
    await Promise.all([...new Set(targets.map((fact) => fact.project_id))].map((id) => invalidateProgress(queryClient, id)));
  }

  const allSelected = queue.facts.length > 0 && queue.facts.every((fact) => selected.has(fact.id));
  const viewerFact = viewer ? queue.facts.find((fact) => fact.id === viewer.fact) : undefined;
  const viewerPhotos = viewerFact ? (photos.get(viewerFact.id) ?? []) : [];

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-4 px-4 py-4">
      <UserMenu />
      <AppNav />
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      {!canApprove ? (
        <div role="alert" className="space-y-1 rounded-xl border p-4" data-testid="approvals-forbidden">
          <p className="font-medium">{t("forbiddenTitle")}</p>
          <p className="text-sm text-muted-foreground">{t("forbiddenText")}</p>
        </div>
      ) : (
        <QueryState
          loading={queue.pending}
          error={queue.error}
          onRetry={() => void queue.projects.refetch()}
        >
          {queue.facts.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground" data-testid="approvals-empty">
              {t("empty")}
            </p>
          ) : (
            <>
              <div className="sticky top-0 z-20 -mx-4 flex flex-wrap items-center gap-3 border-b bg-background/95 px-4 py-3 backdrop-blur">
                <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-5 accent-primary"
                    checked={allSelected}
                    onChange={(event) =>
                      setSelected(event.target.checked ? new Set(queue.facts.map((fact) => fact.id)) : new Set())
                    }
                    data-testid="select-all"
                  />
                  {t("selectAll")}
                </label>
                <p className="text-sm text-muted-foreground" data-testid="approvals-total">
                  {t("total", {
                    count: queue.facts.length,
                    limit: queue.facts.length >= APPROVAL_LIMIT ? `${APPROVAL_LIMIT}+` : String(queue.facts.length),
                  })}
                </p>
                <Button
                  type="button"
                  size="lg"
                  className="ml-auto h-12"
                  disabled={selected.size === 0 || batch?.running === true}
                  onClick={() => void approveSelected()}
                  data-testid="approve-selected"
                >
                  {batch?.running ? (
                    <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" aria-hidden />
                  ) : (
                    <CheckCheck data-icon="inline-start" aria-hidden />
                  )}
                  {t("approveSelected", { count: selected.size })}
                </Button>
              </div>
              {batch ? (
                <p
                  role="status"
                  className="rounded-lg bg-muted/60 p-3 text-sm"
                  data-testid="batch-result"
                >
                  {batch.running
                    ? t("batchRunning", { done: batch.done + Object.keys(batch.failed).length, total: batch.total })
                    : Object.keys(batch.failed).length === 0
                      ? t("batchDone", { count: batch.done })
                      : t("batchPartial", { done: batch.done, failed: Object.keys(batch.failed).length })}
                </p>
              ) : null}
              <ul className="space-y-3">
                {queue.facts.map((fact) => (
                  <ApprovalCard
                    key={fact.id}
                    fact={fact}
                    label={labels.get(fact.node_id) ?? "…"}
                    photos={photos.get(fact.id) ?? []}
                    selected={selected.has(fact.id)}
                    busy={approve.isPending && approve.variables?.id === fact.id}
                    error={errors[fact.id] ?? null}
                    onSelect={(checked) =>
                      setSelected((current) => {
                        const next = new Set(current);
                        if (checked) next.add(fact.id);
                        else next.delete(fact.id);
                        return next;
                      })
                    }
                    onApprove={() => approve.mutate(fact)}
                    onReject={() => setRejecting(fact)}
                    onOpenPhoto={(index) => setViewer({ fact: fact.id, index })}
                  />
                ))}
              </ul>
            </>
          )}
        </QueryState>
      )}

      {rejecting ? (
        <RejectDialog
          fact={rejecting}
          onClose={() => setRejecting(null)}
          onDone={() => {
            setSelected((current) => {
              const next = new Set(current);
              next.delete(rejecting.id);
              return next;
            });
            setRejecting(null);
          }}
        />
      ) : null}
      {viewerFact ? (
        <PhotoLightbox
          projectId={viewerFact.project_id}
          photos={viewerPhotos}
          index={viewer ? viewer.index : null}
          onIndex={(index) => setViewer({ fact: viewerFact.id, index })}
          onClose={() => setViewer(null)}
          describe={() => labels.get(viewerFact.node_id) ?? ""}
        />
      ) : null}
    </main>
  );
}
