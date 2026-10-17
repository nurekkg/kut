"use client";

import { FilePlus } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { CreateDocumentDialog } from "@/components/documents/create-document-dialog";
import { StatusBadge, useDocumentErrorMessage, useKindLabel } from "@/components/documents/shared";
import { Section } from "@/components/equipment/section";
import { Button } from "@/components/ui/button";
import { canWriteKind } from "@/lib/documents/access";
import { useDocumentKinds, useDocuments } from "@/lib/documents/queries";

const LIMIT = 100;

// Файлы единицы в реестре документов (S5b): что загружено как версии, со статусом. Строки срока
// (паспорт, ТО, страховка) остаются в разделе «Документы» выше. Документ техники создаётся здесь же: юрлицо
// и связь с единицей задаются сразу, виды - те, что право пользователя позволяет записать (R2).
// Видят его и документные роли, и право на документы техники (equipment:read:documents).
export function UnitDocumentRegistrySection({ unitId, legalEntityId }: { unitId: string; legalEntityId: string }) {
  const t = useTranslations("Equipment.unit");
  const message = useDocumentErrorMessage();
  const kindLabel = useKindLabel();
  const { user } = useAuth();
  const permissions = useMemo(() => user?.permissions ?? [], [user]);
  const canRead = permissions.includes("documents:read") || permissions.includes("equipment:read:documents");
  const list = useDocuments({ equipmentUnitId: unitId }, { limit: LIMIT, offset: 0 }, canRead);
  const kinds = useDocumentKinds(canRead);
  const [creating, setCreating] = useState(false);
  const writable = useMemo(
    () =>
      (kinds.data ?? []).filter(
        (kind) => kind.links.includes("equipment_unit") && canWriteKind(kind.code, permissions),
      ),
    [kinds.data, permissions],
  );
  if (!canRead) return null;

  return (
    <Section
      id="unit-document-registry"
      title={t("documentRegistryTitle")}
      action={
        writable.length > 0 ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setCreating(true)}
            data-testid="unit-document-create"
          >
            <FilePlus data-icon="inline-start" aria-hidden />
            {t("documentCreate")}
          </Button>
        ) : null
      }
    >
      {list.isPending ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
      {list.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(list.error)}
        </p>
      ) : null}
      {list.isSuccess && list.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("documentRegistryEmpty")}</p>
      ) : null}
      {list.isSuccess && list.data.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border" data-testid="unit-document-registry-list">
          {list.data.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm">
              <Link
                href={`/documents/${row.id}`}
                className="min-w-0 font-medium break-all underline-offset-4 hover:underline"
                data-testid="unit-document-link"
              >
                {row.registration_no ?? row.title}
              </Link>
              <span className="min-w-0 flex-1 break-words text-muted-foreground">{kindLabel(row.kind)}</span>
              <StatusBadge status={row.latest_status} />
            </li>
          ))}
        </ul>
      ) : null}
      {creating ? (
        <CreateDocumentDialog
          unitId={unitId}
          legalEntityId={legalEntityId}
          kinds={writable}
          canCounterparty={false}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </Section>
  );
}
