"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ListPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { QueryState, useDebounced } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";
import type { TreeNode, Work } from "@/lib/projects/types";
import { formatVolume, toNumber } from "@/lib/schedule/format";
import type { ScheduleItem } from "@/lib/schedule/types";

export type PickedWork = {
  work: Pick<Work, "id" | "code" | "name" | "unit">;
  item: ScheduleItem | null;
};

// Шаг «работа»: работы из ГПР узла с остатком объёма; вид работ вне плана выбирается отдельно и
// сопровождается предупреждением (ТЗ §5.3: предупреждение, но не запрет).
export function WorkStep({
  projectId,
  node,
  onPick,
}: {
  projectId: string;
  node: TreeNode;
  onPick: (picked: PickedWork) => void;
}) {
  const t = useTranslations("Field.work");
  const [other, setOther] = useState(false);
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 250);

  const plan = useQuery({
    queryKey: ["projects", projectId, "field-plan", node.id],
    queryFn: () =>
      api.get<ScheduleItem[]>(`${API}/projects/${projectId}/schedule`, {
        node_id: node.id,
        with_progress: true,
        limit: 100,
      }),
    staleTime: 10_000,
  });
  const own = useMemo(
    () => (plan.data ?? []).filter((item) => item.node_id === node.id),
    [plan.data, node.id],
  );

  const catalog = useQuery({
    queryKey: ["works", "field", debounced],
    queryFn: () => api.get<Work[]>(`${API}/works`, { q: debounced, limit: 20 }),
    enabled: other && debounced.length >= 2,
    staleTime: 60_000,
  });

  function pickFromCatalog(work: Work) {
    // Вид работ из справочника может оказаться в плане узла: тогда факт идёт на его строку.
    onPick({ work, item: own.find((item) => item.work_id === work.id) ?? null });
  }

  return (
    <div className="space-y-4">
      <QueryState loading={plan.isPending} error={plan.error} onRetry={() => void plan.refetch()}>
        {own.length === 0 ? (
          <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
            {t("noPlan")}
          </p>
        ) : (
          <ul className="space-y-2" data-testid="plan-works">
            {own.map((item) => {
              const confirmed = toNumber(item.progress?.confirmed_volume);
              const left = Math.max(toNumber(item.plan_volume) - confirmed, 0);
              const done = item.progress?.state === "done";
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    data-testid="work-option"
                    onClick={() =>
                      onPick({
                        work: {
                          id: item.work_id,
                          code: item.work_code,
                          name: item.work_name,
                          unit: item.unit,
                        },
                        item,
                      })
                    }
                    className="flex min-h-16 w-full flex-col items-start justify-center gap-0.5 rounded-xl border px-4 py-2 text-left outline-none active:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <span className="font-medium break-words">{item.work_name}</span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {done
                        ? t("done")
                        : t("remaining", {
                            plan: formatVolume(item.plan_volume),
                            confirmed: formatVolume(confirmed),
                            left: formatVolume(left),
                            unit: item.unit,
                          })}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </QueryState>

      {other ? (
        <section aria-labelledby="field-other" className="space-y-3 rounded-xl border p-3">
          <h2 id="field-other" className="text-sm font-semibold">
            {t("otherTitle")}
          </h2>
          <p className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {t("otherWarning")}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="field-work-search" className="text-base">
              {t("searchLabel")}
            </Label>
            <Input
              id="field-work-search"
              type="search"
              autoComplete="off"
              className="h-14 text-base"
              placeholder={t("searchPlaceholder")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <QueryState
            loading={catalog.isFetching && debounced.length >= 2}
            error={catalog.error}
            onRetry={() => void catalog.refetch()}
          >
            {debounced.length >= 2 && (catalog.data ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("nothingFound")}</p>
            ) : (
              <ul className="space-y-2">
                {(catalog.data ?? []).map((work) => (
                  <li key={work.id}>
                    <button
                      type="button"
                      data-testid="catalog-option"
                      onClick={() => pickFromCatalog(work)}
                      className="flex min-h-14 w-full flex-col items-start justify-center rounded-xl border px-4 py-2 text-left outline-none active:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      <span className="font-medium break-words">{work.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {work.code} · {work.unit}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </section>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-14 w-full text-base"
          data-testid="other-work"
          onClick={() => setOther(true)}
        >
          <ListPlus data-icon="inline-start" className="size-5" aria-hidden />
          {t("other")}
        </Button>
      )}
    </div>
  );
}
