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
import { todayIso } from "@/lib/schedule/dates";

import { useStockErrorMessage } from "../use-stock-error";

type Draft = ReceiptRow & { key: number; certFile: File | null; certDocId: string | null };

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
  // Черновик М-7 после сбоя на середине отправки: повтор продолжает тот же черновик.
  const [draftActId, setDraftActId] = useState<string | null>(null);
  const [draftVersionId, setDraftVersionId] = useState<string | null>(null);

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
        certificateAttached: false,
        certFile: null,
        certDocId: null,
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
      const certificateAttached = row.certFile !== null || row.batchId !== null;
      const problems = receiptProblems({ ...row, certificateAttached });
      if (problems.length > 0) return setError(t("rowProblem", { name: row.material.name }));
    }

    setBusy(true);
    try {
      // 1) Сертификат и партия для сертифицированных материалов. Документ сертификата создаётся один раз
      // на строку и запоминается; партия сохраняется в состоянии сразу. Повтор после сбоя не плодит документы.
      let working = rows;
      for (const row of rows) {
        if (!row.material.requires_certificate || row.batchId !== null) continue;
        if (row.certFile === null) return setError(t("noCertificate", { name: row.material.name }));
        let certificateId = row.certDocId;
        if (certificateId === null) {
          const certificate = await api.post<DocumentDetail>(
            DOCUMENTS_API,
            {
              kind: "material_certificate",
              legal_entity_id: legalEntityId,
              title: `${t("certTitle")} ${row.material.code} ${row.batchNo}`,
            },
            newIdempotencyKey(),
          );
          certificateId = certificate.id;
          working = working.map((item) => (item.key === row.key ? { ...item, certDocId: certificateId } : item));
          setRows(working);
        }
        await uploadVersion(
          await prepareUpload(row.certFile, newIdempotencyKey()),
          versionTransport(certificateId),
          { comment: null, amount: null },
        );
        const batch = await api.post<BatchOut>(`${API}/materials/${row.material.id}/batches`, {
          batch_no: row.batchNo.trim(),
          certificate_document_id: certificateId,
          customs_document_id: null,
          country_of_origin: null,
        });
        working = working.map((item) => (item.key === row.key ? { ...item, batchId: batch.id } : item));
        setRows(working);
      }

      // 2) М-7: черновик запоминается сразу после создания, до загрузки скана; повтор продолжает тот же черновик.
      let actId = draftActId;
      if (actId === null) {
        const created = await api.post<DocumentDetail>(
          DOCUMENTS_API,
          {
            kind: "m7_act",
            legal_entity_id: legalEntityId,
            project_id: projectId,
            title: `${t("actTitle")} ${todayIso()}`,
          },
          newIdempotencyKey(),
        );
        actId = created.id;
        setDraftActId(actId);
      }
      let versionId = draftVersionId;
      if (versionId === null) {
        const version: VersionOut = await uploadVersion(
          await prepareUpload(scan, newIdempotencyKey()),
          versionTransport(actId),
          { comment: null, amount: null },
        );
        versionId = version.id;
        setDraftVersionId(versionId);
      }
      await api.put(
        `${DOCUMENTS_API}/${actId}/versions/${versionId}/lines`,
        { lines: working.map((row) => receiptLinePayload(row, warehouseId)) },
        newIdempotencyKey(),
      );
      await api.post(`${DOCUMENTS_API}/${actId}/versions/${versionId}/send`, undefined, newIdempotencyKey());
      setRows([]);
      setScan(null);
      setDraftActId(null);
      setDraftVersionId(null);
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
