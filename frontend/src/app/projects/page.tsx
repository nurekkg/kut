import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { UserMenu } from "@/components/auth/user-menu";
import { AppNav } from "@/components/projects/app-nav";
import { ProjectList } from "@/components/projects/project-list";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Projects");
  return { title: t("metadataTitle") };
}

export default async function ProjectsPage() {
  const t = await getTranslations("Projects");

  return (
    <AuthGate>
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6">
        <UserMenu />
        <AppNav />
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </header>
        <ProjectList />
      </main>
    </AuthGate>
  );
}
