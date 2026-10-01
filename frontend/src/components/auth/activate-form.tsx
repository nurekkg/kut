"use client";

import { Eye, EyeOff } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Field, INPUT_CLASS, SubmitButton } from "@/components/auth/form-parts";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, authApi } from "@/lib/auth/api";

const MIN_LENGTH = 10;
type ApiFailure = "invalid_invite" | "too_many_attempts" | "validation" | "network" | "unknown";
type Failure = ApiFailure | "mismatch" | "tooShort";

function toFailure(error: unknown): ApiFailure {
  if (!(error instanceof ApiError)) return "network";
  if (error.code === "invalid_invite" || error.code === "too_many_attempts" || error.code === "validation") {
    return error.code;
  }
  return "unknown";
}

export function ActivateForm() {
  const t = useTranslations("Activate");
  const brand = useTranslations("Auth")("brand");
  const params = useSearchParams();
  // Токен читаем один раз и убираем из адресной строки: он одноразовый, но в истории браузера ему не место.
  const [token] = useState(() => params.get("token"));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    if (password.length < MIN_LENGTH) return setFailure("tooShort");
    if (password !== confirm) return setFailure("mismatch");
    setPending(true);
    setFailure(null);
    try {
      await authApi.activate(token, password);
      setDone(true);
    } catch (error) {
      setFailure(toFailure(error));
      setPending(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-4 py-10">
      <p className="text-2xl font-semibold tracking-tight">{brand}</p>
      <Card>
        <CardHeader>
          <CardTitle>
            <h1>{done ? t("doneTitle") : t("title")}</h1>
          </CardTitle>
          <CardDescription>{done ? t("doneText") : token ? t("description") : t("noToken")}</CardDescription>
        </CardHeader>
        <CardContent>
          {done ? (
            <Link href="/login" className={buttonVariants({ size: "lg", className: "h-11 w-full text-base" })}>
              {t("toLogin")}
            </Link>
          ) : token ? (
            <form method="post" onSubmit={submit} className="space-y-5">
              <Field id="new-password" label={t("password")}>
                <div className="relative">
                  <Input
                    id="new-password"
                    name="new-password"
                    type={visible ? "text" : "password"}
                    className={`${INPUT_CLASS} pr-12`}
                    autoComplete="new-password"
                    required
                    minLength={MIN_LENGTH}
                    maxLength={128}
                    autoFocus
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    aria-describedby="activate-error"
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
              <Field id="confirm-password" label={t("confirm")}>
                <Input
                  id="confirm-password"
                  name="confirm-password"
                  type={visible ? "text" : "password"}
                  className={INPUT_CLASS}
                  autoComplete="new-password"
                  required
                  maxLength={128}
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                  aria-describedby="activate-error"
                />
              </Field>
              <div id="activate-error" role="alert" aria-live="assertive">
                {failure ? (
                  <p className="text-sm text-destructive">
                    {failure === "mismatch" || failure === "tooShort" ? t(failure) : t(`errors.${failure}`)}
                  </p>
                ) : null}
              </div>
              <SubmitButton pending={pending}>{pending ? t("submitting") : t("submit")}</SubmitButton>
            </form>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
