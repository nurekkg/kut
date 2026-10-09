"use client";

import { AlertTriangle, Check, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { GeoLink } from "@/components/field/geo-link";
import { PhotoImage } from "@/components/field/photo-image";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";
import type { Actual, Photo } from "@/lib/field/types";
import { formatShortDate } from "@/lib/schedule/dates";
import { clampPercent, formatPercent, formatVolume, toNumber } from "@/lib/schedule/format";

const THUMBS = 4;

// Одна запись на проверке: что внесено, сколько это от плана, где и кем снято, с фото. Решение
// принимается тут же: подтвердить или отклонить с причиной.
export function ApprovalCard({
  fact,
  label,
  photos,
  photosLoading,
  selected,
  busy,
  error,
  onSelect,
  onApprove,
  onReject,
  onOpenPhoto,
}: {
  fact: Actual;
  label: string;
  photos: Photo[];
  photosLoading: boolean;
  selected: boolean;
  busy: boolean;
  error: string | null;
  onSelect: (checked: boolean) => void;
  onApprove: () => void;
  onReject: () => void;
  onOpenPhoto: (index: number) => void;
}) {
  const t = useTranslations("Approvals.card");
  const unit = fact.unit ?? "";
  const volume = toNumber(fact.volume);
  const plan = fact.item_plan_volume === null ? null : toNumber(fact.item_plan_volume);
  const confirmed = toNumber(fact.item_confirmed_volume);
  const after = confirmed + volume;
  const pctConfirmed = plan ? clampPercent((confirmed / plan) * 100) : 0;
  const pctThis = plan ? Math.min(clampPercent((volume / plan) * 100), 100 - pctConfirmed) : 0;
  const over = plan !== null && after > plan;
  // Прораб не должен подтверждать, не увидев доказательств: если снимков пришло меньше, чем есть у
  // записи (сбой запроса, обрезанный ответ), карточка говорит об этом, а не молчит.
  const expected = fact.photo_count ?? 0;
  const photosMissing = !photosLoading && photos.length < expected;

  return (
    <li
      data-testid="approval"
      data-fact={fact.id}
      className="space-y-3 rounded-xl border p-3 text-sm sm:p-4"
    >
      <div className="flex items-start gap-3">
        <label className="flex size-11 shrink-0 cursor-pointer items-center justify-center">
          <input
            type="checkbox"
            checked={selected}
            onChange={(event) => onSelect(event.target.checked)}
            aria-label={t("select", { work: fact.work_name ?? "" })}
            data-testid="approval-select"
            className="size-5 accent-primary"
          />
        </label>
        <div className="min-w-0 flex-1 space-y-0.5">
          <h2 className="text-base font-semibold break-words" data-testid="approval-work">
            {fact.work_name ?? "—"}
          </h2>
          <p className="text-muted-foreground break-words">{label}</p>
          <p className="text-xs text-muted-foreground">
            {t("author", { name: fact.author_name ?? "—" })} · {t("workDate", { date: formatShortDate(fact.work_date) })}{" "}
            · {t("sent", { time: formatDateTime(fact.created_at) })}
          </p>
        </div>
      </div>

      <div className="space-y-1.5 rounded-lg bg-muted/50 p-3">
        <p className="text-base font-medium tabular-nums" data-testid="approval-volume">
          {formatVolume(volume)} {unit}
        </p>
        {plan !== null ? (
          <>
            <div
              role="progressbar"
              aria-label={t("vsPlanLabel")}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(pctConfirmed + pctThis)}
              className="flex h-2 overflow-hidden rounded-full bg-muted"
            >
              <div className="h-full bg-primary" style={{ width: `${pctConfirmed}%` }} />
              <div className="h-full bg-primary/40" style={{ width: `${pctThis}%` }} />
            </div>
            <p className="text-xs text-muted-foreground tabular-nums" data-testid="approval-plan">
              {t("vsPlan", {
                plan: formatVolume(plan),
                confirmed: formatVolume(confirmed),
                after: formatVolume(after),
                percent: formatPercent(plan > 0 ? Math.min((after / plan) * 100, 100) : 0),
                unit,
              })}
            </p>
            {over ? (
              <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
                <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
                {t("overPlan", { over: formatVolume(after - plan), unit })}
              </p>
            ) : null}
          </>
        ) : (
          <p className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
            {t("unplanned")}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {fact.latitude && fact.longitude ? (
          <GeoLink
            latitude={fact.latitude}
            longitude={fact.longitude}
            accuracy={fact.accuracy_m}
            label={t("openMap")}
          />
        ) : (
          <span>{t("noGeo")}</span>
        )}
        <span>{t("photos", { count: fact.photo_count ?? 0 })}</span>
      </div>

      {photosMissing ? (
        <p
          role="status"
          data-testid="photos-missing"
          className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400"
        >
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
          {t("photosMissing", { shown: photos.length, total: expected })}
        </p>
      ) : null}

      {photos.length > 0 ? (
        <ul className="flex gap-2" data-testid="approval-photos">
          {photos.slice(0, THUMBS).map((photo, position) => (
            <li key={photo.id} className="relative">
              <button
                type="button"
                onClick={() => onOpenPhoto(position)}
                aria-label={t("openPhoto", { number: position + 1 })}
                className="block overflow-hidden rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <PhotoImage
                  key={`${photo.id}:${photo.url}`}
                  projectId={fact.project_id}
                  photo={photo}
                  alt={t("photoAlt", { number: position + 1 })}
                  className="size-20 object-cover sm:size-24"
                />
                {position === THUMBS - 1 && photos.length > THUMBS ? (
                  <span className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/55 text-lg font-semibold text-white">
                    +{photos.length - THUMBS + 1}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-2">
        <Button
          type="button"
          size="lg"
          className="h-12"
          disabled={busy}
          onClick={onApprove}
          data-testid="approve"
        >
          <Check data-icon="inline-start" aria-hidden />
          {t("approve")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="h-12"
          disabled={busy}
          onClick={onReject}
          data-testid="reject"
        >
          <X data-icon="inline-start" aria-hidden />
          {t("reject")}
        </Button>
      </div>
    </li>
  );
}
