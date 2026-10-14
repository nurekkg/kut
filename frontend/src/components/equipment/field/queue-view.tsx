"use client";

import { useTranslations } from "next-intl";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { equipmentErrorKey } from "@/lib/equipment/errors";
import { discardShift, flushShifts, retryShift } from "@/lib/equipment/field/queue";
import type { ShiftEntry } from "@/lib/equipment/field/types";

// Очередь машиниста на устройстве: что не ушло на сервер, с причиной. Повтор и удаление - по записи.
export function QueueView({ entries, busy }: { entries: ShiftEntry[]; busy: string | null }) {
  const t = useTranslations("EquipmentField.queue");
  const tErrors = useTranslations("Equipment.errors");
  const { user } = useAuth();

  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("empty")}</p>;
  }
  return (
    <div className="space-y-3" data-testid="shift-queue">
      <h2 className="text-base font-semibold">{t("title")}</h2>
      <Button
        type="button"
        size="lg"
        className="h-12 w-full"
        disabled={busy !== null || !user}
        onClick={() => {
          if (user) void flushShifts(user.id);
        }}
        data-testid="queue-send-now"
      >
        {t("sendNow")}
      </Button>
      <ul className="space-y-3">
        {entries.map((entry) => (
          <li key={entry.id} className="space-y-2 rounded-xl border p-4" data-testid="queue-entry">
            <p className="font-medium">{entry.label.unit}</p>
            <p className="text-sm text-muted-foreground">
              {entry.label.project} · {entry.label.date}
            </p>
            <p
              className="text-sm"
              data-testid="queue-stage"
              data-stage={entry.stage}
            >
              {entry.stage === "failed"
                ? t("failed", { error: tErrors(equipmentErrorKey(entry.error ?? "unknown")) })
                : t("pending")}
            </p>
            <div className="grid grid-cols-2 gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-12"
                onClick={() => void retryShift(entry.id)}
                data-testid="queue-retry"
              >
                {t("retry")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                className="h-12"
                onClick={() => void discardShift(entry.id)}
                data-testid="queue-discard"
              >
                {t("discard")}
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
