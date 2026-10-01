"use client";

import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/auth/api";

export const INPUT_CLASS = "h-11 text-base";

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function SubmitButton({ pending, children }: { pending: boolean; children: ReactNode }) {
  return (
    <Button type="submit" size="lg" className="h-11 w-full text-base" disabled={pending}>
      {pending ? (
        <Loader2 data-icon="inline-start" className="motion-safe:animate-spin" aria-hidden />
      ) : null}
      {children}
    </Button>
  );
}

const KNOWN_ERRORS = [
  "invalid_credentials",
  "too_many_attempts",
  "invalid_code",
  "invalid_token",
  "mfa_already_enabled",
  "mfa_not_started",
  "validation",
] as const;

type ErrorKey = (typeof KNOWN_ERRORS)[number] | "network" | "unknown";

export type FormFailure = { key: ErrorKey; minutes: number };

export function toFailure(error: unknown): FormFailure {
  if (!(error instanceof ApiError)) return { key: "network", minutes: 0 };
  const known = (KNOWN_ERRORS as readonly string[]).includes(error.code);
  return {
    key: known ? (error.code as ErrorKey) : "unknown",
    minutes: Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60)),
  };
}

export function FormError({ id, failure }: { id: string; failure: FormFailure | null }) {
  const t = useTranslations("Auth.errors");
  return (
    <div id={id} role="alert" aria-live="assertive">
      {failure ? (
        <p className="text-sm text-destructive">{t(failure.key, { minutes: failure.minutes })}</p>
      ) : null}
    </div>
  );
}
