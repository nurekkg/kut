"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

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
import type { TokenResponse } from "@/lib/auth/types";

type Props = {
  mfaToken: string;
  onSuccess: (response: TokenResponse) => void;
  onRestart: () => void;
};

export function CodeForm({ mfaToken, onSuccess, onRestart }: Props) {
  const t = useTranslations("Auth.code");
  const [recovery, setRecovery] = useState(false);
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<FormFailure | null>(null);

  function switchMode() {
    setRecovery((current) => !current);
    setValue("");
    setFailure(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFailure(null);
    try {
      const proof = recovery
        ? { recovery_code: value.trim() }
        : { code: value.replace(/\s/g, "") };
      onSuccess(await authApi.verifyMfa(mfaToken, proof));
    } catch (error) {
      setFailure(toFailure(error));
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      {recovery ? (
        <Field id="recovery-code" label={t("recoveryLabel")} hint={t("recoveryHint")}>
          <Input
            id="recovery-code"
            key="recovery"
            className={`${INPUT_CLASS} font-mono`}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="xxxxx-xxxxx"
            maxLength={32}
            required
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
            aria-invalid={failure?.key === "invalid_code" || undefined}
            aria-describedby="recovery-code-hint code-error"
          />
        </Field>
      ) : (
        <Field id="totp-code" label={t("label")} hint={t("hint")}>
          <Input
            id="totp-code"
            key="totp"
            className={`${INPUT_CLASS} text-center font-mono text-2xl tracking-[0.35em] tabular-nums`}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9 ]*"
            maxLength={7}
            placeholder="000000"
            required
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value.replace(/[^0-9 ]/g, ""))}
            aria-invalid={failure?.key === "invalid_code" || undefined}
            aria-describedby="totp-code-hint code-error"
          />
        </Field>
      )}
      <FormError id="code-error" failure={failure} />
      <SubmitButton pending={pending}>{t("submit")}</SubmitButton>
      <div className="flex flex-col items-start gap-1">
        <Button type="button" variant="link" className="h-auto p-0" onClick={switchMode}>
          {recovery ? t("useApp") : t("useRecovery")}
        </Button>
        <Button
          type="button"
          variant="link"
          className="h-auto p-0 text-muted-foreground"
          onClick={onRestart}
        >
          {t("restart")}
        </Button>
      </div>
    </form>
  );
}
