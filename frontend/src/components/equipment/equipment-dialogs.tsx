"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, newIdempotencyKey } from "@/lib/api";
import { equipmentDocumentKind } from "@/lib/documents/equipment-kinds";
import { useDocuments } from "@/lib/documents/queries";
import { useEquipmentErrorMessage } from "@/lib/equipment/errors";
import { EQUIPMENT_KEY } from "@/lib/equipment/keys";
import { EQUIPMENT_API } from "@/lib/equipment/queries";
import {
  DOCUMENT_KINDS,
  MAINTENANCE_KINDS,
  type MaintenanceKind,
  type DocumentKind,
  type UnitListItem,
  type UnitName,
  UNIT_NAMES,
  type UnitRow,
} from "@/lib/equipment/types";
import { todayIso } from "@/lib/schedule/dates";

type Done = () => void;

export function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

// Общая оболочка форм: отправка, ошибка API текстом, кнопки. Диалог монтируется на каждое
// открытие, поэтому ключ идемпотентности создаётся заново для каждой новой записи.
export function FormDialog({
  title,
  description,
  onClose,
  submitDisabled,
  pending,
  error,
  onSubmit,
  children,
}: {
  title: string;
  description?: string;
  onClose: Done;
  submitDisabled: boolean;
  pending: boolean;
  error: unknown;
  onSubmit: () => void;
  children: ReactNode;
}) {
  const t = useTranslations("Equipment.forms");
  const message = useEquipmentErrorMessage();
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!submitDisabled) onSubmit();
          }}
        >
          {children}
          {error ? (
            <p role="alert" className="text-sm text-destructive" data-testid="form-error">
              {message(error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t("cancel")}
            </Button>
            <Button type="submit" disabled={submitDisabled || pending} data-testid="form-submit">
              {pending ? t("saving") : t("save")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function useInvalidate() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: EQUIPMENT_KEY });
}

export function MaintenanceDialog({
  units,
  unitId,
  onClose,
}: {
  units: UnitListItem[];
  unitId?: string;
  onClose: Done;
}) {
  const t = useTranslations("Equipment.forms");
  const kindText = useTranslations("Equipment.maintenanceKind");
  const invalidate = useInvalidate();
  const [key] = useState(newIdempotencyKey);
  const [unit, setUnit] = useState(unitId ?? "");
  const [kind, setKind] = useState<MaintenanceKind>("to");
  const [title, setTitle] = useState("");
  const [from, setFrom] = useState(todayIso);
  const [until, setUntil] = useState("");
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/maintenance`,
        {
          equipment_unit_id: unit,
          kind,
          title: title.trim(),
          planned_from: from,
          planned_to: until || null,
        },
        key,
      ),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  return (
    <FormDialog
      title={t("maintenanceTitle")}
      onClose={onClose}
      submitDisabled={!unit || !title.trim() || !from}
      pending={save.isPending}
      error={save.error}
      onSubmit={() => save.mutate()}
    >
      {unitId ? null : (
        <Field id="maintenance-unit" label={t("unit")}>
          <NativeSelect
            id="maintenance-unit"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
            required
          >
            <option value="">{t("chooseUnit")}</option>
            {units.map((item) => (
              <option key={item.id} value={item.id}>
                {item.registry_number}
              </option>
            ))}
          </NativeSelect>
        </Field>
      )}
      <Field id="maintenance-kind" label={t("kind")}>
        <NativeSelect
          id="maintenance-kind"
          value={kind}
          onChange={(event) => setKind(event.target.value as MaintenanceKind)}
        >
          {MAINTENANCE_KINDS.map((value) => (
            <option key={value} value={value}>
              {kindText(value)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="maintenance-title" label={t("titleField")}>
        <Input
          id="maintenance-title"
          value={title}
          maxLength={300}
          onChange={(event) => setTitle(event.target.value)}
          required
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="maintenance-from" label={t("plannedFrom")}>
          <Input
            id="maintenance-from"
            type="date"
            value={from}
            onChange={(event) => setFrom(event.target.value)}
            required
          />
        </Field>
        <Field id="maintenance-until" label={t("plannedTo")}>
          <Input
            id="maintenance-until"
            type="date"
            value={until}
            onChange={(event) => setUntil(event.target.value)}
          />
        </Field>
      </div>
    </FormDialog>
  );
}

export function DocumentDialog({ unitId, onClose }: { unitId: string; onClose: Done }) {
  const t = useTranslations("Equipment.forms");
  const kindText = useTranslations("Equipment.documentKind");
  const invalidate = useInvalidate();
  const [key] = useState(newIdempotencyKey);
  const [kind, setKind] = useState<DocumentKind>("technical_inspection");
  const [number, setNumber] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [documentId, setDocumentId] = useState("");
  const { user } = useAuth();
  // Строка срока: технический осмотр - механику (equipment:write:maintenance), остальные виды - по праву
  // загрузки техники (PO 2026-10-18). Сервер проверяет то же правило; здесь - только варианты в списке.
  const permissions = user?.permissions ?? [];
  const kinds = DOCUMENT_KINDS.filter(
    (value) => value === "technical_inspection" || permissions.includes("equipment:write:documents"),
  );
  // Связать можно только документ реестра того же вида, что и строка срока: сервер проверяет это ещё раз.
  // Документы техники видны по праву техники (equipment:read:documents), без документного права тоже.
  const canReadDocuments =
    (user?.permissions.includes("documents:read") || user?.permissions.includes("equipment:read:documents")) ??
    false;
  const documents = useDocuments({ equipmentUnitId: unitId }, { limit: 100, offset: 0 }, canReadDocuments);
  const linkable = (documents.data ?? []).filter((item) => item.kind === equipmentDocumentKind(kind));
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/units/${unitId}/documents`,
        {
          kind,
          number: number.trim(),
          valid_until: validUntil,
          // Поле уходит только при выборе: без связи запрос прежний.
          ...(documentId ? { document_id: documentId } : {}),
        },
        key,
      ),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  return (
    <FormDialog
      title={t("documentTitle")}
      description={t("documentHint")}
      onClose={onClose}
      submitDisabled={!number.trim() || !validUntil}
      pending={save.isPending}
      error={save.error}
      onSubmit={() => save.mutate()}
    >
      <Field id="document-kind" label={t("documentKind")}>
        <NativeSelect
          id="document-kind"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as DocumentKind);
            setDocumentId("");
          }}
        >
          {kinds.map((value) => (
            <option key={value} value={value}>
              {kindText(value)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="document-number" label={t("documentNumber")}>
        <Input
          id="document-number"
          value={number}
          maxLength={100}
          onChange={(event) => setNumber(event.target.value)}
          required
        />
      </Field>
      <Field id="document-valid" label={t("validUntil")}>
        <Input
          id="document-valid"
          type="date"
          value={validUntil}
          onChange={(event) => setValidUntil(event.target.value)}
          required
        />
      </Field>
      <Field id="document-link" label={t("documentLink")}>
        <NativeSelect
          id="document-link"
          value={documentId}
          onChange={(event) => setDocumentId(event.target.value)}
          data-testid="equipment-document-link"
        >
          <option value="">{t("documentLinkNone")}</option>
          {linkable.map((item) => (
            <option key={item.id} value={item.id}>
              {[item.registration_no, item.title].filter(Boolean).join(" · ")}
            </option>
          ))}
        </NativeSelect>
        <p className="text-xs text-muted-foreground">
          {linkable.length === 0 ? t("documentLinkEmpty") : t("documentLinkHint")}
        </p>
      </Field>
    </FormDialog>
  );
}

export function OwnRateDialog({ unit, onClose }: { unit: UnitRow; onClose: Done }) {
  const t = useTranslations("Equipment.forms");
  const unitText = useTranslations("Equipment.unitName");
  const invalidate = useInvalidate();
  const [key] = useState(newIdempotencyKey);
  const [name, setName] = useState<UnitName>("hour");
  const [amount, setAmount] = useState("");
  const [validFrom, setValidFrom] = useState(todayIso);
  const save = useMutation({
    mutationFn: () =>
      api.post(
        `${EQUIPMENT_API}/own-rates`,
        {
          legal_entity_id: unit.legal_entity_id,
          equipment_type_id: unit.equipment_type_id,
          unit: name,
          amount: amount.trim(),
          valid_from: validFrom,
        },
        key,
      ),
    onSuccess: async () => {
      await invalidate();
      onClose();
    },
  });
  return (
    <FormDialog
      title={t("rateTitle")}
      description={t("rateHint")}
      onClose={onClose}
      submitDisabled={!amount.trim() || !validFrom}
      pending={save.isPending}
      error={save.error}
      onSubmit={() => save.mutate()}
    >
      <Field id="rate-unit" label={t("rateUnit")}>
        <NativeSelect
          id="rate-unit"
          value={name}
          onChange={(event) => setName(event.target.value as UnitName)}
        >
          {UNIT_NAMES.map((value) => (
            <option key={value} value={value}>
              {unitText(value)}
            </option>
          ))}
        </NativeSelect>
      </Field>
      <Field id="rate-amount" label={t("rateAmount")}>
        <Input
          id="rate-amount"
          inputMode="decimal"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          required
        />
      </Field>
      <Field id="rate-valid" label={t("rateValidFrom")}>
        <Input
          id="rate-valid"
          type="date"
          value={validFrom}
          onChange={(event) => setValidFrom(event.target.value)}
          required
        />
      </Field>
    </FormDialog>
  );
}
