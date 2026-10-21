"use client";

import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/auth/api";
import { stockErrorKey } from "@/lib/stock/errors";

// Ошибка API склада/справочника -> понятный русский текст. Сетевой сбой и неизвестный код - общие тексты.
export function useStockErrorMessage(): (error: unknown) => string {
  const t = useTranslations("StockErrors");
  return (error) => {
    if (!(error instanceof ApiError)) return t("network");
    const key = stockErrorKey(error.code);
    return key === null ? t("unknown") : t(key as Parameters<typeof t>[0]);
  };
}
