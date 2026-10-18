"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import {
  EffectiveMark,
  StatusBadge,
  useDocumentErrorMessage,
  useKindLabel,
} from "@/components/documents/shared";
import { buttonVariants } from "@/components/ui/button";
import { useDocuments } from "@/lib/documents/queries";

const RECENT = 20;

// Вкладка «Документы» узла: последние документы, привязанные именно к этому узлу (project_node_id),
// и переход в реестр объекта.
export function NodeDocumentsTab({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const t = useTranslations("Documents.objectTab");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const kindLabel = useKindLabel();
  const list = useDocuments({ projectId, projectNodeId: nodeId }, { limit: RECENT, offset: 0 });

  return (
    <section aria-labelledby="object-documents-title" className="flex flex-col gap-3" data-testid="object-documents">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="object-documents-title" className="text-sm font-medium">
          {t("title")}
        </h3>
        <Link
          href={`/projects/${projectId}/documents`}
          className={buttonVariants({ variant: "outline", size: "sm" })}
          data-testid="object-documents-open-registry"
        >
          {t("openRegistry")}
        </Link>
      </div>
      {list.isPending ? <p className="text-sm text-muted-foreground">{common("loading")}</p> : null}
      {list.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(list.error)}
        </p>
      ) : null}
      {list.isSuccess && list.data.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">{t("empty")}</p>
      ) : null}
      {list.isSuccess && list.data.length > 0 ? (
        <ul className="flex flex-col divide-y rounded-lg border">
          {list.data.map((row) => (
            <li key={row.id}>
              <Link
                href={`/documents/${row.id}`}
                data-testid="object-document-row"
                data-document-id={row.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm outline-none hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span className="font-medium break-all">{row.registration_no ?? common("noValue")}</span>
                <span className="min-w-0 flex-1 break-words">{row.title}</span>
                <span className="text-muted-foreground">{kindLabel(row.kind)}</span>
                <StatusBadge status={row.latest_status} />
                <EffectiveMark doc={row} />
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
