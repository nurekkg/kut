"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { GeoLink } from "@/components/field/geo-link";
import { PhotoImage } from "@/components/field/photo-image";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatDateTime } from "@/lib/format";
import type { Photo } from "@/lib/field/types";

export function takenAt(photo: Photo): string {
  return formatDateTime(photo.taken_at ?? photo.created_at);
}

// Просмотр снимка крупно: дата, автор, геометка и листание по списку. Снимок открывается по
// подписанной ссылке, срок которой короткий (см. PhotoImage).
export function PhotoLightbox({
  projectId,
  photos,
  index,
  onIndex,
  onClose,
  describe,
}: {
  projectId: string;
  photos: Photo[];
  index: number | null;
  onIndex: (next: number) => void;
  onClose: () => void;
  describe?: (photo: Photo) => string;
}) {
  const t = useTranslations("PhotoViewer");
  const current = index !== null ? (photos[index] ?? null) : null;

  return (
    <Dialog open={current !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent closeLabel={t("close")} className="max-w-4xl">
        {current && index !== null ? (
          <>
            <DialogHeader>
              <DialogTitle>{current.work_name ?? t("photo")}</DialogTitle>
              <DialogDescription>{describe?.(current) ?? ""}</DialogDescription>
            </DialogHeader>
            <PhotoImage
              key={`${current.id}:${current.url}`}
              projectId={projectId}
              photo={current}
              alt={t("alt", { work: current.work_name ?? "", date: takenAt(current) })}
              lazy={false}
              className="max-h-[65dvh] w-full rounded-lg bg-muted object-contain"
            />
            <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">{t("date")}</dt>
                <dd>{takenAt(current)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("author")}</dt>
                <dd>{current.uploaded_by_name ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("geo")}</dt>
                <dd>
                  {current.latitude && current.longitude ? (
                    <GeoLink
                      latitude={current.latitude}
                      longitude={current.longitude}
                      label={t("openMap")}
                    />
                  ) : (
                    t("noGeo")
                  )}
                </dd>
              </div>
            </dl>
            <div className="flex items-center justify-between gap-2">
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-11"
                disabled={index <= 0}
                onClick={() => onIndex(index - 1)}
              >
                <ChevronLeft data-icon="inline-start" aria-hidden />
                {t("previous")}
              </Button>
              <span className="text-xs text-muted-foreground">
                {index + 1} / {photos.length}
              </span>
              <Button
                type="button"
                variant="outline"
                size="lg"
                className="h-11"
                disabled={index >= photos.length - 1}
                onClick={() => onIndex(index + 1)}
              >
                {t("next")}
                <ChevronRight data-icon="inline-end" aria-hidden />
              </Button>
            </div>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
