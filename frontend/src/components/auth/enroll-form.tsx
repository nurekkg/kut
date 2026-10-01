"use client";

import { Check, Copy } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  Field,
  FormError,
  INPUT_CLASS,
  SubmitButton,
  toFailure,
  type FormFailure,
} from "@/components/auth/form-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { authApi } from "@/lib/auth/api";
import type { MfaConfirmResult, MfaSetup } from "@/lib/auth/types";

type Props = {
  mfaToken: string;
  password: string;
  onConfirmed: (result: MfaConfirmResult) => void;
  onRestart: () => void;
};

function groupsOfFour(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

export function EnrollForm({ mfaToken, password, onConfirmed, onRestart }: Props) {
  const t = useTranslations("Auth.enroll");
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<FormFailure | null>(null);

  // Каждый вызов setup выдаёт новый секрет и заменяет прежний, поэтому запрашиваем ровно один раз.
  const requested = useRef(false);
  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    authApi
      .setupMfa(mfaToken, password)
      .then(setSetup)
      .catch((error: unknown) => setFailure(toFailure(error)));
  }, [mfaToken, password]);

  async function copySecret() {
    if (!setup) return;
    try {
      await navigator.clipboard.writeText(setup.secret);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      // буфер обмена недоступен (например, не защищённое соединение): ключ можно выделить вручную
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFailure(null);
    try {
      onConfirmed(await authApi.confirmMfa(mfaToken, code.replace(/\s/g, "")));
    } catch (error) {
      setFailure(toFailure(error));
      setPending(false);
    }
  }

  if (!setup) {
    return (
      <div className="space-y-4">
        <FormError id="enroll-error" failure={failure} />
        {failure ? (
          <Button type="button" variant="outline" size="lg" className="h-11 w-full" onClick={onRestart}>
            {t("restart")}
          </Button>
        ) : (
          <p role="status" className="text-sm text-muted-foreground">
            {t("loading")}
          </p>
        )}
      </div>
    );
  }

  return (
    <form method="post" onSubmit={submit} className="space-y-5">
      <section aria-labelledby="enroll-key" className="space-y-3">
        <h3 id="enroll-key" className="text-sm font-medium">
          {t("step1")}
        </h3>
        <div className="flex items-center justify-between gap-3 rounded-lg bg-muted px-3 py-2.5">
          <code className="font-mono text-base tracking-wide break-all">
            {groupsOfFour(setup.secret)}
          </code>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="shrink-0"
            onClick={() => void copySecret()}
          >
            {copied ? <Check data-icon="inline-start" aria-hidden /> : <Copy data-icon="inline-start" aria-hidden />}
            {copied ? t("copied") : t("copy")}
          </Button>
        </div>
        <a
          href={setup.otpauth_uri}
          className="inline-block text-sm text-primary underline-offset-4 hover:underline"
        >
          {t("openApp")}
        </a>
      </section>
      <Field id="enroll-code" label={t("codeLabel")} hint={t("step2")}>
        <Input
          id="enroll-code"
          className={`${INPUT_CLASS} text-center font-mono text-2xl tracking-[0.35em] tabular-nums`}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9 ]*"
          maxLength={7}
          placeholder="000000"
          required
          value={code}
          onChange={(event) => setCode(event.target.value.replace(/[^0-9 ]/g, ""))}
          aria-invalid={failure?.key === "invalid_code" || undefined}
          aria-describedby="enroll-code-hint enroll-error"
        />
      </Field>
      <FormError id="enroll-error" failure={failure} />
      <SubmitButton pending={pending}>{t("submit")}</SubmitButton>
    </form>
  );
}
