import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AuthGate } from "@/components/auth/auth-gate";
import { UserMenu } from "@/components/auth/user-menu";
import { GroupDashboard } from "@/components/dashboards/group-dashboard";
import { AppNav } from "@/components/projects/app-nav";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("Dashboard");
  return { title: t("metadataTitle") };
}

export default function DashboardPage() {
  return (
    <AuthGate>
      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 px-4 py-6">
        <UserMenu />
        <AppNav />
        <GroupDashboard />
      </main>
    </AuthGate>
  );
}
