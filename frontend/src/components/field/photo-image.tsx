"use client";

import { ImageOff } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";
import type { DownloadUrl, Photo } from "@/lib/field/types";
import { API } from "@/lib/projects/keys";
import { cn } from "@/lib/utils";

// Снимок по подписанной ссылке. Ссылка живёт две минуты, а страницу могли оставить открытой:
// при ошибке загрузки один раз просим у сервера свежую, дальше показываем заглушку.
// Родитель задаёт key = id + url: новая ссылка из обновлённого списка сбрасывает состояние.
export function PhotoImage({
  projectId,
  photo,
  alt,
  className,
  lazy = true,
}: {
  projectId: string;
  photo: Pick<Photo, "id" | "url">;
  alt: string;
  className?: string;
  lazy?: boolean;
}) {
  const [src, setSrc] = useState(photo.url ?? "");
  const [failed, setFailed] = useState(photo.url === null);
  const [renewed, setRenewed] = useState(false);

  async function renew() {
    if (renewed) {
      setFailed(true);
      return;
    }
    setRenewed(true);
    try {
      const fresh = await api.get<DownloadUrl>(
        `${API}/projects/${projectId}/photos/${photo.id}/download-url`,
      );
      setSrc(fresh.url);
    } catch {
      setFailed(true);
    }
  }

  if (failed) {
    return (
      <div className={cn("flex items-center justify-center bg-muted text-muted-foreground", className)}>
        <ImageOff className="size-6" aria-hidden />
        <span className="sr-only">{alt}</span>
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка на S3: оптимизатор Next здесь не нужен
    <img
      src={src}
      alt={alt}
      loading={lazy ? "lazy" : "eager"}
      decoding="async"
      onError={() => void renew()}
      className={className}
    />
  );
}
