"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { StatusBadge, useDocumentErrorMessage, useKindLabel } from "@/components/documents/shared";
import { Section } from "@/components/equipment/section";
import { useDocuments } from "@/lib/documents/queries";

const LIMIT = 100;

// Файлы единицы в реестре документов (S5b): что загружено как версии, со статусом. Строки срока
// (паспорт, ТО, страховка) остаются в разделе «Документы» выше; здесь - ссылка на карточку документа.
export function UnitDocumentRegistrySection({ unitId }: { unitId: string }) {
  const t = useTranslations("Equipment.unit");
  const message = useDocumentErrorMessage();
  const kindLabel = useKindLabel();
  const { user } = useAuth();
  const canRead = user?.permissions.includes("documents:read") ?? false;
  const list = useDocuments({ equipmentUnitId: unitId }, { limit: LIMIT, offset: 0 }, canRead);
  if (!canRead) return null;

  return (
    <Section id="unit-document-registry" title={t("documentRegistryTitle")}>
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
    </Section>
  );
}
