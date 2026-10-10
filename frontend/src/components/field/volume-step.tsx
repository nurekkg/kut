"use client";

import { useTranslations } from "next-intl";

import { Field } from "@/components/auth/form-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatShortDate, todayIso } from "@/lib/schedule/dates";
import { formatVolume, toNumber } from "@/lib/schedule/format";
import type { ScheduleItem } from "@/lib/schedule/types";

const VOLUME = /^\d{1,12}([.]\d{1,3})?$/;

// На телефонах с русской раскладкой цифровая клавиатура даёт запятую: принимаем и её.
export function normalizeVolume(raw: string): string {
  return raw.trim().replace(",", ".");
}

export function isValidVolume(raw: string): boolean {
  const value = normalizeVolume(raw);
  return VOLUME.test(value) && toNumber(value) > 0;
}

// Шаг «объём»: крупное поле с цифровой клавиатурой, единица рядом с полем, остаток по плану.
export function VolumeStep({
  unit,
  item,
  volume,
  workDate,
  onVolume,
  onDate,
  onNext,
}: {
  unit: string;
  item: ScheduleItem | null;
  volume: string;
  workDate: string;
  onVolume: (value: string) => void;
  onDate: (value: string) => void;
  onNext: () => void;
}) {
  const t = useTranslations("Field.volume");
  const today = todayIso();
  const valid = isValidVolume(volume);
  const confirmed = toNumber(item?.progress?.confirmed_volume);
  const left = item ? Math.max(toNumber(item.plan_volume) - confirmed, 0) : null;
  const entered = valid ? toNumber(normalizeVolume(volume)) : 0;

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && workDate <= today) onNext();
      }}
    >
      <Field id="field-volume" label={t("label")} hint={left !== null ? t("left", { left: formatVolume(left), unit }) : t("noPlan")}>
        <div className="flex items-center gap-3">
          <Input
            id="field-volume"
            inputMode="decimal"
            enterKeyHint="next"
            autoComplete="off"
            autoFocus
            className="h-16 flex-1 text-3xl font-semibold tabular-nums"
            placeholder="0"
            value={volume}
            onChange={(event) => onVolume(event.target.value)}
            aria-describedby="field-volume-hint"
            aria-invalid={volume !== "" && !valid}
            data-testid="volume-input"
          />
          <span className="text-2xl font-medium" data-testid="volume-unit">
            {unit}
          </span>
        </div>
      </Field>
      {volume !== "" && !valid ? (
        <p role="alert" className="text-sm text-destructive">
          {t("invalid")}
        </p>
      ) : null}
      {valid && left !== null && entered > left ? (
        <p className="text-sm text-amber-700 dark:text-amber-400">{t("overPlan")}</p>
      ) : null}

      <Field
        id="field-date"
        label={t("date")}
        hint={workDate === today ? t("dateToday", { date: formatShortDate(workDate) }) : undefined}
      >
        <Input
          id="field-date"
          type="date"
          max={today}
          required
          className="h-14 text-base"
          value={workDate}
          onChange={(event) => onDate(event.target.value)}
          aria-describedby="field-date-hint"
        />
      </Field>

      <Button
        type="submit"
        size="lg"
        className="h-14 w-full text-base"
        disabled={!valid || workDate === "" || workDate > today}
        data-testid="volume-next"
      >
        {t("next")}
      </Button>
    </form>
  );
}
