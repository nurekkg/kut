"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { AppNav } from "@/components/projects/app-nav";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { formatQuantity } from "@/lib/documents/format";
import { API } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";
import {
  useBalances,
  useBatchBalances,
  useMaterials,
  useMovements,
  useReverseMovement,
  useWarehouses,
} from "@/lib/stock/queries";
import type { MaterialOut, MovementOut } from "@/lib/stock/types";

import { useStockErrorMessage } from "./use-stock-error";

type Tab = "balances" | "movements";

// Склад объекта: остатки (сертифицированные - по партиям), журнал движений только для чтения. Сторно -
// только у кладовщика и только с подтверждением. Внешние роли экранов склада не видят.
export function WarehouseView({ projectId }: { projectId: string }) {
  const t = useTranslations("Stock.warehouse");
  const message = useStockErrorMessage();
  const { user } = useAuth();
  const canRead = (user?.is_internal ?? false) && (user?.permissions.includes("materials:read") ?? false);
  const canReverse = user?.permissions.includes("materials:write:stock") ?? false;

  const project = useQuery({
    queryKey: ["project", projectId],
    enabled: canRead,
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
  });
  const warehouses = useWarehouses(canRead ? projectId : null);
  const [chosen, setChosen] = useState<string | null>(null);
  const warehouseId = chosen ?? warehouses.data?.[0]?.id ?? null;
  const materials = useMaterials(project.data?.legal_entity_id ?? null, false);
  const balances = useBalances(warehouseId);
  const batchBalances = useBatchBalances(warehouseId);
  const movements = useMovements(warehouseId);
  const reverse = useReverseMovement();

  const [tab, setTab] = useState<Tab>("balances");
  const [confirming, setConfirming] = useState<MovementOut | null>(null);
  const [reverseError, setReverseError] = useState<string | null>(null);

  const byId = useMemo(() => {
    const map = new Map<string, MaterialOut>();
    for (const item of materials.data ?? []) map.set(item.id, item);
    return map;
  }, [materials.data]);
  const nameOf = (materialId: string) => byId.get(materialId)?.name ?? materialId;

  if (!canRead) return <p className="p-6 text-sm text-muted-foreground">{t("noAccess")}</p>;

  async function confirmReverse() {
    if (!confirming) return;
    setReverseError(null);
    try {
      await reverse.mutateAsync(confirming.id);
      setConfirming(null);
    } catch (cause) {
      setReverseError(message(cause));
    }
  }

  return (
    <main
      className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-4"
      data-testid="stock-warehouse"
    >
      <UserMenu />
      <AppNav />
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex min-h-10 w-fit items-center rounded-md text-sm text-muted-foreground outline-none hover:text-foreground"
      >
        {t("toProject")}
      </Link>

      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{project.data?.name ?? ""}</p>
        </div>
        {(warehouses.data?.length ?? 0) > 1 && (
          <NativeSelect value={warehouseId ?? ""} onChange={(event) => setChosen(event.target.value)}>
            {warehouses.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        )}
      </header>

      {warehouses.isSuccess && warehouses.data.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("noWarehouse")}</p>
      )}

      <div className="flex gap-2" role="tablist">
        <Button onClick={() => setTab("balances")} type="button" variant={tab === "balances" ? "default" : "outline"}>
          {t("tabBalances")}
        </Button>
        <Button onClick={() => setTab("movements")} type="button" variant={tab === "movements" ? "default" : "outline"}>
          {t("tabMovements")}
        </Button>
      </div>

      {tab === "balances" && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">{t("material")}</th>
                <th className="px-3 py-2 font-medium">{t("batch")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("qty")}</th>
              </tr>
            </thead>
            <tbody>
              {(materials.isSuccess ? (balances.data ?? []) : [])
                .filter((row) => !byId.get(row.material_id)?.requires_certificate)
                .map((row) => (
                  <tr className="border-t" key={`${row.material_id}`}>
                    <td className="px-3 py-2">{nameOf(row.material_id)}</td>
                    <td className="px-3 py-2 text-muted-foreground">-</td>
                    <td className="px-3 py-2 text-right">
                      {formatQuantity(row.qty)} {row.base_unit}
                    </td>
                  </tr>
                ))}
              {(materials.isSuccess ? (batchBalances.data ?? []) : [])
                .filter((row) => byId.get(row.material_id)?.requires_certificate)
                .map((row) => (
                  <tr className="border-t" key={`${row.material_id}-${row.batch_id}`}>
                    <td className="px-3 py-2">{nameOf(row.material_id)}</td>
                    <td className="px-3 py-2">{row.batch_no ?? "-"}</td>
                    <td className="px-3 py-2 text-right">
                      {formatQuantity(row.qty)} {row.base_unit}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          {balances.isSuccess && (balances.data?.length ?? 0) === 0 && (
            <p className="p-4 text-sm text-muted-foreground">{t("emptyBalances")}</p>
          )}
        </div>
      )}

      {tab === "movements" && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">{t("when")}</th>
                <th className="px-3 py-2 font-medium">{t("kind")}</th>
                <th className="px-3 py-2 font-medium">{t("material")}</th>
                <th className="px-3 py-2 text-right font-medium">{t("qty")}</th>
                {canReverse && <th className="px-3 py-2" />}
              </tr>
            </thead>
            <tbody>
              {(movements.data ?? []).map((row) => (
                <tr className="border-t" key={row.id}>
                  <td className="px-3 py-2">{new Date(row.created_at).toLocaleString("ru-RU")}</td>
                  <td className="px-3 py-2">{t(`kinds.${row.kind}`)}</td>
                  <td className="px-3 py-2">{nameOf(row.material_id)}</td>
                  <td className="px-3 py-2 text-right">
                    {formatQuantity(row.qty)} {row.unit}
                  </td>
                  {canReverse && (
                    <td className="px-3 py-2 text-right">
                      {(row.kind === "receipt" || row.kind === "issue") && (
                        <Button className="min-h-12" onClick={() => setConfirming(row)} type="button" variant="outline">
                          {t("reverse")}
                        </Button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {movements.isSuccess && (movements.data?.length ?? 0) === 0 && (
            <p className="p-4 text-sm text-muted-foreground">{t("emptyMovements")}</p>
          )}
        </div>
      )}

      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("reverseTitle")}</DialogTitle>
            <DialogDescription>{t("reverseHint")}</DialogDescription>
          </DialogHeader>
          {reverseError !== null && <p className="text-sm text-destructive">{reverseError}</p>}
          <DialogFooter className="gap-2">
            <Button onClick={() => setConfirming(null)} type="button" variant="outline">
              {t("cancel")}
            </Button>
            <Button disabled={reverse.isPending} onClick={confirmReverse} type="button">
              {t("reverseConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}
