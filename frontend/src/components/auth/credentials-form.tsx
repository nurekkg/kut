"use client";

import { Eye, EyeOff } from "lucide-react";
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
import type { LoginResult } from "@/lib/auth/types";

export function CredentialsForm({ onResult }: { onResult: (result: LoginResult) => void }) {
  const t = useTranslations("Auth.login");
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<FormFailure | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setFailure(null);
    try {
      onResult(await authApi.login(identifier.trim(), password));
    } catch (error) {
      setFailure(toFailure(error));
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <Field id="identifier" label={t("identifier")}>
        <Input
          id="identifier"
          name="username"
          className={INPUT_CLASS}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder={t("identifierPlaceholder")}
          required
          autoFocus
          value={identifier}
          onChange={(event) => setIdentifier(event.target.value)}
          aria-invalid={failure?.key === "invalid_credentials" || undefined}
          aria-describedby="login-error"
        />
      </Field>
      <Field id="password" label={t("password")}>
        <div className="relative">
          <Input
            id="password"
            name="password"
            type={visible ? "text" : "password"}
            className={`${INPUT_CLASS} pr-12`}
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={failure?.key === "invalid_credentials" || undefined}
            aria-describedby="login-error"
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="absolute top-1/2 right-0.5 size-10 -translate-y-1/2 text-muted-foreground"
            onClick={() => setVisible((value) => !value)}
            aria-label={visible ? t("hidePassword") : t("showPassword")}
            aria-pressed={visible}
          >
            {visible ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
          </Button>
        </div>
      </Field>
      <FormError id="login-error" failure={failure} />
      <SubmitButton pending={pending}>{pending ? t("submitting") : t("submit")}</SubmitButton>
    </form>
  );
}
