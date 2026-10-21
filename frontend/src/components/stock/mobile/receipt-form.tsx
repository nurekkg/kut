"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { DOCUMENTS_API, versionTransport } from "@/lib/documents/queries";
import { prepareUpload, uploadVersion, UPLOAD_ACCEPT } from "@/lib/documents/upload";
import type { DocumentDetail, VersionOut } from "@/lib/documents/types";
import { API } from "@/lib/projects/keys";
import { receiptLinePayload, receiptProblems, unitChoices, type ReceiptRow } from "@/lib/stock/lines";
import type { BatchOut, MaterialOut } from "@/lib/stock/types";
import { useMaterials } from "@/lib/stock/queries";

import { useStockErrorMessage } from "../use-stock-error";

type Draft = ReceiptRow & { key: number; certFile: File | null };

// Приход на телефоне: позиции из справочника, для сертифицированных - партия и сертификат (камера),
// затем скан накладной (камера) и отправка прорабу (М-7 идёт по маршруту «склад»).
export function ReceiptForm({
  projectId,
  legalEntityId,
  warehouseId,
}: {
  projectId: string;
  legalEntityId: string;
  warehouseId: string | null;
}) {
  const t = useTranslations("Stock.phone.receipt");
  const message = useStockErrorMessage();
  const materials = useMaterials(legalEntityId, true);
  const [rows, setRows] = useState<Draft[]>([]);
  const [scan, setScan] = useState<File | null>(null);
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [nextKey, setNextKey] = useState(1);

  function addMaterial(material: MaterialOut) {
    setRows((current) => [
      ...current,
      {
        key: nextKey,
        material,
        quantity: "",
        unit: material.base_unit,
        batchNo: "",
        batchId: null,
        certFile: null,
      },
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
    if (rows.length === 0) return setError(t("noPositions"));
    if (scan === null) return setError(t("noScan"));
    for (const row of rows) {
      const problems = receiptProblems(row);
      if (problems.length > 0) return setError(t("rowProblem", { name: row.material.name }));
    }

    setBusy(true);
    try {
      // 1) Сертификат и партия для сертифицированных материалов: файл - документ вида СЕРТ, затем партия.
      const batches = new Map<number, string>();
      for (const row of rows) {
        if (!row.material.requires_certificate || row.batchId !== null) continue;
        if (row.certFile === null) return setError(t("noCertificate", { name: row.material.name }));
        const certificate = await api.post<DocumentDetail>(
          DOCUMENTS_API,
          {
            kind: "material_certificate",
            legal_entity_id: legalEntityId,
            title: `${t("certTitle")} ${row.material.code} ${row.batchNo}`,
          },
          newIdempotencyKey(),
        );
        await uploadVersion(
          await prepareUpload(row.certFile, newIdempotencyKey()),
          versionTransport(certificate.id),
          { comment: null, amount: null },
        );
        const batch = await api.post<BatchOut>(`${API}/materials/${row.material.id}/batches`, {
          batch_no: row.batchNo.trim(),
          certificate_document_id: certificate.id,
          customs_document_id: null,
          country_of_origin: null,
        });
        batches.set(row.key, batch.id);
      }
      const ready = rows.map((row) => ({ ...row, batchId: batches.get(row.key) ?? row.batchId }));

      // 2) М-7: черновик, скан накладной (версия с файлом), позиции, отправка прорабу.
      const today = new Date().toISOString().slice(0, 10);
      const act = await api.post<DocumentDetail>(
        DOCUMENTS_API,
        { kind: "m7_act", legal_entity_id: legalEntityId, project_id: projectId, title: `${t("actTitle")} ${today}` },
        newIdempotencyKey(),
      );
      const version: VersionOut = await uploadVersion(
        await prepareUpload(scan, newIdempotencyKey()),
        versionTransport(act.id),
        { comment: null, amount: null },
      );
      await api.put(
        `${DOCUMENTS_API}/${act.id}/versions/${version.id}/lines`,
        { lines: ready.map((row) => receiptLinePayload(row, warehouseId)) },
        newIdempotencyKey(),
      );
      await api.post(
        `${DOCUMENTS_API}/${act.id}/versions/${version.id}/send`,
        undefined,
        newIdempotencyKey(),
      );
      setRows([]);
      setScan(null);
      setDone(t("sent"));
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  const choices = materials.data ?? [];

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <p className="text-sm font-medium">{t("basis")}</p>
        <p className="rounded-md border px-3 py-3 text-sm">{t("noRequest")}</p>
        <p className="text-xs text-muted-foreground">{t("requestLater")}</p>
      </section>

      <section className="space-y-2">
        <p className="text-sm font-medium">{t("positions")}</p>
        <label className="block space-y-1">
          <span className="sr-only">{t("pickMaterial")}</span>
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
        </label>

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
              <div className="space-y-2 rounded-md bg-muted/40 p-3">
                <label className="block space-y-1 text-sm">
                  <span className="block text-muted-foreground">{t("batchNo")}</span>
                  <Input
                    className="h-12 text-base"
                    onChange={(event) => patch(row.key, { batchNo: event.target.value })}
                    value={row.batchNo}
                  />
                </label>
                <label className="block space-y-1 text-sm">
                  <span className="block text-muted-foreground">
                    {t("certificate")} {row.certFile ? `· ${row.certFile.name}` : ""}
                  </span>
                  <input
                    accept={UPLOAD_ACCEPT}
                    capture="environment"
                    className="block w-full min-h-12 text-sm"
                    onChange={(event) => patch(row.key, { certFile: event.target.files?.[0] ?? null })}
                    type="file"
                  />
                </label>
              </div>
            )}

            <Button className="min-h-12 w-full" onClick={() => setRows((cur) => cur.filter((item) => item.key !== row.key))} type="button" variant="outline">
              {t("remove")}
            </Button>
          </div>
        ))}
      </section>

      <section className="space-y-2">
        <p className="text-sm font-medium">{t("scan")}</p>
        <input
          accept="image/*,application/pdf"
          capture="environment"
          className="block w-full min-h-12 text-sm"
          onChange={(event) => setScan(event.target.files?.[0] ?? null)}
          type="file"
        />
        {scan && <p className="text-xs text-muted-foreground">{scan.name}</p>}
      </section>

      {error !== null && <p className="text-sm text-destructive">{error}</p>}
      {done !== null && <p className="text-sm">{done}</p>}

      <Button className="min-h-12 w-full text-base" disabled={busy} onClick={submit} type="button">
        {busy ? t("sending") : t("submit")}
      </Button>
    </div>
  );
}
