"use client";

import { useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { CodeForm } from "@/components/auth/code-form";
import { CredentialsForm } from "@/components/auth/credentials-form";
import { EnrollForm } from "@/components/auth/enroll-form";
import { RecoveryCodes } from "@/components/auth/recovery-codes";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { landingPath, safeNext } from "@/lib/auth/next-path";
import type { LoginResult, MfaConfirmResult, TokenResponse } from "@/lib/auth/types";

type Step =
  | { kind: "credentials" }
  | { kind: "code"; mfaToken: string }
  | { kind: "enroll"; mfaToken: string; password: string }
  | { kind: "saved-codes"; codes: string[]; session: TokenResponse };

export function LoginFlow() {
  const t = useTranslations("Auth");
  const router = useRouter();
  const requested = useSearchParams().get("next");
  const { status, signIn, user } = useAuth();
  const next = requested === null ? landingPath(user) : safeNext(requested);
  const [step, setStep] = useState<Step>({ kind: "credentials" });

  // Вошедшего пользователя форма не задерживает. Сеанс после настройки 2FA в память не кладём,
  // пока человек не подтвердит, что сохранил коды восстановления: они показываются один раз.
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  // Пароль нужен только шагу настройки 2FA (ключ выдаётся после его подтверждения) и лежит в памяти, пока шаг открыт.
  function handleLogin(result: LoginResult, password: string) {
    if (result.status === "ok") signIn(result);
    else if (result.status === "mfa_required") setStep({ kind: "code", mfaToken: result.mfa_token });
    else setStep({ kind: "enroll", mfaToken: result.mfa_token, password });
  }

  function handleConfirmed(result: MfaConfirmResult) {
    if (result.session) {
      setStep({ kind: "saved-codes", codes: result.recovery_codes, session: result.session });
    }
  }

  const restart = () => setStep({ kind: "credentials" });

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <p className="text-2xl font-semibold tracking-tight">{t("brand")}</p>
      <Card>
        <CardHeader>
          <CardTitle>
            <h1>{t(`steps.${step.kind}.title`)}</h1>
          </CardTitle>
          <CardDescription>{t(`steps.${step.kind}.description`)}</CardDescription>
        </CardHeader>
        <CardContent>
          {step.kind === "credentials" ? <CredentialsForm onResult={handleLogin} /> : null}
          {step.kind === "code" ? (
            <CodeForm mfaToken={step.mfaToken} onSuccess={signIn} onRestart={restart} />
          ) : null}
          {step.kind === "enroll" ? (
            <EnrollForm
              mfaToken={step.mfaToken}
              password={step.password}
              onConfirmed={handleConfirmed}
              onRestart={restart}
            />
          ) : null}
          {step.kind === "saved-codes" ? (
            <RecoveryCodes codes={step.codes} onContinue={() => signIn(step.session)} />
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
