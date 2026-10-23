"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { api } from "@/lib/api";
import { API } from "@/lib/projects/keys";
import type { Project } from "@/lib/projects/types";

// Раздел «Склад»: объекты, по которым у пользователя есть склад. Для кладовщика - ещё экран телефона.
export function StockIndex() {
  const t = useTranslations("Stock.index");
  const { user } = useAuth();
  const canRead = (user?.is_internal ?? false) && (user?.permissions.includes("materials:read") ?? false);
  const canStock = user?.permissions.includes("materials:write:stock") ?? false;
  const projects = useQuery({
    queryKey: ["projects", "stock-index"],
    enabled: canRead,
    queryFn: () => api.get<Project[]>(`${API}/projects`),
  });

  if (!canRead) return <p className="p-6 text-sm text-muted-foreground">{t("noAccess")}</p>;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-4 px-4 py-6">
      <h1 className="text-2xl font-semibold">{t("title")}</h1>
      <div className="flex gap-3 text-sm">
        <Link className="text-primary underline-offset-4 hover:underline" href="/materials">
          {t("materials")}
        </Link>
      </div>
      {projects.isPending && <p className="text-sm text-muted-foreground">{t("loading")}</p>}
      <ul className="divide-y rounded-md border">
        {projects.data?.map((project) => (
          <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3" key={project.id}>
            <span className="font-medium">{project.name}</span>
            <div className="flex gap-3 text-sm">
              <Link
                className="text-primary underline-offset-4 hover:underline"
                href={`/projects/${project.id}/warehouse`}
              >
                {t("warehouse")}
              </Link>
              {canStock && (
                <Link className="text-primary underline-offset-4 hover:underline" href={`/m/stock/${project.id}`}>
                  {t("phone")}
                </Link>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
