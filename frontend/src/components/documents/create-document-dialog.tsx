"use client";

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { CounterpartyPicker } from "@/components/documents/counterparty-picker";
import { Field, useDocumentErrorMessage, useKindLabel } from "@/components/documents/shared";
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
import { NativeSelect } from "@/components/ui/native-select";
import { newIdempotencyKey } from "@/lib/api";
import { isKindCode } from "@/lib/documents/kinds";
import { documentsApi, useInvalidateDocuments } from "@/lib/documents/queries";
import type { CounterpartyOut, CreateBody, KindOut } from "@/lib/documents/types";

// Создание документа: из реестра объекта (projectId) или из карточки техники (unitId, связь с единицей
// задаётся сразу). Ключ идемпотентности один на открытие диалога: повтор после сбоя не заведёт второй номер.
// Закрыть диалог во время отправки нельзя: исход запроса неизвестен, а новый ключ дал бы дубль.
// Юрлицо и номер присваивает сервер (юрлицо объекта или единицы); сумма в создании не вводится (R1).
export function CreateDocumentDialog({
  projectId,
  legalEntityId,
  unitId,
  kinds,
  canCounterparty,
  onClose,
}: {
  projectId?: string;
  legalEntityId: string | undefined;
  unitId?: string;
  kinds: readonly KindOut[];
  canCounterparty: boolean;
  onClose: () => void;
}) {
  const t = useTranslations("Documents.create");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const kindLabel = useKindLabel();
  const router = useRouter();
  const invalidate = useInvalidateDocuments();
  const [key] = useState(newIdempotencyKey);
  const [kindCode, setKindCode] = useState("");
  const [title, setTitle] = useState("");
  const [counterparty, setCounterparty] = useState<CounterpartyOut | null>(null);

  const kind = kinds.find((item) => item.code === kindCode) ?? null;
  const needsCounterparty = kind?.counterparty_required ?? false;

  const ready =
    kind !== null &&
    legalEntityId !== undefined &&
    title.trim() !== "" &&
    (!needsCounterparty || counterparty !== null);
  const body: CreateBody | null = ready
    ? {
        kind: kind.code,
        legal_entity_id: legalEntityId,
        ...(projectId !== undefined ? { project_id: projectId } : {}),
        title: title.trim(),
        links: unitId !== undefined ? [{ kind: "equipment_unit", id: unitId }] : [],
        ...(needsCounterparty && counterparty !== null ? { counterparty_id: counterparty.id } : {}),
      }
    : null;

  const save = useMutation({
    mutationFn: (value: CreateBody) => documentsApi.create(value, key),
    onSuccess: async (created) => {
      await invalidate();
      router.push(`/documents/${created.id}`);
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (body !== null && !save.isPending) save.mutate(body);
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
    >
      <DialogContent data-testid="documents-create-dialog">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{unitId !== undefined ? t("unitHint") : t("description")}</DialogDescription>
        </DialogHeader>
        <form className="grid gap-3" onSubmit={submit}>
          {legalEntityId === undefined ? <p className="text-sm text-muted-foreground">{t("loadingProject")}</p> : null}
          {canCounterparty ? null : <p className="text-sm text-muted-foreground">{t("counterpartyUnavailable")}</p>}
          {kinds.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("noKinds")}</p>
          ) : (
            <>
              <Field id="create-kind" label={t("kind")}>
                <NativeSelect
                  id="create-kind"
                  value={kindCode}
                  required
                  onChange={(event) => setKindCode(event.target.value)}
                  data-testid="documents-create-kind"
                >
                  <option value="">{t("chooseKind")}</option>
                  {kinds.map((item) => (
                    <option key={item.code} value={item.code}>
                      {isKindCode(item.code) ? kindLabel(item.code) : item.code}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field id="create-title" label={t("titleField")}>
                <Input
                  id="create-title"
                  value={title}
                  maxLength={200}
                  required
                  onChange={(event) => setTitle(event.target.value)}
                  data-testid="documents-create-title"
                />
              </Field>
              {needsCounterparty && canCounterparty ? (
                <Field id="create-counterparty" label={t("counterparty")}>
                  <CounterpartyPicker
                    id="create-counterparty"
                    label={t("counterparty")}
                    selected={counterparty}
                    placeholder={t("chooseCounterparty")}
                    required
                    testId="documents-create-counterparty"
                    onChange={setCounterparty}
                  />
                </Field>
              ) : null}
            </>
          )}
          {save.isError ? (
            <p role="alert" className="text-sm text-destructive" data-testid="documents-create-error">
              {message(save.error)}
            </p>
          ) : null}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
              {common("cancel")}
            </Button>
            <Button type="submit" disabled={body === null || save.isPending} data-testid="documents-create-submit">
              {save.isPending ? t("submitting") : t("submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
