"use client";

import { useMutation } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

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
import { parseAmount } from "@/lib/documents/format";
import { creatableKinds } from "@/lib/documents/kinds";
import { documentsApi, useInvalidateDocuments } from "@/lib/documents/queries";
import type { CreateBody, KindOut } from "@/lib/documents/types";
import { useLegalEntities } from "@/lib/equipment/queries";
import type { CounterpartyRef } from "@/lib/equipment/types";
import type { Project } from "@/lib/projects/types";

// Создание документа в реестре объекта. Ключ идемпотентности один на открытие диалога: повтор после сбоя
// не заведёт второй номер. Юрлицо берётся из объекта, номер присваивает сервер.
export function CreateDocumentDialog({
  projectId,
  project,
  kinds,
  counterparties,
  canCounterparty,
  canFinancial,
  onClose,
}: {
  projectId: string;
  project: Project | undefined;
  kinds: readonly KindOut[];
  counterparties: readonly CounterpartyRef[];
  canCounterparty: boolean;
  canFinancial: boolean;
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
  const [counterpartyId, setCounterpartyId] = useState("");
  const [amountText, setAmountText] = useState("");

  const legalEntities = useLegalEntities();
  const legalName =
    legalEntities.data?.find((item) => item.id === project?.legal_entity_id)?.name ?? common("noValue");
  const options = creatableKinds(kinds, canCounterparty);
  const kind = options.find((item) => item.code === kindCode) ?? null;
  const needsCounterparty = kind?.counterparty_required ?? false;
  const withAmount = kind !== null && kind.financial && canFinancial;
  const amountFilled = withAmount && amountText.trim() !== "";
  const amount = amountFilled ? parseAmount(amountText) : null;
  const amountInvalid = amountFilled && amount === null;

  const ready =
    kind !== null &&
    project !== undefined &&
    title.trim() !== "" &&
    (!needsCounterparty || counterpartyId !== "") &&
    !amountInvalid;
  const body: CreateBody | null = ready
    ? {
        kind: kind.code,
        legal_entity_id: project.legal_entity_id,
        project_id: projectId,
        title: title.trim(),
        links: [],
        ...(needsCounterparty ? { counterparty_id: counterpartyId } : {}),
        ...(amount !== null ? { amount } : {}),
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
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <form className="grid gap-3" onSubmit={submit}>
          <div className="grid gap-0.5" data-testid="documents-create-legal-entity">
            <span className="text-sm font-medium">{t("legalEntity")}</span>
            <span className="text-sm text-muted-foreground">{legalName}</span>
          </div>
          {project === undefined ? <p className="text-sm text-muted-foreground">{t("loadingProject")}</p> : null}
          {canCounterparty ? null : <p className="text-sm text-muted-foreground">{t("counterpartyUnavailable")}</p>}
          {options.length === 0 ? (
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
                  {options.map((item) => (
                    <option key={item.code} value={item.code}>
                      {kindLabel(item.code)}
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
              {needsCounterparty ? (
                <Field id="create-counterparty" label={t("counterparty")}>
                  <NativeSelect
                    id="create-counterparty"
                    value={counterpartyId}
                    required
                    onChange={(event) => setCounterpartyId(event.target.value)}
                    data-testid="documents-create-counterparty"
                  >
                    <option value="">{t("chooseCounterparty")}</option>
                    {counterparties.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              ) : null}
              {withAmount ? (
                <Field id="create-amount" label={t("amount")}>
                  <Input
                    id="create-amount"
                    inputMode="decimal"
                    value={amountText}
                    aria-invalid={amountInvalid ? true : undefined}
                    onChange={(event) => setAmountText(event.target.value)}
                    data-testid="documents-create-amount"
                  />
                  <p className={amountInvalid ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
                    {amountInvalid ? common("amountInvalid") : t("amountHint")}
                  </p>
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
            <Button type="button" variant="outline" onClick={onClose}>
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
