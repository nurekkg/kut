"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { MapPin } from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";

import { PhotoImage } from "@/components/field/photo-image";
import { PhotoLightbox, takenAt } from "@/components/field/photo-lightbox";
import { QueryState } from "@/components/settings/shared";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Photo } from "@/lib/field/types";
import { API } from "@/lib/projects/keys";
import { pathLabel, type TreeIndex } from "@/lib/projects/tree";
import type { TreeNode } from "@/lib/projects/types";
import { photosKey } from "@/lib/schedule/keys";

const PAGE = 48;

// Вкладка «Фото»: снимки фактов узла и всех его потомков, новые сверху. Ссылки подписаны
// сервером на две минуты, поэтому список всегда запрашивается заново (без кэша).
export function NodeMediaTab({
  projectId,
  node,
  index,
}: {
  projectId: string;
  node: TreeNode;
  index: TreeIndex;
}) {
  const t = useTranslations("NodeMedia");
  const [opened, setOpened] = useState<number | null>(null);

  const photos = useInfiniteQuery({
    queryKey: photosKey(projectId, `node:${node.id}`),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api.get<Photo[]>(`${API}/projects/${projectId}/photos`, {
        node_id: node.id,
        subtree: true,
        newest_first: true,
        with_urls: true,
        limit: PAGE,
        offset: pageParam,
      }),
    getNextPageParam: (last, all) => (last.length === PAGE ? all.length * PAGE : undefined),
    staleTime: 0,
    gcTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const items = useMemo(() => photos.data?.pages.flat() ?? [], [photos.data]);

  function describe(photo: Photo): string {
    const owner = photo.node_id ? index.byId.get(photo.node_id) : undefined;
    return owner ? pathLabel(index, owner) : "";
  }

  return (
    <QueryState loading={photos.isPending} error={photos.error} onRetry={() => void photos.refetch()}>
      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground" data-testid="node-media-count">
            {t("count", { count: items.length })}
            {photos.hasNextPage ? ` · ${t("hasMore")}` : ""}
          </p>
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" data-testid="node-media-grid">
            {items.map((photo, position) => (
              <li key={photo.id}>
                <button
                  type="button"
                  onClick={() => setOpened(position)}
                  className="group block w-full overflow-hidden rounded-lg border text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <PhotoImage
                    key={`${photo.id}:${photo.url}`}
                    projectId={projectId}
                    photo={photo}
                    alt={t("alt", { work: photo.work_name ?? "", date: takenAt(photo) })}
                    className="aspect-square w-full object-cover transition-transform group-hover:scale-[1.02]"
                  />
                  <span className="block space-y-0.5 p-2 text-xs">
                    <span className="block truncate font-medium">{photo.work_name ?? "—"}</span>
                    <span className="block truncate text-muted-foreground">
                      {takenAt(photo)} · {photo.uploaded_by_name ?? "—"}
                    </span>
                    {photo.latitude && photo.longitude ? (
                      <span className="flex items-center gap-1 text-muted-foreground">
                        <MapPin className="size-3" aria-hidden />
                        {t("geotag")}
                      </span>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {photos.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="h-10"
              disabled={photos.isFetchingNextPage}
              onClick={() => void photos.fetchNextPage()}
            >
              {photos.isFetchingNextPage ? t("loadingMore") : t("loadMore")}
            </Button>
          ) : null}
        </div>
      )}
      <PhotoLightbox
        projectId={projectId}
        photos={items}
        index={opened}
        onIndex={setOpened}
        onClose={() => setOpened(null)}
        describe={describe}
      />
    </QueryState>
  );
}
