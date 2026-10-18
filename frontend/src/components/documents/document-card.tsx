"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Archive, ArrowLeft, Download, FilePlus, Pencil } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { Fragment, useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { Section } from "@/components/equipment/section";
import { ArchiveDialog, VersionAmountDialog } from "@/components/documents/document-dialogs";
import { UploadVersionDialog } from "@/components/documents/upload-version-dialog";
import { VersionLines } from "@/components/documents/version-lines";
import {
  EffectiveMark,
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
  canEditVersionAmount,
  canEnterAmount,
  canWriteKind,
} from "@/lib/documents/access";
import { formatDay, formatMegabytes, formatMoment, formatMoney } from "@/lib/documents/format";
import { documentsApi, useDocument, useDocumentEvents, useDocumentKinds } from "@/lib/documents/queries";
import { API, projectKey } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";

// Карточка документа: реквизиты, текущая версия, все версии с суммами. Сумма принадлежит версии (R1):
// правится только у черновика, поэтому кнопка «изменить» стоит у строки версии. Внутренним - ещё автор,
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
  // Журнал переходов - по документному праву чтения (сервер: внутренним с documents:read); механику и
  // начальнику техники, которые видят документ техники по праву техники, журнала не показываем.
  const canSeeEvents = internal && permissions.includes("documents:read");
  const events = useDocumentEvents(documentId, canSeeEvents);
  const projectId = doc.data?.project_id ?? null;
  const project = useQuery({
    queryKey: projectKey(projectId ?? "none"),
    queryFn: () => api.get<Project>(`${API}/projects/${projectId}`),
    enabled: internal && projectId !== null,
  });
  const download = useMutation({
    mutationFn: (versionId: string) => documentsApi.downloadUrl(documentId, versionId),
    onSuccess: (link) => openDownload(link.url),
  });
  const [uploading, setUploading] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [amountOf, setAmountOf] = useState<{ id: string; number: number; amount: string | null } | null>(null);
  const [linesOf, setLinesOf] = useState<string | null>(null);
  const kinds = useDocumentKinds();
  const linesT = useTranslations("Documents.lines");

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
  const canUpload = internal && d.archived_at === null && canWriteKind(d.kind, permissions);
  const canArchiveNow = internal && d.archived_at === null && canArchive(permissions);
  const current = d.current_version;
  const downloadable = canDownload(d, permissions, internal);
  const versions = [...d.versions].sort((a, b) => b.version_no - a.version_no);
  const usesLines = kinds.data?.some((kind) => kind.code === d.kind && kind.lines) ?? false;
  // Название объекта отдаёт сервер по видимости; внешним справочника объектов не открыт.
  const projectName = d.project_name ?? project.data?.name ?? common("noValue");

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

      <Section id="document-requisites-title" title={t("requisites")}>
        <dl data-testid="document-requisites" className="grid gap-3 sm:grid-cols-2">
          <Row label={t("kind")}>{kindLabel(d.kind)}</Row>
          {d.registration_no ? <Row label={t("registrationNo")}>{d.registration_no}</Row> : null}
          {d.project_id ? (
            <Row label={t("project")}>
              {internal ? (
                <Link
                  href={`/projects/${d.project_id}`}
                  className="underline-offset-4 hover:underline"
                  data-testid="document-project-link"
                >
                  {projectName}
                </Link>
              ) : (
                <span data-testid="document-project-name">{projectName}</span>
              )}
            </Row>
          ) : null}
          {d.counterparty_id !== null ? (
            <Row label={t("counterparty")}>{d.counterparty_name ?? common("noValue")}</Row>
          ) : null}
          {internal ? <Row label={t("legalEntity")}>{d.legal_entity_name ?? common("noValue")}</Row> : null}
          <Row label={t("created")}>{formatMoment(d.created_at)}</Row>
          {d.archived_at ? <Row label={t("archivedAt")}>{formatMoment(d.archived_at)}</Row> : null}
          {d.amount !== null ? (
            <Row label={t("currentAmount")} testId="document-amount">
              <span className="tabular-nums">{formatMoney(d.amount)}</span>
              {d.amount_version_no !== null ? (
                <span className="ml-2 text-muted-foreground">{t("amountVersion", { number: d.amount_version_no })}</span>
              ) : null}
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
                <EffectiveMark doc={d} />
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
        ) : versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("noVersion")}</p>
        ) : (
          <div className="flex flex-col gap-3" data-testid="document-no-approved">
            <p className="text-sm text-muted-foreground">{t("noApprovedVersion")}</p>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{t("versionNo", { number: versions[0].version_no })}</span>
              <StatusBadge status={versions[0].status} />
              <span className="tabular-nums text-muted-foreground">{formatDay(versions[0].created_at)}</span>
            </div>
          </div>
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
              <Fragment key={version.id}>
                <li
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
                      {version.amount !== null ? (
                        <span className="tabular-nums text-foreground" data-testid="document-version-amount">
                          {t("versionAmount", { amount: formatMoney(version.amount) })}
                        </span>
                      ) : null}
                    </div>
                    {version.comment ? <p className="break-words">{version.comment}</p> : null}
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {canEditVersionAmount(d, version, permissions, internal) ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setAmountOf({ id: version.id, number: version.version_no, amount: version.amount })
                        }
                        data-testid="document-version-amount-open"
                      >
                        <Pencil data-icon="inline-start" aria-hidden />
                        {version.amount === null ? t("setAmount") : t("editAmount")}
                      </Button>
                    ) : null}
                    {usesLines ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-expanded={linesOf === version.id}
                        onClick={() => setLinesOf(linesOf === version.id ? null : version.id)}
                        data-testid="document-version-lines-toggle"
                      >
                        {linesOf === version.id ? linesT("close") : linesT("title")}
                      </Button>
                    ) : null}
                    {downloadable ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={download.isPending}
                        onClick={() => download.mutate(version.id)}
                        data-testid="document-version-download"
                      >
                        <Download data-icon="inline-start" aria-hidden />
                        {t("download")}
                      </Button>
                    ) : null}
                  </div>
                </li>
              {usesLines && linesOf === version.id ? (
                <li className="p-3">
                  <VersionLines
                    documentId={documentId}
                    kind={d.kind}
                    archivedAt={d.archived_at}
                    version={version}
                    permissions={permissions}
                    internal={internal}
                  />
                </li>
              ) : null}
              </Fragment>
            ))}
          </ul>
        )}
      </Section>

      {canSeeEvents ? (
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

      {uploading ? (
        <UploadVersionDialog
          documentId={d.id}
          canAmount={canEnterAmount(d, permissions, internal)}
          onClose={() => setUploading(false)}
        />
      ) : null}
      {archiving ? <ArchiveDialog documentId={d.id} onClose={() => setArchiving(false)} /> : null}
      {amountOf !== null ? (
        <VersionAmountDialog
          documentId={d.id}
          versionId={amountOf.id}
          versionNo={amountOf.number}
          initial={amountOf.amount}
          onClose={() => setAmountOf(null)}
        />
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
