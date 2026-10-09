"use client";

import { AlertTriangle, CheckCircle2, Loader2, MapPinOff } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { POOR_ACCURACY_M, type GeoState } from "@/lib/field/geo";

// Состояние геопозиции на шаге «фото»: точность видна, отказ в доступе объяснён, отправке факта
// это не мешает.
export function GeoCard({ state, onRetry }: { state: GeoState; onRetry: () => void }) {
  const t = useTranslations("Field.geo");
  const poor = state.status === "ready" && state.accuracy > POOR_ACCURACY_M;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="geo-card"
      data-state={state.status}
      className="space-y-2 rounded-xl border p-3 text-sm"
    >
      <div className="flex items-start gap-2.5">
        {state.status === "locating" ? (
          <Loader2 className="mt-0.5 size-5 shrink-0 motion-safe:animate-spin" aria-hidden />
        ) : state.status === "ready" && !poor ? (
          <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-600" aria-hidden />
        ) : state.status === "ready" ? (
          <AlertTriangle className="mt-0.5 size-5 shrink-0 text-amber-600" aria-hidden />
        ) : (
          <MapPinOff className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <div className="min-w-0 space-y-0.5">
          <p className="font-medium">
            {state.status === "locating" ? t("locating") : null}
            {state.status === "ready" ? t("ready", { accuracy: Math.round(state.accuracy) }) : null}
            {state.status === "denied" ? t("deniedTitle") : null}
            {state.status === "unavailable" ? t("unavailableTitle") : null}
            {state.status === "timeout" ? t("timeoutTitle") : null}
          </p>
          {poor ? <p className="text-muted-foreground">{t("poor")}</p> : null}
          {state.status === "denied" ? <p className="text-muted-foreground">{t("deniedHint")}</p> : null}
          {state.status === "unavailable" ? (
            <p className="text-muted-foreground">{t("unavailableHint")}</p>
          ) : null}
          {state.status === "timeout" ? <p className="text-muted-foreground">{t("timeoutHint")}</p> : null}
        </div>
      </div>
      {state.status !== "locating" ? (
        <Button type="button" variant="ghost" size="lg" className="h-12" onClick={onRetry}>
          {t("retry")}
        </Button>
      ) : null}
    </div>
  );
}
