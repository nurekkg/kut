"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { FormMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isRole } from "@/lib/roles";

// Окно живёт, пока смонтировано: родитель показывает его условно, поэтому состояние формы
// и ключ повтора создаются заново при каждом открытии.
export function Modal({
  title,
  description,
  onClose,
  wide = false,
  children,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  wide?: boolean;
  children: ReactNode;
}) {
  const t = useTranslations("Settings.common");
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent closeLabel={t("close")} className={wide ? "max-w-2xl" : undefined}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}

export function PendingIcon({ pending }: { pending: boolean }) {
  return pending ? (
    <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" aria-hidden />
  ) : null;
}

export function FormActions({
  pending,
  submitLabel,
  pendingLabel,
  onCancel,
  error,
}: {
  pending: boolean;
  submitLabel: string;
  pendingLabel: string;
  onCancel: () => void;
  error: unknown;
}) {
  const t = useTranslations("Settings.common");
  return (
    <div className="space-y-3">
      <FormMessage error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" size="lg" onClick={onCancel}>
          {t("cancel")}
        </Button>
        <Button type="submit" size="lg" disabled={pending}>
          <PendingIcon pending={pending} />
          {pending ? pendingLabel : submitLabel}
        </Button>
      </DialogFooter>
    </div>
  );
}

export function ConfirmModal({
  title,
  text,
  confirmLabel,
  pendingLabel,
  pending,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  text: string;
  confirmLabel: string;
  pendingLabel: string;
  pending: boolean;
  error: unknown;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const t = useTranslations("Settings.common");
  return (
    <Modal title={title} description={text} onClose={onClose}>
      <FormMessage error={error} />
      <DialogFooter>
        <Button type="button" variant="outline" size="lg" onClick={onClose}>
          {t("cancel")}
        </Button>
        <Button type="button" variant="destructive" size="lg" disabled={pending} onClick={onConfirm}>
          <PendingIcon pending={pending} />
          {pending ? pendingLabel : confirmLabel}
        </Button>
      </DialogFooter>
    </Modal>
  );
}

export function useRoleLabel(): (code: string, fallback?: string) => string {
  const roles = useTranslations("Roles");
  return (code, fallback) => (isRole(code) ? roles(code) : (fallback ?? code));
}
