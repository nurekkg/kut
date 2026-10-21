"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";
import { useWarehouses } from "@/lib/stock/queries";

import { IssueForm } from "./issue-form";
import { ReceiptForm } from "./receipt-form";

type Tab = "receipt" | "issue";

// Склад на телефоне: одна рука, крупные кнопки (не меньше 48 px), один блок на экран. Приход и выдача
// отправляются сразу, без очереди на устройстве: при сбое сети форма сохраняет введённое.
export function StockPhoneApp({ projectId }: { projectId: string }) {
  const t = useTranslations("Stock.phone");
  const { user } = useAuth();
  const canStock = user?.permissions.includes("materials:write:stock") ?? false;
  const [tab, setTab] = useState<Tab>("receipt");
  const project = useQuery({
    queryKey: ["project", projectId],
    enabled: canStock,
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
  });
  const warehouses = useWarehouses(canStock ? projectId : null);
  const warehouseId = warehouses.data?.[0]?.id ?? null;

  if (!canStock) return <p className="p-6 text-sm text-muted-foreground">{t("noAccess")}</p>;

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-xl flex-col gap-4 px-4 py-4">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-base font-semibold">{project.data?.name ?? t("title")}</p>
          <Link className="text-sm text-primary underline-offset-4 hover:underline" href="/stock">
            {t("back")}
          </Link>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-2">
        <Button className="min-h-12 text-base" onClick={() => setTab("receipt")} type="button" variant={tab === "receipt" ? "default" : "outline"}>
          {t("tabReceipt")}
        </Button>
        <Button className="min-h-12 text-base" onClick={() => setTab("issue")} type="button" variant={tab === "issue" ? "default" : "outline"}>
          {t("tabIssue")}
        </Button>
      </div>

      {warehouses.isSuccess && warehouseId === null && <p className="text-sm text-destructive">{t("noWarehouse")}</p>}

      {project.data && tab === "receipt" && (
        <ReceiptForm legalEntityId={project.data.legal_entity_id} projectId={projectId} warehouseId={warehouseId} />
      )}
      {project.data && tab === "issue" && (
        <IssueForm legalEntityId={project.data.legal_entity_id} projectId={projectId} warehouseId={warehouseId} />
      )}
    </div>
  );
}
