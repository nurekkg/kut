"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/auth/api";
import { formatQuantity } from "@/lib/documents/format";
import { DOCUMENTS_API } from "@/lib/documents/queries";
import type { DocumentDetail } from "@/lib/documents/types";
import { todayIso } from "@/lib/schedule/dates";
import { draftName, readDraft, writeDraft } from "@/lib/stock/draft-store";
import {
  factLinePayload,
  parseFact,
  uncountedPositions,
  type InventoryLine,
  type InventoryLinesOut,
} from "@/lib/stock/inventory";
import { STOCK_KEY } from "@/lib/stock/queries";
import { useStepKeys } from "@/lib/stock/step-keys";
import type { WarehouseOut } from "@/lib/stock/types";

import { useStockErrorMessage } from "../use-stock-error";

type Draft = { actId: string; versionId: string; warehouseId: string };

// Коды ответа API: маршрут уже запущен, то есть отправка прошла раньше (ответ потерян или страница перезагружена).
const SENT_CODES = ["approval.version_not_draft"];
// Строки уже не меняются: акт отправлен раньше, несохранённые количества не записаны - черновик сбрасывается.
const CLOSED_CODES = ["document.lines_not_draft"];

// Инвентаризация на телефоне (ADR-016, S7c): кладовщик выбирает склад, система подставляет позиции с учётным
// остатком, он вводит фактическое количество и отправляет акт прорабу. Остатки меняются только после утверждения
// техдиректором; до отправки и на согласовании склад не трогается.
export function InventoryForm({
  projectId,
  legalEntityId,
  warehouses,
}: {
  projectId: string;
  legalEntityId: string;
  warehouses: WarehouseOut[];
}) {
  const t = useTranslations("Stock.phone.inventory");
  const message = useStockErrorMessage();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const storageName = user === null ? null : draftName("inventory", projectId, user.id);
  const steps = useStepKeys(storageName);
  // Черновик акта (идентификаторы документа и версии) переживает перезагрузку вкладки: повтор не заводит второй акт.
  const [draft, setDraft] = useState<Draft | null>(() =>
    storageName === null ? null : readDraft<Draft>(storageName),
  );
  const [picked, setPicked] = useState("");
  const [typed, setTyped] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const linesKey = [...STOCK_KEY, "inventory", draft?.actId ?? null] as const;
  const lines = useQuery({
    queryKey: linesKey,
    enabled: draft !== null,
    staleTime: 0,
    queryFn: () =>
      api.get<InventoryLinesOut>(`${DOCUMENTS_API}/${draft?.actId}/versions/${draft?.versionId}/lines`),
  });
  const rows = lines.data?.lines ?? [];
  const warehouseId = picked !== "" ? picked : (warehouses[0]?.id ?? "");

  function valueOf(line: InventoryLine): string {
    return typed[line.position_no] ?? line.quantity ?? "";
  }

  function remember(next: Draft | null) {
    if (storageName !== null) writeDraft(storageName, next);
    setDraft(next);
  }

  function closeSentDraft() {
    steps.clear();
    setTyped({});
    remember(null);
    setError(t("sentChangesLost"));
  }

  async function fill(current: Draft) {
    const body = { warehouse_id: current.warehouseId };
    await api.post<InventoryLinesOut>(
      `${DOCUMENTS_API}/${current.actId}/versions/${current.versionId}/inventory/prefill`,
      body,
      steps.keyFor("prefill", JSON.stringify(body)),
    );
    steps.forget("prefill");
    await queryClient.invalidateQueries({ queryKey: [...STOCK_KEY, "inventory", current.actId] });
  }

  async function start() {
    setError(null);
    setDone(null);
    if (warehouseId === "") return setError(t("noWarehouse"));
    setBusy(true);
    try {
      const body = {
        kind: "inventory_act",
        legal_entity_id: legalEntityId,
        project_id: projectId,
        title: `${t("actTitle")} ${todayIso()}`,
      };
      const created = await api.post<DocumentDetail>(DOCUMENTS_API, body, steps.keyFor("create", JSON.stringify(body)));
      steps.forget("create");
      const next = { actId: created.id, versionId: created.versions[0].id, warehouseId };
      remember(next);
      await fill(next);
      setPicked("");
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  async function save(current: Draft) {
    const body = { lines: rows.map((line) => factLinePayload(line, valueOf(line))) };
    await api.put<InventoryLinesOut>(
      `${DOCUMENTS_API}/${current.actId}/versions/${current.versionId}/lines`,
      body,
      steps.keyFor("lines", JSON.stringify(body)),
    );
    steps.forget("lines");
    setTyped({});
    await queryClient.invalidateQueries({ queryKey: [...STOCK_KEY, "inventory", current.actId] });
  }

  // Текст, который не читается как количество, не сохраняется: иначе ранее сохранённый факт стёрся бы.
  function hasInvalidFact(): boolean {
    return rows.some((line) => {
      const text = typed[line.position_no];
      return text !== undefined && text.trim() !== "" && parseFact(text) === null;
    });
  }

  async function saveCount() {
    setError(null);
    setDone(null);
    if (draft === null) return;
    if (hasInvalidFact()) return setError(t("invalidFact"));
    setBusy(true);
    try {
      await save(draft);
      setDone(t("saved"));
    } catch (failure) {
      if (failure instanceof ApiError && CLOSED_CODES.includes(failure.code)) return closeSentDraft();
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setError(null);
    setDone(null);
    if (draft === null) return;
    if (hasInvalidFact()) return setError(t("invalidFact"));
    const facts = Object.fromEntries(rows.map((line) => [line.position_no, valueOf(line)]));
    if (uncountedPositions(rows, facts).length > 0) return setError(t("notCounted"));
    setBusy(true);
    try {
      // Запись строк только при несохранённых правках: повтор после потерянного ответа не шлёт лишний PUT.
      if (Object.keys(typed).length > 0) await save(draft);
      await api.post(
        `${DOCUMENTS_API}/${draft.actId}/versions/${draft.versionId}/send`,
        undefined,
        steps.keyFor("send", draft.actId),
      );
      steps.clear();
      remember(null);
      setDone(t("sent"));
    } catch (failure) {
      // Отправка уже прошла в прошлой попытке (ответ потерян): для пользователя это успех.
      if (failure instanceof ApiError && SENT_CODES.includes(failure.code)) {
        steps.clear();
        remember(null);
        setDone(t("sent"));
      } else if (failure instanceof ApiError && CLOSED_CODES.includes(failure.code)) {
        closeSentDraft();
      } else {
        setError(message(failure));
      }
    } finally {
      setBusy(false);
    }
  }

  if (draft === null) {
    return (
      <section aria-label={t("title")} className="flex flex-col gap-3">
        {warehouses.length === 0 && <p className="text-sm text-destructive">{t("noWarehouse")}</p>}
        {warehouses.length > 0 && (
          <label className="flex flex-col gap-1 text-sm font-medium">
            {t("warehouse")}
            <NativeSelect
              className="h-12 w-full text-base"
              onChange={(event) => setPicked(event.target.value)}
              value={warehouseId}
            >
              {warehouses.map((warehouse) => (
                <option key={warehouse.id} value={warehouse.id}>
                  {warehouse.name}
                </option>
              ))}
            </NativeSelect>
          </label>
        )}
        <Button
          className="min-h-12 text-base"
          disabled={busy || warehouses.length === 0}
          onClick={start}
          type="button"
        >
          {busy ? t("starting") : t("start")}
        </Button>
        {error !== null && <p className="text-sm text-destructive" role="alert">{error}</p>}
        {done !== null && <p className="text-sm" role="status">{done}</p>}
      </section>
    );
  }

  return (
    <section aria-label={t("title")} className="flex flex-col gap-3">
      {error !== null && <p className="text-sm text-destructive" role="alert">{error}</p>}
      {done !== null && <p className="text-sm" role="status">{done}</p>}
      {lines.isSuccess && rows.length === 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
          <Button className="min-h-12 text-base" disabled={busy} onClick={() => fill(draft)} type="button" variant="outline">
            {t("fill")}
          </Button>
        </div>
      )}
      {rows.length > 0 && (
        <>
          <ul className="flex flex-col gap-2">
            {rows.map((line) => (
              <li className="flex flex-col gap-2 rounded-md border p-3" key={line.position_no}>
                <div className="flex items-start justify-between gap-2">
                  <p className="min-w-0 font-medium">{line.name}</p>
                  <p className="shrink-0 text-sm text-muted-foreground">
                    {t("book")}: {formatQuantity(line.balance_now ?? "0")} {line.unit}
                  </p>
                </div>
                <Input
                  aria-label={t("factLabel", { name: line.name })}
                  className="min-h-12 text-base"
                  inputMode="decimal"
                  onChange={(event) =>
                    setTyped((current) => ({ ...current, [line.position_no]: event.target.value }))
                  }
                  placeholder={t("factPlaceholder")}
                  value={valueOf(line)}
                />
              </li>
            ))}
          </ul>
          <div className="grid grid-cols-2 gap-2">
            <Button className="min-h-12 text-base" disabled={busy} onClick={saveCount} type="button" variant="outline">
              {t("save")}
            </Button>
            <Button className="min-h-12 text-base" disabled={busy} onClick={send} type="button">
              {busy ? t("sending") : t("send")}
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
