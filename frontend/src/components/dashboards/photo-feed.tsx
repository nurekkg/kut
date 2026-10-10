"use client";

import { useQuery } from "@tanstack/react-query";
import { ImageOff } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useRef, useState } from "react";

import { EmptyNote } from "@/components/dashboards/section";
import { api } from "@/lib/api";
import { DASH, formatDay } from "@/lib/dashboards/format";
import { usePhotoUrl } from "@/lib/dashboards/queries";
import type { PhotoOut } from "@/lib/dashboards/types";
import { API, treeKey } from "@/lib/projects/keys";
import type { TreeNode } from "@/lib/projects/types";
import { cn } from "@/lib/utils";

// Название узла нет в ответе фото: берём дерево объекта (тот же запрос, что у карточки объекта).
export function PhotoFeed({ projectId, photos }: { projectId: string; photos: PhotoOut[] }) {
  const t = useTranslations("Dashboard.photos");
  const tree = useQuery({
    queryKey: treeKey(projectId),
    queryFn: () => api.get<TreeNode[]>(`${API}/projects/${projectId}/tree`),
    enabled: photos.length > 0,
  });
  const names = useMemo(
    () => new Map((tree.data ?? []).map((node) => [node.id, node.name])),
    [tree.data],
  );

  if (photos.length === 0) return <EmptyNote>{t("empty")}</EmptyNote>;

  return (
    <ul className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {photos.map((photo) => {
        const work = photo.work_name ?? DASH;
        const node = (photo.node_id && names.get(photo.node_id)) || DASH;
        const date = formatDay(photo.work_date ?? photo.created_at.slice(0, 10));
        return (
          <li key={photo.id} data-testid="photo-item" className="min-w-0 space-y-1.5">
            <PhotoThumb projectId={projectId} photoId={photo.id} alt={`${work}, ${node}, ${date}`} />
            <p className="truncate text-sm font-medium">{work}</p>
            <p className="truncate text-xs text-muted-foreground">
              {node} · {date}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

// Подписанная ссылка живёт около двух минут: при ошибке просим свежую один раз, дальше заглушка.
function PhotoThumb({ projectId, photoId, alt }: { projectId: string; photoId: string; alt: string }) {
  const t = useTranslations("Dashboard.photos");
  const link = usePhotoUrl(projectId, photoId);
  const [failed, setFailed] = useState(false);
  const renewed = useRef(false);

  if (failed || link.isError) {
    return (
      <div
        className="flex aspect-square w-full items-center justify-center rounded-lg bg-muted text-muted-foreground"
        role="img"
        aria-label={t("unavailable")}
      >
        <ImageOff className="size-6" aria-hidden />
      </div>
    );
  }
  const src = link.data?.url;
  if (!src) {
    return (
      <div
        aria-hidden
        className={cn("aspect-square w-full rounded-lg bg-muted", "motion-safe:animate-pulse")}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка на S3: оптимизатор Next здесь не нужен
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      className="aspect-square w-full rounded-lg object-cover"
      onError={() => {
        if (renewed.current) {
          setFailed(true);
          return;
        }
        renewed.current = true;
        void link.refetch();
      }}
    />
  );
}
