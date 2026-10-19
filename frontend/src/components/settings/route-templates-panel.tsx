"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { useDocumentErrorMessage } from "@/components/documents/shared";
import { Button } from "@/components/ui/button";
import { DOCUMENTS_KEY, documentsApi, useRouteTemplates } from "@/lib/documents/queries";
import { isRouteRoleCode, ROUTE_ROLE_CODES, type RouteRoleCode } from "@/lib/documents/route";
import type { RouteTemplate } from "@/lib/documents/types";

// Шаблоны маршрутов: владелец и техдиректор меняют порядок ролей и состав шагов. Уже отправленные маршруты
// не меняются: сервер сохраняет их снимком на попытку (ADR-015 часть 5).
export function RouteTemplatesPanel() {
  const t = useTranslations("Settings.routes");
  const templates = useRouteTemplates(true);
  const message = useDocumentErrorMessage();

  return (
    <section className="space-y-6" aria-labelledby="route-templates-title" data-testid="route-templates">
      <div className="space-y-1">
        <h2 id="route-templates-title" className="text-xl font-semibold tracking-tight">
          {t("title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("text")}</p>
      </div>
      {templates.isPending ? <p className="text-sm text-muted-foreground">…</p> : null}
      {templates.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(templates.error)}
        </p>
      ) : null}
      {(templates.data ?? []).map((template) => (
        <TemplateEditor key={template.code} template={template} />
      ))}
    </section>
  );
}

function TemplateEditor({ template }: { template: RouteTemplate }) {
  const t = useTranslations("Settings.routes");
  const roles = useTranslations("Roles");
  const message = useDocumentErrorMessage();
  const queryClient = useQueryClient();
  const [steps, setSteps] = useState<string[]>(() =>
    [...template.steps].sort((a, b) => a.position - b.position).map((step) => step.role_code),
  );
  const [adding, setAdding] = useState<RouteRoleCode>(ROUTE_ROLE_CODES[0]);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: () => documentsApi.saveRouteTemplate(template.code, steps),
    onSuccess: async () => {
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
    },
  });

  function edit(next: string[]) {
    setSaved(false);
    setSteps(next);
  }

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    const next = [...steps];
    [next[index], next[target]] = [next[target], next[index]];
    edit(next);
  }

  const title = template.code === "money" ? t("money") : template.code === "other" ? t("other") : template.name;

  return (
    <article className="space-y-3 rounded-xl border p-4" data-testid="route-template" data-code={template.code}>
      <div className="space-y-1">
        <h3 className="text-base font-semibold">{title}</h3>
        {template.code === "money" ? <p className="text-sm text-muted-foreground">{t("moneyHint")}</p> : null}
      </div>

      {steps.length === 0 ? <p className="text-sm text-destructive">{t("empty")}</p> : null}
      <ol className="flex flex-col gap-2">
        {steps.map((role, index) => (
          <li key={`${index}-${role}`} className="flex items-center gap-2" data-testid="route-template-step">
            <span className="w-6 shrink-0 text-sm tabular-nums text-muted-foreground">{index + 1}</span>
            <select
              value={role}
              aria-label={t("stepRole", { position: index + 1 })}
              onChange={(event) => {
                const next = [...steps];
                next[index] = event.target.value;
                edit(next);
              }}
              className="h-11 min-w-0 flex-1 rounded-lg border border-input bg-transparent px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
              data-testid="route-template-role"
            >
              {ROUTE_ROLE_CODES.map((code) => (
                <option key={code} value={code}>
                  {roles(code)}
                </option>
              ))}
              {isRouteRoleCode(role) ? null : <option value={role}>{role}</option>}
            </select>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-11 shrink-0"
              aria-label={t("up")}
              disabled={index === 0}
              onClick={() => move(index, -1)}
            >
              <ArrowUp aria-hidden />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-11 shrink-0"
              aria-label={t("down")}
              disabled={index === steps.length - 1}
              onClick={() => move(index, 1)}
            >
              <ArrowDown aria-hidden />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="icon"
              className="size-11 shrink-0"
              aria-label={t("remove")}
              onClick={() => edit(steps.filter((_, position) => position !== index))}
              data-testid="route-template-remove"
            >
              <Trash2 aria-hidden />
            </Button>
          </li>
        ))}
      </ol>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <select
          value={adding}
          aria-label={t("addStep")}
          onChange={(event) => setAdding(event.target.value as RouteRoleCode)}
          className="h-11 min-w-0 rounded-lg border border-input bg-transparent px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
        >
          {ROUTE_ROLE_CODES.map((code) => (
            <option key={code} value={code}>
              {roles(code)}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          className="h-11"
          onClick={() => edit([...steps, adding])}
          data-testid="route-template-add"
        >
          <Plus data-icon="inline-start" aria-hidden />
          {t("addStep")}
        </Button>
      </div>

      {save.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(save.error)}
        </p>
      ) : null}
      {saved && !save.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          {t("saved")}
        </p>
      ) : null}
      <Button
        type="button"
        size="lg"
        className="h-11 w-full sm:w-auto"
        disabled={steps.length === 0 || save.isPending}
        onClick={() => save.mutate()}
        data-testid="route-template-save"
      >
        {t("save")}
      </Button>
    </article>
  );
}
