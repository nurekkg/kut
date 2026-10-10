"use client";

import { Camera, Loader2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { newPhoto } from "@/lib/field/entry";
import { compressPhoto } from "@/lib/field/image";
import type { OutboxPhoto } from "@/lib/field/outbox-types";

export const MAX_PHOTOS = 10;

// Съёмка и список снимков факта. На телефоне input с capture открывает камеру; снимок сразу
// сжимается (длинная сторона до 2560 px, JPEG) и остаётся только маленькая картинка для списка.
export function PhotoStrip({
  photos,
  onAdd,
  onRemove,
}: {
  photos: OutboxPhoto[];
  onAdd: (added: OutboxPhoto[]) => void;
  onRemove: (id: string) => void;
}) {
  const t = useTranslations("Field.photos");
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(0);
  const [failed, setFailed] = useState<string[]>([]);
  const room = MAX_PHOTOS - photos.length;

  async function pick(list: FileList | null) {
    const files = list ? Array.from(list).slice(0, Math.max(room, 0)) : [];
    if (input.current) input.current.value = "";
    if (files.length === 0) return;
    setFailed([]);
    setBusy(files.length);
    const added: OutboxPhoto[] = [];
    const bad: string[] = [];
    for (const file of files) {
      try {
        added.push(newPhoto(await compressPhoto(file), file.name));
      } catch {
        bad.push(file.name);
      }
      setBusy((count) => count - 1);
    }
    if (added.length > 0) onAdd(added);
    setFailed(bad);
  }

  return (
    <div className="space-y-3">
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        data-testid="photo-input"
        onChange={(event) => void pick(event.target.files)}
      />
      <Button
        type="button"
        variant="outline"
        size="lg"
        className="h-14 w-full text-base"
        disabled={busy > 0 || room <= 0}
        onClick={() => input.current?.click()}
      >
        {busy > 0 ? (
          <Loader2 data-icon="inline-start" className="size-5 motion-safe:animate-spin" aria-hidden />
        ) : (
          <Camera data-icon="inline-start" className="size-5" aria-hidden />
        )}
        {busy > 0 ? t("processing") : photos.length === 0 ? t("take") : t("takeMore")}
      </Button>
      {room <= 0 ? <p className="text-sm text-muted-foreground">{t("limit", { count: MAX_PHOTOS })}</p> : null}
      {failed.length > 0 ? (
        <p role="alert" className="text-sm text-destructive">
          {t("unsupported", { names: failed.join(", ") })}
        </p>
      ) : null}
      {photos.length > 0 ? (
        <ul className="grid grid-cols-3 gap-2" data-testid="photo-list">
          {photos.map((photo, position) => (
            <li key={photo.id} className="relative">
              {/* eslint-disable-next-line @next/next/no-img-element -- маленькая картинка из памяти устройства */}
              <img
                src={photo.thumb}
                alt={t("alt", { number: position + 1 })}
                className="aspect-square w-full rounded-lg border object-cover"
              />
              <button
                type="button"
                onClick={() => onRemove(photo.id)}
                aria-label={t("remove", { number: position + 1 })}
                className="absolute -top-2 -right-2 flex size-11 items-center justify-center rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span className="flex size-7 items-center justify-center rounded-full bg-background shadow ring-1 ring-foreground/20">
                  <X className="size-4" aria-hidden />
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
