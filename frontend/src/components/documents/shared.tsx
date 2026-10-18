"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/auth/api";
import { effectiveDiffers } from "@/lib/documents/access";
import { documentErrorKey } from "@/lib/documents/errors";
import { isKindCode } from "@/lib/documents/kinds";
import {
  DOCUMENT_STATUSES,
  LINK_KIND_CODES,
  type DocumentStatus,
  type LinkKindCode,
} from "@/lib/documents/types";
import { UploadError } from "@/lib/documents/upload";
import { cn } from "@/lib/utils";

// Текст ошибки для пользователя: код из ответа API или ошибка загрузки, а не сообщение сервера.
export function useDocumentErrorMessage(): (error: unknown) => string {
  const t = useTranslations("Documents.errors");
  return (error) => {
    if (error instanceof ApiError || error instanceof UploadError) return t(documentErrorKey(error.code));
    return t("network");
  };
}

export function useKindLabel(): (code: string) => string {
  const t = useTranslations("Documents.kinds");
  return (code) => (isKindCode(code) ? t(code) : code);
}

export function useLinkKindLabel(): (code: string) => string {
  const t = useTranslations("Documents.linkKinds");
  return (code) => ((LINK_KIND_CODES as readonly string[]).includes(code) ? t(code as LinkKindCode) : code);
}

export function isDocumentStatus(value: string): value is DocumentStatus {
  return (DOCUMENT_STATUSES as readonly string[]).includes(value);
}

export function useStatusLabel(): (status: string) => string {
  const t = useTranslations("Documents.status");
  return (status) => (isDocumentStatus(status) ? t(status) : status);
}

const TONE: Record<DocumentStatus, string> = {
  draft: "border-border text-muted-foreground",
  on_approval: "border-sky-600/30 bg-sky-600/10 text-sky-800 dark:text-sky-300",
  approved: "border-emerald-600/30 bg-emerald-600/10 text-emerald-800 dark:text-emerald-300",
  signed: "border-emerald-600/30 bg-emerald-600/10 text-emerald-800 dark:text-emerald-300",
  archived: "border-border text-muted-foreground",
};

export function StatusBadge({ status }: { status: string | null }) {
  const label = useStatusLabel();
  if (status === null) return <span className="text-muted-foreground">—</span>;
  return (
    <Badge
      variant="outline"
      className={cn("h-6 px-2", isDocumentStatus(status) ? TONE[status] : "")}
      data-status={status}
    >
      {label(status)}
    </Badge>
  );
}

// Отметка «действует vN»: действующая версия (утверждённая или подписанная) не последняя (R1).
export function EffectiveMark({
  doc,
}: {
  doc: { effective_version_no: number | null; latest_version_no: number | null };
}) {
  const t = useTranslations("Documents.common");
  if (doc.effective_version_no === null || !effectiveDiffers(doc)) return null;
  return (
    <span className="text-xs text-muted-foreground tabular-nums" data-testid="document-effective-mark">
      {t("effectiveMark", { version: doc.effective_version_no })}
    </span>
  );
}

export function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}

// Ссылка на файл ведёт в хранилище и отдаётся с Content-Disposition: attachment, поэтому страница
// не меняется. Авторизационный заголовок тут не нужен: ссылка подписана и живёт 120 секунд.
export function openDownload(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
}
