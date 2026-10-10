"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { UserMenu } from "@/components/auth/user-menu";
import { PortalDashboard } from "@/components/dashboards/portal-dashboard";
import { AppNav } from "@/components/projects/app-nav";
import { ProjectWorkspace } from "@/components/projects/project-workspace";

// Заказчик и субподрядчик видят на объекте только свою сводку: дерево и карточка им не нужны.
export function ProjectEntry({ projectId }: { projectId: string }) {
  const { user } = useAuth();
  if (user?.scope !== "counterparty") return <ProjectWorkspace projectId={projectId} />;
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6">
      <UserMenu />
      <AppNav />
      <PortalDashboard projectId={projectId} />
    </main>
  );
}
