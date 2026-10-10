"use client";

import { AlertTriangle, Send } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { GeoCard } from "@/components/field/geo-card";
import { PhotoStrip } from "@/components/field/photo-strip";
import { Button } from "@/components/ui/button";
import { useGeolocation, type GeoState } from "@/lib/field/geo";
import type { OutboxPhoto } from "@/lib/field/outbox-types";
import { formatShortDate } from "@/lib/schedule/dates";
import { formatVolume } from "@/lib/schedule/format";

export type Summary = {
  node: string;
  work: string;
  unit: string;
  volume: string;
  workDate: string;
  unplanned: boolean;
};

// Шаг «фото и место»: снимки с камеры, геопозиция определяется сама, отказ в доступе не мешает
// отправить факт. Кнопка «Отправить» кладёт запись в очередь устройства и сразу отпускает экран.
export function EvidenceStep({
  summary,
  photos,
  onAdd,
  onRemove,
  onSend,
  sending,
  error,
}: {
  summary: Summary;
  photos: OutboxPhoto[];
  onAdd: (added: OutboxPhoto[]) => void;
  onRemove: (id: string) => void;
  onSend: (geo: GeoState) => void;
  sending: boolean;
  error: string | null;
}) {
  const t = useTranslations("Field.evidence");
  const [attempt, setAttempt] = useState(0);
  const geo = useGeolocation(attempt);

  return (
    <div className="space-y-5">
      <section aria-label={t("summary")} className="space-y-1 rounded-xl bg-muted/50 p-3 text-sm">
        <p className="font-medium break-words">{summary.work}</p>
        <p className="text-muted-foreground break-words">{summary.node}</p>
        <p className="tabular-nums" data-testid="evidence-volume">
          {formatVolume(summary.volume)} {summary.unit} · {formatShortDate(summary.workDate)}
        </p>
      </section>
      {summary.unplanned ? (
        <p
          role="note"
          className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {t("unplanned")}
        </p>
      ) : null}

      <section aria-labelledby="field-photos" className="space-y-2">
        <h2 id="field-photos" className="text-sm font-semibold">
          {t("photos")}
        </h2>
        <PhotoStrip photos={photos} onAdd={onAdd} onRemove={onRemove} />
      </section>

      <section aria-labelledby="field-place" className="space-y-2">
        <h2 id="field-place" className="text-sm font-semibold">
          {t("place")}
        </h2>
        <GeoCard state={geo} onRetry={() => setAttempt((value) => value + 1)} />
      </section>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <Button
        type="button"
        size="lg"
        className="h-14 w-full text-base"
        disabled={sending}
        onClick={() => onSend(geo)}
        data-testid="send"
      >
        <Send data-icon="inline-start" className="size-5" aria-hidden />
        {t("send")}
      </Button>
      {photos.length === 0 ? (
        <p className="text-center text-xs text-muted-foreground">{t("noPhotoHint")}</p>
      ) : null}
    </div>
  );
}
