import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { UserMenu } from "@/components/auth/user-menu";
import { AppNav } from "@/components/projects/app-nav";
import { WorksPanel } from "@/components/projects/works-panel";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Works");
  return { title: t("metadataTitle") };
}

export default function WorksPage() {
  return (
    <AuthGate>
      <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-6">
        <UserMenu />
        <AppNav />
        <WorksPanel />
      </main>
    </AuthGate>
  );
}
