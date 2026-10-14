"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Archive, ArrowLeft, Download, FilePlus, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { Section } from "@/components/equipment/section";
import { AmountDialog, ArchiveDialog } from "@/components/documents/document-dialogs";
import { UploadVersionDialog } from "@/components/documents/upload-version-dialog";
import {
  openDownload,
  StatusBadge,
  useDocumentErrorMessage,
  useKindLabel,
  useLinkKindLabel,
  useStatusLabel,
} from "@/components/documents/shared";
import { AppNav } from "@/components/projects/app-nav";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import {
  canArchive,
  canDownload,
  canReadFinancial,
  canShowAmounts,
  canUploadVersion,
} from "@/lib/documents/access";
import { formatAmount, formatDay, formatMegabytes, formatMoment } from "@/lib/documents/format";
import { documentsApi, useDocument, useDocumentEvents } from "@/lib/documents/queries";
import { LEGAL_ENTITIES_REF_KEY, API, projectKey } from "@/lib/projects/keys";
import type { NamedRef, Project } from "@/lib/projects/types";

// Карточка документа: реквизиты, текущая версия, все версии. Внутренним - ещё автор, комментарии,
// события статусов и связи; внешним (заказчик, субподрядчик) только то, что отдал сервер.
export function DocumentCard({ documentId }: { documentId: string }) {
  const t = useTranslations("Documents.card");
  const common = useTranslations("Documents.common");
  const message = useDocumentErrorMessage();
  const kindLabel = useKindLabel();
  const statusLabel = useStatusLabel();
  const linkKindLabel = useLinkKindLabel();
  const { user } = useAuth();
  const permissions = user?.permissions ?? [];
  const internal = user?.is_internal ?? false;
  const doc = useDocument(documentId);
  const events = useDocumentEvents(documentId, internal);
  const projectId = doc.data?.project_id ?? null;
  const project = useQuery({
    queryKey: projectKey(projectId ?? "none"),
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
    enabled: internal && projectId !== null,
  });
  const legalEntities = useQuery({
    queryKey: LEGAL_ENTITIES_REF_KEY,
    queryFn: () => api.get<NamedRef[]>(`${API}/legal-entities`),
    staleTime: 60_000,
    enabled: internal,
  });
  const download = useMutation({
    mutationFn: (versionId: string) => documentsApi.downloadUrl(documentId, versionId),
    onSuccess: (link) => openDownload(link.url),
  });
  const [uploading, setUploading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [editingAmount, setEditingAmount] = useState(false);

  if (doc.isPending) {
    return (
      <Frame backHref="/projects">
        <p className="text-sm text-muted-foreground">{common("loading")}</p>
      </Frame>
    );
  }
  if (doc.isError) {
    return (
      <Frame backHref="/projects">
        <p role="alert" className="text-sm text-destructive">
          {message(doc.error)}
        </p>
      </Frame>
    );
  }

  const d = doc.data;
  const canUpload = internal && d.archived_at === null && canUploadVersion(permissions);
  const canArchiveNow = internal && d.archived_at === null && canArchive(permissions);
  const canSetAmount = internal && d.financial && d.archived_at === null && canReadFinancial(permissions);
  const current = d.current_version;
  const downloadable = canDownload(d, permissions);
  const versions = [...d.versions].sort((a, b) => b.version_no - a.version_no);
  const legalName = legalEntities.data?.find((item) => item.id === d.legal_entity_id)?.name ?? "—";

  return (
    <Frame backHref={d.project_id ? `/projects/${d.project_id}/documents` : "/projects"}>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="min-w-0 text-xl font-semibold tracking-tight break-words">{d.title}</h1>
            {d.archived_at ? (
              <Badge variant="outline" className="h-6 px-2">
                {t("archivedBadge")}
              </Badge>
            ) : null}
          </div>
          <p className="text-sm break-all text-muted-foreground">
            {[kindLabel(d.kind), d.registration_no].filter(Boolean).join(" · ")}
          </p>
        </div>
        {canArchiveNow ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="h-10"
            onClick={() => setArchiving(true)}
            data-testid="document-archive-open"
          >
            <Archive data-icon="inline-start" aria-hidden />
            {t("archive")}
          </Button>
        ) : null}
      </header>

      {download.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {message(download.error)}
        </p>
      ) : null}

      <Section
        id="document-requisites-title"
        title={t("requisites")}
        action={
          canSetAmount ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setEditingAmount(true)}
              data-testid="document-amount-open"
            >
              <Pencil data-icon="inline-start" aria-hidden />
              {d.amount === null ? t("setAmount") : t("changeAmount")}
            </Button>
          ) : null
        }
      >
        <dl data-testid="document-requisites" className="grid gap-3 sm:grid-cols-2">
          <Row label={t("kind")}>{kindLabel(d.kind)}</Row>
          {d.registration_no ? <Row label={t("registrationNo")}>{d.registration_no}</Row> : null}
          {d.project_id ? (
            <Row label={t("project")}>
              <Link
                href={`/projects/${d.project_id}`}
                className="underline-offset-4 hover:underline"
                data-testid="document-project-link"
              >
                {project.data?.name ?? t("openProject")}
              </Link>
            </Row>
          ) : null}
          {d.counterparty_id !== null ? (
            <Row label={t("counterparty")}>{d.counterparty_name ?? common("noValue")}</Row>
          ) : null}
          {internal ? <Row label={t("legalEntity")}>{legalName}</Row> : null}
          <Row label={t("created")}>{formatMoment(d.created_at)}</Row>
          {d.archived_at ? <Row label={t("archivedAt")}>{formatMoment(d.archived_at)}</Row> : null}
          {d.amount !== null && canShowAmounts(permissions, internal) ? (
            <Row label={t("amount")} testId="document-amount">
              <span className="tabular-nums">{formatAmount(d.amount)}</span>
            </Row>
          ) : null}
        </dl>
      </Section>

      <Section id="document-current-title" title={t("currentVersion")}>
        {current ? (
          <div className="flex flex-wrap items-center justify-between gap-3" data-testid="document-current-version">
            <div className="min-w-0 space-y-1">
              <p className="font-medium">{t("versionNo", { number: current.version_no })}</p>
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusBadge status={current.status} />
                <span className="tabular-nums">{formatDay(current.created_at)}</span>
              </div>
            </div>
            {downloadable ? (
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-10"
                disabled={download.isPending}
                onClick={() => download.mutate(current.id)}
                data-testid="document-download"
              >
                <Download data-icon="inline-start" aria-hidden />
                {t("download")}
              </Button>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">{t("noVersion")}</p>
        )}
      </Section>

      <Section
        id="document-versions-title"
        title={t("versions")}
        action={
          canUpload ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setUploading(true)}
              data-testid="document-upload-open"
            >
              <FilePlus data-icon="inline-start" aria-hidden />
              {t("upload")}
            </Button>
          ) : null
        }
      >
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("noVersion")}</p>
        ) : (
          <ul data-testid="document-versions" className="flex flex-col divide-y rounded-lg border">
            {versions.map((version) => (
              <li
                key={version.id}
                data-testid="document-version-row"
                className="flex flex-col gap-2 p-3 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0 space-y-1 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{t("versionNo", { number: version.version_no })}</span>
                    <StatusBadge status={version.status} />
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                    {version.author_name ? <span>{t("authorValue", { name: version.author_name })}</span> : null}
                    <span className="tabular-nums">{formatDay(version.created_at)}</span>
                    <span className="tabular-nums">{t("sizeMb", { value: formatMegabytes(version.size_bytes) })}</span>
                  </div>
                  {version.comment ? <p className="break-words">{version.comment}</p> : null}
                </div>
                {downloadable ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    disabled={download.isPending}
                    onClick={() => download.mutate(version.id)}
                    data-testid="document-version-download"
                  >
                    <Download data-icon="inline-start" aria-hidden />
                    {t("download")}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {internal ? (
        <div data-testid="document-events">
          <Section id="document-events-title" title={t("events")}>
            {events.isPending ? <p className="text-sm text-muted-foreground">{common("loading")}</p> : null}
            {events.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {message(events.error)}
              </p>
            ) : null}
            {events.isSuccess && events.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("noEvents")}</p>
            ) : null}
            {events.isSuccess && events.data.length > 0 ? (
              <ol className="flex flex-col divide-y rounded-lg border">
                {events.data.map((event) => (
                  <li key={event.id} className="flex flex-col gap-1 p-3 text-sm">
                    <span className="font-medium">
                      {t("eventRow", {
                        version: event.version_no,
                        from: event.from_status ? statusLabel(event.from_status) : t("noStatus"),
                        to: statusLabel(event.to_status),
                      })}
                    </span>
                    <span className="text-muted-foreground">
                      {[event.actor_name, formatMoment(event.created_at)].filter(Boolean).join(" · ")}
                    </span>
                    {event.comment ? <span className="break-words">{event.comment}</span> : null}
                  </li>
                ))}
              </ol>
            ) : null}
          </Section>
        </div>
      ) : null}

      {internal ? (
        <div data-testid="document-links">
          <Section id="document-links-title" title={t("links")}>
            {d.links.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("noLinks")}</p>
            ) : (
              <ul className="flex flex-col gap-1 text-sm">
                {d.links.map((link) => (
                  <li key={`${link.kind}-${link.id}`} className="break-all">
                    {t("linkItem", { kind: linkKindLabel(link.kind), id: link.id })}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      ) : null}

      {uploading ? <UploadVersionDialog documentId={d.id} onClose={() => setUploading(false)} /> : null}
      {archiving ? <ArchiveDialog documentId={d.id} onClose={() => setArchiving(false)} /> : null}
      {editingAmount ? (
        <AmountDialog documentId={d.id} initial={d.amount} onClose={() => setEditingAmount(false)} />
      ) : null}
    </Frame>
  );
}

function Frame({ backHref, children }: { backHref: string; children: ReactNode }) {
  const t = useTranslations("Documents.card");
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-5 px-4 py-4" data-testid="document-card">
      <UserMenu />
      <AppNav />
      <Link
        href={backHref}
        className="inline-flex min-h-10 w-fit items-center gap-1.5 rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <ArrowLeft className="size-4" aria-hidden />
        {t("back")}
      </Link>
      {children}
    </main>
  );
}

function Row({ label, testId, children }: { label: string; testId?: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5" data-testid={testId}>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm break-words">{children}</dd>
    </div>
  );
}
