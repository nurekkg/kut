"use client";

import { useTranslations } from "next-intl";
import { useRef, useState, type ChangeEvent } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { newIdempotencyKey } from "@/lib/api";
import { equipmentErrorKey } from "@/lib/equipment/errors";
import { submitNewShift, submitResubmit } from "@/lib/equipment/field/queue";
import type { FuelDraft, ReceiptDraft, ShiftEntry, SubmitOutcome } from "@/lib/equipment/field/types";
import { useQuantityUnit, type ShiftRow } from "@/lib/equipment/shifts";
import { compressPhoto } from "@/lib/field/image";

export type FormTarget =
  | {
      kind: "new";
      assignmentId: string;
      unitId: string;
      unitLabel: string;
      projectId: string;
      projectName: string;
      operatorId: string;
      workDate: string;
      plannedHours: string;
    }
  | { kind: "fix"; shift: ShiftRow; unitLabel: string; projectName: string; operatorId: string };

type Values = {
  hours: string;
  trips: string;
  tonnes: string;
  odometerFrom: string;
  odometerTo: string;
  downtime: string;
  downtimeReason: string;
  liters: string;
  amount: string;
};

type Problem = "hours" | "trips" | "tonnes" | "odometer" | "downtimeReason" | "amount";

const DECIMAL = /^\d{1,4}(\.\d{1,3})?$/;
const INTEGER = /^\d{1,4}$/;

function normal(value: string): string {
  return value.trim().replace(",", ".");
}

function initialValues(target: FormTarget): Values {
  if (target.kind === "new") {
    return {
      hours: target.plannedHours.replace(/\.?0+$/, "") || "8",
      trips: "",
      tonnes: "",
      odometerFrom: "",
      odometerTo: "",
      downtime: "0",
      downtimeReason: "",
      liters: "",
      amount: "",
    };
  }
  const shift = target.shift;
  return {
    hours: shift.hours,
    trips: shift.trips === null ? "" : String(shift.trips),
    tonnes: shift.tonnes ?? "",
    odometerFrom: shift.odometer_from ?? "",
    odometerTo: shift.odometer_to ?? "",
    downtime: shift.downtime_hours,
    downtimeReason: shift.downtime_reason ?? "",
    liters: "",
    amount: "",
  };
}

// Экран смены машиниста: количества - по подсказке единицы тарифа (что вводить), ставка не показана.
// Отправка идёт через очередь: проходит сразу, либо ждёт связи, либо отказ сервера показываем здесь.
// Что вводить по единице тарифа: текст подсказки; неизвестная или скрытая единица - общий текст.
function hintText(tariff: string | null, t: (key: "hint.hour" | "hint.trip" | "hint.tonne" | "hint.motor_hour" | "hint.shift" | "hint.unknown") => string): string {
  switch (tariff) {
    case "hour":
      return t("hint.hour");
    case "trip":
      return t("hint.trip");
    case "tonne":
      return t("hint.tonne");
    case "motor_hour":
      return t("hint.motor_hour");
    case "shift":
      return t("hint.shift");
    default:
      return t("hint.unknown");
  }
}

export function ShiftForm({
  target,
  onBack,
  onFinished,
}: {
  target: FormTarget;
  onBack: () => void;
  onFinished: (outcome: SubmitOutcome) => void;
}) {
  const t = useTranslations("EquipmentField.form");
  const tErrors = useTranslations("Equipment.errors");
  const { user } = useAuth();
  const unitId = target.kind === "new" ? target.unitId : target.shift.equipment_unit_id;
  const operatorId = target.operatorId;
  const workDate = target.kind === "new" ? target.workDate : target.shift.work_date;
  const projectId = target.kind === "new" ? target.projectId : target.shift.project_id;
  const projectName = target.projectName;
  const unitLabel = target.unitLabel;
  const hint = useQuantityUnit(unitId, operatorId, workDate);
  const [values, setValues] = useState<Values>(() => initialValues(target));
  const [photo, setPhoto] = useState<{ blob: Blob; thumb: string } | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const tariff = hint.isError ? "unknown" : (hint.data?.tariff_unit ?? null);
  const needTrips = tariff === "trip";
  const needTonnes = tariff === "tonne";
  const needOdometer = tariff === "motor_hour";
  const fuelActive = values.liters.trim() !== "" || values.amount.trim() !== "" || photo !== null;

  function set<K extends keyof Values>(key: K, value: string): void {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function validate(): Problem | null {
    const hours = Number(normal(values.hours));
    if (!DECIMAL.test(normal(values.hours)) || hours <= 0 || hours > 24) return "hours";
    if (needTrips && !INTEGER.test(values.trips.trim())) return "trips";
    if (needTonnes && !DECIMAL.test(normal(values.tonnes))) return "tonnes";
    if (needOdometer) {
      const from = normal(values.odometerFrom);
      const to = normal(values.odometerTo);
      if (!DECIMAL.test(from) || !DECIMAL.test(to) || Number(to) < Number(from)) return "odometer";
    }
    const downtime = normal(values.downtime || "0");
    if (!DECIMAL.test(downtime) || Number(downtime) > 24) return "hours";
    if (Number(downtime) > 0 && values.downtimeReason.trim() === "") return "downtimeReason";
    if (fuelActive && !DECIMAL.test(normal(values.amount))) return "amount";
    return null;
  }

  async function onPhoto(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setPhoto(await compressPhoto(file));
  }

  async function submit(): Promise<void> {
    if (busy || !user) return;
    const found = validate();
    setProblem(found);
    if (found) return;
    setBusy(true);
    setFailure(null);
    const downtime = normal(values.downtime || "0");
    const hours = normal(values.hours);
    const trips = needTrips ? Number(values.trips.trim()) : null;
    const tonnes = needTonnes ? normal(values.tonnes) : null;
    const odometerFrom = needOdometer ? normal(values.odometerFrom) : null;
    const odometerTo = needOdometer ? normal(values.odometerTo) : null;
    const downtimeReason = Number(downtime) > 0 ? values.downtimeReason.trim() : null;
    try {
      if (target.kind === "new") {
        const receipt: ReceiptDraft | null = photo
          ? {
              blob: photo.blob,
              sizeBytes: photo.blob.size,
              contentType: "image/jpeg",
              key: null,
              uploadUrl: null,
              headers: {},
              presignedAt: null,
              expiresIn: 0,
              uploaded: false,
              confirmed: false,
            }
          : null;
        const fuel: FuelDraft | null = fuelActive
          ? {
              liters: values.liters.trim() === "" ? null : normal(values.liters),
              amount: normal(values.amount),
              key: newIdempotencyKey(),
              fuelId: null,
              receipt,
            }
          : null;
        const entry: ShiftEntry = {
          id: newIdempotencyKey(),
          userId: user.id,
          createdAt: Date.now(),
          mode: "create",
          label: { unit: unitLabel, project: projectName, date: workDate },
          shift: {
            equipment_unit_id: unitId,
            project_id: projectId,
            work_date: workDate,
            operator_id: operatorId,
            hours,
            odometer_from: odometerFrom,
            odometer_to: odometerTo,
            trips,
            tonnes,
            downtime_hours: downtime,
            downtime_reason: downtimeReason,
          },
          createKey: newIdempotencyKey(),
          shiftId: null,
          patch: null,
          patched: false,
          fuel,
          submitted: false,
          stage: "pending",
          error: null,
          attempts: 0,
        };
        const outcome = await submitNewShift(entry);
        if (outcome.kind === "failed" && !outcome.kept) {
          setFailure(tErrors(equipmentErrorKey(outcome.code)));
          return;
        }
        onFinished(outcome);
        return;
      }
      const shift = target.shift;
      const entry: ShiftEntry = {
        id: newIdempotencyKey(),
        userId: user.id,
        createdAt: Date.now(),
        mode: "resubmit",
        label: { unit: unitLabel, project: projectName, date: shift.work_date },
        shift: {
          equipment_unit_id: shift.equipment_unit_id,
          project_id: shift.project_id,
          work_date: shift.work_date,
          operator_id: shift.operator_id,
          hours,
          odometer_from: odometerFrom,
          odometer_to: odometerTo,
          trips,
          tonnes,
          downtime_hours: downtime,
          downtime_reason: downtimeReason,
        },
        createKey: newIdempotencyKey(),
        shiftId: shift.id,
        patch: {
          hours,
          odometer_from: odometerFrom,
          odometer_to: odometerTo,
          trips,
          tonnes,
          downtime_hours: downtime,
          downtime_reason: downtimeReason,
        },
        patched: false,
        fuel: null,
        submitted: false,
        stage: "pending",
        error: null,
        attempts: 0,
      };
      const outcome = await submitResubmit(entry);
      if (outcome.kind === "failed" && !outcome.kept) {
        setFailure(tErrors(equipmentErrorKey(outcome.code)));
        return;
      }
      onFinished(outcome);
    } finally {
      setBusy(false);
    }
  }

  const problemText = problem === null ? null : t(`error.${problem}`);

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      data-testid="shift-form"
    >
      <div className="space-y-1">
        <h1 className="text-lg font-semibold" data-testid="form-title">
          {t("title", { unit: unitLabel })}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t("projectLabel", { name: projectName })} · {t("date")}: {workDate}
        </p>
        {target.kind === "fix" && target.shift.reject_reason ? (
          <p className="rounded-lg bg-muted p-3 text-sm" data-testid="reject-reason">
            {t("rejectedNote", { reason: target.shift.reject_reason })}
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor="shift-hours" className="text-sm">
          {t("hours")}
        </Label>
        <Input
          id="shift-hours"
          inputMode="decimal"
          className="h-12 text-base"
          value={values.hours}
          onChange={(event) => set("hours", event.target.value)}
          data-testid="field-hours"
        />
        <p className="text-sm text-muted-foreground" data-testid="quantity-hint">
          {hintText(tariff, t)}
        </p>
      </div>

      {needTrips ? (
        <div className="space-y-2">
          <Label htmlFor="shift-trips" className="text-sm">
            {t("trips")}
          </Label>
          <Input
            id="shift-trips"
            inputMode="numeric"
            className="h-12 text-base"
            value={values.trips}
            onChange={(event) => set("trips", event.target.value)}
            data-testid="field-trips"
          />
        </div>
      ) : null}

      {needTonnes ? (
        <div className="space-y-2">
          <Label htmlFor="shift-tonnes" className="text-sm">
            {t("tonnes")}
          </Label>
          <Input
            id="shift-tonnes"
            inputMode="decimal"
            className="h-12 text-base"
            value={values.tonnes}
            onChange={(event) => set("tonnes", event.target.value)}
            data-testid="field-tonnes"
          />
        </div>
      ) : null}

      {needOdometer ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label htmlFor="shift-odo-from" className="text-sm">
              {t("odometerFrom")}
            </Label>
            <Input
              id="shift-odo-from"
              inputMode="decimal"
              className="h-12 text-base"
              value={values.odometerFrom}
              onChange={(event) => set("odometerFrom", event.target.value)}
              data-testid="field-odometer-from"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="shift-odo-to" className="text-sm">
              {t("odometerTo")}
            </Label>
            <Input
              id="shift-odo-to"
              inputMode="decimal"
              className="h-12 text-base"
              value={values.odometerTo}
              onChange={(event) => set("odometerTo", event.target.value)}
              data-testid="field-odometer-to"
            />
          </div>
        </div>
      ) : null}

      <div className="space-y-2">
        <Label htmlFor="shift-downtime" className="text-sm">
          {t("downtime")}
        </Label>
        <Input
          id="shift-downtime"
          inputMode="decimal"
          className="h-12 text-base"
          value={values.downtime}
          onChange={(event) => set("downtime", event.target.value)}
          data-testid="field-downtime"
        />
        {Number(normal(values.downtime || "0")) > 0 ? (
          <Input
            aria-label={t("downtimeReason")}
            placeholder={t("downtimeReason")}
            className="h-12 text-base"
            value={values.downtimeReason}
            onChange={(event) => set("downtimeReason", event.target.value)}
            data-testid="field-downtime-reason"
          />
        ) : null}
      </div>

      {target.kind === "new" ? (
        <fieldset className="space-y-3 rounded-xl border p-4">
          <legend className="px-1 text-sm font-medium">{t("fuel")}</legend>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="fuel-liters" className="text-sm">
                {t("liters")}
              </Label>
              <Input
                id="fuel-liters"
                inputMode="decimal"
                className="h-12 text-base"
                value={values.liters}
                onChange={(event) => set("liters", event.target.value)}
                data-testid="field-liters"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="fuel-amount" className="text-sm">
                {t("amount")}
              </Label>
              <Input
                id="fuel-amount"
                inputMode="decimal"
                className="h-12 text-base"
                value={values.amount}
                onChange={(event) => set("amount", event.target.value)}
                data-testid="field-amount"
              />
            </div>
          </div>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={(event) => void onPhoto(event)}
            data-testid="receipt-input"
          />
          {photo ? (
            <div className="flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={photo.thumb} alt="" className="size-16 rounded-lg object-cover" />
              <div className="space-y-2">
                <p className="text-sm">{t("receiptSaved")}</p>
                <Button
                  type="button"
                  variant="outline"
                  className="h-12"
                  onClick={() => fileInput.current?.click()}
                >
                  {t("replaceReceipt")}
                </Button>
              </div>
            </div>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-12 w-full"
              onClick={() => fileInput.current?.click()}
              data-testid="receipt-add"
            >
              {t("addReceipt")}
            </Button>
          )}
        </fieldset>
      ) : null}

      {problemText ? (
        <p role="alert" className="text-sm text-destructive" data-testid="form-problem">
          {problemText}
        </p>
      ) : null}
      {failure ? (
        <p role="alert" className="text-sm text-destructive" data-testid="form-failure">
          {failure}
        </p>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-30 space-y-2 border-t bg-background p-4 pb-[calc(1rem+env(safe-area-inset-bottom))]">
        <Button
          type="submit"
          size="lg"
          className="h-14 w-full text-base"
          disabled={busy}
          data-testid="form-submit"
        >
          {busy ? t("saving") : t("submit")}
        </Button>
        <Button type="button" variant="ghost" className="h-12 w-full" onClick={onBack}>
          {t("back")}
        </Button>
      </div>
    </form>
  );
}
