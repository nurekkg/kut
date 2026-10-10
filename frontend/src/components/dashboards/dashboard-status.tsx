"use client";

import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/auth/api";

// Загрузка, отказ в доступе и сбой: короткий текст вместо падения страницы.
export function DashboardStatus({
  loading,
  error,
  onRetry,
}: {
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const t = useTranslations("Dashboard");
  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  }
  if (error instanceof ApiError && error.status === 403) {
    return (
      <p role="alert" className="text-sm">
        {t("forbidden")}
      </p>
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <p role="alert" className="text-sm">
        {t("notFound")}
      </p>
    );
  }
  if (error) {
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>{t("error")}</p>
        <button
          type="button"
          className="rounded-sm text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          onClick={onRetry}
        >
          {t("retry")}
        </button>
      </div>
    );
  }
  return null;
}
