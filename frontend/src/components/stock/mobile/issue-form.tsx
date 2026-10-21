"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { ApiError } from "@/lib/auth/api";
import { api } from "@/lib/api";
import { DOCUMENTS_API } from "@/lib/documents/queries";
import type { DocumentDetail } from "@/lib/documents/types";
import { API } from "@/lib/projects/keys";
import { todayIso } from "@/lib/schedule/dates";
import type { TreeNode } from "@/lib/projects/types";
import { issueLinePayload, issueProblems, unitChoices, type IssueRow } from "@/lib/stock/lines";
import { useBatchBalances, useMaterials, usePostWaybill } from "@/lib/stock/queries";
import { useStepKeys } from "@/lib/stock/step-keys";
import type { MaterialOut } from "@/lib/stock/types";

import { useStockErrorMessage } from "../use-stock-error";

type Draft = IssueRow & { key: number };

// Выдача на телефоне: узел (участок) -> ответственный (мастер участка) -> позиции -> провести сразу,
// без маршрута. ВН создаётся без файла: версия приходит в ответе создания.
export function IssueForm({
  projectId,
  legalEntityId,
  warehouseId,
}: {
  projectId: string;
  legalEntityId: string;
  warehouseId: string | null;
}) {
  const t = useTranslations("Stock.phone.issue");
  const message = useStockErrorMessage();
  const materials = useMaterials(legalEntityId, true);
  const tree = useQuery({
    queryKey: ["projects", projectId, "tree"],
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
  });
  const batches = useBatchBalances(warehouseId);
  const post = usePostWaybill();
  const steps = useStepKeys();
  const [nodeId, setNodeId] = useState("");
  const [rows, setRows] = useState<Draft[]>([]);
  const [picked, setPicked] = useState("");
  const [nextKey, setNextKey] = useState(1);
  const [draftWaybill, setDraftWaybill] = useState<{ id: string; versionId: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const node = tree.data?.find((item) => item.id === nodeId) ?? null;
  const responsible = node?.master_user_id ?? null;
  const choices = materials.data ?? [];

  // Партии сертифицированного материала на складе: только с остатком больше нуля.
  const batchOptions = useMemo(
    () => (batches.data ?? []).filter((row) => row.batch_id !== null && Number(row.qty) > 0),
    [batches.data],
  );

  function addMaterial(material: MaterialOut) {
    setRows((current) => [
      ...current,
      { key: nextKey, material, quantity: "", unit: material.base_unit, batchId: null },
    ]);
    setNextKey((value) => value + 1);
    setPicked("");
  }

  function patch(key: number, change: Partial<Draft>) {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...change } : row)));
  }

  async function submit() {
    setError(null);
    setDone(null);
    if (warehouseId === null) return setError(t("noWarehouse"));
    if (node === null) return setError(t("noNode"));
    if (responsible === null) return setError(t("noResponsible"));
    if (rows.length === 0) return setError(t("noPositions"));
    for (const row of rows) {
      if (issueProblems(row).length > 0) return setError(t("rowProblem", { name: row.material.name }));
    }

    setBusy(true);
    try {
      // Черновик ВН создаётся один раз (версия без файла): повтор после сбоя идёт в тот же черновик и с тем же ключом.
      let draft = draftWaybill;
      if (draft === null) {
        const waybill = await api.post<DocumentDetail>(
          DOCUMENTS_API,
          {
            kind: "waybill_internal",
            legal_entity_id: legalEntityId,
            project_id: projectId,
            title: `${t("title")} ${todayIso()}`,
          },
          steps.keyFor("waybill"),
        );
        steps.forget("waybill");
        const version = waybill.versions[0];
        if (!version) return setError(t("noVersion"));
        draft = { id: waybill.id, versionId: version.id };
        setDraftWaybill(draft);
      }
      const lines = rows.map((row) => issueLinePayload(row, warehouseId, node.id, responsible));
      await api.put(
        `${DOCUMENTS_API}/${draft.id}/versions/${draft.versionId}/lines`,
        { lines },
        steps.keyFor(`lines:${JSON.stringify(lines)}`),
      );
      let result;
      try {
        result = await post.mutateAsync({ documentId: draft.id, versionId: draft.versionId });
      } catch (cause) {
        // Проведение уже прошло в прошлой попытке (ответ потерян): черновик наш, остатки списаны один раз.
        if (cause instanceof ApiError && cause.code === "stock.version_not_draft") {
          steps.clear();
          setRows([]);
          setDraftWaybill(null);
          setDone(t("alreadyPosted"));
          return;
        }
        throw cause;
      }
      steps.clear();
      setRows([]);
      setDraftWaybill(null);
      setDone(t("posted", { count: result.movements }));
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset className="space-y-5" disabled={busy}>
      <section className="space-y-2">
        <p className="text-sm font-medium">{t("node")}</p>
        <NativeSelect className="h-12 w-full text-base" onChange={(event) => setNodeId(event.target.value)} value={nodeId}>
          <option value="">{t("pickNode")}</option>
          {tree.data?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </NativeSelect>
        {node !== null && responsible === null && <p className="text-sm text-destructive">{t("noResponsible")}</p>}
        {node !== null && responsible !== null && <p className="text-sm text-muted-foreground">{t("responsibleSet")}</p>}
      </section>

      <section className="space-y-2">
        <p className="text-sm font-medium">{t("positions")}</p>
        <NativeSelect
          className="h-12 w-full text-base"
          onChange={(event) => {
            const found = choices.find((item) => item.id === event.target.value);
            if (found) addMaterial(found);
          }}
          value={picked}
        >
          <option value="">{t("pickMaterial")}</option>
          {choices.map((item) => (
            <option key={item.id} value={item.id}>
              {item.code} {item.name}
            </option>
          ))}
        </NativeSelect>

        {rows.map((row) => (
          <div className="space-y-3 rounded-md border p-3" key={row.key}>
            <p className="font-medium">{row.material.name}</p>
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1 text-sm">
                <span className="block text-muted-foreground">{t("quantity")}</span>
                <Input
                  className="h-12 text-base"
                  inputMode="decimal"
                  onChange={(event) => patch(row.key, { quantity: event.target.value })}
                  value={row.quantity}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="block text-muted-foreground">{t("unit")}</span>
                <NativeSelect
                  className="h-12 w-full text-base"
                  onChange={(event) => patch(row.key, { unit: event.target.value })}
                  value={row.unit}
                >
                  {unitChoices(row.material).map((unit) => (
                    <option key={unit} value={unit}>
                      {unit}
                    </option>
                  ))}
                </NativeSelect>
              </label>
            </div>
            {row.material.requires_certificate && (
              <label className="block space-y-1 text-sm">
                <span className="block text-muted-foreground">{t("batch")}</span>
                <NativeSelect
                  className="h-12 w-full text-base"
                  onChange={(event) => patch(row.key, { batchId: event.target.value || null })}
                  value={row.batchId ?? ""}
                >
                  <option value="">{t("pickBatch")}</option>
                  {batchOptions
                    .filter((option) => option.material_id === row.material.id)
                    .map((option) => (
                      <option key={option.batch_id ?? ""} value={option.batch_id ?? ""}>
                        {option.batch_no} ({option.qty} {option.base_unit})
                      </option>
                    ))}
                </NativeSelect>
              </label>
            )}
            <Button className="min-h-12 w-full" onClick={() => setRows((cur) => cur.filter((item) => item.key !== row.key))} type="button" variant="outline">
              {t("remove")}
            </Button>
          </div>
        ))}
      </section>

      {error !== null && <p className="text-sm text-destructive">{error}</p>}
      {done !== null && <p className="text-sm">{done}</p>}

      <Button className="min-h-12 w-full text-base" disabled={busy || responsible === null} onClick={submit} type="button">
        {busy ? t("sending") : t("submit")}
      </Button>
    </fieldset>
  );
}
