"use client";

import { AlertCircle, CloudOff, Loader2, RotateCw, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { useCodeMessage } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { discardEntry, retryAll, retryEntry, useOutbox } from "@/lib/field/outbox";
import { formatVolume } from "@/lib/schedule/format";

// Записи, которые ещё не дошли до сервера: ждут связи, идут сейчас или отклонены сервером по
// существу. Запись остаётся в очереди, пока сервер не подтвердит её, - потерять факт нельзя.
export function OutboxQueue() {
  const t = useTranslations("Field.queue");
  const codeMessage = useCodeMessage();
  const outbox = useOutbox();
  if (outbox.entries.length === 0) return null;

  return (
    <section aria-labelledby="outbox-title" className="space-y-2" data-testid="outbox">
      <h2 id="outbox-title" className="text-sm font-semibold">
        {t("title", { count: outbox.entries.length })}
      </h2>
      <ul className="space-y-2">
        {outbox.entries.map((entry) => {
          const progress = outbox.progress[entry.id];
          const sending = outbox.busy === entry.id;
          const percent =
            progress && progress.total > 0 ? Math.round((progress.loaded / progress.total) * 100) : 0;
          return (
            <li
              key={entry.id}
              data-testid="outbox-entry"
              data-stage={entry.stage}
              className="space-y-2 rounded-xl border p-3 text-sm"
            >
              <div className="space-y-0.5">
                <p className="font-medium break-words">{entry.label.work}</p>
                <p className="text-muted-foreground break-words">{entry.label.node}</p>
                <p className="tabular-nums">
                  {formatVolume(entry.payload.volume)} {entry.label.unit}
                  {entry.photos.length > 0 ? ` · ${t("photos", { count: entry.photos.length })}` : ""}
                </p>
              </div>
              {entry.stage === "failed" ? (
                <div role="alert" className="space-y-2">
                  <p className="flex items-start gap-2 text-destructive">
                    <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
                    {codeMessage(entry.error ?? "unknown")}
                  </p>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="lg"
                      className="h-12 flex-1"
                      onClick={() => void retryEntry(entry.id)}
                    >
                      <RotateCw data-icon="inline-start" aria-hidden />
                      {t("retry")}
                    </Button>
                    <Button
                      type="button"
                      variant="destructive"
                      size="lg"
                      className="h-12 flex-1"
                      onClick={() => void discardEntry(entry.id)}
                    >
                      <Trash2 data-icon="inline-start" aria-hidden />
                      {t("discard")}
                    </Button>
                  </div>
                </div>
              ) : sending ? (
                <div className="space-y-1.5" role="status">
                  <p className="flex items-center gap-2">
                    <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
                    {progress
                      ? t("uploading", { photo: progress.photo, photos: progress.photos, percent })
                      : t("sending")}
                  </p>
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={percent}
                    className="h-2 overflow-hidden rounded-full bg-muted"
                  >
                    <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between gap-2">
                  <p className="flex items-center gap-2 text-muted-foreground">
                    <CloudOff className="size-4 shrink-0" aria-hidden />
                    {entry.attempts > 0 ? t("waitingForConnection") : t("queued")}
                  </p>
                  <Button type="button" variant="outline" size="lg" className="h-12" onClick={retryAll}>
                    {t("sendNow")}
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
